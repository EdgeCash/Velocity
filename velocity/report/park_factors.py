"""Committed MLB park-factor table (100 = neutral).

A park factor summarizes how a ballpark inflates or suppresses offense relative to
a league-average park: 100 is neutral, >100 favors hitters, <100 favors pitchers.
These move little year to year, so they live as a committed constant table rather
than a live fetch — keyed by the same three-letter team code the cards use.

Values are rounded multi-year composites (Statcast park factors, baseballsavant.mlb.com,
100 = league average). They are shown as descriptive context on the card; the model
does not consume them here. (The sim's ``features.baseball.apply_hr_park_factor``
could, using the ``hr`` column — a separate, deliberate wiring, out of scope here.)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from velocity.models.run_environment import game_run_environment


@dataclass(frozen=True)
class ParkFactor:
    """A home park's run + home-run factor (100 = neutral) and its name."""

    park: str
    runs: int
    hr: int

    @property
    def lean(self) -> str:
        """A one-word read of the park's offensive tilt, from the runs factor."""
        if self.runs >= 103:
            return "hitter"
        if self.runs <= 97:
            return "pitcher"
        return "neutral"


# Keyed by the card's team code (see MLB_TEAM_ALIASES). Composite Statcast factors.
PARK_FACTORS: dict[str, ParkFactor] = {
    "ARI": ParkFactor("Chase Field", 103, 102),
    "ATL": ParkFactor("Truist Park", 101, 102),
    "ATH": ParkFactor("Sutter Health Park", 100, 100),
    "BAL": ParkFactor("Camden Yards", 101, 104),
    "BOS": ParkFactor("Fenway Park", 108, 96),
    "CHC": ParkFactor("Wrigley Field", 101, 100),
    "CIN": ParkFactor("Great American Ball Park", 104, 116),
    "CLE": ParkFactor("Progressive Field", 98, 98),
    "COL": ParkFactor("Coors Field", 112, 110),
    "CWS": ParkFactor("Rate Field", 99, 102),
    "DET": ParkFactor("Comerica Park", 97, 92),
    "HOU": ParkFactor("Daikin Park", 100, 101),
    "KC": ParkFactor("Kauffman Stadium", 103, 92),
    "LAA": ParkFactor("Angel Stadium", 101, 103),
    "LAD": ParkFactor("Dodger Stadium", 99, 102),
    "MIA": ParkFactor("loanDepot park", 96, 94),
    "MIL": ParkFactor("American Family Field", 99, 102),
    "MIN": ParkFactor("Target Field", 100, 99),
    "NYM": ParkFactor("Citi Field", 98, 97),
    "NYY": ParkFactor("Yankee Stadium", 100, 110),
    "PHI": ParkFactor("Citizens Bank Park", 102, 108),
    "PIT": ParkFactor("PNC Park", 99, 92),
    "SD": ParkFactor("Petco Park", 96, 96),
    "SEA": ParkFactor("T-Mobile Park", 95, 96),
    "SF": ParkFactor("Oracle Park", 95, 90),
    "STL": ParkFactor("Busch Stadium", 99, 94),
    "TB": ParkFactor("Tropicana Field", 97, 95),
    "TEX": ParkFactor("Globe Life Field", 101, 101),
    "TOR": ParkFactor("Rogers Centre", 100, 103),
    "WSH": ParkFactor("Nationals Park", 100, 101),
}


def park_for(team_code: str | None) -> ParkFactor | None:
    """The home park's factor for a team code, or ``None`` if unknown."""
    return None if team_code is None else PARK_FACTORS.get(team_code)


def park_hr_factors() -> dict[str, float]:
    """The per-park HR factors as multipliers (100 → 1.0), keyed by team code.

    This is the model-facing view of the table: :func:`simulate_baseball.simulate_game`
    scales both lineups' home-run rate by the home park's factor, so a Coors game
    (110 → 1.10) projects more runs and an Oracle game (90 → 0.90) fewer.
    """
    return {code: pf.hr / 100.0 for code, pf in PARK_FACTORS.items()}


def run_environment_maps(
    weather: Mapping[str, tuple[float | None, bool]] | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """The two model inputs per home team: ``(hr_factor_map, run_env_tilt_map)``.

    Folds the committed park factors together with optional first-pitch weather via
    :func:`velocity.models.run_environment.game_run_environment` — so the HR map is
    park × temperature and the tilt map is the non-HR park run environment + a small
    temperature term. ``weather`` maps a team code to ``(temp_f, indoors)`` for the
    clubs playing at home today; a code without weather (or ``weather=None``) is
    park-only. Covers all 30 clubs so any home team resolves.
    """
    hr_factors: dict[str, float] = {}
    tilts: dict[str, float] = {}
    for code, pf in PARK_FACTORS.items():
        temp_f, indoors = (weather or {}).get(code, (None, False))
        env = game_run_environment(
            park_hr_index=pf.hr, park_runs_index=pf.runs, temp_f=temp_f, indoors=indoors
        )
        hr_factors[code], tilts[code] = env.hr_factor, env.tilt
    return hr_factors, tilts
