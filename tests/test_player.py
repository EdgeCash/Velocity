"""Player usage features — shares, empirical-Bayes shrinkage, injury repricing."""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.features.player import (
    empirical_bayes_share,
    redistribute_shares,
    usage_shares,
)


def test_usage_shares_sum_to_one() -> None:
    shares = usage_shares(pd.Series({"WR1": 100, "WR2": 60, "TE": 40}))
    assert shares.sum() == pytest.approx(1.0)
    assert shares["WR1"] == pytest.approx(0.5)


def test_usage_shares_all_zero() -> None:
    shares = usage_shares(pd.Series({"A": 0, "B": 0}))
    assert (shares == 0).all()


def test_empirical_bayes_regresses_small_samples() -> None:
    # A 3-vs-1 split is too thin to trust; EB pulls it toward the 0.5 baseline.
    eb = empirical_bayes_share(pd.Series({"RB1": 3, "RB2": 1}), prior_strength=10.0)
    assert eb.sum() == pytest.approx(1.0)
    assert 0.5 < eb["RB1"] < 0.75  # regressed in from the raw 0.75


def test_empirical_bayes_trusts_large_samples() -> None:
    raw = pd.Series({"WR1": 300, "WR2": 100})
    eb = empirical_bayes_share(raw, prior_strength=10.0)
    assert eb["WR1"] == pytest.approx(0.75, abs=0.02)  # barely moved from 0.75


def test_empirical_bayes_custom_prior() -> None:
    counts = pd.Series({"A": 0, "B": 0})  # no data → falls back entirely to prior
    prior = pd.Series({"A": 0.8, "B": 0.2})
    eb = empirical_bayes_share(counts, prior_strength=10.0, prior_share=prior)
    assert eb["A"] == pytest.approx(0.8)


def test_redistribute_lifts_teammates() -> None:
    shares = usage_shares(pd.Series({"WR1": 100, "WR2": 60, "TE": 40}))
    repriced = redistribute_shares(shares, ["WR1"])
    assert repriced.sum() == pytest.approx(1.0)
    assert repriced["WR1"] == 0.0
    assert repriced["WR2"] > shares["WR2"]  # WR1's share flows to teammates
    # Relative order among the active players is preserved.
    assert repriced["WR2"] > repriced["TE"]


def test_redistribute_all_inactive() -> None:
    shares = usage_shares(pd.Series({"A": 1, "B": 1}))
    assert (redistribute_shares(shares, ["A", "B"]) == 0).all()


# --- QB-decomposed team ratings (velocity.features.team.fit_qb_ratings) --------


def test_fit_qb_ratings_decomposes_passer_from_team() -> None:
    import pandas as pd
    from velocity.features.team import fit_qb_ratings

    rows = []
    pid = 0
    # Team A: elite starter QB1 on passes (+0.5), neutral runs. Team B: neutral.
    for _ in range(120):
        pid += 1
        rows.append({"play_id": pid, "game_id": "s1", "season": 2025, "week": 1,
                     "posteam": "A", "defteam": "B", "play_type": "pass",
                     "epa": 0.5, "passer_player_id": "QB1"})
    for _ in range(80):
        pid += 1
        rows.append({"play_id": pid, "game_id": "s1", "season": 2025, "week": 1,
                     "posteam": "A", "defteam": "B", "play_type": "run",
                     "epa": 0.0, "passer_player_id": None})
    for _ in range(200):
        pid += 1
        rows.append({"play_id": pid, "game_id": "s1", "season": 2025, "week": 1,
                     "posteam": "B", "defteam": "A", "play_type": "pass" if pid % 2 else "run",
                     "epa": 0.0, "passer_player_id": "QB2" if pid % 2 else None})
    # Week 2: A's backup QB3 starts and is bad (-0.4/dropback).
    for _ in range(60):
        pid += 1
        rows.append({"play_id": pid, "game_id": "s2", "season": 2025, "week": 2,
                     "posteam": "A", "defteam": "B", "play_type": "pass",
                     "epa": -0.4, "passer_player_id": "QB3"})
    plays = pd.DataFrame(rows)

    ratings = fit_qb_ratings(plays, ridge_lambda=50.0, qb_lambda=20.0,
                             min_dropbacks=30)
    assert ratings.qb["QB1"] > ratings.qb["QB3"]  # the starter outrates the backup
    assert ratings.starters["A"] == "QB3"  # week 2's primary passer detected
    assert ratings.starters["B"] == "QB2"
    assert 0.0 < ratings.pass_rate["A"] < 1.0
    # Pricing the backup lowers A's matchup delta vs pricing the starter.
    with_backup = ratings.matchup_delta("A", "B")
    with_starter = ratings.matchup_delta("A", "B", qb_id="QB1")
    assert with_starter > with_backup
    # Unknown QB/team fall back to league average, never a guess.
    assert ratings.matchup_delta("A", "B", qb_id="nobody") == (
        ratings.offense["A"] + ratings.defense["B"]
    )


def test_fit_qb_ratings_requires_the_passer_column() -> None:
    import pandas as pd
    import pytest
    from velocity.features.team import fit_qb_ratings

    plays = pd.DataFrame([{"play_id": 1, "game_id": "g", "season": 2025,
                           "week": 1, "posteam": "A", "defteam": "B",
                           "play_type": "pass", "epa": 0.1}])
    with pytest.raises(ValueError, match="passer_player_id"):
        fit_qb_ratings(plays)
