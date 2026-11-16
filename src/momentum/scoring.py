"""Combine Momentum score + Support score into the four dashboard groups."""

from __future__ import annotations

import pandas as pd

# Design Spec changelog pointer - bump this whenever a scoring formula
# changes anywhere in the momentum package, and add a row to the Phasing
# doc's "Scoring methodology changelog" table.
FORMULA_VERSION = "v2.0"
FORMULA_VERSION_DATE = "2026-11-15"

STRONG_THRESHOLD = 60  # score >= this counts as "strong" on that axis
FLAT_THRESHOLD = 40  # momentum <= this counts as "flat" for the "not moving yet" group

EARLY_SIGNS = "early_signs"
SUPPORTED_TREND = "supported_trend"
HYPE_RISK = "hype_risk"
NOT_MOVING_YET = "not_moving_yet"

GROUP_LABELS = {
    EARLY_SIGNS: "🟢 Early signs",
    SUPPORTED_TREND: "🔵 Supported trend",
    HYPE_RISK: "🟠 Hype risk",
    NOT_MOVING_YET: "⚪ Not moving yet",
}


def classify(momentum_score: float | None, support_score: float | None, early_turn: bool) -> str | None:
    if momentum_score is None or support_score is None:
        return None

    strong_momentum = momentum_score >= STRONG_THRESHOLD
    strong_support = support_score >= STRONG_THRESHOLD
    flat_momentum = momentum_score <= FLAT_THRESHOLD

    if early_turn and strong_support:
        return EARLY_SIGNS
    if strong_momentum and strong_support:
        return SUPPORTED_TREND
    if strong_momentum and not strong_support:
        return HYPE_RISK
    if strong_support and flat_momentum:
        return NOT_MOVING_YET
    return None  # doesn't meet any group's bar - stays in the full table, ungrouped


def classify_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["group"] = df.apply(
        lambda row: classify(
            row.get("momentum_score"), row.get("support_score"), bool(row.get("early_turn_flag"))
        ),
        axis=1,
    )
    df["group_label"] = df["group"].map(GROUP_LABELS)
    return df


# Overall Score: a single 0-100 ranking number blending all three layers.
# Weights sum to 1.0 so a stock with a confirmed catalyst can reach 100, while
# a stock with no catalyst recorded yet (Catalyst Score 0 - which means "none
# researched/live", not "a catalyst was found and it's bad") simply caps out
# at the momentum+support weight sum (85) rather than being penalized further
# for the absence of research. This is additive confirmation, not a 3-way
# average - consistent with how a negative catalyst is handled (it raises the
# Risk Score's news_trigger instead of pulling this number down).
OVERALL_SCORE_WEIGHTS = {
    "momentum_score": 0.50,
    "support_score": 0.35,
    "catalyst_score": 0.15,
}


def overall_score(
    momentum_score: float | None, support_score: float | None, catalyst_score: float | None
) -> float | None:
    if momentum_score is None or support_score is None:
        return None
    score = (
        momentum_score * OVERALL_SCORE_WEIGHTS["momentum_score"]
        + support_score * OVERALL_SCORE_WEIGHTS["support_score"]
        + (catalyst_score or 0) * OVERALL_SCORE_WEIGHTS["catalyst_score"]
    )
    return round(min(max(score, 0), 100), 1)


def add_overall_score(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["overall_score"] = df.apply(
        lambda row: overall_score(
            row.get("momentum_score"), row.get("support_score"), row.get("catalyst_score")
        ),
        axis=1,
    )
    return df
