"""A home-field edge fitted inside the EPA ridge (the college HFA round)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.backtest.lab import compress_plays
from velocity.features.team import fit_ratings


def _plays(seed: int = 1, home_edge: float = 0.06) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    teams = ["A", "B", "C", "D"]
    strength = {"A": 0.10, "B": 0.03, "C": -0.03, "D": -0.10}
    games, plays = [], []
    gid = 0
    for week in range(1, 9):
        order = rng.permutation(teams)
        for h, a in ((order[0], order[1]), (order[2], order[3])):
            gid += 1
            neutral = week == 8
            games.append({"game_id": f"g{gid}", "season": 2025, "week": week,
                          "home_team": h, "away_team": a, "neutral_site": neutral})
            for off, deff, at_home in ((h, a, not neutral), (a, h, False)):
                edge = home_edge if at_home else (0.0 if neutral else -home_edge)
                mu = strength[off] - strength[deff] + edge
                for _ in range(60):
                    plays.append({"game_id": f"g{gid}", "season": 2025, "week": week,
                                  "posteam": off, "defteam": deff,
                                  "epa": mu + rng.normal(0.0, 0.4)})
    return pd.DataFrame(plays), pd.DataFrame(games)


def test_cells_carry_the_home_flag_and_the_fit_recovers_the_edge() -> None:
    plays, games = _plays()
    cells = compress_plays(plays, games)
    assert set(cells["home"].unique()) == {0.5, -0.5, 0.0}
    assert (cells.loc[cells["week"] == 8, "home"] == 0.0).all()
    fitted = fit_ratings(cells, ridge_lambda=1.0, weights=cells["n"].astype(float),
                         home_col="home")
    # +0.06 at home, −0.06 away: the home column's coefficient is the gap.
    assert fitted.home_epa == pytest.approx(0.12, abs=0.03)
    # Team deviations survive, ordered as planted.
    assert fitted.offense["A"] > fitted.offense["B"] > fitted.offense["C"] > fitted.offense["D"]


def test_without_a_home_column_nothing_changes() -> None:
    plays, games = _plays()
    plain = compress_plays(plays)
    assert "home" not in plain.columns
    fitted = fit_ratings(plain, ridge_lambda=1.0, weights=plain["n"].astype(float))
    assert fitted.home_epa == 0.0
    # Asking for a column the cells do not carry is the plain fit, not an error.
    same = fit_ratings(plain, ridge_lambda=1.0, weights=plain["n"].astype(float),
                       home_col="home")
    assert same.offense == fitted.offense and same.home_epa == 0.0
