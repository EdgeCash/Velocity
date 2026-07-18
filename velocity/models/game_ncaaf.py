"""NCAAF game model — prior-anchored, pace-aware team ratings → distributions.

Structurally this mirrors the NFL model (opponent-adjusted efficiency × pace →
expected points → shared Monte Carlo), but three college-specific realities are
built in (DESIGN §4.3):

1. **Priors dominate early.** Each team's offense/defense ratings are shrunk
   toward a preseason prior (from :mod:`velocity.features.priors`) using a
   conjugate-normal update whose data weight grows with games played. A team
   with two games is mostly its prior; by midseason it is mostly its results.
   The prior is split symmetrically into an offensive and a defensive half so the
   totals stay coherent.
2. **Pace is team-specific.** Tempo varies enormously, so expected plays come
   from the matchup's two teams (:func:`velocity.features.team.matchup_pace`),
   not a league constant.
3. **Variance is larger.** Blowouts and heteroscedastic outcomes mean wider
   margin/total dispersion than the NFL, and home-field is bigger and more
   variable — both reflected in the defaults.

The connected opponent-adjustment college football requires is already provided
by the ridge solve in :func:`velocity.features.team.fit_ratings`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from velocity.features.priors import shrink_to_prior
from velocity.features.team import TeamRatings, matchup_pace
from velocity.models.game_nfl import GameProjection
from velocity.models.simulate import SimConfig, simulate_game
from velocity.util.seed import make_rng

DEFAULT_NCAAF_BASE_POINTS = 27.0
DEFAULT_NCAAF_HFA_POINTS = 3.0
DEFAULT_NCAAF_LEAGUE_PACE = 70.0


@dataclass(frozen=True)
class NCAAFModelConfig:
    """Calibration constants for the college model (wider than the NFL's)."""

    base_points: float = DEFAULT_NCAAF_BASE_POINTS
    hfa_points: float = DEFAULT_NCAAF_HFA_POINTS
    league_pace: float = DEFAULT_NCAAF_LEAGUE_PACE
    prior_strength: float = 6.0
    sim: SimConfig = field(
        default_factory=lambda: SimConfig(sd_margin=16.0, sd_total=12.0)
    )


class NCAAFGameModel:
    """Projects NCAAF games from ridge ratings, pace, and preseason priors."""

    def __init__(
        self,
        ratings: TeamRatings,
        pace: dict[str, float],
        config: NCAAFModelConfig | None = None,
        *,
        priors: pd.Series | None = None,
        games_played: pd.Series | None = None,
    ) -> None:
        self.ratings = ratings
        self.pace = pace
        self.config = config or NCAAFModelConfig()
        self._offense: dict[str, float] = {}
        self._defense: dict[str, float] = {}
        for team in ratings.teams:
            off = ratings.offense[team]
            deff = ratings.defense[team]
            if priors is not None and games_played is not None:
                prior = float(priors.get(team, 0.0))
                n = float(games_played.get(team, 0.0))
                strength = self.config.prior_strength
                # Split the net prior symmetrically into offense/defense halves.
                off = shrink_to_prior(prior / 2.0, off, n, prior_strength=strength)
                deff = shrink_to_prior(-prior / 2.0, deff, n, prior_strength=strength)
            self._offense[team] = off
            self._defense[team] = deff

    def expected_points(
        self, home_team: str, away_team: str, *, neutral_site: bool = False
    ) -> tuple[float, float]:
        """Expected points for (home, away), pace-scaled and prior-anchored."""
        cfg = self.config
        pace = matchup_pace(self.pace, home_team, away_team, cfg.league_pace)
        oh = self._offense.get(home_team, 0.0)
        oa = self._offense.get(away_team, 0.0)
        dh = self._defense.get(home_team, 0.0)
        da = self._defense.get(away_team, 0.0)

        mu_home = cfg.base_points + pace * (oh + da)
        mu_away = cfg.base_points + pace * (oa + dh)
        if not neutral_site:
            mu_home += cfg.hfa_points / 2.0
            mu_away -= cfg.hfa_points / 2.0
        return mu_home, mu_away

    def project(
        self,
        home_team: str,
        away_team: str,
        *,
        neutral_site: bool = False,
        rng: np.random.Generator | None = None,
    ) -> GameProjection:
        """Simulate the matchup and return a priced :class:`GameProjection`."""
        rng = rng if rng is not None else make_rng()
        mu_home, mu_away = self.expected_points(
            home_team, away_team, neutral_site=neutral_site
        )
        sim = simulate_game(
            mu_margin=mu_home - mu_away,
            mu_total=mu_home + mu_away,
            rng=rng,
            config=self.config.sim,
        )
        return GameProjection(
            home_team=home_team,
            away_team=away_team,
            mu_home=mu_home,
            mu_away=mu_away,
            sim=sim,
        )
