"""Player prop models — distributions, decomposition, and correlated simulation."""

from __future__ import annotations

import numpy as np
import pytest
from velocity.features.player import redistribute_shares
from velocity.models.props import NegativeBinomial, expected_stat, simulate_props
from velocity.util.seed import make_rng

_SHARES = {"WR1": 0.28, "WR2": 0.20, "TE": 0.15, "RB": 0.12}
_CATCH = {"WR1": 0.65, "WR2": 0.62, "TE": 0.70, "RB": 0.75}
_YPR = {"WR1": 13.0, "WR2": 11.0, "TE": 10.0, "RB": 7.0}


def test_expected_stat_decomposition() -> None:
    # 35 pass attempts × 0.28 target share × 13 yards per target.
    assert expected_stat(35.0, 0.28, 13.0) == pytest.approx(127.4)


def test_negative_binomial_moments() -> None:
    nb = NegativeBinomial(mean=5.0, dispersion=8.0)
    assert nb.mean == 5.0
    assert nb.variance == pytest.approx(5.0 + 25.0 / 8.0)  # mean + mean^2/r


def test_negative_binomial_pmf_sums_to_one() -> None:
    nb = NegativeBinomial(mean=6.0, dispersion=10.0)
    assert sum(nb.pmf(k) for k in range(200)) == pytest.approx(1.0, abs=1e-9)


def test_negative_binomial_over_under_complementary() -> None:
    nb = NegativeBinomial(mean=5.0, dispersion=8.0)
    # A half-point line has no push, so over and under partition the mass.
    assert nb.prob_over(4.5) + nb.prob_under(4.5) == pytest.approx(1.0)
    assert nb.prob_over(4.5) > nb.prob_over(6.5)  # monotone decreasing in the line


def test_negative_binomial_approaches_poisson() -> None:
    # Huge dispersion → variance collapses toward the Poisson mean.
    nb = NegativeBinomial(mean=4.0, dispersion=1e6)
    assert nb.variance == pytest.approx(4.0, rel=1e-3)


def test_negative_binomial_rejects_bad_params() -> None:
    with pytest.raises(ValueError):
        NegativeBinomial(mean=-1.0)
    with pytest.raises(ValueError):
        NegativeBinomial(mean=5.0, dispersion=0.0)


def test_simulation_is_deterministic() -> None:
    a = simulate_props(35.0, _SHARES, _CATCH, _YPR, make_rng(), n_sims=5_000)
    b = simulate_props(35.0, _SHARES, _CATCH, _YPR, make_rng(), n_sims=5_000)
    assert np.array_equal(a.receptions["WR1"], b.receptions["WR1"])
    assert np.array_equal(a.receiving_yards["WR1"], b.receiving_yards["WR1"])


def test_simulation_mean_matches_decomposition() -> None:
    sim = simulate_props(35.0, _SHARES, _CATCH, _YPR, make_rng(), n_sims=60_000)
    # E[receptions] ≈ volume × share × catch_rate.
    expected = expected_stat(35.0, _SHARES["WR1"], _CATCH["WR1"])
    assert sim.mean("WR1", "receptions") == pytest.approx(expected, rel=0.05)


def test_props_are_correlated() -> None:
    sim = simulate_props(35.0, _SHARES, _CATCH, _YPR, make_rng(), n_sims=40_000)
    # Shared game volume makes a player's receptions rise with team volume,
    # and the passing game move with the WR — the point of in-sim correlation.
    assert np.corrcoef(sim.volume, sim.receptions["WR1"])[0, 1] > 0.3
    qb_yards = sim.team_pass_yards()
    assert np.corrcoef(qb_yards, sim.receiving_yards["WR1"])[0, 1] > 0.5


def test_prob_over_is_a_probability() -> None:
    sim = simulate_props(35.0, _SHARES, _CATCH, _YPR, make_rng(), n_sims=20_000)
    p = sim.prob_over("WR1", "receiving_yards", 59.5)
    assert 0.0 <= p <= 1.0


def test_scratching_a_starter_reprices_teammates() -> None:
    import pandas as pd

    full = simulate_props(35.0, _SHARES, _CATCH, _YPR, make_rng(), n_sims=40_000)
    repriced = redistribute_shares(pd.Series(_SHARES), ["WR1"]).to_dict()
    injured = simulate_props(35.0, repriced, _CATCH, _YPR, make_rng(), n_sims=40_000)
    # WR1 out → WR2 absorbs targets and projects for more receptions.
    assert injured.mean("WR2", "receptions") > full.mean("WR2", "receptions")
    assert injured.mean("WR1", "receptions") == pytest.approx(0.0, abs=0.05)
