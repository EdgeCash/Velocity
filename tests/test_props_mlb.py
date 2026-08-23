"""Pitcher-strikeout prop model — shrinkage, opponent factor, pricing."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from velocity.models.props_mlb import MARKET, PitcherKModel

REPO = Path(__file__).parent.parent


def _history() -> tuple[pd.DataFrame, pd.DataFrame]:
    games, starters = [], []
    kick = pd.Timestamp("2026-06-01")
    # 20 games: the ace (high-K) starts 10 for team A vs B; a rookie makes
    # one start. Team B's lineup whiffs a lot (they face everyone here).
    for i in range(10):
        gid = f"g{i}"
        games.append({"game_id": gid, "kickoff": kick + pd.Timedelta(days=i),
                      "home_team": "A", "away_team": "B"})
        starters.append({"game_id": gid, "side": "home", "starter_id": "ace",
                         "starter_name": "Ace Starter", "batters_faced": 24,
                         "k": 9, "outs": 18})
        starters.append({"game_id": gid, "side": "away", "starter_id": f"b{i}",
                         "starter_name": f"B Pitcher {i}", "batters_faced": 22,
                         "k": 4, "outs": 15})
    games.append({"game_id": "g99", "kickoff": kick + pd.Timedelta(days=20),
                  "home_team": "A", "away_team": "B"})
    starters.append({"game_id": "g99", "side": "home", "starter_id": "rookie",
                     "starter_name": "Rookie Arm", "batters_faced": 18,
                     "k": 9, "outs": 15})
    starters.append({"game_id": "g99", "side": "away", "starter_id": "b99",
                     "starter_name": "B Pitcher 99", "batters_faced": 20,
                     "k": 4, "outs": 15})
    return pd.DataFrame(starters), pd.DataFrame(games)


def test_fit_shrinks_small_samples_toward_league() -> None:
    starters, games = _history()
    model = PitcherKModel.fit(starters, games)
    ace, rookie = model.means["ace"], model.means["rookie"]
    league_mean = model.league_bf * model.league_rate
    # The ace's 240-BF sample keeps most of his edge; the rookie's single
    # hot start (50% K rate) is pulled well toward league.
    assert ace > league_mean
    assert rookie < ace
    assert rookie - league_mean < (9.0 - league_mean) * 0.5


def test_opponent_factor_scales_the_mean() -> None:
    starters, games = _history()
    model = PitcherKModel.fit(starters, games)
    # Team B faced the high-K ace all sample, team A faced average arms —
    # so B's factor exceeds A's.
    assert model.opp_factor["B"] > model.opp_factor["A"]
    scoped = model.for_game({"ace": "B"})
    neutral = model.distribution("ace")
    assert neutral is not None
    assert scoped._dist("ace").mean > 0
    assert scoped._dist("ace").mean == pytest.approx(
        neutral.mean * model.opp_factor["B"] / 1.0)


def test_pricing_half_and_whole_lines() -> None:
    starters, games = _history()
    model = PitcherKModel.fit(starters, games)
    scoped = model.for_game({"ace": "B"})
    over = scoped.prob_over("ace", MARKET, 6.5)
    under = scoped.prob_under("ace", MARKET, 6.5)
    assert over + under == pytest.approx(1.0)  # half lines never push
    over_w = scoped.prob_over("ace", MARKET, 7.0)
    under_w = scoped.prob_under("ace", MARKET, 7.0)
    assert over_w + under_w < 1.0  # whole lines carry push mass
    assert scoped.has("ace", MARKET)
    assert not scoped.has("nobody", MARKET)
    assert not scoped.has("ace", "receptions")


def test_fit_on_the_banked_history_is_sane() -> None:
    starters_file = REPO / "datasets/mlb/starters.parquet"
    games_file = REPO / "datasets/mlb/games.parquet"
    if not starters_file.exists():
        pytest.skip("no banked MLB starters")
    model = PitcherKModel.fit(pd.read_parquet(starters_file),
                              pd.read_parquet(games_file))
    means = pd.Series(model.means)
    assert len(means) > 200
    assert 2.0 < means.median() < 7.0
    assert 5.0 <= model.dispersion <= 100.0
    factors = pd.Series(model.opp_factor)
    assert factors.min() > 0.8 and factors.max() < 1.25
