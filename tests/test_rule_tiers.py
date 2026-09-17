"""Rule tiers (velocity.wagering.tiers): the curated list's ranking, from the lab.

The table is the wager lab's output pinned in code; the last test recomputes
it from the committed projections so the numbers cannot drift from the
evidence without this file saying so.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from velocity.backtest.wagers import WagerRule, grade_frame, score_rule
from velocity.wagering.tiers import RULE_TIERS, RuleTier, tier_for, tier_rank

REPO = Path(__file__).parent.parent


def test_tier_for_picks_the_highest_tier_whose_rule_admits_the_play() -> None:
    # NFL: an under 4+ is tier A, an over 4+ tier B, anything under 4 or on
    # another market nothing.
    assert tier_for("nfl", "total", "under", 5.0).tier == "A"
    assert tier_for("nfl", "total", "over", 4.0).tier == "B"
    assert tier_for("nfl", "total", "over", 3.9) is None
    assert tier_for("nfl", "spread", "home", 12.0) is None
    assert tier_for("nfl", "total", "under", None) is None
    # College: unders only — 8+ is tier A, 4–8 tier B, overs nothing.
    assert tier_for("ncaaf", "total", "under", 9.0).tier == "A"
    assert tier_for("ncaaf", "total", "under", 5.0).tier == "B"
    assert tier_for("ncaaf", "total", "over", 9.0) is None
    assert tier_for("mlb", "total", "under", 9.0) is None
    assert tier_rank(tier_for("nfl", "total", "under", 5.0)) < tier_rank(None)
    record = tier_for("ncaaf", "total", "under", 9.0).record
    assert record == "57.2% over 297 bets, 8 of 11 seasons"
    # A caller's own table.
    custom = {"nfl": (RuleTier("A", "spread", frozenset({"away"}), 6.0, 0.6, 50, 4, 5),)}
    assert tier_for("nfl", "spread", "away", 6.0, custom).tier == "A"
    assert tier_for("nfl", "total", "under", 6.0, custom) is None


@pytest.mark.parametrize("league", ["nfl", "ncaaf"])
def test_the_table_matches_the_committed_projections(league: str) -> None:
    """Each tier's record is what the lab computes today, within rounding."""
    projections = pd.read_parquet(REPO / "datasets" / league / "projections_promoted.parquet")
    games = pd.read_parquet(REPO / "datasets" / league / "games.parquet")
    frame = grade_frame(projections, games)
    for tier in RULE_TIERS[league]:
        side = next(iter(tier.sides))
        record = score_rule(frame, WagerRule(tier.market, side, min_points=tier.min_points))
        assert record.win_rate == pytest.approx(tier.win_rate, abs=0.006), tier
        assert abs(record.n - tier.n) <= 5, tier
        assert record.seasons_above_break_even == tier.seasons_cleared, tier
        assert record.seasons == tier.seasons, tier
