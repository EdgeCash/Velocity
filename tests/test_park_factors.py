"""Committed park-factor table (velocity.report.park_factors)."""

from __future__ import annotations

from velocity.report.park_factors import PARK_FACTORS, park_for
from velocity.wagering.live import MLB_TEAM_ALIASES


def test_every_club_has_a_park() -> None:
    codes = set(MLB_TEAM_ALIASES.values())
    assert set(PARK_FACTORS) == codes  # all 30 clubs covered, no strays


def test_lookup_and_lean() -> None:
    coors = park_for("COL")
    assert coors is not None and coors.park == "Coors Field"
    assert coors.runs == 112 and coors.lean == "hitter"
    assert park_for("SF").lean == "pitcher"  # Oracle, 95
    assert park_for("MIN").lean == "neutral"  # Target, 100


def test_unknown_code_is_none() -> None:
    assert park_for(None) is None
    assert park_for("XXX") is None
