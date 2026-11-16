"""Shared data access for every dashboard page - single source of truth for
reading a run's output, so the Overview and deep-dive pages can never drift
into reading the same files two different ways.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RUNS_DIR = DATA_DIR / "runs"
# Gitignored: "My Holdings" tier + is_held flag live only here, never in the
# publicly-tracked RUNS_DIR. Superset of the public run when present.
HOLDINGS_RUNS_DIR = DATA_DIR / "holdings" / "runs"
FUNDAMENTALS_CACHE = DATA_DIR / "fundamentals_cache.json"
ANALYST_CONSENSUS_CACHE = DATA_DIR / "analyst_consensus_cache.json"


def _run_json_path(run_date: str) -> Path:
    private = HOLDINGS_RUNS_DIR / run_date / "run.json"
    return private if private.exists() else RUNS_DIR / run_date / "run.json"


def _run_csv_path(run_date: str) -> Path:
    private = HOLDINGS_RUNS_DIR / run_date / "run.csv"
    return private if private.exists() else RUNS_DIR / run_date / "run.csv"


def load_latest_run() -> tuple[str, pd.DataFrame, list[dict]] | None:
    latest_file = RUNS_DIR / "latest.txt"
    if not latest_file.exists():
        return None
    run_date = latest_file.read_text().strip()
    return load_run(run_date)


def load_run(run_date: str) -> tuple[str, pd.DataFrame, list[dict]] | None:
    run_path = _run_json_path(run_date)
    if not run_path.exists():
        return None
    df = pd.DataFrame(json.loads(run_path.read_text()))
    if "is_held" not in df.columns:
        df["is_held"] = False

    catalysts_path = RUNS_DIR / run_date / "catalysts.json"
    catalysts = json.loads(catalysts_path.read_text()) if catalysts_path.exists() else []
    private_catalysts_path = HOLDINGS_RUNS_DIR / run_date / "catalysts.json"
    if private_catalysts_path.exists():
        catalysts += json.loads(private_catalysts_path.read_text())
    return run_date, df, catalysts


def load_excluded_holdings(run_date: str) -> dict | None:
    """Funds/ETFs and unresolved holdings for this run - names only, no
    quantity/cost/value. None if no holdings file has ever been processed."""
    path = HOLDINGS_RUNS_DIR / run_date / "excluded_holdings.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def catalysts_by_ticker(catalyst_records: list[dict]) -> dict[str, list[dict]]:
    by_ticker: dict[str, list[dict]] = {}
    for rec in catalyst_records:
        by_ticker.setdefault(rec["ticker"], []).append(rec)
    return by_ticker


def list_run_dates() -> list[str]:
    """All run dates with saved output, oldest first - used to build a
    stock's score-history sparkline as more weekly runs accumulate."""
    if not RUNS_DIR.exists():
        return []
    dates = [p.name for p in RUNS_DIR.iterdir() if p.is_dir() and (p / "run.csv").exists()]
    return sorted(dates)


def score_history(ticker: str) -> pd.DataFrame:
    """overall/momentum/support/catalyst/risk score for one ticker across
    every saved run, for the deep-dive page's sparkline."""
    rows = []
    for run_date in list_run_dates():
        run_df = pd.read_csv(_run_csv_path(run_date))
        match = run_df[run_df["ticker"] == ticker]
        if match.empty:
            continue
        r = match.iloc[0]
        rows.append(
            {
                "run_date": run_date,
                "overall_score": r.get("overall_score"),
                "momentum_score": r.get("momentum_score"),
                "support_score": r.get("support_score"),
                "catalyst_score": r.get("catalyst_score"),
                "risk_score": r.get("risk_score"),
            }
        )
    history_df = pd.DataFrame(rows)
    if not history_df.empty:
        history_df["run_date"] = pd.to_datetime(history_df["run_date"])
    return history_df


def load_fundamentals_cache() -> dict:
    if not FUNDAMENTALS_CACHE.exists():
        return {}
    return json.loads(FUNDAMENTALS_CACHE.read_text())


def load_analyst_consensus_cache() -> dict:
    if not ANALYST_CONSENSUS_CACHE.exists():
        return {}
    return json.loads(ANALYST_CONSENSUS_CACHE.read_text())
