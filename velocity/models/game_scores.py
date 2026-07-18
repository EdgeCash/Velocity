"""Scores-based game model — projections from points ratings + the shared sim.

A thin model over :class:`velocity.features.scores.ScoresRatings`: expected points
come straight from the opponent-adjusted offense/defense ratings (already in
points), home-field advantage is the learned ``home_edge``, and the shared Monte
Carlo turns the two expected scores into a full distribution and every priced
market. It exists so the pipeline can run on schedule-only data when play-by-play
is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from velocity.features.scores import ScoresRatings
from velocity.models.game_nfl import GameProjection
from velocity.models.simulate import SimConfig, simulate_game
from velocity.util.seed import make_rng


@dataclass(frozen=True)
class ScoresModelConfig:
    """Simulation config for the scores-based model (points ratings carry the mean)."""

    sim: SimConfig = field(default_factory=SimConfig)


class ScoresGameModel:
    """Projects games from opponent-adjusted points ratings."""

    def __init__(self, ratings: ScoresRatings, config: ScoresModelConfig | None = None) -> None:
        self.ratings = ratings
        self.config = config or ScoresModelConfig()

    def expected_points(
        self, home_team: str, away_team: str, *, neutral_site: bool = False
    ) -> tuple[float, float]:
        """Expected (home, away) points, with home-field advantage unless neutral."""
        mu_home = self.ratings.expected_points(home_team, away_team, at_home=not neutral_site)
        mu_away = self.ratings.expected_points(away_team, home_team, at_home=False)
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
        mu_home, mu_away = self.expected_points(home_team, away_team, neutral_site=neutral_site)
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
