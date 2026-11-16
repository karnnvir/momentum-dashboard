"""Phase 2 (Design Spec v2): Sector Rotation State and Relative Value Flag.

Both are calculated, reproducible numbers/classifications, not judgment
calls - same principle as every other score in this project:

  - Sector Rotation State reuses the SAME relative-strength input already
    computed per stock for the Momentum Score (rs_vs_nifty_3m), just
    averaged per sector instead of per stock. This deliberately avoids
    introducing a new price data source (an official NSE sectoral index) -
    "the sector" here means "this universe's own stocks tagged to that
    sector," which is exactly what's already fetched and trusted.

  - Relative Value Flag compares a stock's PEG ratio to the current PEG
    distribution of its own sector peers within the universe - a relative
    percentile comparison, not an absolute "cheap/expensive" judgment.

A sector needs a minimum number of universe stocks before either of these
means anything; below that, both are left unclassified (None) rather than
computed from too small a sample.
"""

from __future__ import annotations

import pandas as pd

MIN_SECTOR_SIZE = 3  # fewer universe stocks than this and a sector average isn't meaningful

# --- Sector Rotation State ---

SECTOR_LEADING = "leading"
SECTOR_IMPROVING = "improving"
SECTOR_WEAKENING = "weakening"
SECTOR_LAGGING = "lagging"

SECTOR_STATE_LABELS = {
    SECTOR_LEADING: "⬆️ Leading",
    SECTOR_IMPROVING: "↗️ Improving",
    SECTOR_WEAKENING: "↘️ Weakening",
    SECTOR_LAGGING: "⬇️ Lagging",
}

# A sector's average relative-strength-vs-Nifty, percentile-ranked against
# every other sector in this run. >= this percentile counts as "strong."
SECTOR_STRENGTH_PERCENTILE_THRESHOLD = 50

# Design Spec v2: Catalyst Score sector modifier.
CATALYST_SECTOR_MODIFIERS = {
    SECTOR_LEADING: 1.1,
    SECTOR_IMPROVING: 1.1,
    SECTOR_WEAKENING: 1.0,
    SECTOR_LAGGING: 0.9,
}

# --- Relative Value Flag ---

REL_VALUE_PREMIUM = "premium"
REL_VALUE_FAIR = "fair"
REL_VALUE_DISCOUNT = "discount"

REL_VALUE_LABELS = {
    REL_VALUE_PREMIUM: "▲ Premium",
    REL_VALUE_FAIR: "● Fair",
    REL_VALUE_DISCOUNT: "▼ Discount",
}

REL_VALUE_DISCOUNT_PERCENTILE = 33
REL_VALUE_PREMIUM_PERCENTILE = 67

# Below this YoY profit growth %, PEG isn't a meaningful ratio (flat/negative
# growth makes P/E-over-growth either explode or go negative) - left blank
# rather than computed into a misleading number.
PEG_MIN_GROWTH_PCT = 1.0


def _sector_counts(df: pd.DataFrame) -> pd.Series:
    return df.groupby("sector")["rs_vs_nifty_3m"].count()


def compute_sector_rotation(df: pd.DataFrame, prev_sector_rs: dict[str, float] | None = None) -> pd.DataFrame:
    """Adds sector_rs_percentile, sector_state, sector_state_label.

    Classification is the standard 2x2 rotation-quadrant read on (level,
    trend) - level from this run's cross-sector percentile, trend from the
    change versus the previous run's percentile for the same sector. With no
    previous run for a sector yet, trend is unknown and the state is set
    from level alone (Leading if strong, Lagging if not) rather than
    guessing a direction.
    """
    df = df.copy()
    counts = _sector_counts(df)
    sector_rs = df.groupby("sector")["rs_vs_nifty_3m"].mean()
    sector_rs_percentile = sector_rs.rank(pct=True) * 100

    def state_for(sector: str | None) -> str | None:
        if sector is None or pd.isna(sector) or counts.get(sector, 0) < MIN_SECTOR_SIZE:
            return None
        level = sector_rs_percentile.get(sector)
        if level is None or pd.isna(level):
            return None
        strong = level >= SECTOR_STRENGTH_PERCENTILE_THRESHOLD

        prev_level = (prev_sector_rs or {}).get(sector)
        if prev_level is None:
            return SECTOR_LEADING if strong else SECTOR_LAGGING

        rising = level >= prev_level
        if strong and rising:
            return SECTOR_LEADING
        if strong and not rising:
            return SECTOR_WEAKENING
        if not strong and rising:
            return SECTOR_IMPROVING
        return SECTOR_LAGGING

    df["sector_rs_percentile"] = df["sector"].map(sector_rs_percentile).round(1)
    df["sector_state"] = df["sector"].map(state_for)
    df["sector_state_label"] = df["sector_state"].map(SECTOR_STATE_LABELS)
    return df


def sector_summary(df: pd.DataFrame) -> pd.DataFrame:
    """One row per sector - feeds the dashboard's Sector view."""
    counts = _sector_counts(df)
    summary = (
        df.dropna(subset=["sector"])
        .groupby("sector")
        .agg(
            stocks=("ticker", "count"),
            sector_rs_percentile=("sector_rs_percentile", "first"),
            sector_state=("sector_state", "first"),
            sector_state_label=("sector_state_label", "first"),
        )
        .reset_index()
    )
    summary = summary[summary["sector"].map(lambda s: counts.get(s, 0) >= MIN_SECTOR_SIZE)]
    return summary.sort_values("sector_rs_percentile", ascending=False)


def _peg(pe_ratio: float | None, recent_profit_growth_pct: float | None) -> float | None:
    if pe_ratio is None or pd.isna(pe_ratio):
        return None
    if recent_profit_growth_pct is None or pd.isna(recent_profit_growth_pct):
        return None
    if recent_profit_growth_pct <= PEG_MIN_GROWTH_PCT:
        return None
    return round(pe_ratio / recent_profit_growth_pct, 2)


def compute_relative_value(df: pd.DataFrame) -> pd.DataFrame:
    """Adds peg_ratio, relative_value_flag, relative_value_label."""
    df = df.copy()
    df["peg_ratio"] = df.apply(
        lambda r: _peg(r.get("pe_ratio"), r.get("recent_profit_growth_pct")), axis=1
    )

    def flag_for(row: pd.Series) -> str | None:
        sector = row.get("sector")
        own_peg = row.get("peg_ratio")
        if sector is None or pd.isna(sector) or own_peg is None or pd.isna(own_peg):
            return None
        peers = df.loc[(df["sector"] == sector) & df["peg_ratio"].notna(), "peg_ratio"]
        if len(peers) < MIN_SECTOR_SIZE:
            return None
        discount_cutoff = peers.quantile(REL_VALUE_DISCOUNT_PERCENTILE / 100)
        premium_cutoff = peers.quantile(REL_VALUE_PREMIUM_PERCENTILE / 100)
        if own_peg <= discount_cutoff:
            return REL_VALUE_DISCOUNT
        if own_peg >= premium_cutoff:
            return REL_VALUE_PREMIUM
        return REL_VALUE_FAIR

    df["relative_value_flag"] = df.apply(flag_for, axis=1)
    df["relative_value_label"] = df["relative_value_flag"].map(REL_VALUE_LABELS)
    return df
