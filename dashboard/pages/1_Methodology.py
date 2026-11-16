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
    RISK_BAND_LABELS,
    RISK_BAND_WATCH,
    RISK_BAND_REDUCE_EXIT,
)
from momentum.sector import (  # noqa: E402
    MIN_SECTOR_SIZE,
    SECTOR_STRENGTH_PERCENTILE_THRESHOLD,
    CATALYST_SECTOR_MODIFIERS,
    REL_VALUE_DISCOUNT_PERCENTILE,
    REL_VALUE_PREMIUM_PERCENTILE,
    PEG_MIN_GROWTH_PCT,
)
from momentum.scoring import (  # noqa: E402
    STRONG_THRESHOLD,
    FLAT_THRESHOLD,
    FORMULA_VERSION,
    FORMULA_VERSION_DATE,
    OVERALL_SCORE_WEIGHTS,
    GROUP_LABELS,
    EARLY_SIGNS,
    SUPPORTED_TREND,
    HYPE_RISK,
    NOT_MOVING_YET,
)

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
st.markdown(
    "**v2 addition:** the result is then multiplied by a Sector Modifier (see Sector Rotation "
    "State below) before it's stored as the Catalyst Score shown elsewhere on the dashboard:"
)
st.dataframe(
    pd.DataFrame(
        [{"Sector Rotation State": k.replace("_", " ").title(), "Modifier": f"×{v}"} for k, v in CATALYST_SECTOR_MODIFIERS.items()]
    ),
    hide_index=True,
    use_container_width=True,
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
st.code(
    f"""{GROUP_LABELS[EARLY_SIGNS]}     = early-turn AND support >= {STRONG_THRESHOLD}
{GROUP_LABELS[SUPPORTED_TREND]} = momentum >= {STRONG_THRESHOLD} AND support >= {STRONG_THRESHOLD}
{GROUP_LABELS[HYPE_RISK]}      = momentum >= {STRONG_THRESHOLD} AND support < {STRONG_THRESHOLD}
{GROUP_LABELS[NOT_MOVING_YET]} = support >= {STRONG_THRESHOLD} AND momentum <= {FLAT_THRESHOLD}"""
)
st.caption(
    "A stock that clears none of these four bars - most commonly moderate momentum "
    f"(between {FLAT_THRESHOLD} and {STRONG_THRESHOLD}) paired with support below "
    f"{STRONG_THRESHOLD} - gets no group. That's by design: the four groups are a highlights "
    "reel of distinctive setups, not an exhaustive partition of the universe. An ungrouped "
    "stock still appears in the All stocks table, just without a card."
)

st.header("7. Overall Score (0-100)")
st.code(
    "Overall Score = Momentum Score x {:.0%} + Support Score x {:.0%} + Catalyst Score x {:.0%}".format(
        OVERALL_SCORE_WEIGHTS["momentum_score"],
        OVERALL_SCORE_WEIGHTS["support_score"],
        OVERALL_SCORE_WEIGHTS["catalyst_score"],
    )
)
st.dataframe(
    pd.DataFrame([{"Input": k, "Weight": f"{v:.0%}"} for k, v in OVERALL_SCORE_WEIGHTS.items()]),
    hide_index=True,
    use_container_width=True,
)
st.markdown(
    "A single ranking number across all three layers, used to sort the dashboard's tables. "
    "The weights sum to 100%, so a stock needs a confirmed catalyst to reach the top of the "
    "range - but a stock with **no catalyst recorded** (score 0, meaning none researched or "
    "live yet, not that one was found and discounted) simply caps at "
    f"**{(OVERALL_SCORE_WEIGHTS['momentum_score'] + OVERALL_SCORE_WEIGHTS['support_score']) * 100:.0f}**, "
    "rather than being dragged down further. Catalysts only add here; a *negative* catalyst "
    "is instead routed to the Risk Score's news trigger above, not subtracted from this number."
)

st.header("8. Sector Rotation State (v2, categorical)")
st.write(
    "Leading / Improving / Weakening / Lagging - the same relative-strength-vs-Nifty math as "
    "the Momentum Score's `rs_vs_nifty_3m` input, just averaged per sector instead of per "
    "stock, then percentile-ranked against every other sector in this run. "
    "**Deliberately uses this universe's own stocks as the sector, not an external sectoral "
    "index** - no new price data source, and every input is already trusted."
)
st.markdown(
    f"""
- A sector needs at least **{MIN_SECTOR_SIZE}** universe stocks before it's classified at all.
- **Level**: this sector's average relative strength, percentile-ranked - "strong" at or above
  the **{SECTOR_STRENGTH_PERCENTILE_THRESHOLD}th** percentile.
- **Trend**: whether that percentile rose or fell versus last run. With no prior run yet for a
  sector, trend is unknown and the state is set from level alone (Leading if strong, Lagging if
  not) rather than guessing a direction.
- ⬆️ **Leading** = strong & rising · ↘️ **Weakening** = strong & falling · ↗️ **Improving** =
  not yet strong & rising · ⬇️ **Lagging** = not strong & falling.
"""
)

st.header("9. Relative Value Flag (v2, categorical)")
st.write(
    "Premium / Fair / Discount - where a stock's PEG ratio sits versus its own sector peers' "
    "current PEG distribution in this universe. A relative comparison, not an absolute "
    "valuation judgment."
)
st.code("PEG = Stock P/E ÷ most recent YoY profit growth %")
st.markdown(
    f"""
- Left **blank** (not computed) when YoY profit growth is at or below **{PEG_MIN_GROWTH_PCT}%**
  - flat or negative growth makes PEG explode or go negative, which isn't a meaningful ratio.
- A sector needs at least **{MIN_SECTOR_SIZE}** stocks with a valid PEG before any flag is set.
- **Discount**: PEG at or below the sector's **{REL_VALUE_DISCOUNT_PERCENTILE}th** percentile ·
  **Premium**: at or above the **{REL_VALUE_PREMIUM_PERCENTILE}th** percentile · **Fair**: in between.
"""
)

st.header("10. How to read a flagged stock")
st.warning(
    "This is a screening checklist, not a buy signal. Nothing below is investment advice - "
    "it's a reading order for the numbers already on this dashboard, so a decision uses all "
    "of them deliberately instead of anchoring on whichever one is biggest."
)
st.markdown(
    f"""
**1. Start with the Group - it tells you what kind of story you're looking at.**
- {GROUP_LABELS[SUPPORTED_TREND]} - price and fundamentals confirm each other. The strongest
  confirmation this system can give.
- {GROUP_LABELS[EARLY_SIGNS]} - fundamentals already strong, price just turning. Earlier entry,
  less confirmation.
- {GROUP_LABELS[HYPE_RISK]} - price moving *without* fundamental support. Needs an explanation,
  not a buy by default.
- {GROUP_LABELS[NOT_MOVING_YET]} - fundamentals good, price hasn't reacted. A watchlist candidate,
  not a momentum signal, by definition.

**2. For {GROUP_LABELS[HYPE_RISK]} especially, check the Catalyst.**
Catalyst Score 0 means the move is unexplained by anything in this system - pure price action.
A real catalyst (order win, approval, turnaround) at least tells you *why* it's moving, which
is not the same as telling you it's safe.

**3. Let the Risk Band act as a veto, checked last - not folded into the ranking.**
A high Overall Score next to {RISK_BAND_LABELS[RISK_BAND_WATCH]} or
{RISK_BAND_LABELS[RISK_BAND_REDUCE_EXIT]} is a contradiction worth resolving, not averaging
away - see why Overall Score excludes Risk Score above.

**4. Check the trend, not just the snapshot.**
`momentum_score_delta` / `support_score_delta` on the dashboard show whether a stock is
improving or fading week over week. A score that's rising into {STRONG_THRESHOLD} is a
different situation from one that's falling out of it.

**5. Open the Support Score's composition before trusting it.**
A score built on real profit growth and cash conversion is different from one propped up
mainly by low debt - see section 3 above for what feeds it.

**What this checklist deliberately leaves out:** valuation versus the sector, position sizing,
stop-loss levels, and comparison against other stocks you could buy instead. None of that is
wired into this system yet - it stays a manual judgment call.
"""
)
