"""Sector view - Phase 2 (Design Spec: "V2 - with the sector/relative-value
work"). Every sector in this run with its Rotation State, computed from this
universe's own stocks (see momentum/sector.py) rather than an external
sectoral index.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from momentum.sector import sector_summary, MIN_SECTOR_SIZE  # noqa: E402
from data_loader import load_latest_run  # noqa: E402

st.set_page_config(page_title="Sectors - Momentum Dashboard", layout="wide")

st.title("Sectors")
st.caption(
    "Sector Rotation State = this universe's own stocks tagged to that sector, averaged, "
    "then ranked against every other sector - not an external sectoral index. "
    "See Methodology for the exact formula."
)

result = load_latest_run()
if result is None:
    st.warning("No run found yet.")
    st.stop()

run_date, df, _ = result

if "sector" not in df.columns:
    st.warning(
        "Sector data isn't in this run yet - re-run the pipeline after the sector-scraping "
        "update to pick it up."
    )
    st.stop()

summary = sector_summary(df)
st.caption(f"Run {run_date}. Sectors with fewer than {MIN_SECTOR_SIZE} universe stocks are left out.")

if summary.empty:
    st.info("No sector has enough universe stocks yet to classify.")
    st.stop()

display = summary.rename(
    columns={
        "sector": "Sector",
        "stocks": "Stocks",
        "sector_rs_percentile": "RS Percentile",
        "sector_state_label": "Rotation State",
    }
)[["Sector", "Stocks", "RS Percentile", "Rotation State"]]

st.dataframe(display, hide_index=True, use_container_width=True)

st.divider()
st.subheader("Stocks by sector")
selected_sector = st.selectbox("Sector", summary["sector"].tolist())
sector_stocks = df[df["sector"] == selected_sector].sort_values("overall_score", ascending=False)
cols = ["ticker", "name", "overall_score", "momentum_score", "support_score", "relative_value_label", "group_label"]
st.dataframe(
    sector_stocks[[c for c in cols if c in sector_stocks.columns]],
    hide_index=True,
    use_container_width=True,
)
