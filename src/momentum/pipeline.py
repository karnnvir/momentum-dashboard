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
from momentum.scoring import classify_dataframe, add_overall_score
from momentum import catalysts as cat
from momentum import risk
from momentum import sector as sec
from momentum import holdings as hold
from momentum import analyst_consensus as ac

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
RUNS_DIR = DATA_DIR / "runs"
FUNDAMENTALS_CACHE = DATA_DIR / "fundamentals_cache.json"
FUNDAMENTALS_MAX_AGE_DAYS = 75
ANALYST_CONSENSUS_CACHE = DATA_DIR / "analyst_consensus_cache.json"
ANALYST_CONSENSUS_MAX_AGE_DAYS = 75  # same cadence as fundamentals - broker reports trickle in over weeks, not daily


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
                "sector": entry.get("sector"),
                "pe_ratio": entry.get("pe_ratio"),
                "recent_profit_growth_pct": entry.get("recent_profit_growth_pct"),
            }
        )
    return pd.DataFrame(rows)


def _load_analyst_consensus_cache() -> dict:
    if ANALYST_CONSENSUS_CACHE.exists():
        return json.loads(ANALYST_CONSENSUS_CACHE.read_text())
    return {}


def _save_analyst_consensus_cache(cache: dict) -> None:
    ANALYST_CONSENSUS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    ANALYST_CONSENSUS_CACHE.write_text(json.dumps(cache, indent=2))


def get_analyst_consensus(symbols: list[str], force_refresh: bool = False) -> pd.DataFrame:
    """Phase 2 feasibility result (analyst_consensus.py): Buy/Hold/Sell
    counts from Trendlyne, cached on the same ~75-day cadence as
    fundamentals - broker reports accumulate over weeks, not daily. A
    symbol with no analyst coverage (or not on Trendlyne's list at all)
    simply has no cache entry and no row fields - never a fabricated
    score."""
    cache = _load_analyst_consensus_cache()
    today = date.today()
    stale_cutoff = today - timedelta(days=ANALYST_CONSENSUS_MAX_AGE_DAYS)

    to_fetch = []
    for symbol in symbols:
        entry = cache.get(symbol)
        if force_refresh or entry is None or date.fromisoformat(entry["scraped_as_of"]) < stale_cutoff:
            to_fetch.append(symbol)

    if to_fetch:
        print(f"[pipeline] refreshing analyst consensus for {len(to_fetch)}/{len(symbols)} stocks...")
        for batch_start in range(0, len(to_fetch), 20):
            batch = to_fetch[batch_start : batch_start + 20]
            fresh, failed = ac.fetch_analyst_consensus_for_universe(batch)
            for symbol in batch:
                if symbol in failed:
                    continue  # transient failure - leave uncached so it's retried next run
                data = fresh.get(symbol)
                cache[symbol] = {"scraped_as_of": today.isoformat(), **data} if data else {
                    "scraped_as_of": today.isoformat()
                }
            _save_analyst_consensus_cache(cache)
            print(f"[pipeline]   ...{min(batch_start + 20, len(to_fetch))}/{len(to_fetch)} analyst consensus saved")

    rows = []
    for symbol in symbols:
        entry = cache.get(symbol, {})
        rows.append(
            {
                "symbol": symbol,
                "analyst_consensus_score": entry.get("analyst_consensus_score"),
                "analyst_count": entry.get("analyst_count"),
                "analyst_avg_reco_text": entry.get("analyst_avg_reco_text"),
                "analyst_consensus_as_of": entry.get("analyst_consensus_as_of"),
            }
        )
    return pd.DataFrame(rows)


def compute_catalyst_and_risk(merged: pd.DataFrame, run_date: str, include_holdings_catalysts: bool = False) -> pd.DataFrame:
    """Adds catalyst_score, risk_score, risk_band (Design Spec) using
    whatever catalysts.json currently holds for this run_date. Safe to call
    again after catalysts.save_catalysts() adds more records - re-running
    this is much cheaper than re-running the full pipeline.

    include_holdings_catalysts also merges in catalysts saved under the
    private holdings runs tree (My-Holdings-only tickers) - pass True only
    when merged includes the MY_HOLDINGS tier, so those records have a
    matching row to apply to."""
    extra_dir = hold.HOLDINGS_RUNS_DIR if include_holdings_catalysts else None
    records_by_ticker: dict[str, list[cat.CatalystRecord]] = {}
    for record in cat.load_catalyst_records(run_date, extra_dir=extra_dir):
        records_by_ticker.setdefault(record.ticker, []).append(record)

    catalyst_scores, risk_scores, risk_bands, risk_band_labels, triggers = [], [], [], [], []
    for _, row in merged.iterrows():
        records = records_by_ticker.get(row["ticker"], [])
        sector_modifier = sec.CATALYST_SECTOR_MODIFIERS.get(row.get("sector_state"), 1.0)
        cscore = round(cat.stock_catalyst_score(records) * sector_modifier)
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
    merged = add_overall_score(merged)
    return merged


def _save_run(merged: pd.DataFrame, run_date: str, has_holdings: bool) -> None:
    """Splits merged into the public (safe to commit) and private
    (holdings-aware) outputs. The public path NEVER includes MY_HOLDINGS-
    tier rows or the is_held column, regardless of what merged contains -
    enforced here at the point of writing, not just by convention upstream."""
    public_df = merged[merged.get("index") != hold.MY_HOLDINGS_INDEX].drop(columns=["is_held"], errors="ignore")
    out_dir = RUNS_DIR / run_date
    out_dir.mkdir(parents=True, exist_ok=True)
    public_df.to_csv(out_dir / "run.csv", index=False)
    public_df.to_json(out_dir / "run.json", orient="records", indent=2)
    (RUNS_DIR / "latest.txt").write_text(run_date)

    if has_holdings:
        private_dir = hold.HOLDINGS_RUNS_DIR / run_date
        private_dir.mkdir(parents=True, exist_ok=True)
        merged.to_csv(private_dir / "run.csv", index=False)
        merged.to_json(private_dir / "run.json", orient="records", indent=2)
        (hold.HOLDINGS_RUNS_DIR / "latest.txt").write_text(run_date)


def rescan_with_catalysts(run_date: str) -> pd.DataFrame:
    """Reload a saved run and recompute catalyst/risk scores - call this
    after catalysts.save_catalysts() so the dashboard reflects what was
    just researched, without re-running the whole weekly pipeline. Reads
    from the private holdings-aware file when one exists for this run_date,
    since it's a strict superset of the public one."""
    private_path = hold.HOLDINGS_RUNS_DIR / run_date / "run.csv"
    has_holdings = private_path.exists()
    source_path = private_path if has_holdings else RUNS_DIR / run_date / "run.csv"
    merged = pd.read_csv(source_path)
    merged = compute_catalyst_and_risk(merged, run_date, include_holdings_catalysts=has_holdings)
    _save_run(merged, run_date, has_holdings=has_holdings)
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


def _previous_run_scores(exclude_date: str) -> tuple[str, pd.DataFrame] | None:
    """The most recent saved run strictly BEFORE exclude_date (today's run
    date), plus which date it came from. Excluding today's own date matters
    because this project has sometimes run the pipeline more than once in a
    day (testing, backfills) - without this, a same-day re-run would diff
    against itself from a few hours ago and silently call it a "weekly"
    delta. The caller is expected to show the returned date alongside any
    delta, rather than implying it's always exactly a week old."""
    if not RUNS_DIR.exists():
        return None
    run_dates = sorted(
        (p.name for p in RUNS_DIR.iterdir() if p.is_dir() and p.name != exclude_date), reverse=True
    )
    for run_date in run_dates:
        f = RUNS_DIR / run_date / "run.csv"
        if f.exists():
            df = pd.read_csv(f)
            return run_date, df[["ticker", "momentum_score", "support_score"]]
    return None


def _previous_sector_rs(exclude_date: str) -> dict[str, float] | None:
    """Last run's sector_rs_percentile per sector, for Sector Rotation
    State's trend component. None (not an empty dict) when no prior run has
    this column yet, so compute_sector_rotation knows to skip trend rather
    than treat "no data" as "falling." Excludes exclude_date (today's run
    date) for the same same-day-rerun reason as _previous_run_scores."""
    if not RUNS_DIR.exists():
        return None
    run_dates = sorted(
        (p.name for p in RUNS_DIR.iterdir() if p.is_dir() and p.name != exclude_date), reverse=True
    )
    for run_date in run_dates:
        f = RUNS_DIR / run_date / "run.csv"
        if not f.exists():
            continue
        df = pd.read_csv(f)
        if "sector_rs_percentile" not in df.columns:
            return None
        return (
            df.dropna(subset=["sector", "sector_rs_percentile"])
            .drop_duplicates(subset="sector")
            .set_index("sector")["sector_rs_percentile"]
            .to_dict()
        )
    return None


def run(
    quick: int | None = None,
    force_fundamentals_refresh: bool = False,
    force_analyst_consensus_refresh: bool = False,
) -> pd.DataFrame:
    run_date = date.today().isoformat()

    universe = fetch_universe()

    # Merge in "My Holdings" (data/holdings/portfolio.tsv) if present: a 4th
    # tier for tickers not already in the three Nifty index tiers, plus an
    # is_held flag on every matching row either way. See holdings.py and
    # _save_run for how this is kept out of the publicly-tracked data/runs/.
    holdings_data = hold.load_my_holdings()
    has_holdings = holdings_data is not None
    if has_holdings:
        held_tickers = set(holdings_data["equities"]["ticker"])
        new_tier = holdings_data["equities"][~holdings_data["equities"]["ticker"].isin(universe["ticker"])].copy()
        new_tier["index"] = hold.MY_HOLDINGS_INDEX
        universe = pd.concat([universe, new_tier[["symbol", "name", "index", "ticker"]]], ignore_index=True)
        universe["is_held"] = universe["ticker"].isin(held_tickers)
        print(
            f"[pipeline] My Holdings: {len(held_tickers)} tickers held, "
            f"{len(new_tier)} added as a new tier, {len(holdings_data['funds'])} funds/ETFs "
            f"and {len(holdings_data['unresolved'])} unresolved excluded from scoring"
        )
    else:
        universe["is_held"] = False

    if quick:
        universe = universe.head(quick)

    momentum_df = score_universe(universe["ticker"].tolist())
    merged = universe.merge(momentum_df, on="ticker", how="left")

    fundamentals_df = get_fundamentals(universe["symbol"].tolist(), force_refresh=force_fundamentals_refresh)
    merged = merged.merge(fundamentals_df, on="symbol", how="left")

    analyst_consensus_df = get_analyst_consensus(
        universe["symbol"].tolist(), force_refresh=force_analyst_consensus_refresh
    )
    merged = merged.merge(analyst_consensus_df, on="symbol", how="left")

    merged = classify_dataframe(merged)

    # Sector aggregate stats (the per-sector average relative strength, the
    # PEG percentile cutoffs) are computed ONLY from the public tiers, even
    # when has_holdings is True - so "My Holdings"-only tickers get a real
    # classification without being able to nudge anyone else's numbers by
    # even a fraction of a percentile. See sector.py's reference_df docs.
    public_reference = merged[merged["index"] != hold.MY_HOLDINGS_INDEX] if has_holdings else None
    merged = sec.compute_sector_rotation(
        merged, prev_sector_rs=_previous_sector_rs(exclude_date=run_date), reference_df=public_reference
    )
    merged = sec.compute_relative_value(merged, reference_df=public_reference)

    prev_result = _previous_run_scores(exclude_date=run_date)
    if prev_result is not None:
        prev_run_date, prev = prev_result
        merged = merged.merge(prev, on="ticker", how="left", suffixes=("", "_prev"))
        merged["momentum_score_delta"] = (merged["momentum_score"] - merged["momentum_score_prev"]).round(1)
        merged["support_score_delta"] = (merged["support_score"] - merged["support_score_prev"]).round(1)
        merged["compared_to_run_date"] = prev_run_date
    else:
        merged["momentum_score_delta"] = None
        merged["support_score_delta"] = None
        merged["compared_to_run_date"] = None

    merged = compute_catalyst_and_risk(merged, run_date, include_holdings_catalysts=has_holdings)

    _save_run(merged, run_date, has_holdings=has_holdings)

    _market_distribution_day_warning()

    flagged = merged[merged["group"].notna()]
    print(f"[pipeline] run {run_date}: {len(merged)} scored, {len(flagged)} flagged into groups")
    print(flagged["group_label"].value_counts().to_string())

    # Catalyst search queries: public-tier flagged stocks go to the public
    # path as before. My-Holdings-only flagged stocks go to the private
    # holdings path instead, so their presence never reaches the repo.
    is_holdings_tier = flagged["index"] == hold.MY_HOLDINGS_INDEX
    public_flagged = flagged[~is_holdings_tier]
    out_dir = RUNS_DIR / run_date
    queries_path = out_dir / "catalyst_queries.json"
    queries = {
        row["ticker"]: cat.catalyst_search_queries(row["symbol"], row["name"])
        for _, row in public_flagged.iterrows()
    }
    queries_path.write_text(json.dumps(queries, indent=2))
    print(f"[pipeline] wrote catalyst search queries for {len(queries)} flagged stocks -> {queries_path}")

    if has_holdings:
        private_dir = hold.HOLDINGS_RUNS_DIR / run_date
        private_dir.mkdir(parents=True, exist_ok=True)

        holdings_flagged = flagged[is_holdings_tier]
        if len(holdings_flagged):
            holdings_queries = {
                row["ticker"]: cat.catalyst_search_queries(row["symbol"], row["name"])
                for _, row in holdings_flagged.iterrows()
            }
            (private_dir / "catalyst_queries.json").write_text(json.dumps(holdings_queries, indent=2))
            print(
                f"[pipeline] wrote catalyst search queries for {len(holdings_queries)} "
                f"My-Holdings-only flagged stocks -> {private_dir / 'catalyst_queries.json'}"
            )

        # Transparency note on what's excluded from scoring - names only,
        # never quantity, cost, or value.
        excluded = {
            "funds": holdings_data["funds"][["Stock Symbol", "Company Name"]].to_dict(orient="records"),
            "unresolved": holdings_data["unresolved"][["Stock Symbol", "Company Name"]].to_dict(orient="records"),
        }
        (private_dir / "excluded_holdings.json").write_text(json.dumps(excluded, indent=2))

    print("[pipeline] next: ask Claude Code to research catalysts for these and call catalysts.save_catalysts()")

    return merged


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", type=int, default=None, help="limit to first N universe stocks, for smoke testing")
    parser.add_argument("--refresh-fundamentals", action="store_true")
    parser.add_argument("--refresh-analyst-consensus", action="store_true")
    parser.add_argument(
        "--rescan",
        metavar="YYYY-MM-DD",
        help="recompute catalyst/risk scores for an existing run after catalysts.save_catalysts() added records, without re-running the full pipeline",
    )
    args = parser.parse_args()
    if args.rescan:
        rescan_with_catalysts(args.rescan)
    else:
        run(
            quick=args.quick,
            force_fundamentals_refresh=args.refresh_fundamentals,
            force_analyst_consensus_refresh=args.refresh_analyst_consensus,
        )
