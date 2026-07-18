"""Scores-based ratings and model — recovery, determinism, and a golden projection.

Fit on the frozen backtest season (whose scores come from known strengths), so
the ratings should recover the true ordering, and the projection is pinned.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from velocity.features.scores import fit_scores_ratings
from velocity.models.game_scores import ScoresGameModel, ScoresModelConfig
from velocity.models.simulate import SimConfig
from velocity.util.seed import make_rng

FIXTURES = Path(__file__).parent / "fixtures"
_SIM = SimConfig(n_sims=20_000)


@pytest.fixture
def season_games() -> pd.DataFrame:
    df = pd.read_csv(FIXTURES / "nfl_season_games.csv")
    df["kickoff"] = pd.to_datetime(df["kickoff"])
    return df


def test_ratings_recover_team_ordering(season_games: pd.DataFrame) -> None:
    r = fit_scores_ratings(season_games, ridge_lambda=6.0)
    net = {t: r.offense[t] - r.defense[t] for t in r.teams}
    assert max(net, key=net.get) in {"BUF", "KC"}  # true top offenses
    assert min(net, key=net.get) == "CAR"  # true worst team
    assert r.base_points > 0


def test_fit_is_deterministic(season_games: pd.DataFrame) -> None:
    a = fit_scores_ratings(season_games)
    b = fit_scores_ratings(season_games)
    assert a.offense == b.offense
    assert a.defense == b.defense
    assert a.home_edge == b.home_edge


def test_stronger_ridge_shrinks(season_games: pd.DataFrame) -> None:
    teams = fit_scores_ratings(season_games).teams
    spreads = []
    for lam in (2.0, 8.0, 40.0):
        r = fit_scores_ratings(season_games, ridge_lambda=lam)
        spreads.append(float(np.std([r.offense[t] for t in teams])))
    assert spreads[0] > spreads[1] > spreads[2]


def test_unseen_team_defaults_to_average(season_games: pd.DataFrame) -> None:
    r = fit_scores_ratings(season_games)
    # A team not in the fit projects at the league baseline, not a crash.
    assert r.expected_points("NOBODY", "CAR", at_home=False) == pytest.approx(
        r.base_points + r.defense["CAR"]
    )


def test_empty_games_raise(season_games: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="no played games"):
        fit_scores_ratings(season_games.assign(home_score=None, away_score=None))


def test_model_hfa_and_neutral(season_games: pd.DataFrame) -> None:
    model = ScoresGameModel(fit_scores_ratings(season_games), ScoresModelConfig(sim=_SIM))
    a = model.expected_points("KC", "CAR", neutral_site=True)
    b = model.expected_points("CAR", "KC", neutral_site=True)
    assert (a[0] - a[1]) == pytest.approx(-(b[0] - b[1]))  # antisymmetric at neutral


def test_golden_projection(season_games: pd.DataFrame) -> None:
    model = ScoresGameModel(
        fit_scores_ratings(season_games, ridge_lambda=6.0), ScoresModelConfig(sim=_SIM)
    )
    proj = model.project("KC", "CAR", rng=make_rng())
    assert proj.mu_home == pytest.approx(33.442821, abs=1e-4)
    assert proj.mu_away == pytest.approx(12.963925, abs=1e-4)
    assert proj.p_home_win() == pytest.approx(0.942375, abs=1e-6)
    assert proj.fair_spread() == pytest.approx(-20.0, abs=1e-9)
