"""Phase 1.5 fast-follow: "My Holdings". Merges the user's actual portfolio
into the universe as a 4th tier for anything not already covered by the
three Nifty index tiers, and flags is_held=True on matching rows either way.

Privacy: the uploaded broker export lives at data/holdings/portfolio.tsv,
which is entirely gitignored - it holds real quantities, cost basis and
P&L, which must never reach the public repo. This module only ever reads
tickers/names from it for scoring; it never persists quantity, cost, or P&L
anywhere. pipeline.py is responsible for keeping every holdings-aware
output (the 4th tier, the is_held flag) out of the publicly-tracked
data/runs/ path entirely - see HOLDINGS_RUNS_DIR usage there.

Broker exports use their own internal short codes (e.g. "MAHMAH" for
Mahindra & Mahindra), not real NSE trading symbols, so tickers are resolved
via ISIN against NSE's own published equity master list - never guessed
from the company name or the broker's code. Indian ISINs for mutual
fund/ETF units start with "INF" (vs "INE" for equities) - that prefix is
what separates fund holdings (never scored - no promoter holding, no
quarterly results, the Support/Catalyst formulas don't apply) from real
company holdings (scored exactly like the rest of the universe).
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
HOLDINGS_FILE = ROOT / "data" / "holdings" / "portfolio.tsv"
HOLDINGS_RUNS_DIR = ROOT / "data" / "holdings" / "runs"  # gitignored - never the public data/runs/

NSE_EQUITY_MASTER_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
MASTER_CACHE = ROOT / "data" / "universe_cache" / "nse_equity_master.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

MY_HOLDINGS_INDEX = "MY_HOLDINGS"

# A stock that only lists on BSE (not NSE) can't be resolved against NSE's
# equity master list at all - its yfinance ticker and its screener.in URL
# then diverge from this project's usual "symbol == ticker prefix ==
# screener.in slug" assumption, which every NSE-listed stock satisfies.
# Verified by hand per entry (2026-11-16): National Stock Exchange of
# India itself IPO'd onto BSE on 2026-09-24 (an exchange can't list on
# itself) - Yahoo Finance recognises the vanity ticker "NSE.BO", but
# screener.in needs BSE's numeric scrip code (544937) instead. This is a
# small, explicit, manually-verified table, not a general automated BSE
# resolver - add an entry here only after confirming both the yfinance
# ticker and the screener.in page actually work, the same diligence as
# every other data-source choice in this project.
BSE_ONLY_OVERRIDES: dict[str, dict[str, str]] = {
    "INE721I01024": {  # National Stock Exchange of India Ltd
        "ticker_symbol": "NSE",
        "screener_symbol": "544937",
    },
}


def fetch_isin_master() -> pd.DataFrame:
    """NSE's own published ISIN <-> SYMBOL list - the only reliable way to
    resolve a broker's internal short code to a real trading symbol."""
    MASTER_CACHE.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = requests.get(NSE_EQUITY_MASTER_URL, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        text = resp.text
        MASTER_CACHE.write_text(text)
    except Exception as exc:  # noqa: BLE001
        if MASTER_CACHE.exists():
            print(f"[holdings] live fetch of NSE equity master failed ({exc}); using cache")
            text = MASTER_CACHE.read_text()
        else:
            raise
    master = pd.read_csv(io.StringIO(text))
    master.columns = [c.strip() for c in master.columns]
    master["ISIN NUMBER"] = master["ISIN NUMBER"].str.strip()
    return master


def load_portfolio_file(path: Path = HOLDINGS_FILE) -> pd.DataFrame | None:
    if not path.exists():
        return None
    df = pd.read_csv(path, sep="\t")
    df = df.loc[:, ~df.columns.str.contains("^Unnamed")]
    df["ISIN Code"] = df["ISIN Code"].str.strip()
    return df


def resolve_holdings(portfolio: pd.DataFrame, master: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """equities: real symbol/name/ticker for every "INE" (equity) ISIN found
    in the NSE master list, or in BSE_ONLY_OVERRIDES - these flow into the
    scoring pipeline. Every row also carries screener_symbol: equal to
    symbol for an ordinary NSE-listed stock, or the overridden BSE scrip
    code for a BSE_ONLY_OVERRIDES entry - fundamentals.py should fetch
    screener.in using THIS, not symbol, since the two can diverge.
    funds: every "INF" (fund/ETF unit) ISIN - never scored.
    unresolved: an "INE" equity ISIN not found in the NSE master list and
    not in BSE_ONLY_OVERRIDES either (e.g. genuinely unlisted, or BSE-only
    but not yet manually verified) - flagged rather than guessed at.
    """
    merged = portfolio.merge(
        master[["SYMBOL", "NAME OF COMPANY", "ISIN NUMBER"]],
        left_on="ISIN Code",
        right_on="ISIN NUMBER",
        how="left",
    )
    is_fund = merged["ISIN Code"].str.startswith("INF")
    funds = merged[is_fund][["Stock Symbol", "Company Name", "ISIN Code"]].copy()
    equities_all = merged[~is_fund].copy()

    nse_resolved = equities_all[equities_all["SYMBOL"].notna()].copy()
    nse_resolved["symbol"] = nse_resolved["SYMBOL"]
    nse_resolved["name"] = nse_resolved["NAME OF COMPANY"]
    nse_resolved["ticker"] = nse_resolved["symbol"] + ".NS"
    nse_resolved["screener_symbol"] = nse_resolved["symbol"]

    still_unresolved = equities_all[equities_all["SYMBOL"].isna()].copy()
    bse_rows = []
    truly_unresolved_rows = []
    for _, row in still_unresolved.iterrows():
        override = BSE_ONLY_OVERRIDES.get(row["ISIN Code"])
        if override is None:
            truly_unresolved_rows.append(row)
            continue
        bse_rows.append(
            {
                "symbol": override["ticker_symbol"],
                "name": row["Company Name"],
                "ticker": f"{override['ticker_symbol']}.BO",
                "screener_symbol": override["screener_symbol"],
            }
        )
    bse_resolved = pd.DataFrame(bse_rows, columns=["symbol", "name", "ticker", "screener_symbol"])

    equities = pd.concat(
        [nse_resolved[["symbol", "name", "ticker", "screener_symbol"]], bse_resolved], ignore_index=True
    )

    unresolved = pd.DataFrame(truly_unresolved_rows)[["Stock Symbol", "Company Name", "ISIN Code"]] if truly_unresolved_rows else still_unresolved.iloc[0:0][["Stock Symbol", "Company Name", "ISIN Code"]]

    return {"equities": equities, "funds": funds, "unresolved": unresolved}


def load_my_holdings() -> dict | None:
    """Entry point for pipeline.py. None if no portfolio file is present."""
    portfolio = load_portfolio_file()
    if portfolio is None:
        return None
    master = fetch_isin_master()
    return resolve_holdings(portfolio, master)
