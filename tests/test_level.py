"""The scoring level, fitted through the model instead of assumed."""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.features.team import TeamRatings
from velocity.models.game_nfl import NFLGameModel, NFLModelConfig
from velocity.models.level import (
    calibrate_level,
    level_shift,
    mean_points_per_team,
    trailing_weeks,
)


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


def test_the_scores_half_levels_through_its_intercept() -> None:
    from velocity.features.scores import fit_scores_ratings
    from velocity.models.game_scores import ScoresGameModel, ScoresModelConfig
    from velocity.models.level import calibrate_scores_level

    rows = []
    # Two eras: a high-scoring 2023 and a low-scoring 2024–2025, so an
    # unweighted intercept sits above the trailing two seasons' level.
    for season, level in ((2023, 34), (2024, 24), (2025, 24)):
        for week in range(1, 7):
            for home, away in (("A", "B"), ("C", "D"), ("A", "C"), ("B", "D")):
                rows.append({"season": season, "week": week, "home_team": home,
                             "away_team": away, "home_score": level + 3,
                             "away_score": level - 3, "neutral_site": False})
    games = pd.DataFrame(rows)
    model = ScoresGameModel(fit_scores_ratings(games, ridge_lambda=1.0), ScoresModelConfig())
    before = level_shift(model, games, seasons=2)
    assert before > 2.0  # the intercept carries the 2023 era into 2025
    levelled = calibrate_scores_level(model, games, seasons=2)
    assert level_shift(levelled, games, seasons=2) == pytest.approx(0.0, abs=1e-9)
    # Ratings and the home edge are untouched; only the level moved.
    assert levelled.ratings.offense == model.ratings.offense
    assert levelled.ratings.home_edge == model.ratings.home_edge
    assert levelled.ratings.base_points == pytest.approx(model.ratings.base_points - before)


def _weekly_games() -> pd.DataFrame:
    """Two seasons of one game a week, scoring hotter each week.

    Week ``w`` of season ``s`` scores ``40 + 2·w`` (season 2025) or
    ``60 + 2·w`` (2026), so any window's mean total says exactly which
    weeks it held. Postseason is left out on purpose: the window counts
    on-field weeks, whatever their number.
    """
    rows = []
    for season, base in ((2025, 40), (2026, 60)):
        for week in range(1, 8):
            total = base + 2 * week
            rows.append({"season": season, "week": week, "home_team": "A",
                         "away_team": "B", "home_score": total - 20,
                         "away_score": 20, "neutral_site": False})
    # An unplayed week at the end must not count as a cell.
    rows.append({"season": 2026, "week": 8, "home_team": "A", "away_team": "B",
                 "home_score": None, "away_score": None, "neutral_site": False})
    return pd.DataFrame(rows)


def test_trailing_weeks_counts_on_field_weeks_and_crosses_the_season_boundary() -> None:
    games = _weekly_games()
    # Four weeks back from 2026 week 7 is 2026 weeks 4–7: all one season.
    four = trailing_weeks(games, 4)
    assert set(zip(four["season"], four["week"], strict=True)) == {
        (2026, 4), (2026, 5), (2026, 6), (2026, 7)}
    # Ten weeks back reaches into last season's finish — the boundary is
    # not a wall — and the unplayed week 8 is not a cell.
    ten = trailing_weeks(games, 10)
    assert (2025, 5) in set(zip(ten["season"], ten["week"], strict=True))
    assert (2025, 4) not in set(zip(ten["season"], ten["week"], strict=True))
    assert len(ten) == 10
    # More weeks than exist is every played game; zero or less is too.
    assert len(trailing_weeks(games, 99)) == 14
    assert len(trailing_weeks(games, 0)) == 14


def test_level_shift_on_trailing_weeks_follows_a_within_season_drift() -> None:
    """The point of the window: a level that can move inside a season."""
    model = _model()
    games = _weekly_games()
    two_seasons = level_shift(model, games, seasons=2)
    four_weeks = level_shift(model, games, weeks=4)
    # Recent weeks score hotter, so the model runs LESS high on them: the
    # four-week shift sits below the two-season one, by the amount the
    # scoring rose.
    assert four_weeks < two_seasons
    # ``weeks`` wins when both are given.
    assert level_shift(model, games, seasons=2, weeks=4) == four_weeks
    # And calibrating on the window lands the mean projected total on the
    # window's own mean actual total.
    levelled = calibrate_level(model, games, weeks=4)
    window = trailing_weeks(games, 4)
    projected = sum(sum(levelled.expected_points(h, a)) for h, a in
                    zip(window["home_team"], window["away_team"], strict=True))
    actual = float((window["home_score"] + window["away_score"]).sum())
    assert projected == pytest.approx(actual, abs=1e-6)


def test_a_within_season_window_stops_at_the_boundary_and_the_shrink_bridges_it() -> None:
    """The NFL level round's cost — December's scoring carried into September — and its fix."""
    model = _model()
    games = _weekly_games()
    # Within the season, ten weeks back is only the seven 2026 has played.
    inside = trailing_weeks(games, 10, within_season=True)
    assert set(inside["season"]) == {2026} and len(inside) == 7
    # "The season" is the latest one with a final score in it: with one 2026
    # week played, the window is that one week and nothing of 2025.
    week_two = pd.concat([games[games["season"] == 2025],
                          games[(games["season"] == 2026) & (games["week"] == 1)]])
    assert len(trailing_weeks(week_two, 8, within_season=True)) == 1
    assert len(trailing_weeks(week_two, 8)) == 8
    # One game is no level to trust: the shrink weighs it against the
    # season window by games, so here it is 1 part in (1 + k).
    recent_one = level_shift(model, week_two, weeks=8, within_season=True)
    prior_two = level_shift(model, week_two, seasons=2)
    assert level_shift(model, week_two, weeks=8, seasons=2, within_season=True,
                       shrink_games=9.0) == pytest.approx(0.1 * recent_one + 0.9 * prior_two)
    # The shrink is a games-weighted blend: four weeks of one game each
    # against a shrink of four is an even split of the two levels.
    recent = level_shift(model, games, weeks=4)
    prior = level_shift(model, games, seasons=2)
    blended = level_shift(model, games, weeks=4, seasons=2, shrink_games=4.0)
    assert blended == pytest.approx(0.5 * recent + 0.5 * prior)
    # No shrink is the bare window; a huge shrink is the season window.
    assert level_shift(model, games, weeks=4, seasons=2, shrink_games=0.0) == recent
    assert level_shift(model, games, weeks=4, seasons=2, shrink_games=1e9) == pytest.approx(
        prior, abs=1e-6)
    # And the calibrated model carries the blended shift.
    levelled = calibrate_level(model, games, weeks=4, seasons=2, shrink_games=4.0)
    assert levelled.config.base_points == pytest.approx(model.config.base_points - blended)
