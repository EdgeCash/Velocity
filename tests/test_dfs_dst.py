"""The DST projection — brackets off the sim, counting stats from FP."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.dfs.dst import (
    FP_PA_COLUMNS,
    LEAGUE_PA_PROBS,
    bracket_expectation,
    dst_expected_points,
    dst_samples,
    opponent_scores_from_projections,
    pa_points,
    project_dst,
)


def _fp(with_brackets: bool = False) -> pd.DataFrame:
    rows = []
    for team, name, sacks, ints in (("KC", "Kansas City Chiefs", 2.6, 0.9),
                                    ("BUF", "Buffalo Bills", 2.1, 0.7)):
        stats = {"def_sack": sacks, "def_int": ints, "def_fr": 0.5, "def_td": 0.12,
                 "def_safety": 0.03, "def_retd": 0.05, "def_ff": 0.8}
        if with_brackets:
            stats.update(dict(zip(
                FP_PA_COLUMNS, (0.02, 0.10, 0.25, 0.30, 0.20, 0.09, 0.04), strict=True)))
        for stat, value in stats.items():
            rows.append({"player_id": team, "player_name": name, "team": team,
                         "position": "DST", "stat": stat, "value": value})
    rows.append({"player_id": "qb1", "player_name": "Some QB", "team": "KC",
                 "position": "QB", "stat": "pass_yds", "value": 270.0})
    return pd.DataFrame(rows)


def test_points_allowed_brackets_are_draftkings() -> None:
    assert pa_points([0, 6, 7, 13, 14, 20, 21, 27, 28, 34, 35, 50]).tolist() == [
        10, 7, 4, 4, 1, 1, 0, 0, -1, -1, -4, -4]
    assert bracket_expectation([1, 0, 0, 0, 0, 0, 0]) == 10.0
    assert bracket_expectation([0] * 7) == 0.0
    # The league fallback sits where a ~23-point opponent lands.
    assert 0.5 < bracket_expectation(LEAGUE_PA_PROBS) < 2.0


def test_the_sim_prices_the_bracket_and_fp_the_counting_stats() -> None:
    fp = _fp()
    # KC's opponent scores 10 every sim → +4 a game; BUF has no sim.
    scores = {"KC": np.full(1000, 10.0)}
    projections = {p.team: p for p in project_dst(fp, scores)}
    kc = projections["KC"]
    assert kc.pa_source == "sim" and kc.pa_expected == 4.0
    counted = 2.6 * 1 + 0.9 * 2 + 0.5 * 2 + 0.12 * 6 + 0.03 * 2 + 0.05 * 6  # def_ff unscored
    assert kc.points == pytest.approx(counted + 4.0)
    buf = projections["BUF"]
    assert buf.pa_source == "league"
    frame = dst_expected_points(fp, scores)
    assert frame["position"].tolist() == ["DST", "DST"]
    assert set(frame.columns) == {"player_id", "player_name", "team", "position", "points"}
    assert frame.set_index("team").loc["KC", "points"] == pytest.approx(kc.points, abs=0.01)


def test_fantasypros_brackets_are_the_middle_fallback() -> None:
    projections = {p.team: p for p in project_dst(_fp(with_brackets=True), None)}
    assert projections["KC"].pa_source == "fantasypros"
    expected = bracket_expectation((0.02, 0.10, 0.25, 0.30, 0.20, 0.09, 0.04))
    assert projections["KC"].pa_expected == pytest.approx(expected)
    assert dst_expected_points(pd.DataFrame(), None).empty


def test_dst_samples_move_against_the_opponents_score() -> None:
    fp = _fp()
    opp = np.random.default_rng(2).normal(24.0, 10.0, 20_000).clip(0)
    scores = {"KC": opp}
    samples = dst_samples(project_dst(fp, scores), scores, np.random.default_rng(7), 20_000)
    kc = samples["Kansas City Chiefs"]
    assert "Buffalo Bills" not in samples  # no sim, no array
    assert kc.mean() == pytest.approx(dst_expected_points(fp, scores)
                                      .set_index("team").loc["KC", "points"], abs=0.3)
    # Reproducible pairing (the index draw is the generator's first call):
    # a low-scoring opponent sim is a high-scoring DST.
    idx = np.random.default_rng(7).integers(0, 20_000, size=20_000)
    assert np.corrcoef(opp[idx], kc)[0, 1] < -0.5


def test_opponent_scores_come_from_the_runs_projections() -> None:
    projections = pd.DataFrame([
        {"game_id": "g1", "home": "KC", "away": "BUF", "mu_home": 27.0, "mu_away": 20.0},
        {"game_id": "g1", "home": "KC", "away": "BUF", "mu_home": 27.0, "mu_away": 20.0},
    ])
    scores = opponent_scores_from_projections(projections, 20_000, np.random.default_rng(1))
    assert set(scores) == {"KC", "BUF"}
    # KC's defense allows BUF's score (~20); BUF's allows KC's (~27).
    assert scores["KC"].mean() == pytest.approx(20.0, abs=1.0)
    assert scores["BUF"].mean() == pytest.approx(27.0, abs=1.0)
    assert opponent_scores_from_projections(pd.DataFrame(), 100, np.random.default_rng(1)) == {}
