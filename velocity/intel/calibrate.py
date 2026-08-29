"""Publish-gate calibration — fit the conviction/context floors to real boards.

The gate's floors (`DEFAULT_MIN_CONVICTION`, `DEFAULT_MIN_CONTEXT`) were
reasoned, not fitted (docs/PUBLISH_GATE.md §3): the audit frame banks
``conviction`` and ``context`` for every candidate precisely so the
thresholds can be set from a few weeks of live boards instead of argued
about. This module re-applies the gate's threshold-dependent rules to those
banked frames across a grid of candidate floors and reports the volume each
pair would have produced — plays per night, empty-night share — so moving a
constant is a decision made on data.

It calibrates VOLUME only. The audit frames carry no outcomes, so nothing
here claims a win rate; joining graded results stays with the CLV grader.

Pure functions of frames; offline-testable, no network.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from velocity.intel.publish import (
    DEFAULT_MAX_ADVERSE_DRIFT,
    DEFAULT_MAX_EDGE,
    DEFAULT_MAX_PLAYS,
    DEFAULT_MIN_EDGE,
    PUBLISHABLE_TIERS,
)

# The default grids bracket the current constants (0.72 / 0.05) on both sides,
# down to "tier A alone" — the configuration §3 measured at 30 of 121.
DEFAULT_CONVICTION_GRID = (0.65, 0.68, 0.70, 0.72, 0.75, 0.80)
DEFAULT_CONTEXT_GRID = (0.00, 0.05, 0.10, 0.15, 0.20)


def load_audits(folder: Path | str) -> pd.DataFrame:
    """Every banked ``publish_*.parquet`` audit frame under ``folder``, stacked.

    Adds a ``night`` column (the calendar date of ``generated_at``) — the unit
    the gate's volume guardrail thinks in. Multiple runs on one night stack;
    that is the honest read of what the board offered that day.
    """
    frames = []
    for path in sorted(Path(folder).glob("publish_*.parquet")):
        frame = pd.read_parquet(path)
        frame["source_file"] = path.name
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    audit = pd.concat(frames, ignore_index=True)
    audit["night"] = pd.to_datetime(audit["generated_at"]).dt.date
    return audit


def _fixed_rules_pass(audit: pd.DataFrame) -> pd.Series:
    """The gate's threshold-independent rules, re-applied to banked numbers.

    Tier must be publishable (vetoes carry tier "X" and fail here), the edge
    must sit inside the band, and the market must not have drifted against us
    — exactly ``gate_bet``'s order, minus the two floors being calibrated.
    An unreadable edge fails (the gate rejects those); an absent drift passes
    (an unknown drift never rejects a play on its own).
    """
    tier_ok = audit["tier"].isin(PUBLISHABLE_TIERS)
    edge = pd.to_numeric(audit["edge"], errors="coerce")
    edge_ok = (edge >= DEFAULT_MIN_EDGE) & (edge <= DEFAULT_MAX_EDGE)
    drift = pd.to_numeric(audit["drift"], errors="coerce")
    drift_ok = drift.isna() | (drift <= DEFAULT_MAX_ADVERSE_DRIFT)
    return tier_ok & edge_ok & drift_ok


def sweep_floors(
    audit: pd.DataFrame,
    conviction_grid: Sequence[float] = DEFAULT_CONVICTION_GRID,
    context_grid: Sequence[float] = DEFAULT_CONTEXT_GRID,
    max_plays: int = DEFAULT_MAX_PLAYS,
) -> pd.DataFrame:
    """Plays-per-night volume at every (min_conviction, min_context) pair.

    One row per grid point: ``plays`` (total across all nights, after capping
    each night at ``max_plays`` by conviction), ``plays_per_night``,
    ``empty_nights`` (share of nights the gate returned nothing — "no picks
    is a pick" made measurable), and ``nights``.
    """
    if audit.empty:
        return pd.DataFrame(
            columns=["min_conviction", "min_context", "plays", "plays_per_night",
                     "empty_nights", "nights"]
        )
    eligible = audit[_fixed_rules_pass(audit)].copy()
    nights = int(audit["night"].nunique())
    rows = []
    for min_conviction in conviction_grid:
        for min_context in context_grid:
            passed = eligible[
                (eligible["conviction"] >= min_conviction)
                & (eligible["context"] >= min_context)
            ]
            if max_plays >= 0 and not passed.empty:
                passed = (
                    passed.sort_values("conviction", ascending=False)
                    .groupby("night", sort=False)
                    .head(max_plays)
                )
            n_nights_with = int(passed["night"].nunique()) if not passed.empty else 0
            rows.append({
                "min_conviction": min_conviction,
                "min_context": min_context,
                "plays": int(len(passed)),
                "plays_per_night": round(len(passed) / nights, 2) if nights else 0.0,
                "empty_nights": round(1.0 - n_nights_with / nights, 2) if nights else 1.0,
                "nights": nights,
            })
    return pd.DataFrame(rows)


def rejection_summary(audit: pd.DataFrame) -> pd.DataFrame:
    """Why candidates failed, counted — the shape of the boards the gate saw."""
    if audit.empty:
        return pd.DataFrame(columns=["reason", "n"])
    reasons = audit.loc[~audit["published"].astype(bool), "reason"].value_counts()
    return reasons.rename_axis("reason").reset_index(name="n")
