"""MLB game model — lineups + pitchers → priced distributions.

Composes the two MLB pieces built earlier — the shrunk per-PA rates
(:mod:`velocity.features.baseball`) carried on :class:`~velocity.models.simulate_baseball.Team`
objects, and the Monte Carlo (:func:`~velocity.models.simulate_baseball.simulate_game`) —
into the same shape the wagering stack already consumes.

The key move: the full-game result is wrapped in the *football*
:class:`~velocity.models.game_nfl.GameProjection`. That type is just "a
``GameSim`` plus pricing helpers," and a baseball ``GameSim`` satisfies it, so
:func:`velocity.wagering.slate.build_slate` prices the **run line** (spread),
**total** and **moneyline** off the baseball simulation with **no changes** — the
whole de-vig / edge / Kelly / CLV path is inherited. The first-5-innings market is
a second ``GameProjection`` over the innings-1–5 scores, priced by the identical
helpers; team totals read straight off the per-team score samples.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

from velocity.models.game_nfl import GameProjection
from velocity.models.simulate_baseball import (
    BaseballSimConfig,
    BaseballSimResult,
    Team,
    simulate_game,
)


@dataclass(frozen=True)
class MLBProjection:
    """A fully simulated MLB matchup.

    ``full`` and ``f5`` are ordinary :class:`GameProjection` objects (over the
    final and first-5-innings run distributions), so every wagering helper applies
    to each. ``result`` keeps the raw sim for player props (Phase M5).
    """

    full: GameProjection
    f5: GameProjection
    result: BaseballSimResult

    def p_home_win(self) -> float:
        return self.full.p_home_win()

    def prob_team_over(self, side: str, point: float) -> float:
        """P(a team's *full-game* runs exceed ``point``) — the team-total market."""
        scores = self.result.full.home_score if side == "home" else self.result.full.away_score
        return float(np.mean(scores > point))


@dataclass
class MLBGameModel:
    """Projects MLB games by simulating each matchup's lineups and pitchers.

    ``teams`` maps a rating key (e.g. ``"LAD"``) to its :class:`Team` (batting
    order + starter) for the slate being run. ``seed`` makes projection
    deterministic: each matchup draws from a fresh seeded generator, so a game's
    price does not depend on slate order.
    """

    teams: Mapping[str, Team]
    config: BaseballSimConfig = field(default_factory=BaseballSimConfig)
    seed: int = 0

    @property
    def known_teams(self) -> list[str]:
        return list(self.teams)

    def project(self, home_key: str, away_key: str) -> MLBProjection:
        rng = np.random.default_rng(self.seed)
        result = simulate_game(self.teams[home_key], self.teams[away_key], rng, self.config)
        full = GameProjection(
            home_team=home_key,
            away_team=away_key,
            mu_home=float(result.full.home_score.mean()),
            mu_away=float(result.full.away_score.mean()),
            sim=result.full,
        )
        f5 = GameProjection(
            home_team=home_key,
            away_team=away_key,
            mu_home=float(result.f5.home_score.mean()),
            mu_away=float(result.f5.away_score.mean()),
            sim=result.f5,
        )
        return MLBProjection(full=full, f5=f5, result=result)

    def project_full(self, home_key: str, away_key: str) -> GameProjection:
        """The full-game :class:`GameProjection` — the callable ``build_live_slate`` wants."""
        return self.project(home_key, away_key).full
