"""Layer 2: fundamental-support scoring from screener.in.

screener.in has no official public API, so this scrapes the public company
page (no login required for the basic ratios/quarterly tables). The page
layout is not guaranteed to stay stable - if scraping breaks, this module
should fail loudly per-ticker (never silently zero a score) so a bad parse
doesn't masquerade as "no fundamental support".

Run cadence: refreshed each results season (roughly quarterly), not every
weekly run - cache the output and let pipeline.py decide when to re-pull.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

SCREENER_URL = "https://www.screener.in/company/{symbol}/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
REQUEST_DELAY_SECONDS = 1.5  # be polite - this is one person's weekly screen, not a crawler

# Design Spec: earnings-quality discount constants, the single source of
# truth also rendered on the dashboard's Methodology page.
CASH_CONVERSION_THRESHOLD = 0.8  # CFO / Net Profit below this for 2+ years is the red flag
CASH_CONVERSION_DISCOUNT_MULTIPLIER = 0.7


@dataclass
class StockFundamentals:
    symbol: str
    roce: float | None = None
    debt_to_equity: float | None = None
    promoter_holding: float | None = None
    promoter_pledge: float | None = None
    fii_holding: float | None = None
    dii_holding: float | None = None
    quarterly_sales: list = field(default_factory=list)  # oldest -> newest
    quarterly_profit: list = field(default_factory=list)
    quarterly_opm: list = field(default_factory=list)  # operating margin %
    annual_net_profit: list = field(default_factory=list)  # oldest -> newest
    annual_cash_from_operations: list = field(default_factory=list)
    support_score: float | None = None
    earnings_quality_discounted: bool = False
    # Design Spec v2 (Phase 2, sector/relative-value): "Broad Sector" from
    # screener.in's peer-comparison breadcrumb - coarse enough that each
    # sector has multiple universe stocks to average, unlike the finer
    # "Industry" tag.
    sector: str | None = None
    pe_ratio: float | None = None
    recent_profit_growth_pct: float | None = None  # set by score_fundamentals; feeds PEG
    error: str | None = None


def _parse_ratio(soup: BeautifulSoup, label: str) -> float | None:
    """Find a top-ratios value by its label text, e.g. 'ROCE', 'Debt to equity'."""
    li = soup.find("li", string=re.compile(re.escape(label), re.I))
    if li is None:
        # label and value often sit in sibling spans within the same <li>
        for candidate in soup.select("li.flex"):
            if label.lower() in candidate.get_text(" ", strip=True).lower():
                li = candidate
                break
    if li is None:
        return None
    value_span = li.find("span", class_=re.compile("number"))
    text = (value_span or li).get_text(" ", strip=True)
    match = re.search(r"-?[\d,]+\.?\d*", text)
    return float(match.group().replace(",", "")) if match else None


def _parse_quarterly_series(soup: BeautifulSoup, row_label: str) -> list[float]:
    table = soup.find("section", id="quarters")
    if table is None:
        return []
    row = table.find("tr", string=re.compile(re.escape(row_label), re.I))
    if row is None:
        for r in table.find_all("tr"):
            if row_label.lower() in r.get_text(" ", strip=True).lower():
                row = r
                break
    if row is None:
        return []
    values = []
    for cell in row.find_all("td")[1:]:
        text = cell.get_text(strip=True).replace(",", "")
        try:
            values.append(float(text))
        except ValueError:
            continue
    return values


def _parse_annual_series(soup: BeautifulSoup, section_id: str, row_label: str) -> list[float]:
    """Same idea as _parse_quarterly_series but for an annual table
    (profit-loss, cash-flow, balance-sheet sections on screener.in)."""
    table = soup.find("section", id=section_id)
    if table is None:
        return []
    row = table.find("tr", string=re.compile(re.escape(row_label), re.I))
    if row is None:
        for r in table.find_all("tr"):
            if row_label.lower() in r.get_text(" ", strip=True).lower():
                row = r
                break
    if row is None:
        return []
    values = []
    for cell in row.find_all("td")[1:]:
        text = cell.get_text(strip=True).replace(",", "")
        try:
            values.append(float(text))
        except ValueError:
            continue
    return values


def fetch_fundamentals(symbol: str, screener_symbol: str | None = None) -> StockFundamentals:
    """screener_symbol overrides what's used to build the screener.in URL,
    for the rare holding where it diverges from the universe `symbol` (a
    BSE-only stock - see holdings.BSE_ONLY_OVERRIDES). Defaults to symbol."""
    sf = StockFundamentals(symbol=symbol)
    try:
        resp = requests.get(SCREENER_URL.format(symbol=screener_symbol or symbol), headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        sf.roce = _parse_ratio(soup, "ROCE")
        sf.debt_to_equity = _parse_ratio(soup, "Debt to equity")
        sf.promoter_holding = _parse_ratio(soup, "Promoter holding")
        sf.promoter_pledge = _parse_ratio(soup, "Pledged percentage")
        sf.fii_holding = _parse_ratio(soup, "FII holding")
        sf.dii_holding = _parse_ratio(soup, "DII holding")
        sf.pe_ratio = _parse_ratio(soup, "Stock P/E")

        sector_link = soup.select_one('a[title="Broad Sector"]')
        sf.sector = sector_link.get_text(strip=True) if sector_link else None

        sf.quarterly_sales = _parse_quarterly_series(soup, "Sales")
        sf.quarterly_profit = _parse_quarterly_series(soup, "Net Profit")
        sf.quarterly_opm = _parse_quarterly_series(soup, "OPM")

        sf.annual_net_profit = _parse_annual_series(soup, "profit-loss", "Net Profit")
        sf.annual_cash_from_operations = _parse_annual_series(
            soup, "cash-flow", "Cash from Operating Activity"
        )

    except Exception as exc:  # noqa: BLE001
        sf.error = str(exc)

    return sf


def _yoy_growth(series: list[float]) -> list[float]:
    """Quarter-over-same-quarter-last-year growth, assuming 4 quarters/year."""
    if len(series) < 5:
        return []
    return [
        (series[i] / series[i - 4] - 1.0) if series[i - 4] else None
        for i in range(4, len(series))
    ]


def cash_conversion_ratios(sf: StockFundamentals) -> list[float]:
    """CFO / Net Profit per year, oldest -> newest. Design Spec's earnings-
    quality check: a ratio sustained below 0.8 means reported profit isn't
    showing up as cash, the Sloan-accrual red flag."""
    years = min(len(sf.annual_net_profit), len(sf.annual_cash_from_operations))
    ratios = []
    for i in range(years):
        ni = sf.annual_net_profit[-years + i]
        cfo = sf.annual_cash_from_operations[-years + i]
        if ni:
            ratios.append(cfo / ni)
    return ratios


def score_fundamentals_breakdown(sf: StockFundamentals) -> list[dict]:
    """Every point contribution that feeds score_fundamentals, labeled -
    the single source of truth both functions share, so the Support Score
    shown on the dashboard and the breakdown shown on the deep-dive page
    can never drift apart. Also sets sf.recent_profit_growth_pct as a
    side effect, same as before this was split out."""
    if sf.error:
        return []

    breakdown: list[dict] = []

    profit_growth = _yoy_growth(sf.quarterly_profit)
    if profit_growth and profit_growth[-1] is not None:
        recent_growth = profit_growth[-1]
        sf.recent_profit_growth_pct = round(recent_growth * 100, 1)
        pts = min(max(recent_growth, -1), 1) * 50 + 50
        breakdown.append(
            {"label": "Recent YoY profit growth", "detail": f"{recent_growth:+.1%}", "points": round(pts, 1)}
        )
        if len(profit_growth) >= 2 and profit_growth[-2] is not None:
            accelerating = profit_growth[-1] > profit_growth[-2]
            if accelerating:
                breakdown.append({"label": "Profit growth accelerating", "detail": "yes", "points": 80.0})

    # loss -> profit turnaround
    if len(sf.quarterly_profit) >= 2 and sf.quarterly_profit[-2] <= 0 < sf.quarterly_profit[-1]:
        breakdown.append({"label": "Loss-to-profit turnaround", "detail": "yes", "points": 90.0})

    if len(sf.quarterly_opm) >= 2:
        margin_delta = sf.quarterly_opm[-1] - sf.quarterly_opm[-2]
        pts = min(max(margin_delta, -10), 10) * 3 + 50
        breakdown.append(
            {"label": "Operating margin change (QoQ)", "detail": f"{margin_delta:+.1f}pp", "points": round(pts, 1)}
        )

    if sf.roce is not None:
        pts = min(max(sf.roce, 0), 40) / 40 * 100
        breakdown.append({"label": "ROCE", "detail": f"{sf.roce:.1f}%", "points": round(pts, 1)})

    if sf.debt_to_equity is not None:
        pts = max(0, 100 - sf.debt_to_equity * 50)
        breakdown.append({"label": "Debt to equity", "detail": f"{sf.debt_to_equity:.2f}", "points": round(pts, 1)})

    if sf.promoter_pledge is not None:
        pts = max(0, 100 - sf.promoter_pledge * 4)
        breakdown.append({"label": "Promoter pledge", "detail": f"{sf.promoter_pledge:.1f}%", "points": round(pts, 1)})

    return breakdown


def score_fundamentals(sf: StockFundamentals) -> float | None:
    """Rough 0-100 support score from the raw fundamentals. This is a
    starting heuristic, not calibrated against outcomes yet - revisit the
    weights once a few quarters of real runs are in."""
    if sf.error:
        return None

    breakdown = score_fundamentals_breakdown(sf)
    if not breakdown:
        return None
    score = sum(b["points"] for b in breakdown) / len(breakdown)

    # Earnings-quality discount (Design Spec): CFO/NI < 0.8 for 2+
    # consecutive years multiplies the score by 0.7, so manufactured-looking
    # profit growth can't fake a high Support Score.
    cfo_ratios = cash_conversion_ratios(sf)
    sf.earnings_quality_discounted = len(cfo_ratios) >= 2 and all(
        r < CASH_CONVERSION_THRESHOLD for r in cfo_ratios[-2:]
    )
    if sf.earnings_quality_discounted:
        score *= CASH_CONVERSION_DISCOUNT_MULTIPLIER

    return round(score, 1)


def fetch_fundamentals_for_universe(
    symbols: list[str], screener_symbol_overrides: dict[str, str] | None = None
) -> dict[str, StockFundamentals]:
    overrides = screener_symbol_overrides or {}
    results = {}
    for i, symbol in enumerate(symbols):
        sf = fetch_fundamentals(symbol, screener_symbol=overrides.get(symbol))
        try:
            sf.support_score = score_fundamentals(sf)
        except Exception as exc:  # noqa: BLE001 - a bad scoring input shouldn't lose the whole batch
            sf.error = sf.error or f"score_fundamentals failed: {exc}"
        results[symbol] = sf
        if i < len(symbols) - 1:
            time.sleep(REQUEST_DELAY_SECONDS)
    return results


if __name__ == "__main__":
    sf = fetch_fundamentals("TRENT")
    sf.support_score = score_fundamentals(sf)
    print(sf)
