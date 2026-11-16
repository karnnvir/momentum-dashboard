"""Stock deep-dive page - Phase 1.5 fast-follow (Design Spec: "V1.5 - fast-
follow right after"). Opened from the Overview page's table/cards via
st.session_state["selected_ticker"], or by picking a stock directly below.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
import yfinance as yf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from momentum.scoring import GROUP_LABELS  # noqa: E402
from momentum.universe import INDEX_DISPLAY_NAMES  # noqa: E402
from momentum.risk import TRIGGER_POINTS  # noqa: E402
from momentum.catalysts import (  # noqa: E402
    TYPE_WEIGHTS,
    CONFIRMATION_MULTIPLIERS,
    RECENCY_DECAY_DAYS,
    catalyst_score,
    CatalystRecord,
)
from data_loader import load_latest_run, catalysts_by_ticker, score_history, load_fundamentals_cache  # noqa: E402

st.set_page_config(page_title="Stock Deep-Dive - Momentum Dashboard", layout="wide")

result = load_latest_run()
if result is None:
    st.warning("No run found yet. Run the pipeline first, then come back here.")
    st.stop()

run_date, df, catalyst_records = result
by_ticker = catalysts_by_ticker(catalyst_records)

# --- Stock selector ---
df_sorted = df.sort_values("name")
labels = [f"{'📌 ' if r.get('is_held') else ''}{r['name']} ({r['ticker']})" for _, r in df_sorted.iterrows()]
label_to_ticker = {label: r["ticker"] for label, (_, r) in zip(labels, df_sorted.iterrows())}
ticker_to_label = {v: k for k, v in label_to_ticker.items()}

default_ticker = st.session_state.get("selected_ticker")
default_label = ticker_to_label.get(default_ticker)
default_index = labels.index(default_label) if default_label in labels else 0

selected_label = st.selectbox("Stock", labels, index=default_index)
ticker = label_to_ticker[selected_label]
st.session_state["selected_ticker"] = ticker

row = df[df["ticker"] == ticker].iloc[0]

# --- Header ---
held_badge = "📌 " if row.get("is_held") else ""
st.title(f"{held_badge}{row['name']} ({row['ticker']})")
header_bits = [
    INDEX_DISPLAY_NAMES.get(row.get("index"), row.get("index")),
    row.get("sector") if pd.notna(row.get("sector")) else None,
    row.get("sector_state_label") if pd.notna(row.get("sector_state_label")) else None,
    row.get("relative_value_label") if pd.notna(row.get("relative_value_label")) else None,
    GROUP_LABELS.get(row.get("group"), "Ungrouped this run"),
    f"Run {run_date}",
]
st.caption(" · ".join(b for b in header_bits if b))
if "sector" not in df.columns:
    st.caption("Sector / Relative Value not available yet - run the pipeline to pick them up.")

m1, m2, m3, m4, m5 = st.columns(5)
momentum_delta = row.get("momentum_score_delta")
support_delta = row.get("support_score_delta")
m1.metric("Overall", row.get("overall_score"))
m2.metric("Momentum", row.get("momentum_score"), momentum_delta if pd.notna(momentum_delta) else None)
m3.metric("Support", row.get("support_score"), support_delta if pd.notna(support_delta) else None)
m4.metric("Catalyst", row.get("catalyst_score", 0))
m5.metric("Risk", f"{row.get('risk_score', 0)} {row.get('risk_band_label', '')}")

compared_to = row.get("compared_to_run_date")
if (pd.notna(momentum_delta) or pd.notna(support_delta)) and pd.notna(compared_to):
    st.caption(f"Momentum/Support deltas are vs the {compared_to} run.")

peg_ratio = row.get("peg_ratio")
if pd.notna(peg_ratio):
    st.caption(
        f"PEG {peg_ratio} (P/E {row.get('pe_ratio')} ÷ {row.get('recent_profit_growth_pct')}% YoY profit growth) "
        f"vs. {row.get('sector')} sector peers → {row.get('relative_value_label', '—')}"
    )

st.page_link("pages/1_Methodology.py", label="ⓘ How every number above is calculated", icon="ℹ️")

st.divider()

# --- Price chart ---
st.subheader("Price & moving averages")


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_price_history(tkr: str) -> pd.DataFrame:
    hist = yf.download(tkr, period="500d", auto_adjust=True, progress=False)
    if isinstance(hist.columns, pd.MultiIndex):
        hist.columns = hist.columns.get_level_values(0)
    return hist


with st.spinner(f"Fetching price history for {ticker}..."):
    hist = fetch_price_history(ticker)

if hist.empty:
    st.warning("Could not fetch price history for this ticker.")
else:
    hist = hist.copy()
    hist["50dma"] = hist["Close"].rolling(50).mean()
    hist["150dma"] = hist["Close"].rolling(150).mean()
    hist["200dma"] = hist["Close"].rolling(200).mean()
    plot_df = hist.reset_index()[["Date", "Close", "50dma", "150dma", "200dma", "Volume"]].tail(250)

    price_long = plot_df.melt(
        id_vars="Date",
        value_vars=["Close", "50dma", "150dma", "200dma"],
        var_name="Series",
        value_name="Price",
    ).dropna(subset=["Price"])

    price_chart = (
        alt.Chart(price_long)
        .mark_line()
        .encode(
            x=alt.X("Date:T", title=None),
            y=alt.Y("Price:Q", scale=alt.Scale(zero=False)),
            color=alt.Color(
                "Series:N",
                scale=alt.Scale(
                    domain=["Close", "50dma", "150dma", "200dma"],
                    range=["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"],
                ),
            ),
        )
        .properties(height=320)
    )

    early_turn_flags = []
    if row.get("early_turn_cross_above_50dma"):
        early_turn_flags.append("crossed above 50-DMA on volume")
    if row.get("early_turn_rs_turning_up"):
        early_turn_flags.append("relative strength turning up")
    if row.get("early_turn_fresh_52w_high"):
        early_turn_flags.append("fresh 52-week high after consolidation")

    if early_turn_flags:
        marker_df = pd.DataFrame(
            {
                "Date": [plot_df["Date"].iloc[-1]],
                "Price": [plot_df["Close"].iloc[-1]],
                "Label": [", ".join(early_turn_flags)],
            }
        )
        marker_points = (
            alt.Chart(marker_df)
            .mark_point(shape="star", size=250, color="gold", filled=True)
            .encode(x="Date:T", y="Price:Q", tooltip=["Label:N"])
        )
        price_chart = price_chart + marker_points
        st.caption(
            f"⭐ marks today's early-turn flag(s): {', '.join(early_turn_flags)}. "
            "Only the current flag state is stored, not historical trigger dates."
        )

    st.altair_chart(price_chart, use_container_width=True)

    extension_pct = row.get("extension_pct")
    if pd.notna(extension_pct):
        st.caption(
            f"Extension above 50-DMA: **{extension_pct:.1%}** "
            "(feeds the Risk Score's technical-exhaustion trigger)."
        )

    volume_chart = (
        alt.Chart(plot_df)
        .mark_bar(color="#888888")
        .encode(x=alt.X("Date:T", title=None), y=alt.Y("Volume:Q"))
        .properties(height=100)
    )
    st.altair_chart(volume_chart, use_container_width=True)

st.divider()

# --- Score history sparkline ---
st.subheader("Score history")
hist_scores = score_history(ticker)
if len(hist_scores) < 2:
    st.caption(
        "Only one weekly run saved so far - this chart builds up as more runs accumulate."
    )
else:
    score_long = hist_scores.melt(
        id_vars="run_date",
        value_vars=["overall_score", "momentum_score", "support_score", "catalyst_score", "risk_score"],
        var_name="Score",
        value_name="Value",
    )
    sparkline = (
        alt.Chart(score_long)
        .mark_line(point=True)
        .encode(x=alt.X("run_date:T", title=None), y="Value:Q", color="Score:N")
        .properties(height=220)
    )
    st.altair_chart(sparkline, use_container_width=True)

st.divider()

# --- Quarterly revenue / profit ---
st.subheader("Quarterly revenue & profit")
fundamentals_cache = load_fundamentals_cache()
fundamentals_entry = fundamentals_cache.get(row["symbol"], {})
quarterly_sales = fundamentals_entry.get("quarterly_sales") or []
quarterly_profit = fundamentals_entry.get("quarterly_profit") or []

if not quarterly_sales and not quarterly_profit:
    st.caption("No fundamentals cached yet for this stock.")
else:
    sales = quarterly_sales[-8:]
    profit = quarterly_profit[-8:]
    n = max(len(sales), len(profit))
    sales = [None] * (n - len(sales)) + sales
    profit = [None] * (n - len(profit)) + profit
    quarter_labels = [f"Q-{n - 1 - i}" if i < n - 1 else "Latest" for i in range(n)]

    quarterly_df = pd.DataFrame({"Quarter": quarter_labels, "Sales": sales, "Net Profit": profit})
    quarterly_long = quarterly_df.melt(id_vars="Quarter", var_name="Metric", value_name="Amount").dropna(
        subset=["Amount"]
    )
    quarterly_chart = (
        alt.Chart(quarterly_long)
        .mark_bar()
        .encode(
            x=alt.X("Quarter:N", sort=quarter_labels, title=None),
            y=alt.Y("Amount:Q", title="₹ crore"),
            color="Metric:N",
            xOffset="Metric:N",
        )
        .properties(height=280)
    )
    st.altair_chart(quarterly_chart, use_container_width=True)
    st.caption(
        'Quarter labels are relative order (oldest → latest) - screener.in\'s exact period '
        'labels (e.g. "Mar 2026") aren\'t scraped yet.'
    )

st.divider()

# --- Catalysts with full formula breakdown ---
st.subheader("Catalysts")
ticker_catalysts = by_ticker.get(ticker, [])
if not ticker_catalysts:
    st.caption("No catalysts researched yet for this stock.")
else:
    for cat in ticker_catalysts:
        record = CatalystRecord(**cat)
        score = catalyst_score(record)
        type_weight = TYPE_WEIGHTS.get(record.catalyst_type, 0.5)
        confirmation_multiplier = CONFIRMATION_MULTIPLIERS.get(record.confirmation, 1.0)
        if record.order_value_pct_revenue is not None:
            size_score = min(1.0, record.order_value_pct_revenue / 0.25)
        else:
            size_score = 1.0
        days_since = (date.today() - date.fromisoformat(record.published_date)).days
        recency_decay = max(0.0, 1 - days_since / RECENCY_DECAY_DAYS)
        direction_badge = "🟢 positive" if record.direction == "positive" else "🔴 negative"

        with st.container(border=True):
            st.markdown(f"**[{record.headline}]({record.url})** &nbsp; {direction_badge}")
            st.caption(f"{record.catalyst_type} · {record.published_date} · {record.source}")
            breakdown_df = pd.DataFrame(
                [
                    {
                        "TypeWeight": type_weight,
                        "ConfirmationMultiplier": confirmation_multiplier,
                        "SizeScore": round(size_score, 2),
                        "RecencyDecay": round(recency_decay, 2),
                        "Score": score,
                    }
                ]
            )
            st.dataframe(breakdown_df, hide_index=True, use_container_width=True)
            if record.direction == "negative":
                st.caption(
                    "Negative direction - feeds the Risk Score's news trigger, not the Catalyst Score."
                )
            if record.note:
                st.caption(record.note)

st.divider()

# --- Risk Score trigger checklist ---
st.subheader("Risk Score trigger checklist")
raw_triggers = row.get("risk_active_triggers")
active_triggers = set(raw_triggers.split(",")) if isinstance(raw_triggers, str) and raw_triggers else set()

checklist_df = pd.DataFrame(
    [
        {
            "Trigger": name.replace("_", " ").title(),
            "Active": "✅" if name in active_triggers else "—",
            "Points": points if name in active_triggers else 0,
        }
        for name, points in TRIGGER_POINTS.items()
    ]
)
st.dataframe(checklist_df, hide_index=True, use_container_width=True)
st.caption(
    f"Risk Score = {row.get('risk_score', 0)} → {row.get('risk_band_label', '')}. "
    "Full trigger definitions are on the Methodology page."
)

st.divider()
if st.button("← Back to Overview"):
    st.switch_page("app.py")
