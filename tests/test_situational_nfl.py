"""Two of nfelo's home-field findings, priced off the schedule columns.

The away team on a surface unlike its own runs a point worse; a Pacific-time
team at an early Eastern kickoff runs two points worse. Both are margin
effects split across the sides so the total is untouched, keyed like the
rest and divisional wrappers by (home, away, kickoff date).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.backtest.lab import (
    EARLY_KICKOFF_HOUR,
    PACIFIC_TEAMS,
    BodyClockModel,
    SurfaceMismatchModel,
    home_surface_by_team_season,
    surface_class,
)


class _Inner:
    """Records the bonuses it was handed, and whether it saw the kickoff."""

    def __init__(self, takes_kickoff: bool = True) -> None:
        self.calls: list[dict[str, object]] = []
        if takes_kickoff:
            self.project = self._with_kickoff  # type: ignore[method-assign]
        else:
            self.project = self._without_kickoff  # type: ignore[method-assign]

    def _with_kickoff(self, home: str, away: str, *, neutral_site: bool = False,
                      rng: object = None, kickoff: object = None,
                      home_bonus: float = 0.0, away_bonus: float = 0.0) -> dict[str, object]:
        call = {"home_bonus": home_bonus, "away_bonus": away_bonus, "kickoff": kickoff}
        self.calls.append(call)
        return call

    def _without_kickoff(self, home: str, away: str, *, neutral_site: bool = False,
                         rng: object = None, home_bonus: float = 0.0,
                         away_bonus: float = 0.0) -> dict[str, object]:
        call = {"home_bonus": home_bonus, "away_bonus": away_bonus, "kickoff": None}
        self.calls.append(call)
        return call


def _schedule() -> pd.DataFrame:
    """A season where GRS plays on grass at home, TRF on turf, SEA out west."""
    rows = [
        # Home games establish each team's own surface.
        ("GRS", "TRF", "2025-09-07", "grass ", "13:00"),
        ("GRS", "SEA", "2025-09-21", "grass", "16:25"),
        ("TRF", "GRS", "2025-09-14", "fieldturf", "13:00"),
        ("TRF", "SEA", "2025-09-28", "sportturf", "13:00"),   # SEA east, early, on turf
        ("SEA", "TRF", "2025-10-05", "fieldturf", "16:05"),   # SEA at home
        ("SEA", "GRS", "2025-10-12", "fieldturf", "13:00"),   # GRS is not Pacific
        ("GRS", "TRF", "2025-10-19", "", "12:30"),            # unknown surface, early
        ("NEU", "SEA", "2025-11-02", "grass", "13:00"),       # neutral-site game
    ]
    frame = pd.DataFrame(rows, columns=["home_team", "away_team", "kickoff", "surface", "gametime"])
    frame["season"] = 2025
    frame["kickoff"] = pd.to_datetime(frame["kickoff"])
    frame["neutral_site"] = frame["home_team"].eq("NEU")
    return frame


# --- the surface taxonomy --------------------------------------------------

def test_surface_class_reads_the_dirty_column_into_grass_or_turf() -> None:
    assert surface_class("grass") == "grass"
    assert surface_class("grass ") == "grass", "the trailing-space variant on 93 rows"
    for brand in ("fieldturf", "sportturf", "matrixturf", "astroturf", "a_turf", "astroplay"):
        assert surface_class(brand) == "turf", brand
    assert surface_class("") is None
    assert surface_class(None) is None
    assert surface_class(float("nan")) is None
    assert surface_class("dirt") is None, "unknown is unknown, not guessed"


def test_a_team_s_own_surface_is_the_mode_of_its_home_games_that_season() -> None:
    own = home_surface_by_team_season(_schedule())
    assert own[("GRS", 2025)] == "grass"
    assert own[("TRF", 2025)] == "turf"
    assert own[("SEA", 2025)] == "turf"
    # A resurfacing shows up the season it happens, not never.
    later = _schedule().assign(season=2026, surface="fieldturf")
    both = home_surface_by_team_season(pd.concat([_schedule(), later]))
    assert both[("GRS", 2025)] == "grass" and both[("GRS", 2026)] == "turf"


# --- the surface wrapper ----------------------------------------------------

def _project(model: object, home: str, away: str, when: str, **kw: object) -> dict[str, object]:
    return model.project(  # type: ignore[attr-defined]
        home, away, kickoff=pd.Timestamp(when), rng=np.random.default_rng(1), **kw)


def test_surface_mismatch_moves_the_margin_and_not_the_total() -> None:
    inner = _Inner()
    model = SurfaceMismatchModel(inner, _schedule(), points=1.0)
    # GRS (grass at home) visiting TRF's turf: a point off GRS, split.
    call = _project(model, "TRF", "GRS", "2025-09-14")
    assert call["home_bonus"] == pytest.approx(0.5)
    assert call["away_bonus"] == pytest.approx(-0.5)
    assert call["home_bonus"] + call["away_bonus"] == pytest.approx(0.0)  # type: ignore[operator]
    # SEA (turf at home) visiting TRF's turf: nothing.
    same = _project(model, "TRF", "SEA", "2025-09-28")
    assert same["home_bonus"] == 0.0 and same["away_bonus"] == 0.0
    # An unknown surface prices nothing rather than guessing.
    unknown = _project(model, "GRS", "TRF", "2025-10-19")
    assert unknown["home_bonus"] == 0.0 and unknown["away_bonus"] == 0.0
    # A neutral site has no home surface to be unlike.
    neutral = _project(model, "NEU", "SEA", "2025-11-02", neutral_site=True)
    assert neutral["home_bonus"] == 0.0 and neutral["away_bonus"] == 0.0
    # Existing bonuses are added to, not replaced.
    stacked = _project(model, "TRF", "GRS", "2025-09-14", home_bonus=2.0, away_bonus=-1.0)
    assert stacked["home_bonus"] == pytest.approx(2.5)
    assert stacked["away_bonus"] == pytest.approx(-1.5)


# --- the body-clock wrapper -------------------------------------------------

def test_body_clock_prices_a_pacific_team_at_an_early_eastern_kickoff() -> None:
    assert "SEA" in PACIFIC_TEAMS and "ARI" in PACIFIC_TEAMS and "DEN" not in PACIFIC_TEAMS
    inner = _Inner()
    model = BodyClockModel(inner, _schedule(), points=2.0)
    # SEA at TRF, 13:00: two points off SEA, split.
    early = _project(model, "TRF", "SEA", "2025-09-28")
    assert early["home_bonus"] == pytest.approx(1.0)
    assert early["away_bonus"] == pytest.approx(-1.0)
    # SEA at GRS, 16:25: a late window, nothing.
    late = _project(model, "GRS", "SEA", "2025-09-21")
    assert late["home_bonus"] == 0.0 and late["away_bonus"] == 0.0
    # GRS at SEA, 13:00: the away team is not Pacific, nothing.
    other_way = _project(model, "SEA", "GRS", "2025-10-12")
    assert other_way["home_bonus"] == 0.0 and other_way["away_bonus"] == 0.0
    # 12:30 is inside the early window too.
    assert EARLY_KICKOFF_HOUR == 13


def test_body_clock_is_nothing_without_a_kickoff_time_column() -> None:
    model = BodyClockModel(_Inner(), _schedule().drop(columns=["gametime"]), points=2.0)
    call = _project(model, "TRF", "SEA", "2025-09-28")
    assert call["home_bonus"] == 0.0 and call["away_bonus"] == 0.0


# --- the shared plumbing ----------------------------------------------------

def test_the_kickoff_is_forwarded_only_to_an_inner_that_takes_one() -> None:
    takes = SurfaceMismatchModel(_Inner(takes_kickoff=True), _schedule(), 1.0)
    assert _project(takes, "TRF", "GRS", "2025-09-14")["kickoff"] == pd.Timestamp("2025-09-14")
    lacks = SurfaceMismatchModel(_Inner(takes_kickoff=False), _schedule(), 1.0)
    assert _project(lacks, "TRF", "GRS", "2025-09-14")["kickoff"] is None
    # And a wrapper with no kickoff at all prices nothing, silently.
    bare = takes.project("TRF", "GRS", rng=np.random.default_rng(1))  # type: ignore[attr-defined]
    assert bare["home_bonus"] == 0.0
