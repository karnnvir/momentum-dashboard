"""Weekly orchestration: universe -> momentum -> fundamentals -> groups -> save.

Usage:
    python -m momentum.pipeline              # full run
    python -m momentum.pipeline --quick 20    # smoke test on first 20 tickers

Intended cadence: Saturday morning IST, after the week's close (per the
build spec). Fundamentals are cached and only refreshed when stale (a
results season is ~75 days), since screener.in scraping is slow and that
layer is meant to update quarterly, not weekly.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from momentum.universe import fetch_universe
from momentum.price_momentum import score_universe, NIFTY50_TICKER, LOOKBACK_DAYS
from momentum.fundamentals import fetch_fundamentals_for_universe, StockFundamentals
from momentum.scoring import classify_dataframe
from momentum import catalysts as cat
from momentum import risk

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
RUNS_DIR = DATA_DIR / "runs"
FUNDAMENTALS_CACHE = DATA_DIR / "fundamentals_cache.json"
FUNDAMENTALS_MAX_AGE_DAYS = 75


def _load_fundamentals_cache() -> dict:
    if FUNDAMENTALS_CACHE.exists():
        return json.loads(FUNDAMENTALS_CACHE.read_text())
    return {}


def _save_fundamentals_cache(cache: dict) -> None:
    FUNDAMENTALS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    FUNDAMENTALS_CACHE.write_text(json.dumps(cache, indent=2))


def get_fundamentals(symbols: list[str], force_refresh: bool = False) -> pd.DataFrame:
    cache = _load_fundamentals_cache()
    today = date.today()
    stale_cutoff = today - timedelta(days=FUNDAMENTALS_MAX_AGE_DAYS)

    to_fetch = []
    for symbol in symbols:
        entry = cache.get(symbol)
        if force_refresh or entry is None or date.fromisoformat(entry["as_of"]) < stale_cutoff:
            to_fetch.append(symbol)

    if to_fetch:
        print(f"[pipeline] refreshing fundamentals for {len(to_fetch)}/{len(symbols)} stocks...")
        # Fetch + save incrementally (every 20 stocks) rather than all-at-once
        # at the end: ~250 stocks x 1.5s is several minutes of scraping, and a
        # single bad response shouldn't throw away everything fetched so far.
        for batch_start in range(0, len(to_fetch), 20):
            batch = to_fetch[batch_start : batch_start + 20]
            fresh = fetch_fundamentals_for_universe(batch)
            for symbol, sf in fresh.items():
                entry = {"as_of": today.isoformat(), **{k: v for k, v in vars(sf).items() if k != "symbol"}}
                cache[symbol] = entry
            _save_fundamentals_cache(cache)
            print(f"[pipeline]   ...{min(batch_start + 20, len(to_fetch))}/{len(to_fetch)} fundamentals saved")

    rows = []
    for symbol in symbols:
        entry = cache.get(symbol, {})
        rows.append(
            {
                "symbol": symbol,
                "support_score": entry.get("support_score"),
                "fundamentals_as_of": entry.get("as_of"),
                "promoter_pledge": entry.get("promoter_pledge"),
                "debt_to_equity": entry.get("debt_to_equity"),
                "earnings_quality_discounted": entry.get("earnings_quality_discounted", False),
            }
        )
    return pd.DataFrame(rows)


def compute_catalyst_and_risk(merged: pd.DataFrame, run_date: str) -> pd.DataFrame:
    """Adds catalyst_score, risk_score, risk_band (Design Spec) using
    whatever catalysts.json currently holds for this run_date. Safe to call
    again after catalysts.save_catalysts() adds more records - re-running
    this is much cheaper than re-running the full pipeline."""
    records_by_ticker: dict[str, list[cat.CatalystRecord]] = {}
    for record in cat.load_catalyst_records(run_date):
        records_by_ticker.setdefault(record.ticker, []).append(record)

    catalyst_scores, risk_scores, risk_bands, risk_band_labels, triggers = [], [], [], [], []
    for _, row in merged.iterrows():
        records = records_by_ticker.get(row["ticker"], [])
        cscore = cat.stock_catalyst_score(records)
        has_negative = any(r.direction == "negative" for r in records)
        assessment = risk.assess_risk(
            ticker=row["ticker"],
            group=row.get("group"),
            catalyst_score=cscore,
            extension_pct=row.get("extension_pct"),
            ret_1m=row.get("ret_1m"),
            promoter_pledge=row.get("promoter_pledge"),
            debt_to_equity=row.get("debt_to_equity"),
            earnings_quality_discounted=bool(row.get("earnings_quality_discounted")),
            has_negative_catalyst=has_negative,
        )
        catalyst_scores.append(cscore)
        risk_scores.append(assessment.risk_score)
        risk_bands.append(assessment.risk_band)
        risk_band_labels.append(risk.RISK_BAND_LABELS[assessment.risk_band])
        triggers.append(",".join(assessment.active_triggers))

    merged["catalyst_score"] = catalyst_scores
    merged["risk_score"] = risk_scores
    merged["risk_band"] = risk_bands
    merged["risk_band_label"] = risk_band_labels
    merged["risk_active_triggers"] = triggers
    return merged


def rescan_with_catalysts(run_date: str) -> pd.DataFrame:
    """Reload a saved run and recompute catalyst/risk scores - call this
    after catalysts.save_catalysts() so the dashboard reflects what was
    just researched, without re-running the whole weekly pipeline."""
    run_path = RUNS_DIR / run_date / "run.csv"
    merged = pd.read_csv(run_path)
    merged = compute_catalyst_and_risk(merged, run_date)
    merged.to_csv(run_path, index=False)
    merged.to_json(RUNS_DIR / run_date / "run.json", orient="records", indent=2)
    return merged


def _market_distribution_day_warning() -> None:
    nifty_hist = yf.download(
        NIFTY50_TICKER, period=f"{LOOKBACK_DAYS}d", auto_adjust=True, progress=False
    )
    count = risk.market_distribution_day_count(nifty_hist)
    if count >= 4:
        print(
            f"[pipeline] WARNING: {count} Nifty 50 distribution days in the last ~4 weeks - "
            "market uptrend may be weakening. Consider tighter stops across the whole book, "
            "not just individual flagged stocks."
        )
    else:
        print(f"[pipeline] Nifty 50 distribution days (last ~4 weeks): {count} - no book-wide warning")


def _previous_run_scores() -> pd.DataFrame | None:
    if not RUNS_DIR.exists():
        return None
    run_dates = sorted((p.name for p in RUNS_DIR.iterdir() if p.is_dir()), reverse=True)
    for run_date in run_dates:
        f = RUNS_DIR / run_date / "run.csv"
        if f.exists():
            df = pd.read_csv(f)
            return df[["ticker", "momentum_score", "support_score"]]
    return None


def run(quick: int | None = None, force_fundamentals_refresh: bool = False) -> pd.DataFrame:
    universe = fetch_universe()
    if quick:
        universe = universe.head(quick)

    momentum_df = score_universe(universe["ticker"].tolist())
    merged = universe.merge(momentum_df, on="ticker", how="left")

    fundamentals_df = get_fundamentals(universe["symbol"].tolist(), force_refresh=force_fundamentals_refresh)
    merged = merged.merge(fundamentals_df, on="symbol", how="left")

    merged = classify_dataframe(merged)

    prev = _previous_run_scores()
    if prev is not None:
        merged = merged.merge(prev, on="ticker", how="left", suffixes=("", "_prev"))
        merged["momentum_score_delta"] = (merged["momentum_score"] - merged["momentum_score_prev"]).round(1)
        merged["support_score_delta"] = (merged["support_score"] - merged["support_score_prev"]).round(1)
    else:
        merged["momentum_score_delta"] = None
        merged["support_score_delta"] = None

    run_date = date.today().isoformat()
    merged = compute_catalyst_and_risk(merged, run_date)

    out_dir = RUNS_DIR / run_date
    out_dir.mkdir(parents=True, exist_ok=True)
    merged.to_csv(out_dir / "run.csv", index=False)
    merged.to_json(out_dir / "run.json", orient="records", indent=2)
    (RUNS_DIR / "latest.txt").write_text(run_date)

    _market_distribution_day_warning()

    flagged = merged[merged["group"].notna()]
    print(f"[pipeline] run {run_date}: {len(merged)} scored, {len(flagged)} flagged into groups")
    print(flagged["group_label"].value_counts().to_string())

    queries_path = out_dir / "catalyst_queries.json"
    queries = {
        row["ticker"]: cat.catalyst_search_queries(row["symbol"], row["name"])
        for _, row in flagged.iterrows()
    }
    queries_path.write_text(json.dumps(queries, indent=2))
    print(f"[pipeline] wrote catalyst search queries for {len(queries)} flagged stocks -> {queries_path}")
    print("[pipeline] next: ask Claude Code to research catalysts for these and call catalysts.save_catalysts()")

    return merged


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", type=int, default=None, help="limit to first N universe stocks, for smoke testing")
    parser.add_argument("--refresh-fundamentals", action="store_true")
    parser.add_argument(
        "--rescan",
        metavar="YYYY-MM-DD",
        help="recompute catalyst/risk scores for an existing run after catalysts.save_catalysts() added records, without re-running the full pipeline",
    )
    args = parser.parse_args()
    if args.rescan:
        rescan_with_catalysts(args.rescan)
    else:
        run(quick=args.quick, force_fundamentals_refresh=args.refresh_fundamentals)
