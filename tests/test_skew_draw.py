"""The right-skewed draw — the one asymmetry football actually has.

Total residuals against the market close carry a skew of +0.33 in the NFL and
+0.34 in college; the spreads carry +0.10 and +0.01. The sim was symmetric on
both, and the ladder gate measured what that costs: at 4.5 points out it
overstates the over tail by 0.028 and understates the under by 0.008
(docs/MODEL_LAB.md, the gate-reference round).
"""

from __future__ import annotations

import numpy as np
import pytest
from velocity.models.simulate import SimConfig, simulate_game
from velocity.models.skew import (
    MAX_EPSILON,
    fit_epsilon,
    skew_draw,
    skewness,
    solve_epsilon,
    standard_moments,
)

NFL_TOTAL_SKEW = 0.33


def _sample(epsilon: float, n: int = 2_000_000, seed: int = 7) -> np.ndarray:
    z = np.random.default_rng(seed).standard_normal(n)
    return skew_draw(z, epsilon)


# --- the closed form ------------------------------------------------------

def test_the_closed_form_moments_are_the_draw_s_own() -> None:
    """Everything else rests on these, and nothing else would catch them.

    The standardization uses these rather than the sample's mean and sd, so
    an error here would not show up as a crash — it would show up as a
    distribution quietly off its stated sd at some other ``n_sims``.
    """
    z = np.random.default_rng(3).standard_normal(2_000_000)
    for epsilon in (0.0, 0.1, 0.2, 0.35, -0.25):
        mean, sd, skew = standard_moments(epsilon)
        raw = z * np.cosh(epsilon) + np.sqrt(1.0 + z * z) * np.sinh(epsilon)
        assert mean == pytest.approx(float(raw.mean()), abs=0.003)
        assert sd == pytest.approx(float(raw.std()), abs=0.003)
        measured = float(((raw - raw.mean()) ** 3).mean() / raw.std() ** 3)
        assert skew == pytest.approx(measured, abs=0.005)


def test_zero_is_the_symmetric_draw_exactly() -> None:
    assert standard_moments(0.0) == (0.0, pytest.approx(1.0), 0.0)
    z = np.random.default_rng(5).standard_normal(1000)
    assert np.array_equal(skew_draw(z, 0.0), z)


def test_skew_is_monotone_through_zero() -> None:
    """What makes a bisection the right solver and not a lucky one."""
    values = [skewness(float(e)) for e in np.linspace(-1.0, 1.0, 41)]
    assert all(b > a for a, b in zip(values[:-1], values[1:], strict=True))
    assert skewness(-0.3) < 0 < skewness(0.3)


# --- the solve ------------------------------------------------------------

def test_the_solve_hits_the_skew_it_was_asked_for() -> None:
    for target in (0.10, NFL_TOTAL_SKEW, 0.34, 0.8, -0.4):
        assert skewness(solve_epsilon(target)) == pytest.approx(target, abs=1e-6)


def test_a_skew_past_the_family_is_clamped_not_chased() -> None:
    """A league wanting more than this can express needs a different family."""
    assert solve_epsilon(50.0) == MAX_EPSILON
    assert solve_epsilon(-50.0) == -MAX_EPSILON
    assert solve_epsilon(0.0) == 0.0
    assert solve_epsilon(float("nan")) == 0.0


def test_the_fit_recovers_the_skew_of_its_own_sample() -> None:
    rng = np.random.default_rng(11)
    for target in (0.0, 0.2, NFL_TOTAL_SKEW):
        truth = solve_epsilon(target)
        sample = skew_draw(rng.standard_normal(400_000), truth) * 13.6 + 45.0
        assert skewness(fit_epsilon(sample)) == pytest.approx(target, abs=0.02)


def test_the_fit_declines_what_it_cannot_measure() -> None:
    assert fit_epsilon(np.array([])) == 0.0
    assert fit_epsilon(np.array([1.0, 2.0])) == 0.0
    assert fit_epsilon(np.full(500, 7.0)) == 0.0, "no spread, so no shape"
    # Non-finite rows are dropped rather than poisoning the moments.
    clean = np.random.default_rng(2).standard_normal(50_000)
    dirty = np.concatenate([clean, [np.nan, np.inf]])
    assert fit_epsilon(dirty) == pytest.approx(fit_epsilon(clean), abs=1e-9)


# --- the draw -------------------------------------------------------------

def test_the_draw_is_standardized_whatever_the_skew() -> None:
    """``sd_total`` has to keep meaning what it says."""
    for target in (0.2, NFL_TOTAL_SKEW, 0.6):
        drawn = _sample(solve_epsilon(target))
        assert drawn.mean() == pytest.approx(0.0, abs=0.004)
        assert drawn.std() == pytest.approx(1.0, abs=0.004)
        measured = float(((drawn - drawn.mean()) ** 3).mean() / drawn.std() ** 3)
        assert measured == pytest.approx(target, abs=0.02)


def test_it_reorders_nothing() -> None:
    """Monotone, so a jointly-drawn margin and total stay sensibly paired."""
    z = np.sort(np.random.default_rng(4).standard_normal(20_000))
    drawn = skew_draw(z, 0.35)
    assert np.all(np.diff(drawn) > 0)


def test_the_standardization_does_not_depend_on_how_many_draws() -> None:
    """A sample-standardized draw would, and would stop being reproducible."""
    epsilon = solve_epsilon(NFL_TOTAL_SKEW)
    small = _sample(epsilon, n=5_000, seed=9)
    big = _sample(epsilon, n=500_000, seed=9)
    assert np.allclose(small, big[:5_000])


# --- in the sim -----------------------------------------------------------

def _sim(total_skew: float, n: int = 400_000):
    return simulate_game(2.0, 45.0, np.random.default_rng(7), SimConfig(
        n_sims=n, sd_margin=13.0, sd_total=13.6, total_skew=total_skew))


def test_the_sim_gains_the_skew_and_keeps_everything_else() -> None:
    flat, skewed = _sim(0.0), _sim(solve_epsilon(NFL_TOTAL_SKEW))
    def skew_of(x): return float(((x - x.mean()) ** 3).mean() / x.std() ** 3)
    assert skew_of(flat.total) == pytest.approx(0.0, abs=0.08)
    assert 0.25 < skew_of(skewed.total) < 0.45
    # The total's own centre and spread are where they were.
    assert skewed.total.mean() == pytest.approx(float(flat.total.mean()), abs=0.1)
    assert skewed.total.std() == pytest.approx(float(flat.total.std()), abs=0.15)


def test_the_margin_is_not_touched() -> None:
    """Spread residuals are near-symmetric (+0.10 NFL, +0.01 college); this
    is a totals fix and must not move the ladder it is not aimed at."""
    flat, skewed = _sim(0.0), _sim(solve_epsilon(0.5))
    assert skewed.margin.mean() == pytest.approx(float(flat.margin.mean()), abs=0.05)
    assert skewed.margin.std() == pytest.approx(float(flat.margin.std()), abs=0.05)
    assert skewed.p_home_win() == pytest.approx(flat.p_home_win(), abs=0.005)


def test_the_skew_moves_the_tails_the_way_the_gate_measured() -> None:
    """The defect this exists for: too much over, too little under, near the line.

    A right-skewed total shifts its bulk left and lengthens its right tail, so
    a moderate over threshold gets LESS mass and the matching under threshold
    gets more — which is the sign of the miss the ladder gate found.
    """
    flat, skewed = _sim(0.0), _sim(solve_epsilon(NFL_TOTAL_SKEW))
    assert skewed.prob_over(45.0 + 4.5) < flat.prob_over(45.0 + 4.5)
    assert (1.0 - skewed.prob_over(45.0 - 4.5)) > (1.0 - flat.prob_over(45.0 - 4.5))
    # And far out the right tail is fatter, which is what skew means.
    assert skewed.prob_over(45.0 + 30.0) > flat.prob_over(45.0 + 30.0)


def test_a_residual_pool_is_not_skewed_twice() -> None:
    """A pool already carries the league's own shape, skew included."""
    from velocity.models.residuals import ResidualPool
    rng = np.random.default_rng(6)
    pool = ResidualPool(margin_z=rng.standard_normal(4000),
                        total_z=rng.standard_normal(4000),
                        sd_margin=13.0, sd_total=13.6)
    config = SimConfig(n_sims=50_000, sd_margin=13.0, sd_total=13.6,
                       total_skew=solve_epsilon(0.6), residuals=pool)
    with_pool = simulate_game(2.0, 45.0, np.random.default_rng(7), config)
    from dataclasses import replace
    without = simulate_game(2.0, 45.0, np.random.default_rng(7),
                            replace(config, total_skew=0.0))
    assert np.array_equal(with_pool.total, without.total)
