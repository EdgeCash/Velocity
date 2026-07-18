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
