"""Daily lightweight risk check - the "Daily lightweight check" cadence
decided early in this project for the Risk Score, separate from the weekly
full-universe discovery scan (see pipeline.py).

Re-prices ONLY currently-held stocks (data/holdings/portfolio.tsv) and
recomputes their Risk Score/Band from the latest cached fundamentals and
whatever catalyst records already exist - no fundamentals re-scrape (that
stays quarterly), no new catalyst research (that stays manual/ad-hoc). Fast
enough to run every day: one bulk price fetch for ~100 tickers instead of
the full ~290-stock weekly pipeline.

Usage:
    python -m momentum.daily_risk_check
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from momentum import holdings as hold
from momentum import catalysts as cat
from momentum import risk
from momentum.price_momentum import score_universe
from momentum.scoring import classify_dataframe
from momentum.pipeline import get_fundamentals, RUNS_DIR

DAILY_CHECKS_DIR = hold.HOLDINGS_RUNS_DIR / "daily_risk_checks"


def run_daily_risk_check() -> pd.DataFrame:
    holdings_data = hold.load_my_holdings()
    if holdings_data is None:
        print("[daily-risk-check] No holdings file found at data/holdings/portfolio.tsv - nothing to check.")
        return pd.DataFrame()

    held = holdings_data["equities"]
    print(f"[daily-risk-check] {date.today().isoformat()}: checking {len(held)} held tickers...")

    momentum_df = score_universe(held["ticker"].tolist())
    merged = held.merge(momentum_df, on="ticker", how="left")

    fundamentals_df = get_fundamentals(held["symbol"].tolist())  # cached - no force refresh, stays quarterly
    merged = merged.merge(fundamentals_df, on="symbol", how="left")
    merged = classify_dataframe(merged)

    latest_file = RUNS_DIR / "latest.txt"
    run_date_for_catalysts = latest_file.read_text().strip() if latest_file.exists() else date.today().isoformat()
    records = cat.load_catalyst_records(run_date_for_catalysts, extra_dir=hold.HOLDINGS_RUNS_DIR)
    records_by_ticker: dict[str, list[cat.CatalystRecord]] = {}
    for r in records:
        records_by_ticker.setdefault(r.ticker, []).append(r)

    rows = []
    for _, row in merged.iterrows():
        recs = records_by_ticker.get(row["ticker"], [])
        cscore = cat.stock_catalyst_score(recs)
        has_negative = any(r.direction == "negative" for r in recs)
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
        rows.append(
            {
                "ticker": row["ticker"],
                "name": row["name"],
                "risk_score": assessment.risk_score,
                "risk_band": assessment.risk_band,
                "risk_band_label": risk.RISK_BAND_LABELS[assessment.risk_band],
                "active_triggers": ",".join(assessment.active_triggers),
            }
        )

    result = pd.DataFrame(rows).sort_values("risk_score", ascending=False)

    DAILY_CHECKS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DAILY_CHECKS_DIR / f"{date.today().isoformat()}.csv"
    result.to_csv(out_path, index=False)
    (DAILY_CHECKS_DIR / "latest.txt").write_text(date.today().isoformat())

    watch_or_worse = result[result["risk_band"] != risk.RISK_BAND_CLEAR]
    print(
        f"[daily-risk-check] {len(result)} holdings checked, "
        f"{len(watch_or_worse)} in Watch/Reduce-Exit -> {out_path}"
    )
    if len(watch_or_worse):
        print(watch_or_worse[["ticker", "risk_score", "risk_band_label", "active_triggers"]].to_string(index=False))

    return result


if __name__ == "__main__":
    run_daily_risk_check()
