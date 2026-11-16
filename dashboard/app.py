"""Momentum Dashboard - Streamlit UI for the weekly scored universe.

Run with:
    streamlit run dashboard/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from momentum.scoring import GROUP_LABELS, EARLY_SIGNS, SUPPORTED_TREND, HYPE_RISK, NOT_MOVING_YET  # noqa: E402
from momentum.universe import INDEX_DISPLAY_NAMES  # noqa: E402
from momentum.risk import RISK_BAND_LABELS  # noqa: E402
from data_loader import (  # noqa: E402
    load_latest_run,
    catalysts_by_ticker as _catalysts_by_ticker,
    load_excluded_holdings,
)

st.set_page_config(page_title="Momentum Dashboard", layout="wide")

result = load_latest_run()

st.title("Momentum Dashboard")
st.caption(
    "Nifty 50 + Nifty Next 50 + Nifty Midcap 150, scored weekly for price momentum and fundamental support. "
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
if "compared_to_run_date" in df.columns:
    compared_to_values = df["compared_to_run_date"].dropna().unique()
    compared_to_run_date = compared_to_values[0] if len(compared_to_values) else None
else:
    compared_to_run_date = None
if compared_to_run_date:
    st.caption(f"Last run: {run_date}. Deltas are vs the {compared_to_run_date} run.")
else:
    st.caption(f"Last run: {run_date}. No prior run yet to compare deltas against.")

filter_col1, filter_col2, filter_col3, filter_col4, filter_col5 = st.columns([2, 1, 1, 1, 1])

with filter_col1:
    search_query = st.text_input("Search ticker or name", placeholder="e.g. TCS or Divi's")
if search_query.strip():
    q = search_query.strip().lower()
    df = df[df["ticker"].str.lower().str.contains(q) | df["name"].str.lower().str.contains(q)]

with filter_col2:
    universe_options = ["All"] + [INDEX_DISPLAY_NAMES.get(i, i) for i in sorted(df["index"].dropna().unique())]
    selected_universe = st.selectbox("Universe", universe_options)
if selected_universe != "All":
    index_key = next(k for k, v in INDEX_DISPLAY_NAMES.items() if v == selected_universe)
    df = df[df["index"] == index_key]

with filter_col3:
    risk_band_options = ["All"] + [RISK_BAND_LABELS.get(b, b) for b in sorted(df["risk_band"].dropna().unique())]
    selected_risk_band = st.selectbox("Risk Band", risk_band_options)
if selected_risk_band != "All":
    risk_band_key = next(k for k, v in RISK_BAND_LABELS.items() if v == selected_risk_band)
    df = df[df["risk_band"] == risk_band_key]

with filter_col4:
    group_options = ["All"] + [GROUP_LABELS.get(g, g) for g in sorted(df["group"].dropna().unique())]
    selected_group = st.selectbox("Group", group_options)
if selected_group != "All":
    group_key = next(k for k, v in GROUP_LABELS.items() if v == selected_group)
    df = df[df["group"] == group_key]

has_sector_data = "sector" in df.columns
with filter_col5:
    if has_sector_data:
        sector_options = ["All"] + sorted(df["sector"].dropna().unique())
        selected_sector = st.selectbox("Sector", sector_options)
    else:
        st.selectbox("Sector", ["Not available yet"], disabled=True)
        selected_sector = "All"
if has_sector_data and selected_sector != "All":
    df = df[df["sector"] == selected_sector]

holdings_only = st.checkbox("📌 My Holdings only", value=False)
if holdings_only:
    df = df[df["is_held"]]

excluded = load_excluded_holdings(run_date)
if excluded and (excluded.get("funds") or excluded.get("unresolved")):
    with st.expander(
        f"ℹ️ {len(excluded.get('funds', []))} fund/ETF holdings and "
        f"{len(excluded.get('unresolved', []))} unresolved holdings aren't scored"
    ):
        if excluded.get("funds"):
            st.caption(
                "Funds/ETFs - no Support/Catalyst score, since those formulas assume a "
                "company with quarterly results and promoter holding, which a fund doesn't have:"
            )
            st.dataframe(pd.DataFrame(excluded["funds"]), hide_index=True, use_container_width=True)
        if excluded.get("unresolved"):
            st.caption("Not found on NSE's listed-equity master list (e.g. genuinely unlisted):")
            st.dataframe(pd.DataFrame(excluded["unresolved"]), hide_index=True, use_container_width=True)

catalysts_by_ticker = _catalysts_by_ticker(catalyst_records)

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
    "is_held", "ticker", "index", "sector", "overall_score", "momentum_score", "support_score",
    "catalyst_score", "analyst_consensus_score", "risk_score", "risk_band_label", "group_label",
    "sector_state_label", "relative_value_label", "ret_1m", "ret_3m", "early_turn_flag",
    "momentum_score_delta", "support_score_delta",
]
display_df = df[[c for c in table_cols if c in df.columns]].copy()
if "is_held" in display_df.columns:
    display_df = display_df.rename(columns={"is_held": "held"})
    display_df["held"] = display_df["held"].map({True: "📌", False: ""})
if "index" in display_df.columns:
    display_df["index"] = display_df["index"].map(lambda i: INDEX_DISPLAY_NAMES.get(i, i))
for pct_col in ("ret_1m", "ret_3m"):
    if pct_col in display_df.columns:
        display_df[pct_col] = (display_df[pct_col] * 100).round(1)
display_df = display_df.sort_values("overall_score", ascending=False).reset_index(drop=True)
st.caption(
    "Sorted by **Overall Score** = 50% Momentum + 35% Support + 15% Catalyst — "
    "see Methodology for why catalyst only adds, never subtracts. "
    "Click a row to open its deep-dive page."
)
if "table_nav_count" not in st.session_state:
    st.session_state["table_nav_count"] = 0

table_event = st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    # Keyed with a counter that bumps on every navigation, so the widget is
    # reset (no stale selection) when the user comes back from the deep-dive
    # page - otherwise returning here would immediately re-trigger the old
    # selection and bounce straight back.
    key=f"all_stocks_table_{st.session_state['table_nav_count']}",
)
selected_rows = table_event.selection.rows if table_event and table_event.selection else []
if selected_rows:
    selected_ticker = display_df.iloc[selected_rows[0]]["ticker"]
    st.session_state["selected_ticker"] = selected_ticker
    st.session_state["table_nav_count"] += 1
    st.switch_page("pages/2_Stock_Deep_Dive.py")

st.divider()

# --- Cards for flagged stocks ---
st.subheader("Flagged stocks")
flagged = df[df["group"].notna()].sort_values("overall_score", ascending=False)

if flagged.empty:
    st.info("No stocks fell into a group this run.")

for _, row in flagged.iterrows():
    with st.container(border=True):
        header_col, button_col = st.columns([5, 1])
        with header_col:
            held_badge = "📌 " if row.get("is_held") else ""
            st.markdown(
                f"### {held_badge}{row['group_label']}  —  {row['name']} ({row['ticker']})  "
                f"&nbsp; {row.get('risk_score', '')} {row.get('risk_band_label', '')}"
            )
            sector_bits = [b for b in [row.get("sector"), row.get("sector_state_label"), row.get("relative_value_label")] if b and pd.notna(b)]
            if sector_bits:
                st.caption(" · ".join(sector_bits))
        with button_col:
            if st.button("🔍 Deep dive", key=f"dive_{row['ticker']}"):
                st.session_state["selected_ticker"] = row["ticker"]
                st.switch_page("pages/2_Stock_Deep_Dive.py")
        momentum_delta = row.get("momentum_score_delta")
        support_delta = row.get("support_score_delta")
        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Overall", row.get("overall_score"))
        c2.metric("Momentum", row.get("momentum_score"), momentum_delta if pd.notna(momentum_delta) else None)
        c3.metric("Support", row.get("support_score"), support_delta if pd.notna(support_delta) else None)
        c4.metric("Catalyst", row.get("catalyst_score", 0))
        c5.metric("1M return", f"{row.get('ret_1m', 0) * 100:.1f}%" if pd.notna(row.get("ret_1m")) else "—")
        c6.metric("3M return", f"{row.get('ret_3m', 0) * 100:.1f}%" if pd.notna(row.get("ret_3m")) else "—")

        compared_to = row.get("compared_to_run_date")
        if (pd.notna(momentum_delta) or pd.notna(support_delta)) and pd.notna(compared_to):
            st.caption(f"Momentum/Support deltas are vs the {compared_to} run.")

        if pd.notna(row.get("analyst_consensus_score")):
            st.caption(
                f"Analyst consensus: {row['analyst_consensus_score']}/100 "
                f"({row.get('analyst_avg_reco_text', '—')}, {int(row.get('analyst_count', 0))} analysts, "
                f"as of {row.get('analyst_consensus_as_of', '—')}) — informational, not yet part of Overall Score."
            )

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
