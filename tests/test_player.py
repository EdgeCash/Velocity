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
