"""NFL game model — ratings + pace + simulation → priced distributions.

This is the backbone projection. It composes two pieces built elsewhere:

* :func:`velocity.features.team.fit_ratings` — opponent-adjusted offense/defense
  EPA/play ratings.
* :func:`velocity.models.simulate.simulate_game` — the shared Monte Carlo.

and turns them into a full outcome distribution for any matchup. The scoring
model is deliberately simple and honest::

    expected_points(team)  =  base_points  +  plays_per_game × (off + opp_def)

where ``off``/``opp_def`` are the ridge-adjusted EPA/play *deviations* (the
league mean lives in ``base_points``, so the intercept is not double-counted).
Home-field advantage shifts the expected margin by ``hfa_points`` (split evenly
across the two teams so the total is unaffected), and is dropped at neutral
sites.

``base_points``, ``plays_per_game`` and ``hfa_points`` are calibration
constants. Sensible NFL priors are baked in as defaults; on real historical data
they are tuned so the model is calibrated against closing lines.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from velocity.features.team import TeamRatings
from velocity.models.simulate import GameSim, SimConfig, simulate_game
from velocity.util.seed import make_rng

# League priors for the NFL (points per team per game, offensive plays per game,
# and home-field advantage in points). Tuned on real data in the backtest phase.
DEFAULT_BASE_POINTS = 22.5
DEFAULT_PLAYS_PER_GAME = 63.0
DEFAULT_HFA_POINTS = 2.0


@dataclass(frozen=True)
class NFLModelConfig:
    """Scoring-model calibration constants and the simulation config."""

    base_points: float = DEFAULT_BASE_POINTS
    plays_per_game: float = DEFAULT_PLAYS_PER_GAME
    hfa_points: float = DEFAULT_HFA_POINTS
    sim: SimConfig = field(default_factory=SimConfig)


@dataclass(frozen=True)
class GameProjection:
    """A fully simulated, priced projection for a single matchup.

    Holds the expected points for each team and the underlying
    :class:`~velocity.models.simulate.GameSim`. The pricing helpers are thin
    passthroughs so callers price every market off the same samples.
    """

    home_team: str
    away_team: str
    mu_home: float
    mu_away: float
    sim: GameSim

    @property
    def mu_margin(self) -> float:
        """Expected home-minus-away points."""
        return self.mu_home - self.mu_away

    @property
    def mu_total(self) -> float:
        """Expected combined points."""
        return self.mu_home + self.mu_away

    def p_home_win(self) -> float:
        return self.sim.p_home_win()

    def p_away_win(self) -> float:
        return 1.0 - self.sim.p_home_win()

    def fair_spread(self) -> float:
        """Fair home spread (negative = home favored)."""
        return self.sim.fair_spread()

    def fair_total(self) -> float:
        return self.sim.fair_total()

    def prob_home_cover(self, home_spread: float) -> float:
        return self.sim.prob_home_cover(home_spread)

    def prob_over(self, total_point: float) -> float:
        return self.sim.prob_over(total_point)


class NFLGameModel:
    """Projects NFL games from fitted team ratings."""

    def __init__(self, ratings: TeamRatings, config: NFLModelConfig | None = None) -> None:
        self.ratings = ratings
        self.config = config or NFLModelConfig()

    def expected_points(
        self, home_team: str, away_team: str, *, neutral_site: bool = False
    ) -> tuple[float, float]:
        """Expected points for (home, away), before simulation.

        Combines each offense's adjusted efficiency against the opposing defense,
        scales by pace into points over the league baseline, and applies
        home-field advantage unless the game is at a neutral site.
        """
        cfg = self.config
        home_delta = self.ratings.matchup_delta(home_team, away_team)
        away_delta = self.ratings.matchup_delta(away_team, home_team)

        mu_home = cfg.base_points + cfg.plays_per_game * home_delta
        mu_away = cfg.base_points + cfg.plays_per_game * away_delta

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
        """Simulate the matchup and return a priced :class:`GameProjection`.

        Pass an explicit seeded ``rng`` for reproducibility across a slate; when
        omitted a default-seeded generator is used, so a bare call is still
        deterministic.
        """
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
