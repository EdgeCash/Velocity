"""Monte Carlo engine — determinism, calibration, and pricing consistency."""

from __future__ import annotations

import numpy as np
import pytest
from velocity.models.simulate import GameSim, SimConfig, simulate_game
from velocity.util.seed import make_rng


def test_same_seed_same_samples() -> None:
    cfg = SimConfig(n_sims=5_000)
    a = simulate_game(6.0, 45.0, make_rng(), cfg)
    b = simulate_game(6.0, 45.0, make_rng(), cfg)
    assert np.array_equal(a.home_score, b.home_score)
    assert np.array_equal(a.away_score, b.away_score)


def test_different_seed_differs() -> None:
    cfg = SimConfig(n_sims=5_000)
    a = simulate_game(6.0, 45.0, make_rng(1), cfg)
    b = simulate_game(6.0, 45.0, make_rng(2), cfg)
    assert not np.array_equal(a.margin, b.margin)


def test_marginal_calibration() -> None:
    # Sample means/SDs must track the configured moments (large-sample, no rounding).
    cfg = SimConfig(n_sims=200_000, sd_margin=13.5, sd_total=10.5, round_scores=False)
    sim = simulate_game(6.0, 45.0, make_rng(), cfg)
    assert sim.margin.mean() == pytest.approx(6.0, abs=0.15)
    assert sim.total.mean() == pytest.approx(45.0, abs=0.15)
    assert sim.margin.std() == pytest.approx(13.5, rel=0.03)
    assert sim.total.std() == pytest.approx(10.5, rel=0.03)


def test_scores_are_nonnegative_and_integer_when_rounded() -> None:
    cfg = SimConfig(n_sims=10_000, round_scores=True)
    sim = simulate_game(0.0, 44.0, make_rng(), cfg)
    assert (sim.home_score >= 0).all()
    assert (sim.away_score >= 0).all()
    assert np.array_equal(sim.home_score, np.rint(sim.home_score))


def test_fair_lines_price_to_coin_flip() -> None:
    cfg = SimConfig(n_sims=100_000)
    sim = simulate_game(6.5, 45.0, make_rng(), cfg)
    # By construction the fair spread/total sit at the ~50/50 point.
    assert sim.prob_home_cover(sim.fair_spread()) == pytest.approx(0.5, abs=0.03)
    assert sim.prob_over(sim.fair_total()) == pytest.approx(0.5, abs=0.03)


def test_win_prob_moves_with_expected_margin() -> None:
    cfg = SimConfig(n_sims=100_000)
    favored = simulate_game(10.0, 45.0, make_rng(), cfg).p_home_win()
    pickem = simulate_game(0.0, 45.0, make_rng(), cfg).p_home_win()
    dog = simulate_game(-10.0, 45.0, make_rng(), cfg).p_home_win()
    assert favored > pickem > dog
    assert pickem == pytest.approx(0.5, abs=0.03)


def test_bigger_spread_lowers_cover_prob() -> None:
    cfg = SimConfig(n_sims=100_000)
    sim = simulate_game(7.0, 45.0, make_rng(), cfg)
    # Laying more points must never make covering more likely (monotonic).
    assert sim.prob_home_cover(-3.0) > sim.prob_home_cover(-7.0)
    assert sim.prob_home_cover(-7.0) > sim.prob_home_cover(-10.0)


def test_derived_arrays_are_consistent() -> None:
    sim = GameSim(home_score=np.array([24.0, 17.0]), away_score=np.array([20.0, 21.0]))
    assert np.array_equal(sim.margin, np.array([4.0, -4.0]))
    assert np.array_equal(sim.total, np.array([44.0, 38.0]))


def test_invalid_config_rejected() -> None:
    with pytest.raises(ValueError):
        SimConfig(n_sims=0)
    with pytest.raises(ValueError):
        SimConfig(sd_margin=-1.0)
    with pytest.raises(ValueError):
        SimConfig(margin_total_corr=1.5)
