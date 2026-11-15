"""Risk/Exit overlay (Design Spec) - independent of the entry-side Group.

Risk Score (0-100) is a weighted sum of fixed points from five trigger
families (research doc point H), each a calculated check against data
already in the pipeline - never a judgment call. Risk Band is just a
display bucket over that number; the number itself is always shown too.

Meant to run more often than the weekly full-universe scan - see
pipeline.py's `assess_risk` for the daily/on-demand check on current
holdings.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# Design Spec: fixed points per trigger family, summed and capped at 100.
TRIGGER_POINTS = {
    "time_decay": 10,
    "technical_exhaustion": 25,
    "valuation_extremes": 20,
    "fundamental_deterioration": 30,
    "news_trigger": 40,
}

RISK_BAND_CLEAR = "clear"
RISK_BAND_WATCH = "watch"
RISK_BAND_REDUCE_EXIT = "reduce_exit"

RISK_BAND_LABELS = {
    RISK_BAND_CLEAR: "\U0001f6e1️ Clear",
    RISK_BAND_WATCH: "⚠️ Watch",
    RISK_BAND_REDUCE_EXIT: "\U0001f6d1 Reduce-Exit",
}

EXTENSION_EXHAUSTION_THRESHOLD = 0.25  # 25% above the 50dma
CLIMAX_RETURN_THRESHOLD = 0.30  # 30%+ in a month
PLEDGE_DANGER_PCT = 40.0
DEBT_TO_EQUITY_DANGER = 1.0


@dataclass
class RiskAssessment:
    ticker: str
    risk_score: int = 0
    risk_band: str = RISK_BAND_CLEAR
    active_triggers: list = field(default_factory=list)

    def label(self) -> str:
        return f"{self.risk_score} {RISK_BAND_LABELS[self.risk_band]}"


def _time_decay_trigger(group: str | None, catalyst_score: int) -> bool:
    """A stock sitting in a momentum-flagged group with no live catalyst
    has old momentum and no new reason - the textbook reversal setup."""
    if group is None or group == "not_moving_yet":
        return False
    return catalyst_score == 0


def _technical_exhaustion_trigger(
    extension_pct: float | None, ret_1m: float | None
) -> bool:
    if extension_pct is not None and extension_pct > EXTENSION_EXHAUSTION_THRESHOLD:
        return True
    if ret_1m is not None and ret_1m > CLIMAX_RETURN_THRESHOLD:
        return True
    return False


def _valuation_extremes_trigger() -> bool:
    """Not implemented yet - PEG/valuation data isn't scraped in this
    version (no P/E or growth-estimate source wired up). Deliberately
    returns False rather than fabricating a number; see the Later list in
    the Phasing doc."""
    return False


def _fundamental_deterioration_trigger(
    promoter_pledge: float | None,
    debt_to_equity: float | None,
    earnings_quality_discounted: bool,
) -> bool:
    if promoter_pledge is not None and promoter_pledge > PLEDGE_DANGER_PCT:
        return True
    if debt_to_equity is not None and debt_to_equity > DEBT_TO_EQUITY_DANGER:
        return True
    return bool(earnings_quality_discounted)


def _news_trigger(has_negative_catalyst: bool) -> bool:
    return has_negative_catalyst


def assess_risk(
    ticker: str,
    group: str | None,
    catalyst_score: int,
    extension_pct: float | None,
    ret_1m: float | None,
    promoter_pledge: float | None,
    debt_to_equity: float | None,
    earnings_quality_discounted: bool,
    has_negative_catalyst: bool,
) -> RiskAssessment:
    triggers = {
        "time_decay": _time_decay_trigger(group, catalyst_score),
        "technical_exhaustion": _technical_exhaustion_trigger(extension_pct, ret_1m),
        "valuation_extremes": _valuation_extremes_trigger(),
        "fundamental_deterioration": _fundamental_deterioration_trigger(
            promoter_pledge, debt_to_equity, earnings_quality_discounted
        ),
        "news_trigger": _news_trigger(has_negative_catalyst),
    }
    active = [name for name, fired in triggers.items() if fired]
    score = min(100, sum(TRIGGER_POINTS[name] for name in active))

    if score >= 60:
        band = RISK_BAND_REDUCE_EXIT
    elif score >= 25:
        band = RISK_BAND_WATCH
    else:
        band = RISK_BAND_CLEAR

    return RiskAssessment(ticker=ticker, risk_score=score, risk_band=band, active_triggers=active)


def market_distribution_day_count(index_hist: pd.DataFrame, lookback_sessions: int = 20) -> int:
    """IBD-style distribution days for the benchmark index over the last
    `lookback_sessions` (~4 weeks): a close down >=0.2% on higher volume
    than the prior session. Book-wide signal, not stock-specific - per the
    research doc, 4+ in a few weeks is a market-uptrend-weakening warning
    worth raising caution across the whole momentum book."""
    close = index_hist["Close"]
    volume = index_hist["Volume"]
    # yfinance sometimes returns MultiIndex columns even for a single ticker.
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    if isinstance(volume, pd.DataFrame):
        volume = volume.iloc[:, 0]
    close = close.dropna()
    volume = volume.dropna()
    window = min(lookback_sessions, len(close) - 1)
    if window <= 0:
        return 0

    count = 0
    for i in range(len(close) - window, len(close)):
        pct_change = close.iloc[i] / close.iloc[i - 1] - 1.0
        if pct_change <= -0.002 and volume.iloc[i] > volume.iloc[i - 1]:
            count += 1
    return count
