"""The joint phase ridge in the QB fit (velocity.features.team.fit_qb_ratings).

Offline, synthetic. One design in place of the rejected two-fit split
(docs/PROJECTION_AUDIT.md §3 #16): the team columns stay the all-plays
rating, and a pass-phase deviation per team on offense and on defense fires
on pass plays only, shrunk toward 0 at its own ridge.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.features.team import fit_qb_ratings


def _plays(n_games: int = 60, per_side: int = 60, seed: int = 9) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    teams = ["A", "B", "C", "D"]
    # A: a passing offense (+0.3 on passes, −0.1 on runs); C: a pass defense
    # that allows −0.25 on passes and is average against the run.
    pass_off = {"A": 0.3, "B": 0.0, "C": 0.0, "D": 0.0}
    rush_off = {"A": -0.1, "B": 0.0, "C": 0.0, "D": 0.0}
    pass_def = {"A": 0.0, "B": 0.0, "C": -0.25, "D": 0.0}
    rows = []
    for g in range(n_games):
        home, away = rng.choice(teams, size=2, replace=False)
        for off, dfn in ((home, away), (away, home)):
            for i in range(per_side):
                is_pass = i % 5 != 0  # 80% passes
                epa = ((pass_off[off] + pass_def[dfn]) if is_pass else rush_off[off]) \
                    + rng.normal(0.0, 0.4)
                rows.append({
                    "play_id": str(len(rows)), "game_id": f"g{g}", "season": 2025,
                    "week": 1 + g // 4, "posteam": off, "defteam": dfn,
                    "play_type": "pass" if is_pass else "run", "epa": epa,
                    "passer_player_id": f"qb{off}" if is_pass else None,
                })
    frame = pd.DataFrame(rows)
    frame["passer_player_id"] = frame["passer_player_id"].astype("string")
    return frame


def test_phase_columns_recover_the_passing_edges_and_price_at_the_pass_rate() -> None:
    plays = _plays()
    plain = fit_qb_ratings(plays, ridge_lambda=5.0, qb_lambda=5.0, min_dropbacks=30)
    phased = fit_qb_ratings(plays, ridge_lambda=5.0, qb_lambda=5.0, min_dropbacks=30,
                            phase_col="play_type", phase_lambda=5.0)
    assert plain.pass_offense == {} and plain.phase_lambda == 0.0
    assert set(phased.pass_offense) == set(phased.pass_defense) == {"A", "B", "C", "D"}
    assert phased.phase_lambda == 5.0
    # A's passing game is its edge over its all-plays rating; C's pass
    # defense allows less than its all-plays defence.
    assert phased.pass_offense["A"] > max(phased.pass_offense[t] for t in "BCD") + 0.1
    assert phased.pass_defense["C"] < min(phased.pass_defense[t] for t in "ABD") - 0.1
    # The matchup prices the deviations at the offense's pass rate.
    rate = phased.pass_rate["A"]
    assert rate == pytest.approx(0.8, abs=1e-9)
    expected = (phased.offense["A"] + phased.defense["C"]
                + rate * (phased.qb["qbA"] + phased.pass_offense["A"] + phased.pass_defense["C"]))
    assert phased.matchup_delta("A", "C") == pytest.approx(expected)
    # An unknown team contributes nothing on the phase side either.
    assert phased.matchup_delta("Z", "Z") == 0.0


def test_a_heavy_phase_ridge_collapses_to_the_plain_fit() -> None:
    plays = _plays()
    plain = fit_qb_ratings(plays, ridge_lambda=5.0, qb_lambda=5.0, min_dropbacks=30)
    heavy = fit_qb_ratings(plays, ridge_lambda=5.0, qb_lambda=5.0, min_dropbacks=30,
                           phase_col="play_type", phase_lambda=1e9)
    for team in "ABCD":
        assert heavy.offense[team] == pytest.approx(plain.offense[team], abs=1e-6)
        assert abs(heavy.pass_offense[team]) < 1e-6
    # No phase column in the frame: the plain fit, whatever the ridge.
    bare = fit_qb_ratings(plays.drop(columns=["play_type"]), ridge_lambda=5.0, qb_lambda=5.0,
                          min_dropbacks=30, phase_col="play_type", phase_lambda=5.0)
    assert bare.pass_offense == {}
    with pytest.raises(ValueError):
        fit_qb_ratings(plays, phase_col="play_type", phase_lambda=0.0)
