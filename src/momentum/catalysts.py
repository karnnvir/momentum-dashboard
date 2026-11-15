"""Layer 3: news & catalyst tagging for stocks flagged by layers 1 and 2.

This layer is deliberately NOT automated end-to-end: it needs real web
search with judgment about source credibility and publish dates, which is
what Claude Code is for. This module defines:

  - the query list to search for each flagged stock
  - the CatalystRecord shape to fill in from search results
  - the Catalyst Score formula (Design Spec): a calculated number, not a
    judgment rating. The only judgment call is EXTRACTION - classifying a
    catalyst's type from the fixed list below and reading off its value and
    confirmation status. The score itself is then pure arithmetic from
    those facts, every time.
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
    "turnaround",
    "new_market_product",
    "policy_regulation",
    "order_client_win",
    "promoter_buying",
    "debt_cut",
    "brokerage_upgrade",
    "fund_raise",
    "partnership",
]

# Design Spec: TypeWeight, looked up by catalyst type - a fixed, documented
# table, not a per-catalyst judgment call. Ordering follows the durability
# ranking in the research doc's point E.
TYPE_WEIGHTS = {
    "turnaround": 1.0,
    "new_market_product": 0.9,
    "policy_regulation": 0.85,
    "order_client_win": 0.8,
    "promoter_buying": 0.7,
    "debt_cut": 0.6,
    "brokerage_upgrade": 0.5,
    "fund_raise": 0.4,
    "partnership": 0.3,
}

# Design Spec: ConfirmationMultiplier, looked up by how firm the
# announcement is.
CONFIRMATION_MULTIPLIERS = {
    "confirmed": 1.0,  # signed, priced, committed
    "provisional": 0.6,  # "up to" / multi-year ceiling / framework agreement
    "non_binding": 0.4,  # MoU / LoI
}

RECENCY_DECAY_DAYS = 60  # matches the PEAD drift window from the research doc

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "runs"


@dataclass
class CatalystRecord:
    ticker: str
    catalyst_type: str  # one of CATALYST_TYPES
    headline: str
    published_date: str  # ISO date - verify this is the REAL publish date, not a resurfaced one
    source: str
    url: str
    confirmation: str = "confirmed"  # confirmed | provisional | non_binding
    order_value_pct_revenue: float | None = None  # e.g. 0.15 for an order worth 15% of TTM revenue
    direction: str = "positive"  # positive | negative - a negative one feeds the Risk Score, not the Catalyst Score
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


def catalyst_score(record: CatalystRecord, as_of: date | None = None) -> int:
    """Design Spec formula:

        Catalyst Score = round(100 x TypeWeight x ConfirmationMultiplier x SizeScore x RecencyDecay)

    A calculated number from extracted facts - never a 1-5 judgment rating.
    """
    as_of = as_of or date.today()
    type_weight = TYPE_WEIGHTS.get(record.catalyst_type, 0.5)
    confirmation_multiplier = CONFIRMATION_MULTIPLIERS.get(record.confirmation, 1.0)

    if record.order_value_pct_revenue is not None:
        size_score = min(1.0, record.order_value_pct_revenue / 0.25)
    else:
        # Not every catalyst type carries a clean "% of revenue" figure
        # (a brokerage upgrade, a policy tailwind) - don't penalize those
        # for lacking one.
        size_score = 1.0

    published = date.fromisoformat(record.published_date)
    days_since = (as_of - published).days
    recency_decay = max(0.0, 1 - days_since / RECENCY_DECAY_DAYS)

    return round(100 * type_weight * confirmation_multiplier * size_score * recency_decay)


def stock_catalyst_score(records: list[CatalystRecord], as_of: date | None = None) -> int:
    """A stock's Catalyst Score is the MAX of its live positive catalysts'
    individual scores, not a sum - one big catalyst isn't diluted by two
    small ones. Negative-direction catalysts don't count here; they feed
    the Risk Score's news trigger instead (see risk.py)."""
    positive = [r for r in records if r.direction == "positive"]
    if not positive:
        return 0
    return max(catalyst_score(r, as_of) for r in positive)


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


def load_catalyst_records(run_date: str) -> list[CatalystRecord]:
    return [CatalystRecord(**d) for d in load_catalysts(run_date)]


if __name__ == "__main__":
    print(catalyst_search_queries("TRENT", "Trent Ltd"))
    example = CatalystRecord(
        ticker="TRENT.NS",
        catalyst_type="order_client_win",
        headline="Example order win worth 20% of TTM revenue",
        published_date=date.today().isoformat(),
        source="example",
        url="https://example.com",
        order_value_pct_revenue=0.20,
    )
    print("Catalyst Score:", catalyst_score(example))
