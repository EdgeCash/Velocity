"""Walk-forward backtest — point-in-time correctness, reproducibility, and report.

Runs the real engine over the frozen synthetic season and asserts the properties
that make a backtest trustworthy: no lookahead, deterministic under seed, an
informative projection (beats the market baseline), and a coherent bankroll/CLV
report. It deliberately does *not* assert profitability — over a small sample the
honest signal is closing-line value, not short-run ROI.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from velocity.backtest.engine import BacktestConfig, walk_forward
from velocity.features.team import fit_ratings
from velocity.models.game_nfl import NFLGameModel, NFLModelConfig
from velocity.models.simulate import SimConfig
from velocity.wagering.slate import SlateConfig

FIXTURES = Path(__file__).parent / "fixtures"


def _factory(train: pd.DataFrame) -> NFLGameModel:
    return NFLGameModel(
        fit_ratings(train, ridge_lambda=150.0),
        NFLModelConfig(sim=SimConfig(n_sims=4_000)),
    )


@pytest.fixture
def season_games() -> pd.DataFrame:
    df = pd.read_csv(FIXTURES / "nfl_season_games.csv")
    df["kickoff"] = pd.to_datetime(df["kickoff"])
    return df


@pytest.fixture
def season_lines() -> pd.DataFrame:
    df = pd.read_csv(FIXTURES / "nfl_season_lines.csv")
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


@pytest.fixture
def config() -> BacktestConfig:
    return BacktestConfig(slate=SlateConfig(min_edge=0.03), min_train_games=8)


def test_backtest_runs_and_reports(season_games, season_lines, plays, config) -> None:
    res = walk_forward(season_games, plays, season_lines, _factory, config)
    assert not res.projections.empty
    assert not res.ledger.empty
    assert not res.bankroll_curve.empty
    # The report carries the full acceptance picture.
    for key in ("brier", "calibration_error", "roi", "line_clv_mean", "final_bankroll"):
        assert key in res.metrics


def test_projection_beats_market_baseline(season_games, season_lines, plays, config) -> None:
    res = walk_forward(season_games, plays, season_lines, _factory, config)
    # A model that has learned the ratings must beat "always predict the base rate."
    assert res.metrics["brier"] < res.metrics["brier_baseline"]


def test_model_earns_positive_closing_line_value(season_games, season_lines, plays, config) -> None:
    res = walk_forward(season_games, plays, season_lines, _factory, config)
    # The acceptance signal: on average the model beats the closing number, and
    # does so on a majority of bets — even where short-run ROI is negative.
    assert res.metrics["line_clv_mean"] > 0.0
    assert res.metrics["pct_beat_close"] > 0.5


def test_backtest_is_reproducible(season_games, season_lines, plays, config) -> None:
    a = walk_forward(season_games, plays, season_lines, _factory, config)
    b = walk_forward(season_games, plays, season_lines, _factory, config)
    assert a.metrics == b.metrics
    pd.testing.assert_frame_equal(a.projections, b.projections)


def test_bankroll_curve_covers_every_week(season_games, season_lines, plays, config) -> None:
    res = walk_forward(season_games, plays, season_lines, _factory, config)
    weeks_predicted = set(res.projections["week"])
    assert set(res.bankroll_curve["week"]) == weeks_predicted


def test_no_lookahead(season_games, season_lines, plays, config) -> None:
    """Projections for early weeks must not change when later plays are removed.

    If the engine ever trained on future plays, truncating the play history would
    move an earlier week's projection. It must not.
    """
    cut = 6
    full = walk_forward(season_games, plays, season_lines, _factory, config)
    truncated_plays = plays[plays["week"] < cut]
    partial = walk_forward(season_games, truncated_plays, season_lines, _factory, config)

    full_early = full.projections[full.projections["week"] < cut].set_index("game_id")
    partial_early = partial.projections[partial.projections["week"] < cut].set_index("game_id")
    common = full_early.index.intersection(partial_early.index)
    assert len(common) > 0
    # Byte-identical projections on the overlap → no future data leaked in.
    pd.testing.assert_series_equal(
        full_early.loc[common, "p_home_win"], partial_early.loc[common, "p_home_win"]
    )


def test_projections_carry_the_finals_and_the_models_own_state(
        season_games, season_lines, plays, config) -> None:
    """Projection ERROR has to be recoverable from this frame alone.

    "How good is the projection" is a different question from "did the bet
    win", and both get answered off the same leak-safe pass rather than a
    second one. Everything added here is model-internal on purpose: nothing
    the market said belongs in a measure of how sure the model is entitled to
    be.
    """
    proj = walk_forward(season_games, plays, season_lines, _factory, config).projections
    for column in ("home_score", "away_score", "mu_home", "mu_away",
                   "sd_margin", "sd_total", "train_games"):
        assert column in proj.columns, column
        assert proj[column].notna().all()

    # The finals are the games' own, joined back by game_id.
    finals = season_games.set_index("game_id")
    for row in proj.to_dict("records"):
        game = finals.loc[row["game_id"]]
        assert row["home_score"] == float(game["home_score"])
        assert row["away_score"] == float(game["away_score"])

    # Dispersion is a positive width, and the training count only ever grows
    # as the walk-forward moves through the season.
    assert (proj["sd_margin"] > 0).all()
    assert (proj["sd_total"] > 0).all()
    ordered = proj.sort_values(["season", "week"])["train_games"]
    assert ordered.is_monotonic_increasing
