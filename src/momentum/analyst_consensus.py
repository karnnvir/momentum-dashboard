"""Phase 2 feasibility result: analyst Buy/Hold/Sell consensus from
Trendlyne - the half of the originally-planned "Institutional Score" that
turned out to be scrapable.

Feasibility check findings (2026-11-16):
  - Mutual-fund-holding data (Trendlyne's /equity/ownership/ page) requires
    a logged-in account - even a real browser executing its JS gets "No
    Stock found" for an anonymous visitor. NOT built; would need the user
    to decide whether to get a Trendlyne account.
  - Analyst consensus (Buy/Hold/Sell counts) IS server-rendered as a JSON
    blob directly in the page HTML at /equity/consensus-estimates/{id}/
    {symbol}/{slug}/ - no login needed. This module implements that half
    only. The Design Spec's "Institutional Score" (blending both) stays
    unbuilt until/unless the MF-holding half gets a data source.

Trendlyne's URLs need its own internal numeric stock ID, not the NSE
symbol - resolved once via its public equity-sitemap-stocks.xml (every
listed stock, symbol -> id -> URL slug), cached like the NSE equity master.
"""

from __future__ import annotations

import html
import re
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
SITEMAP_URL = "https://trendlyne.com/equity-sitemap-stocks.xml"
SITEMAP_CACHE = ROOT / "data" / "universe_cache" / "trendlyne_sitemap.xml"
CONSENSUS_URL = "https://trendlyne.com/equity/consensus-estimates/{id}/{symbol}/{slug}/"
REQUEST_DELAY_SECONDS = 1.5  # be polite - same etiquette as fundamentals.py's screener.in scraping

# Trendlyne intermittently serves a page with no RECOMMENDATION block for a
# stock that demonstrably has one (confirmed 2026-11-17: Kalyan Jewellers,
# Fortis, Glaxo all came back empty during a full-universe batch run, then
# returned real data seconds later on a standalone retry - repeated,
# genuinely-uncovered stocks like Bajaj Holdings stayed empty across 3
# back-to-back attempts). Looks like soft rate-limiting under sustained
# sequential load, not a real "no coverage" signal - so a single empty
# result is retried before being accepted as genuine, rather than cached
# as confirmed-no-coverage off one unlucky response.
NO_DATA_RETRY_ATTEMPTS = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# Design Spec-style fixed bucket weights, documented on the Methodology
# page - a calculated score from the real analyst-count distribution, never
# a judgment call. 100 = unanimous Strong Buy, 0 = unanimous Strong Sell.
RATING_WEIGHTS = {
    "STRONG_BUY": 1.0,
    "BUY": 0.75,
    "HOLD": 0.5,
    "SELL": 0.25,
    "STRONG_SELL": 0.0,
}

_SITEMAP_URL_RE = re.compile(r"<loc>https://trendlyne\.com/equity/(\d+)/([^/]+)/([^/]+)/</loc>")
_RECOMMENDATION_RE = re.compile(r'"RECOMMENDATION":\s*\[(\{.*?\})\]', re.DOTALL)
_BUCKET_RE = re.compile(r'"(STRONG_SELL|SELL|HOLD|BUY|STRONG_BUY)":\s*\{"value":\s*([\d.]+)')
_ASOF_RE = re.compile(r'"asofdate":\s*"([\d-]+)"')
_AVG_RECO_RE = re.compile(r'"AVG_RECO_TEXT":\s*"([^"]+)"')


def fetch_trendlyne_symbol_map() -> dict[str, tuple[str, str]]:
    """{NSE symbol: (trendlyne_id, url_slug)} for every stock Trendlyne
    lists - resolved once from their public sitemap, not guessed or hit
    per-stock. Cached like the NSE equity master list."""
    SITEMAP_CACHE.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = requests.get(SITEMAP_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        text = resp.text
        SITEMAP_CACHE.write_text(text)
    except Exception as exc:  # noqa: BLE001
        if SITEMAP_CACHE.exists():
            print(f"[analyst_consensus] live sitemap fetch failed ({exc}); using cache")
            text = SITEMAP_CACHE.read_text()
        else:
            raise
    symbol_map: dict[str, tuple[str, str]] = {}
    for match in _SITEMAP_URL_RE.finditer(text):
        tl_id, symbol, slug = match.groups()
        symbol_map[symbol] = (tl_id, slug)
    return symbol_map


def fetch_analyst_consensus(symbol: str, tl_id: str, slug: str) -> dict | None:
    """Scrapes the Buy/Hold/Sell analyst counts for one stock. Returns None
    (never a fabricated/zeroed score) if the page doesn't have a
    RECOMMENDATION block - e.g. a stock with no analyst coverage."""
    url = CONSENSUS_URL.format(id=tl_id, symbol=symbol, slug=slug)
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    # The page embeds this JSON HTML-entity-escaped (&quot; instead of "),
    # not as literal JSON - unescape before matching.
    page_text = html.unescape(resp.text)

    reco_match = _RECOMMENDATION_RE.search(page_text)
    if reco_match is None:
        return None
    block = reco_match.group(1)

    counts = {bucket: float(value) for bucket, value in _BUCKET_RE.findall(block)}
    if not counts or sum(counts.values()) == 0:
        return None

    asof_match = _ASOF_RE.search(block)
    avg_reco_match = _AVG_RECO_RE.search(block)

    total = sum(counts.values())
    weighted = sum(counts.get(bucket, 0.0) * weight for bucket, weight in RATING_WEIGHTS.items())
    score = round(100 * weighted / total, 1)

    return {
        "analyst_consensus_score": score,
        "analyst_count": int(total),
        "analyst_counts_by_rating": counts,
        "analyst_avg_reco_text": avg_reco_match.group(1) if avg_reco_match else None,
        "analyst_consensus_as_of": asof_match.group(1) if asof_match else None,
    }


def fetch_analyst_consensus_for_universe(symbols: list[str]) -> tuple[dict[str, dict], set[str]]:
    """Returns (results, failed).

    results holds every symbol that was actually CHECKED - with data if a
    RECOMMENDATION block was found, absent (but still worth caching as "no
    coverage") otherwise. failed holds symbols where the request itself
    raised (network error, HTTP error, etc.) - the caller should NOT cache
    these as checked, so a transient failure gets retried next run instead
    of being mistaken for "confirmed no analyst coverage" for the next ~75
    days."""
    symbol_map = fetch_trendlyne_symbol_map()
    results: dict[str, dict] = {}
    failed: set[str] = set()
    for i, symbol in enumerate(symbols):
        mapping = symbol_map.get(symbol)
        if mapping is not None:
            tl_id, slug = mapping
            data = None
            last_exc: Exception | None = None
            for attempt in range(NO_DATA_RETRY_ATTEMPTS):
                try:
                    data = fetch_analyst_consensus(symbol, tl_id, slug)
                    last_exc = None
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    data = None
                if data is not None:
                    break
                if attempt < NO_DATA_RETRY_ATTEMPTS - 1:
                    time.sleep(REQUEST_DELAY_SECONDS)
            if data is not None:
                results[symbol] = data
            elif last_exc is not None:
                print(f"[analyst_consensus] {symbol} failed after {NO_DATA_RETRY_ATTEMPTS} attempts: {last_exc}")
                failed.add(symbol)
            # else: no RECOMMENDATION block on every one of
            # NO_DATA_RETRY_ATTEMPTS clean responses - accepted as genuine
            # no-coverage, not a transient blip.
        if i < len(symbols) - 1:
            time.sleep(REQUEST_DELAY_SECONDS)
    return results, failed
