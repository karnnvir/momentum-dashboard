"""Fetch the stock universe: Nifty 50 + Nifty Next 50 + Nifty Midcap 150.

Pulls live constituent CSVs from niftyindices.com so index rebalances are
picked up automatically. Falls back to a bundled cached CSV (data/universe_cache/)
if the site is unreachable or changes shape, so a weekly run never hard-fails
on this step alone.

The three tiers don't overlap by rank construction (1-50, 51-100, 101-250),
so `index` is a single tag per stock, not a multi-membership flag - keeps
the dashboard's universe dropdown a simple filter on one column.
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import requests

NIFTY_50_URL = "https://niftyindices.com/IndexConstituent/ind_nifty50list.csv"
NIFTY_NEXT50_URL = "https://niftyindices.com/IndexConstituent/ind_niftynext50list.csv"
NIFTY_MIDCAP150_URL = "https://niftyindices.com/IndexConstituent/ind_niftymidcap150list.csv"

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "universe_cache"

HEADERS = {
    # niftyindices.com rejects requests with no browser-like UA.
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def _fetch_csv(url: str, cache_name: str) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / cache_name
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        cache_path.write_text(resp.text)
        return df
    except Exception as exc:  # network blocked, site changed, etc.
        if cache_path.exists():
            print(f"[universe] live fetch failed ({exc}); using cached {cache_name}")
            return pd.read_csv(cache_path)
        raise RuntimeError(
            f"Could not fetch {url} and no cache at {cache_path}. "
            "Download the constituent CSV manually from niftyindices.com and "
            f"place it at {cache_path}."
        ) from exc


def _normalize(df: pd.DataFrame, index_name: str) -> pd.DataFrame:
    df = df.rename(columns={c: c.strip() for c in df.columns})
    # niftyindices CSVs use "Symbol" and "Company Name" columns.
    symbol_col = next(c for c in df.columns if c.lower() == "symbol")
    name_col = next((c for c in df.columns if "company" in c.lower()), symbol_col)
    out = pd.DataFrame(
        {
            "symbol": df[symbol_col].astype(str).str.strip(),
            "name": df[name_col].astype(str).str.strip(),
            "index": index_name,
        }
    )
    out["ticker"] = out["symbol"] + ".NS"
    return out


INDEX_DISPLAY_NAMES = {
    "NIFTY_50": "Nifty 50",
    "NIFTY_NEXT_50": "Nifty Next 50",
    "NIFTY_MIDCAP_150": "Nifty Midcap 150",
}


def fetch_universe() -> pd.DataFrame:
    """Return a DataFrame with columns: symbol, name, index, ticker.

    `index` is one of "NIFTY_50", "NIFTY_NEXT_50", "NIFTY_MIDCAP_150". The
    three tiers don't overlap by construction (ranks 1-50, 51-100, 101-250),
    so no de-dup is needed, but duplicates are still dropped defensively in
    case a stock migrated between tiers mid-cache.
    """
    nifty50 = _normalize(_fetch_csv(NIFTY_50_URL, "nifty_50.csv"), "NIFTY_50")
    next50 = _normalize(
        _fetch_csv(NIFTY_NEXT50_URL, "nifty_next50.csv"), "NIFTY_NEXT_50"
    )
    midcap150 = _normalize(
        _fetch_csv(NIFTY_MIDCAP150_URL, "nifty_midcap150.csv"), "NIFTY_MIDCAP_150"
    )
    universe = pd.concat([nifty50, next50, midcap150], ignore_index=True)
    universe = universe.drop_duplicates(subset="symbol", keep="first")
    return universe.reset_index(drop=True)


if __name__ == "__main__":
    u = fetch_universe()
    print(f"Universe: {len(u)} stocks ({u['index'].value_counts().to_dict()})")
    print(u.head())
