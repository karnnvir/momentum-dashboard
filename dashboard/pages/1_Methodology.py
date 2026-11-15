"""Methodology page - renders the scoring formulas LIVE from the same
constants the scoring code uses, so this page can never drift out of sync
with what's actually running. See the Design Spec doc for the full
rationale; this page is the dashboard-native mirror of it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from momentum.price_momentum import (  # noqa: E402
    MOMENTUM_WEIGHTS,
    MOMENTUM_STRUCTURE_BONUS,
    MOMENTUM_TREND_SLOPE_BONUS,
)
from momentum.fundamentals import (  # noqa: E402
    CASH_CONVERSION_THRESHOLD,
    CASH_CONVERSION_DISCOUNT_MULTIPLIER,
)
from momentum.catalysts import (  # noqa: E402
    TYPE_WEIGHTS,
    CONFIRMATION_MULTIPLIERS,
    RECENCY_DECAY_DAYS,
)
from momentum.risk import (  # noqa: E402
    TRIGGER_POINTS,
    EXTENSION_EXHAUSTION_THRESHOLD,
    CLIMAX_RETURN_THRESHOLD,
    PLEDGE_DANGER_PCT,
    DEBT_TO_EQUITY_DANGER,
)
from momentum.scoring import STRONG_THRESHOLD, FLAT_THRESHOLD, FORMULA_VERSION, FORMULA_VERSION_DATE  # noqa: E402

st.set_page_config(page_title="Methodology - Momentum Dashboard", layout="wide")

st.title("Methodology")
st.caption(
    f"Formula version **{FORMULA_VERSION}** (as of {FORMULA_VERSION_DATE}). "
    "Every table below is read directly from the scoring code's own constants - "
    "if a weight changes in code, this page changes with it."
)

st.header("1. Momentum Score (0-100)")
st.write("Weighted blend of percentile ranks across the universe:")
momentum_table = pd.DataFrame(
    [{"Input": k, "Weight": f"{v:.0%}"} for k, v in MOMENTUM_WEIGHTS.items()]
)
st.dataframe(momentum_table, hide_index=True, use_container_width=True)
st.caption(
    f"+ {MOMENTUM_STRUCTURE_BONUS} flat points for higher-highs/higher-lows structure, "
    f"+ {MOMENTUM_TREND_SLOPE_BONUS} flat points per rising moving average (50d, 200d)."
)

st.header("2. Extension %")
st.write(
    "% above the 50-day moving average. Reported separately from the Momentum Score on "
    "purpose - it feeds the Risk Score's technical-exhaustion trigger below instead."
)

st.header("3. Support Score (0-100)")
st.write(
    "Currently an **unweighted average** of whichever fundamental signals are available "
    "(profit growth & acceleration, turnaround, margin change, ROCE, D/E, promoter pledge). "
    "Flagged as a near-term refinement - see the Phasing doc's Later list."
)
st.markdown(
    f"**Earnings-quality discount:** if CFO / Net Profit stays below "
    f"**{CASH_CONVERSION_THRESHOLD}** for 2 consecutive years, the Support Score is "
    f"multiplied by **{CASH_CONVERSION_DISCOUNT_MULTIPLIER}**."
)

st.header("4. Catalyst Score (0-100)")
st.code("Catalyst Score = round(100 x TypeWeight x ConfirmationMultiplier x SizeScore x RecencyDecay)")
col1, col2 = st.columns(2)
with col1:
    st.markdown("**TypeWeight**")
    st.dataframe(
        pd.DataFrame([{"Catalyst type": k, "Weight": v} for k, v in TYPE_WEIGHTS.items()]),
        hide_index=True,
        use_container_width=True,
    )
with col2:
    st.markdown("**ConfirmationMultiplier**")
    st.dataframe(
        pd.DataFrame([{"Confirmation": k, "Multiplier": v} for k, v in CONFIRMATION_MULTIPLIERS.items()]),
        hide_index=True,
        use_container_width=True,
    )
st.caption(
    "SizeScore = min(1.0, order_value / (0.25 x trailing-12-month revenue)) - 1.0 for types "
    "with no clean size figure. "
    f"RecencyDecay = max(0, 1 - days_since_announcement / {RECENCY_DECAY_DAYS}). "
    "A stock's score is the MAX across its live catalysts, not a sum."
)

st.header("5. Risk Score (0-100) -> Risk Band")
st.dataframe(
    pd.DataFrame([{"Trigger family": k.replace("_", " ").title(), "Points when active": v} for k, v in TRIGGER_POINTS.items()]),
    hide_index=True,
    use_container_width=True,
)
st.markdown(
    f"""
- **Time decay**: active if the stock is in a momentum-flagged group with Catalyst Score = 0 (no live catalyst)
- **Technical exhaustion**: Extension % > {EXTENSION_EXHAUSTION_THRESHOLD:.0%}, or 1-month return > {CLIMAX_RETURN_THRESHOLD:.0%}
- **Valuation extremes**: *not implemented in v1* - no PEG/valuation data source wired up yet; always inactive rather than fabricated
- **Fundamental deterioration**: promoter pledge > {PLEDGE_DANGER_PCT:.0f}%, or D/E > {DEBT_TO_EQUITY_DANGER}, or the earnings-quality discount above fired
- **News trigger**: any catalyst recorded with `direction: negative` in its recency window

Risk Score = sum of active trigger points (capped at 100). Risk Band: 0-24 Clear, 25-59 Watch, 60-100 Reduce-Exit -
the number is always shown alongside the band, never the band alone.
"""
)

st.header("6. Group (classification)")
st.write(
    f"A deterministic function of Momentum Score x Support Score x the early-turn flag "
    f"(strong threshold ≥ {STRONG_THRESHOLD}, flat threshold ≤ {FLAT_THRESHOLD}). "
    "Not a separate number - a named region of the other two."
)
