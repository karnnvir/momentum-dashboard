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
    support_score: float | None = None
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


def fetch_fundamentals(symbol: str) -> StockFundamentals:
    sf = StockFundamentals(symbol=symbol)
    try:
        resp = requests.get(SCREENER_URL.format(symbol=symbol), headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        sf.roce = _parse_ratio(soup, "ROCE")
        sf.debt_to_equity = _parse_ratio(soup, "Debt to equity")
        sf.promoter_holding = _parse_ratio(soup, "Promoter holding")
        sf.promoter_pledge = _parse_ratio(soup, "Pledged percentage")
        sf.fii_holding = _parse_ratio(soup, "FII holding")
        sf.dii_holding = _parse_ratio(soup, "DII holding")

        sf.quarterly_sales = _parse_quarterly_series(soup, "Sales")
        sf.quarterly_profit = _parse_quarterly_series(soup, "Net Profit")
        sf.quarterly_opm = _parse_quarterly_series(soup, "OPM")

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


def score_fundamentals(sf: StockFundamentals) -> float | None:
    """Rough 0-100 support score from the raw fundamentals. This is a
    starting heuristic, not calibrated against outcomes yet - revisit the
    weights once a few quarters of real runs are in."""
    if sf.error:
        return None

    points = []

    profit_growth = _yoy_growth(sf.quarterly_profit)
    if profit_growth and all(g is not None for g in profit_growth[-2:]):
        accelerating = profit_growth[-1] > profit_growth[-2]
        recent_growth = profit_growth[-1]
        points.append(min(max(recent_growth, -1), 1) * 50 + 50)
        if accelerating:
            points.append(80)

    # loss -> profit turnaround
    if len(sf.quarterly_profit) >= 2 and sf.quarterly_profit[-2] <= 0 < sf.quarterly_profit[-1]:
        points.append(90)

    if len(sf.quarterly_opm) >= 2:
        margin_delta = sf.quarterly_opm[-1] - sf.quarterly_opm[-2]
        points.append(min(max(margin_delta, -10), 10) * 3 + 50)

    if sf.roce is not None:
        points.append(min(max(sf.roce, 0), 40) / 40 * 100)

    if sf.debt_to_equity is not None:
        points.append(max(0, 100 - sf.debt_to_equity * 50))

    if sf.promoter_pledge is not None:
        points.append(max(0, 100 - sf.promoter_pledge * 4))

    if not points:
        return None
    return round(sum(points) / len(points), 1)


def fetch_fundamentals_for_universe(symbols: list[str]) -> dict[str, StockFundamentals]:
    results = {}
    for i, symbol in enumerate(symbols):
        results[symbol] = fetch_fundamentals(symbol)
        results[symbol].support_score = score_fundamentals(results[symbol])
        if i < len(symbols) - 1:
            time.sleep(REQUEST_DELAY_SECONDS)
    return results


if __name__ == "__main__":
    sf = fetch_fundamentals("TRENT")
    sf.support_score = score_fundamentals(sf)
    print(sf)
