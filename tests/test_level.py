"""The scoring level, fitted through the model instead of assumed."""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.features.team import TeamRatings
from velocity.models.game_nfl import NFLGameModel, NFLModelConfig
from velocity.models.level import calibrate_level, level_shift, mean_points_per_team


def _model(offset: float = 0.0) -> NFLGameModel:
    # Two teams whose deviations do not cancel: the fit "runs hot" by
    # ``offset`` EPA/play on every matchup, the QB-starter effect in
    # miniature.
    ratings = TeamRatings(
        offense={"A": 0.02 + offset, "B": -0.02 + offset},
        defense={"A": 0.0, "B": 0.0}, league_epa=0.0, ridge_lambda=1.0,
        n_plays=100, teams=("A", "B"),
    )
    return NFLGameModel(ratings, NFLModelConfig(base_points=22.5, plays_per_game=63.0))


def _games() -> pd.DataFrame:
    return pd.DataFrame([
        {"season": 2024, "home_team": "A", "away_team": "B", "home_score": 24,
         "away_score": 20, "neutral_site": False},
        {"season": 2025, "home_team": "B", "away_team": "A", "home_score": 17,
         "away_score": 27, "neutral_site": False},
        {"season": 2025, "home_team": "A", "away_team": "B", "home_score": 21,
         "away_score": 17, "neutral_site": True},
        {"season": 2025, "home_team": "A", "away_team": "B", "home_score": None,
         "away_score": None, "neutral_site": False},
    ])


def test_mean_points_per_team_reads_the_trailing_window() -> None:
    games = _games()
    # 2025 only: (44 + 38) / 2 games / 2 teams
    assert mean_points_per_team(games, 1) == pytest.approx(20.5)
    assert mean_points_per_team(games, 2) == pytest.approx((44 + 44 + 38) / 3 / 2)
    assert mean_points_per_team(pd.DataFrame(), fallback=28.5) == 28.5


def test_level_shift_is_half_the_total_gap_and_calibration_removes_it() -> None:
    hot = _model(offset=0.02)  # +0.02 EPA/play × 63 plays = +1.26 pts a team
    games = _games()
    # Every deviation nets to zero across the two teams, so the model's mean
    # total is 45 + 2 × 1.26 = 47.52 against an actual mean of 42.
    shift = level_shift(hot, games)
    assert shift == pytest.approx((47.52 - 42.0) / 2.0)
    levelled = calibrate_level(hot, games)
    assert level_shift(levelled, games) == pytest.approx(0.0, abs=1e-9)
    # Margins are untouched: the shift is the same for both teams.
    h0, a0 = hot.expected_points("A", "B")
    h1, a1 = levelled.expected_points("A", "B")
    assert h0 - a0 == pytest.approx(h1 - a1)
    assert h1 + a1 == pytest.approx(h0 + a0 - 2 * shift)


def test_level_shift_honours_the_window_and_the_empty_case() -> None:
    model = _model()
    games = _games()
    assert level_shift(model, games, seasons=1) != level_shift(model, games)
    assert level_shift(model, games.iloc[:0]) == 0.0
    assert calibrate_level(model, games.iloc[:0]) is model
