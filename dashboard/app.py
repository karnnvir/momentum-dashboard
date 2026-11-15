"""Momentum Dashboard - Streamlit UI for the weekly scored universe.

Run with:
    streamlit run dashboard/app.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from momentum.scoring import GROUP_LABELS, EARLY_SIGNS, SUPPORTED_TREND, HYPE_RISK, NOT_MOVING_YET  # noqa: E402

DATA_DIR = ROOT / "data"
RUNS_DIR = DATA_DIR / "runs"

st.set_page_config(page_title="Momentum Dashboard", layout="wide")


def load_latest_run() -> tuple[str, pd.DataFrame, list[dict]] | None:
    latest_file = RUNS_DIR / "latest.txt"
    if not latest_file.exists():
        return None
    run_date = latest_file.read_text().strip()
    run_path = RUNS_DIR / run_date / "run.json"
    if not run_path.exists():
        return None
    df = pd.DataFrame(json.loads(run_path.read_text()))
    catalysts_path = RUNS_DIR / run_date / "catalysts.json"
    catalysts = json.loads(catalysts_path.read_text()) if catalysts_path.exists() else []
    return run_date, df, catalysts


result = load_latest_run()

st.title("Momentum Dashboard")
st.caption(
    "Nifty Next 50 + Nifty Midcap 150, scored weekly for price momentum and fundamental support. "
    "**These scores are screening signals, not buy recommendations.** "
    "Every score is a calculated number from documented inputs — see the **Methodology** page "
    "(sidebar) for the exact formula behind each one."
)

if result is None:
    st.warning(
        "No run found yet. From the project root:\n\n"
        "```\npip install -r requirements.txt\npython -m momentum.pipeline\n```"
    )
    st.stop()

run_date, df, catalyst_records = result
st.caption(f"Last run: {run_date}")

catalysts_by_ticker: dict[str, list[dict]] = {}
for rec in catalyst_records:
    catalysts_by_ticker.setdefault(rec["ticker"], []).append(rec)

# --- Summary row ---
group_order = [EARLY_SIGNS, SUPPORTED_TREND, HYPE_RISK, NOT_MOVING_YET]
counts = df["group"].value_counts()
cols = st.columns(len(group_order))
for col, group in zip(cols, group_order):
    col.metric(GROUP_LABELS[group], int(counts.get(group, 0)))

st.divider()

# --- Sortable table of all stocks ---
st.subheader("All stocks")
table_cols = [
    "ticker", "index", "momentum_score", "support_score", "catalyst_score",
    "risk_band_label", "group_label",
    "ret_1m", "ret_3m", "early_turn_flag",
    "momentum_score_delta", "support_score_delta",
]
display_df = df[[c for c in table_cols if c in df.columns]].copy()
for pct_col in ("ret_1m", "ret_3m"):
    if pct_col in display_df.columns:
        display_df[pct_col] = (display_df[pct_col] * 100).round(1)
display_df = display_df.sort_values("momentum_score", ascending=False)
st.dataframe(display_df, use_container_width=True, hide_index=True)

st.divider()

# --- Cards for flagged stocks ---
st.subheader("Flagged stocks")
flagged = df[df["group"].notna()].sort_values("momentum_score", ascending=False)

if flagged.empty:
    st.info("No stocks fell into a group this run.")

for _, row in flagged.iterrows():
    with st.container(border=True):
        st.markdown(
            f"### {row['group_label']}  —  {row['name']} ({row['ticker']})  "
            f"&nbsp; {row.get('risk_band_label', '')}"
        )
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Momentum", row.get("momentum_score"), row.get("momentum_score_delta"))
        c2.metric("Support", row.get("support_score"), row.get("support_score_delta"))
        c3.metric("Catalyst", row.get("catalyst_score", 0))
        c4.metric("1M return", f"{row.get('ret_1m', 0) * 100:.1f}%" if pd.notna(row.get("ret_1m")) else "—")
        c5.metric("3M return", f"{row.get('ret_3m', 0) * 100:.1f}%" if pd.notna(row.get("ret_3m")) else "—")

        if row.get("risk_active_triggers"):
            st.caption(f"Risk triggers active: {row['risk_active_triggers']}")

        flags = []
        if row.get("early_turn_cross_above_50dma"):
            flags.append("crossed above 50-DMA on volume")
        if row.get("early_turn_rs_turning_up"):
            flags.append("relative strength turning up")
        if row.get("early_turn_fresh_52w_high"):
            flags.append("fresh 52-week high after consolidation")
        if flags:
            st.caption("Early-turn: " + "; ".join(flags))

        ticker_catalysts = catalysts_by_ticker.get(row["ticker"], [])
        if ticker_catalysts:
            st.markdown("**Catalysts**")
            for cat in ticker_catalysts:
                st.markdown(
                    f"- [{cat['headline']}]({cat['url']}) — "
                    f"*{cat['catalyst_type']}, {cat['published_date']}, {cat['source']}*"
                )
        else:
            st.caption("No catalysts researched yet for this run.")
