"""Model lab — weighted fits, phase splits, and the disagreement sweeps.

The lab is only trustworthy if its plumbing is exact: weights=None is
bit-identical to the unweighted fit, recency decay halves at the half-life,
the phase blend is the linear combination it claims, and the spread sweep's
sign convention matches nflverse (positive spread_line = home favored).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.backtest.lab import (
    ats_ou_vs_close,
    combine_ratings,
    disagreement_sweep,
    fit_split_ratings,
    recency_weights,
)
from velocity.features.team import TeamRatings, fit_ratings


def _plays(n_per_pair: int = 40, seed: int = 5) -> pd.DataFrame:
    """Synthetic two-team schedule where A's offense is clearly better."""
    rng = np.random.default_rng(seed)
    rows = []
    for season, week in ((2024, 1), (2024, 2), (2025, 1)):
        for _ in range(n_per_pair):
            rows.append({"season": season, "week": week, "posteam": "A", "defteam": "B",
                         "play_type": "pass", "epa": rng.normal(0.15, 0.3)})
            rows.append({"season": season, "week": week, "posteam": "B", "defteam": "A",
                         "play_type": "run", "epa": rng.normal(-0.10, 0.3)})
    return pd.DataFrame(rows)


def test_weights_none_is_bit_identical_to_unweighted() -> None:
    plays = _plays()
    a = fit_ratings(plays)
    b = fit_ratings(plays, weights=None)
    assert a.offense == b.offense and a.defense == b.defense


def test_uniform_weights_match_unweighted() -> None:
    plays = _plays()
    a = fit_ratings(plays)
    w = pd.Series(1.0, index=plays.index)
    b = fit_ratings(plays, weights=w)
    for team in a.offense:
        assert b.offense[team] == pytest.approx(a.offense[team])


def test_recency_weights_halve_at_the_half_life() -> None:
    plays = _plays()
    w = recency_weights(plays, half_life_weeks=24.0)
    newest = w[(plays["season"] == 2025) & (plays["week"] == 1)].iloc[0]
    oldest = w[(plays["season"] == 2024) & (plays["week"] == 1)].iloc[0]
    assert newest == 1.0
    # 2024w1 → 2025w1 is 25 key-steps... on the contiguous key: (2025*25+1)-(2024*25+1)=25.
    assert oldest == pytest.approx(0.5 ** (25 / 24))
    # More recent plays always weigh at least as much.
    mid = w[(plays["season"] == 2024) & (plays["week"] == 2)].iloc[0]
    assert oldest < mid < newest


def test_recency_weighted_fit_leans_toward_recent_form() -> None:
    plays = _plays().copy()
    # Team A collapses in the most recent slice.
    recent = (plays["season"] == 2025) & (plays["posteam"] == "A")
    plays.loc[recent, "epa"] = -0.5
    flat = fit_ratings(plays)
    recency = fit_ratings(plays, weights=recency_weights(plays, half_life_weeks=4.0))
    assert recency.offense["A"] < flat.offense["A"]  # the collapse counts more


def test_combine_ratings_is_the_stated_linear_blend() -> None:
    p = TeamRatings(offense={"A": 0.2}, defense={"A": -0.1}, league_epa=0.05,
                    ridge_lambda=200.0, n_plays=100, teams=("A",))
    r = TeamRatings(offense={"A": -0.05, "B": 0.02}, defense={"B": 0.03}, league_epa=0.01,
                    ridge_lambda=200.0, n_plays=50, teams=("A", "B"))
    combined = combine_ratings(p, r, pass_weight=0.75)
    assert combined.offense["A"] == pytest.approx(0.75 * 0.2 + 0.25 * -0.05)
    assert combined.offense["B"] == pytest.approx(0.25 * 0.02)  # missing → league avg 0
    assert combined.defense["A"] == pytest.approx(0.75 * -0.1)
    assert combined.league_epa == pytest.approx(0.75 * 0.05 + 0.25 * 0.01)
    with pytest.raises(ValueError, match="pass_weight"):
        combine_ratings(p, r, pass_weight=1.5)


def test_fit_split_ratings_separates_the_phases() -> None:
    plays = _plays()
    # All of A's plays are passes; at pass_weight=1.0 the blend IS the pass fit.
    split_all_pass = fit_split_ratings(plays, pass_weight=1.0)
    pass_only = fit_ratings(plays[plays["play_type"] == "pass"])
    assert split_all_pass.offense["A"] == pytest.approx(pass_only.offense["A"])


GAMES = pd.DataFrame({
    "game_id": ["g1", "g2", "g3"],
    "home_score": [27.0, 20.0, 30.0],
    "away_score": [20.0, 24.0, 27.0],
    "spread_line": [3.0, 7.0, 3.0],   # nflverse: positive = home favored
    "total_line": [44.5, 47.5, 51.5],
})
PROJECTIONS = pd.DataFrame({
    "game_id": ["g1", "g2", "g3"],
    # fair_spread is the fair HOME spread: negative = home favored.
    "fair_spread": [-6.0, -3.0, -4.0],
    "fair_total": [49.5, 44.5, 52.5],
})


def test_spread_sweep_signs_match_nflverse() -> None:
    sweep = disagreement_sweep(PROJECTIONS, GAMES, market="spread", thresholds=(0.0, 3.5))
    flat = sweep.iloc[0]
    # g1: model home −6 vs line home −3 → pick home; margin 7 > 3 → WIN.
    # g2: model home −3 vs line home −7 → pick away; margin −4 < 7 → WIN.
    # g3: model home −4 vs line −3 → pick home; margin 3 = line 3 → push, excluded.
    assert flat["bets"] == 2
    assert flat["win_rate"] == pytest.approx(1.0)
    tight = sweep.iloc[1]
    # Gaps: g1 |6−3| = 3 (below 3.5), g2 |3−7| = 4 (kept) → one bet survives.
    assert tight["bets"] == 1
    # A threshold beyond every gap leaves nothing.
    empty = disagreement_sweep(PROJECTIONS, GAMES, market="spread", thresholds=(10.0,))
    assert empty.iloc[0]["bets"] == 0


def test_total_sweep_and_flat_summary() -> None:
    sweep = disagreement_sweep(PROJECTIONS, GAMES, market="total", thresholds=(0.0,))
    # g1: model 49.5 > 44.5 → over; realized 47 > 44.5 → WIN.
    # g2: model 44.5 < 47.5 → under; realized 44 < 47.5 → WIN.
    # g3: model 52.5 > 51.5 → over; realized 57 > 51.5 → WIN.
    assert sweep.iloc[0]["bets"] == 3
    assert sweep.iloc[0]["win_rate"] == pytest.approx(1.0)
    summary = ats_ou_vs_close(PROJECTIONS, GAMES)
    assert summary["ats_bets"] == 2.0
    assert summary["ou_win_rate"] == pytest.approx(1.0)
