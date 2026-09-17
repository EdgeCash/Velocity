"""The injury burden: usage shares entering a week, summed over the players out."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.features.injuries import burden_by_team_week, shares_entering


def _weeks() -> pd.DataFrame:
    rows = []
    # 2024: WR1 has 60 of 100 touches, RB1 40; 2025 weeks 1-4: WR1 20/50, RB1 30/50.
    for week in range(1, 18):
        rows.append({"season": 2024, "week": week, "team": "A", "player_id": "wr1",
                     "position": "WR", "targets": 6, "carries": 0})
        rows.append({"season": 2024, "week": week, "team": "A", "player_id": "rb1",
                     "position": "RB", "targets": 1, "carries": 3})
    for week in range(1, 5):
        rows.append({"season": 2025, "week": week, "team": "A", "player_id": "wr1",
                     "position": "WR", "targets": 2, "carries": 0})
        rows.append({"season": 2025, "week": week, "team": "A", "player_id": "rb1",
                     "position": "RB", "targets": 0, "carries": 3})
    return pd.DataFrame(rows)


def test_shares_entering_switch_from_last_season_to_season_to_date() -> None:
    weeks = _weeks()
    early = shares_entering(weeks, 2025, 2).set_index("player_id")["share"]
    assert early["wr1"] == pytest.approx(0.6) and early["rb1"] == pytest.approx(0.4)
    late = shares_entering(weeks, 2025, 5).set_index("player_id")["share"]
    assert late["wr1"] == pytest.approx(0.4) and late["rb1"] == pytest.approx(0.6)
    # Week 5 sees weeks 1-4 only; a week the bank does not reach yields nothing.
    assert shares_entering(weeks, 2026, 6).empty


def test_burden_sums_the_outs_and_skips_quarterbacks_and_unknown_seasons() -> None:
    weeks = _weeks()
    injuries = pd.DataFrame({
        "season": [2025, 2025, 2025, 2025, 2019],
        "week": [2, 2, 2, 5, 3],
        "team": ["A", "A", "A", "A", "A"],
        "player_id": ["wr1", "qb1", "new", "rb1", "wr1"],
        "position": ["WR", "QB", "WR", "RB", "WR"],
        "is_out": [True, True, True, True, True],
    })
    burden = burden_by_team_week(injuries, weeks)
    by_key = {(r.season, r.week, r.team): r.burden for r in burden.itertuples()}
    # Week 2: WR1 out at last season's 0.6; the QB is excluded; the new signing has no share.
    assert by_key[(2025, 2, "A")] == pytest.approx(0.6)
    # Week 5: RB1 out at the season-to-date 0.6.
    assert by_key[(2025, 5, "A")] == pytest.approx(0.6)
    # 2019 has no usage bank behind it: no row, not a zero.
    assert (2019, 3, "A") not in by_key


def test_injury_burden_model_takes_points_off_the_side_that_is_short() -> None:
    from velocity.backtest.lab import InjuryBurdenModel
    from velocity.features.team import TeamRatings
    from velocity.models.game_nfl import NFLGameModel, NFLModelConfig
    from velocity.models.simulate import SimConfig

    ratings = TeamRatings(offense={"A": 0.0, "B": 0.0}, defense={"A": 0.0, "B": 0.0},
                          league_epa=0.0, ridge_lambda=200.0, n_plays=10, teams=("A", "B"))
    model = NFLGameModel(ratings, NFLModelConfig(sim=SimConfig(n_sims=200)))
    kick = pd.Timestamp("2025-09-14 17:00")
    schedule = pd.DataFrame({"home_team": ["A"], "away_team": ["B"], "kickoff": [kick],
                             "season": [2025], "week": [2]})
    burden = pd.DataFrame({"season": [2025], "week": [2], "team": ["A"], "burden": [0.15]})
    wrapped = InjuryBurdenModel(model, schedule, burden, points_per_unit=8.0)
    rng = np.random.default_rng(0)
    bare = model.project("A", "B", rng=rng)
    short = wrapped.project("A", "B", kickoff=kick, rng=rng)
    assert bare.mu_home - short.mu_home == pytest.approx(1.2)
    assert short.mu_away == pytest.approx(bare.mu_away)
    # An unknown game, or a team with no row, costs nothing.
    other = wrapped.project("A", "B", kickoff=kick + pd.Timedelta(days=7), rng=rng)
    assert other.mu_home == pytest.approx(bare.mu_home)


def test_injury_burden_model_can_be_pinned_to_one_week_for_a_live_slate() -> None:
    from velocity.backtest.lab import InjuryBurdenModel
    from velocity.features.team import TeamRatings
    from velocity.models.game_nfl import NFLGameModel, NFLModelConfig
    from velocity.models.simulate import SimConfig

    ratings = TeamRatings(offense={"A": 0.0, "B": 0.0}, defense={"A": 0.0, "B": 0.0},
                          league_epa=0.0, ridge_lambda=200.0, n_plays=10, teams=("A", "B"))
    model = NFLGameModel(ratings, NFLModelConfig(sim=SimConfig(n_sims=200)))
    burden = pd.DataFrame({"season": [2025], "week": [3], "team": ["B"], "burden": [0.25]})
    # No schedule row for the board game, but the week is named outright.
    pinned = InjuryBurdenModel(model, pd.DataFrame(columns=["home_team", "away_team", "kickoff",
                                                            "season", "week"]),
                               burden, 4.0, fixed_season_week=(2025, 3))
    rng = np.random.default_rng(0)
    bare = model.project("A", "B", rng=rng)
    proj = pinned.project("A", "B", kickoff=pd.Timestamp("2025-09-21"), rng=rng)
    assert bare.mu_away - proj.mu_away == pytest.approx(1.0)
    assert proj.mu_home == pytest.approx(bare.mu_home)
