"""Layer 3: news & catalyst tagging for stocks flagged by layers 1 and 2.

This layer is deliberately NOT automated end-to-end: it needs real web
search with judgment about source credibility and publish dates, which is
what Claude Code is for. This module just defines:

  - the query list to search for each flagged stock
  - the CatalystRecord shape to fill in from search results
  - persistence so the dashboard can read whatever was found

Weekly flow: run pipeline.py to get the flagged list, then ask Claude Code
("research catalysts for the flagged stocks from this week's run") to
search, fill in CatalystRecord entries, and call save_catalysts().
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

CATALYST_TYPES = [
    "policy_regulation",
    "new_market_product",
    "order_client_win",
    "partnership",
    "turnaround",
    "fund_raise",
    "debt_cut",
    "brokerage_upgrade",
]

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "runs"


@dataclass
class CatalystRecord:
    ticker: str
    catalyst_type: str  # one of CATALYST_TYPES
    headline: str
    published_date: str  # ISO date - verify this is the REAL publish date, not a resurfaced one
    source: str
    url: str
    note: str = ""


def catalyst_search_queries(symbol: str, company_name: str) -> list[str]:
    """Search queries to run (last 2-4 weeks) for a flagged stock."""
    return [
        f"{company_name} {symbol} news",
        f"{company_name} order win OR partnership OR contract",
        f"{company_name} brokerage upgrade OR price target",
        f"{company_name} results OR turnaround OR margin",
        f"{company_name} stake OR fundraise OR debt reduction",
    ]


def save_catalysts(run_date: str, records: list[CatalystRecord]) -> Path:
    out_path = DATA_DIR / run_date / "catalysts.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_catalysts(run_date)
    merged = existing + [asdict(r) for r in records]
    out_path.write_text(json.dumps(merged, indent=2))
    return out_path


def load_catalysts(run_date: str) -> list[dict]:
    path = DATA_DIR / run_date / "catalysts.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


if __name__ == "__main__":
    print(catalyst_search_queries("TRENT", "Trent Ltd"))
