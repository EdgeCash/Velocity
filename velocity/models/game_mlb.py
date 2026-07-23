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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import numpy as np

from velocity.features.baseball import DEFAULT_BAT_PRIOR, DEFAULT_BIP_PRIOR, DEFAULT_PIT_PRIOR
from velocity.models.game_nfl import GameProjection
from velocity.models.simulate_baseball import (
    DEFAULT_HFA,
    DEFAULT_TTO_PENALTY,
    BaseballSimConfig,
    BaseballSimResult,
    Team,
    batter_from_rates,
    pitcher_from_rates,
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
    park_hr_factors: Mapping[str, float] = field(default_factory=dict)
    run_env_tilts: Mapping[str, float] = field(default_factory=dict)

    @property
    def known_teams(self) -> list[str]:
        return list(self.teams)

    def project(self, home_key: str, away_key: str) -> MLBProjection:
        rng = np.random.default_rng(self.seed)
        park = self.park_hr_factors.get(home_key, 1.0)
        tilt = self.run_env_tilts.get(home_key, 0.0)
        result = simulate_game(
            self.teams[home_key], self.teams[away_key], rng, self.config,
            park_hr_factor=park, run_env_tilt=tilt,
        )
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


def league_average_model(
    team_codes: Iterable[str],
    *,
    n_sims: int = 10_000,
    starter_outs: int = 18,
    seed: int = 0,
    park_hr_factors: Mapping[str, float] | None = None,
    run_env_tilts: Mapping[str, float] | None = None,
) -> MLBGameModel:
    """An :class:`MLBGameModel` where every club is a league-average team.

    A baseline so the live runner executes end-to-end today: it resolves any
    matchup and produces a valid slate. Every lineup is identical, so team edges
    are near zero — but ``park_hr_factors`` (the home park's HR multiplier by team
    code) still tilts each game's total by venue, so e.g. a Coors game prices over
    a neutral one. Replacing the identical lineups with real per-team rates from
    StatsAPI is the remaining data wiring; the orchestration is already proven.
    """
    teams: dict[str, Team] = {}
    for code in team_codes:
        lineup = [
            batter_from_rates(f"{code}{i}", DEFAULT_BAT_PRIOR, DEFAULT_BIP_PRIOR) for i in range(9)
        ]
        pitcher = pitcher_from_rates(f"{code}_p", DEFAULT_PIT_PRIOR)
        teams[code] = Team(lineup=lineup, pitcher=pitcher)
    config = BaseballSimConfig(
        n_sims=n_sims, starter_outs=starter_outs, hfa=DEFAULT_HFA,
        tto_penalty=DEFAULT_TTO_PENALTY,
    )
    return MLBGameModel(
        teams=teams, config=config, seed=seed,
        park_hr_factors=dict(park_hr_factors or {}),
        run_env_tilts=dict(run_env_tilts or {}),
    )
