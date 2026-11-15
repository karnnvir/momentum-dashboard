"""Layer 1: scripted price-momentum scoring for the whole universe.

Produces a 0-100 Momentum score per stock plus early-turn flags, from pure
price/volume data (no fundamentals, no news). This layer runs for every
stock in the universe, so it has to be fast and tolerate the odd ticker
failing (delisted, renamed, no data yet) without killing the whole batch.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import yfinance as yf

NIFTY50_TICKER = "^NSEI"

LOOKBACK_DAYS = 400  # enough for a trailing 200-dma plus 52-week high/low

# Design Spec: Momentum Score weights, the single source of truth also
# rendered on the dashboard's Methodology page - keep these two in sync by
# construction, not by remembering to update both places.
# pct_above_50dma is deliberately excluded: it's reported separately as
# extension_pct so a fresh breakout and an already-extended move don't get
# the same momentum treatment baked in.
MOMENTUM_WEIGHTS = {
    "ret_1m": 0.12,
    "ret_3m": 0.18,
    "ret_6m": 0.12,
    "rs_vs_nifty_3m": 0.18,
    "pct_above_200dma": 0.12,
    "pct_below_52w_high": 0.14,  # closer to 0 (the high) ranks better
    "vol_20d_over_90d": 0.14,
}
MOMENTUM_STRUCTURE_BONUS = 3  # higher-highs/higher-lows structure, flat points
MOMENTUM_TREND_SLOPE_BONUS = 1.5  # per rising DMA (50d, 200d), flat points


@dataclass
class StockMomentum:
    ticker: str
    ret_1m: float | None = None
    ret_3m: float | None = None
    ret_6m: float | None = None
    rs_vs_nifty_1m: float | None = None
    rs_vs_nifty_3m: float | None = None
    rs_trend_up: bool | None = None
    pct_above_50dma: float | None = None
    pct_above_200dma: float | None = None
    dma50_slope_up: bool | None = None
    dma200_slope_up: bool | None = None
    pct_below_52w_high: float | None = None
    vol_20d_over_90d: float | None = None
    higher_highs_lows: bool | None = None
    early_turn_cross_above_50dma: bool = False
    early_turn_rs_turning_up: bool = False
    early_turn_fresh_52w_high: bool = False
    momentum_score: float | None = None
    error: str | None = None
    flags: list = field(default_factory=list)

    def early_turn_any(self) -> bool:
        return (
            self.early_turn_cross_above_50dma
            or self.early_turn_rs_turning_up
            or self.early_turn_fresh_52w_high
        )


def _pct_change(series: pd.Series, days: int) -> float | None:
    if len(series) <= days:
        return None
    return float(series.iloc[-1] / series.iloc[-1 - days] - 1.0)


def _slope_up(series: pd.Series, window: int = 10) -> bool | None:
    """True if the series' own trailing `window`-day slope is positive."""
    if len(series) < window + 1:
        return None
    recent = series.iloc[-window:]
    x = np.arange(len(recent))
    slope = np.polyfit(x, recent.values, 1)[0]
    return bool(slope > 0)


def _higher_highs_lows(close: pd.Series, lookback: int = 60) -> bool | None:
    """Rough structure check: each of the last 3 ~20-day windows makes a
    higher high and a higher low than the window before it."""
    if len(close) < lookback:
        return None
    window = close.iloc[-lookback:]
    thirds = np.array_split(window, 3)
    highs = [t.max() for t in thirds]
    lows = [t.min() for t in thirds]
    return bool(highs[0] < highs[1] < highs[2] and lows[0] < lows[1] < lows[2])


def _score_from_rank(series: pd.Series) -> pd.Series:
    """Percentile-rank a metric across the universe into a 0-100 scale,
    NaNs ranked at the bottom rather than dropped."""
    return series.rank(pct=True, na_option="bottom") * 100


def compute_stock_momentum(ticker: str, hist: pd.DataFrame, nifty_hist: pd.DataFrame) -> StockMomentum:
    sm = StockMomentum(ticker=ticker)
    try:
        close = hist["Close"].dropna()
        volume = hist["Volume"].dropna()
        if len(close) < 60:
            sm.error = "insufficient price history"
            return sm

        sm.ret_1m = _pct_change(close, 21)
        sm.ret_3m = _pct_change(close, 63)
        sm.ret_6m = _pct_change(close, 126)

        nifty_close = nifty_hist["Close"].dropna()
        nifty_ret_1m = _pct_change(nifty_close, 21)
        nifty_ret_3m = _pct_change(nifty_close, 63)
        if sm.ret_1m is not None and nifty_ret_1m is not None:
            sm.rs_vs_nifty_1m = sm.ret_1m - nifty_ret_1m
        if sm.ret_3m is not None and nifty_ret_3m is not None:
            sm.rs_vs_nifty_3m = sm.ret_3m - nifty_ret_3m

        rs_line = (close / close.iloc[0]) / (nifty_close / nifty_close.iloc[0]).reindex(
            close.index, method="ffill"
        )
        rs_line = rs_line.dropna()
        sm.rs_trend_up = _slope_up(rs_line, window=20)
        rs_slope_recent = _slope_up(rs_line, window=10)
        rs_slope_prior = _slope_up(rs_line.iloc[:-10], window=20) if len(rs_line) > 30 else None
        sm.early_turn_rs_turning_up = bool(
            rs_slope_recent and prior_down_or_flat(rs_slope_prior)
        )

        dma50 = close.rolling(50).mean()
        dma200 = close.rolling(200).mean()
        last_close = close.iloc[-1]
        if not np.isnan(dma50.iloc[-1]):
            sm.pct_above_50dma = float(last_close / dma50.iloc[-1] - 1.0)
            sm.dma50_slope_up = _slope_up(dma50.dropna(), window=10)
        if not np.isnan(dma200.iloc[-1]):
            sm.pct_above_200dma = float(last_close / dma200.iloc[-1] - 1.0)
            sm.dma200_slope_up = _slope_up(dma200.dropna(), window=10)

        # Fresh cross above the 50dma in the last 5 sessions, on above-average volume.
        if len(close) > 6 and not dma50.iloc[-6:].isna().any():
            above = close.iloc[-6:] > dma50.iloc[-6:]
            crossed = (not above.iloc[0]) and above.iloc[-1] and above.iloc[1:].any()
            vol_recent = volume.iloc[-5:].mean()
            vol_base = volume.iloc[-90:-5].mean() if len(volume) > 95 else volume.mean()
            sm.early_turn_cross_above_50dma = bool(crossed and vol_recent > vol_base)

        high_52w = close.iloc[-252:].max() if len(close) >= 252 else close.max()
        sm.pct_below_52w_high = float(last_close / high_52w - 1.0)
        prior_high_52w = close.iloc[-262:-10].max() if len(close) >= 262 else None
        consolidated = (
            prior_high_52w is not None
            and close.iloc[-30:-5].max() < prior_high_52w * 0.97
        )
        sm.early_turn_fresh_52w_high = bool(
            sm.pct_below_52w_high > -0.02 and consolidated
        )

        if len(volume) >= 90:
            sm.vol_20d_over_90d = float(volume.iloc[-20:].mean() / volume.iloc[-90:].mean())

        sm.higher_highs_lows = _higher_highs_lows(close)

    except Exception as exc:  # noqa: BLE001 - one bad ticker shouldn't kill the batch
        sm.error = str(exc)

    return sm


def prior_down_or_flat(slope_up: bool | None) -> bool:
    return slope_up is False or slope_up is None


def score_universe(tickers: list[str]) -> pd.DataFrame:
    """Download price history for every ticker + the Nifty 50 benchmark,
    compute per-stock momentum features, then rank them into a 0-100
    Momentum score. Returns one row per ticker."""
    print(f"[price_momentum] downloading {len(tickers)} tickers...")
    data = yf.download(
        tickers,
        period=f"{LOOKBACK_DAYS}d",
        group_by="ticker",
        auto_adjust=True,
        threads=True,
        progress=False,
    )
    nifty_hist = yf.download(
        NIFTY50_TICKER, period=f"{LOOKBACK_DAYS}d", auto_adjust=True, progress=False
    )

    rows = []
    for ticker in tickers:
        try:
            hist = data[ticker] if isinstance(data.columns, pd.MultiIndex) else data
        except KeyError:
            rows.append(StockMomentum(ticker=ticker, error="no data returned"))
            continue
        rows.append(compute_stock_momentum(ticker, hist, nifty_hist))

    df = pd.DataFrame([vars(r) for r in rows])

    # Weighted blend of ranked sub-metrics -> momentum_score (0-100).
    ranked = pd.DataFrame(index=df.index)
    for col in MOMENTUM_WEIGHTS:
        ranked[col] = _score_from_rank(df[col])
    blended = sum(ranked[col] * w for col, w in MOMENTUM_WEIGHTS.items())

    structure_bonus = df["higher_highs_lows"].fillna(False).astype(int) * MOMENTUM_STRUCTURE_BONUS
    trend_bonus = (
        df["dma50_slope_up"].fillna(False).astype(int)
        + df["dma200_slope_up"].fillna(False).astype(int)
    ) * MOMENTUM_TREND_SLOPE_BONUS

    df["momentum_score"] = (blended + structure_bonus + trend_bonus).clip(0, 100).round(1)
    df.loc[df["error"].notna(), "momentum_score"] = np.nan

    # Extension % (Design Spec): how stretched the stock is above its 50dma,
    # kept separate from momentum_score, feeds the Risk Score's technical-
    # exhaustion trigger in risk.py instead.
    df["extension_pct"] = df["pct_above_50dma"]

    df["early_turn_flag"] = (
        df["early_turn_cross_above_50dma"]
        | df["early_turn_rs_turning_up"]
        | df["early_turn_fresh_52w_high"]
    )

    return df


if __name__ == "__main__":
    from momentum.universe import fetch_universe

    uni = fetch_universe()
    scored = score_universe(uni["ticker"].tolist()[:20])  # smoke test on a slice
    print(scored[["ticker", "momentum_score", "early_turn_flag", "error"]])
