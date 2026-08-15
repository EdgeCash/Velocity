"""Preseason priors and Bayesian shrinkage."""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.features.priors import (
    PriorWeights,
    preseason_prior,
    shrink_series,
    shrink_to_prior,
)


@pytest.fixture
def team_inputs() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "team": ["A", "B", "C", "D"],
            "recruiting": [0.9, 0.2, 0.5, 0.1],
            "returning_production": [0.8, 0.3, 0.6, 0.2],
            "prior_rating": [0.15, -0.05, 0.05, -0.15],
        }
    )


def test_prior_orders_teams_by_strength(team_inputs: pd.DataFrame) -> None:
    prior = preseason_prior(team_inputs)
    assert prior["A"] == prior.max()  # best on every input
    assert prior["D"] == prior.min()
    assert prior.index.tolist() == ["A", "B", "C", "D"]


def test_prior_is_centered(team_inputs: pd.DataFrame) -> None:
    # Standardized components are mean-zero, so the blended prior is too.
    prior = preseason_prior(team_inputs)
    assert prior.mean() == pytest.approx(0.0, abs=1e-9)


def test_prior_scale_controls_spread(team_inputs: pd.DataFrame) -> None:
    small = preseason_prior(team_inputs, rating_scale=0.05)
    big = preseason_prior(team_inputs, rating_scale=0.20)
    assert big.std() == pytest.approx(4.0 * small.std())


def test_zero_variance_input_contributes_nothing() -> None:
    inputs = pd.DataFrame(
        {
            "team": ["A", "B"],
            "recruiting": [1.0, 1.0],  # no spread → contributes zero
            "returning_production": [0.9, 0.1],
            "prior_rating": [0.0, 0.0],
        }
    )
    prior = preseason_prior(inputs)
    assert prior["A"] > prior["B"]  # driven entirely by returning production


def test_weights_normalize_and_reject_nonpositive() -> None:
    assert sum(PriorWeights(1.0, 1.0, 2.0).normalized()) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        PriorWeights(0.0, 0.0, 0.0).normalized()


def test_shrink_regresses_hard_early_trusts_data_late() -> None:
    prior, observed = 0.10, -0.05
    assert shrink_to_prior(prior, observed, 0) == pytest.approx(prior)  # no data → prior
    late = shrink_to_prior(prior, observed, 100)
    assert late == pytest.approx(observed, abs=0.01)  # lots of data → observed


def test_shrink_is_monotone_in_games() -> None:
    prior, observed = 0.0, 0.20
    values = [shrink_to_prior(prior, observed, n) for n in (0, 2, 6, 20)]
    assert values == sorted(values)  # more games → closer to the higher observed


def test_shrink_strength_controls_regression() -> None:
    weak = shrink_to_prior(0.0, 0.2, 6, prior_strength=2.0)
    strong = shrink_to_prior(0.0, 0.2, 6, prior_strength=20.0)
    assert weak > strong  # a stronger prior holds the estimate down harder


def test_shrink_series_aligns_and_defaults() -> None:
    prior = pd.Series({"A": 0.1, "B": -0.1})
    observed = pd.Series({"A": 0.3})  # B unobserved
    n_games = pd.Series({"A": 6.0})
    out = shrink_series(prior, observed, n_games, prior_strength=6.0)
    assert out["A"] == pytest.approx(0.5 * 0.1 + 0.5 * 0.3)  # 6/(6+6)=0.5
    assert out["B"] == pytest.approx(-0.1)  # no data → stays at prior


def test_scores_recency_weights_favor_recent_games() -> None:
    import pandas as pd
    from velocity.features.scores import fit_scores_ratings, scores_recency_weights

    games = pd.DataFrame({
        "season": [2024, 2024, 2025, 2025],
        "week": [1, 10, 1, 10],
        "home_team": ["A", "A", "A", "A"],
        "away_team": ["B", "B", "B", "B"],
        "home_score": [10.0, 10.0, 30.0, 30.0],  # A's offense transformed in 2025
        "away_score": [20.0, 20.0, 14.0, 14.0],
    })
    w = scores_recency_weights(games, 17.0)
    assert w.iloc[3] == 1.0  # newest game
    assert w.iloc[0] < w.iloc[2] < w.iloc[3]  # strictly fresher = heavier

    flat = fit_scores_ratings(games, ridge_lambda=1.0)
    weighted = fit_scores_ratings(games, ridge_lambda=1.0, weights=w)
    # The recency fit believes the 2025 version of the matchup (30-14) more
    # than the flat fit, which averages the eras.
    # A hosts in every fixture game, so evaluate the at-home expectation.
    flat_mu = flat.expected_points("A", "B", at_home=True)
    weighted_mu = weighted.expected_points("A", "B", at_home=True)
    assert weighted_mu > flat_mu
    assert weighted_mu > 24.0  # pulled toward the 2025 scoring level (30)

    bad = w.copy()
    bad.iloc[0] = float("nan")
    import pytest
    with pytest.raises(ValueError, match="finite"):
        fit_scores_ratings(games, weights=bad)
