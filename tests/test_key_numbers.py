"""Football's margin lattice, measured and reapplied.

The shipped sim puts 5.4% of NFL margins on 3 where football puts 14.8%, and
the possession sampler that fixes that is more dispersed than NFL football
is (``docs/MODEL_LAB.md``, the drive round). This is the third option: take
the lattice from the data, leave the dispersion alone.

Reference figures are the 3,044 NFL walk-forward games from 2015 on. Against
the shipped normal over those games, a margin of 3 is short by 9.4 points of
probability while 2 and 4 together are long by only 1.6 — which is why the
correction has to be a weight on every absolute margin and not a local snap.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from velocity.models.keynumbers import (
    DEFAULT_MAX_ABS,
    LATTICE_COLUMNS,
    LATTICE_FILE,
    LatticeWeights,
    fit_lattice_from_residuals,
    fit_lattice_weights,
    load_lattice_weights,
    rounded_normal_mass,
    simulated_margin_mass,
)
from velocity.models.simulate import SimConfig, simulate_game

SD_MARGIN = 13.0
CONFIG = SimConfig(n_sims=60_000, sd_margin=SD_MARGIN, sd_total=13.6)


def _games(n: int = 3000, seed: int = 11) -> np.ndarray:
    """Projected margins with a realistic spread of favourites."""
    return np.random.default_rng(seed).normal(0.0, 5.5, size=n)


# The lattice table the pipeline actually produces: weights fitted by
# `fit_lattice_weights` on the NFL walk-forward's pre-2019 seasons, shrunk by
# the default prior. The RAW 2015+ ratios are more extreme (3 at 2.73 rather
# than 2.29, 12 at 0.48 rather than 0.59), and an earlier draft of these tests
# used those — which exaggerated every invariance check, moving the moneyline
# at a 14-point favourite ten times as far as the real table does. The
# shrunken table is the one that ever gets applied, so it is the one to test
# against; the unshrunk one is a measurement, not a correction.
NFL_LATTICE = np.array([
    0.42, 0.82, 0.79, 2.29, 0.94, 0.77, 1.25, 1.65, 0.84, 0.48, 1.12,
    0.72, 0.59, 0.81, 1.36, 0.68, 0.90, 1.02, 0.90, 0.66, 0.98, 1.15,
])


def _lattice_actuals(mu: np.ndarray, seed: int = 4) -> np.ndarray:
    """Finished margins carrying football's own lattice.

    Drawn from the same rounded normal the sim draws from, then resampled in
    proportion to the measured ratios above — so the only thing that
    separates these from the sim's own output is the lattice, which is
    exactly what the fit has to recover.
    """
    rng = np.random.default_rng(seed)
    margin = np.rint(mu + rng.normal(0.0, SD_MARGIN, size=mu.size))
    weight = NFL_LATTICE[np.clip(np.abs(margin).astype(int), 0, NFL_LATTICE.size - 1)]
    picks = rng.choice(margin.size, size=margin.size, p=weight / weight.sum())
    return margin[picks]


# --- the reference mass ----------------------------------------------------

def test_the_closed_form_matches_what_the_sim_actually_draws() -> None:
    """The weights are a ratio, so a biased denominator biases every one."""
    mu = _games(n=200)
    closed = rounded_normal_mass(mu, SD_MARGIN)
    drawn = simulated_margin_mass(
        mu, np.full(mu.size, 45.0),
        lambda m, t, r: simulate_game(m, t, r, CONFIG),
        np.random.default_rng(3), sample=None)
    assert np.allclose(closed, drawn, atol=0.004)


def test_the_reference_mass_is_a_distribution() -> None:
    mass = rounded_normal_mass(_games(), SD_MARGIN)
    assert mass.size == DEFAULT_MAX_ABS + 1
    assert mass.sum() == pytest.approx(1.0)
    assert np.all(mass >= 0)
    # The last bin is the whole tail, not a single margin, so it is fat.
    assert mass[DEFAULT_MAX_ABS] > mass[DEFAULT_MAX_ABS - 1]


def test_the_simulated_reference_subsamples_deterministically() -> None:
    """Two runs of the fit must agree, or a promoted config is not reproducible."""
    mu, tot = _games(n=4000), np.full(4000, 45.0)
    def measure() -> np.ndarray:
        return simulated_margin_mass(
            mu, tot, lambda m, t, r: simulate_game(m, t, r, CONFIG),
            np.random.default_rng(9), sample=200)

    a, b = measure(), measure()
    assert np.array_equal(a, b)


# --- the fit ---------------------------------------------------------------

def test_the_weights_find_the_lattice_they_were_shown() -> None:
    mu = _games()
    # The fixture's table carries a corrected tail (1.15), so the recovery is
    # checked with the tail corrected too; the default leaves it at 1.
    weights = fit_lattice_weights(_lattice_actuals(mu), rounded_normal_mass(mu, SD_MARGIN),
                                  correct_tail=True)
    # The two big ones come back clearly; 6 and 14 are smaller corrections
    # and, shrunk twice over on three thousand games, come back smaller still.
    for key in (3, 7):
        assert weights.weights[key] > 1.2, key
    for key in (6, 10, 14):
        assert weights.weights[key] > 1.0, key
    # And the margins football genuinely avoids come back suppressed. Only
    # the bins carrying real signal are checked: bin 4's true weight is 0.94,
    # which is no correction at all, so an assertion there would be an
    # assertion about sampling noise.
    for plain in (2, 5, 9, 12, 15):
        assert weights.weights[plain] < 1.0, plain
    # The recovered table is the one it was shown, bin for bin.
    assert np.corrcoef(weights.weights, NFL_LATTICE)[0, 1] > 0.95


def test_a_sim_that_is_already_right_is_left_alone() -> None:
    """No lattice in the data means no correction, not a correction of zero."""
    mu = _games()
    actual = np.rint(mu + np.random.default_rng(2).normal(0.0, SD_MARGIN, mu.size))
    weights = fit_lattice_weights(actual, rounded_normal_mass(mu, SD_MARGIN))
    assert np.allclose(weights.weights, 1.0, atol=0.25)


def test_a_thin_bin_is_shrunk_toward_no_correction() -> None:
    """The whole defence against overfitting a twenty-two bin table.

    Two bins are wrong by the same RATIO; the one with the evidence moves and
    the one without it barely does.
    """
    mass = np.full(6, 1.0 / 6.0)
    # 3,000 games: bin 1 is four times over-represented, bin 5 is too — but
    # bin 5's reference mass is tiny, so its count is tiny with it.
    mass = np.array([0.30, 0.30, 0.30, 0.08, 0.015, 0.005])
    counts = np.concatenate([
        np.zeros(int(3000 * 0.30)), np.ones(int(3000 * 0.30 * 2.0)),
        np.full(int(3000 * 0.30), 2), np.full(int(3000 * 0.08), 3),
        np.full(int(3000 * 0.015 * 2.0), 4), np.full(int(3000 * 0.005 * 2.0), 5),
    ])
    weights = fit_lattice_weights(counts, mass / mass.sum(), correct_tail=True)
    assert weights.weights[1] > weights.weights[5], (
        "the well-evidenced bin should move further than the thin one")


def test_the_tail_bin_is_left_alone_unless_asked() -> None:
    """The tail round: the last bin's ratio is dispersion, not lattice.

    Football's residuals are heavier-tailed than the normal, so the ratio out
    there reads above 1 — and applying it moved a college favourite's blowout
    mass enough to close eight ladder sides. The default leaves it at 1; the
    key numbers inside are untouched either way.
    """
    mu = _games()
    actual = _lattice_actuals(mu)
    plain = fit_lattice_weights(actual, rounded_normal_mass(mu, SD_MARGIN))
    corrected = fit_lattice_weights(actual, rounded_normal_mass(mu, SD_MARGIN),
                                    correct_tail=True)
    assert plain.weights[-1] == 1.0
    assert corrected.weights[-1] != 1.0
    assert np.array_equal(plain.weights[:-1], corrected.weights[:-1])
    residuals = pd.DataFrame({"mu_margin": mu, "resid_margin": actual - mu})
    assert fit_lattice_from_residuals(residuals, SD_MARGIN) == plain
    assert fit_lattice_from_residuals(residuals, SD_MARGIN, correct_tail=True) == corrected


def test_the_fit_refuses_an_empty_training_set() -> None:
    with pytest.raises(ValueError):
        fit_lattice_weights(np.array([]), rounded_normal_mass(_games(), SD_MARGIN))


def test_weights_reject_what_cannot_be_a_correction() -> None:
    for bad in (np.array([1.0]), np.array([[1.0, 1.0]]),
                np.array([1.0, -0.5]), np.array([1.0, np.nan])):
        with pytest.raises(ValueError):
            LatticeWeights(bad)


# --- applying it -----------------------------------------------------------

def _applied(mu_margin: float = 0.0, seed: int = 7, **kw: float):
    mu = _games()
    weights = fit_lattice_weights(_lattice_actuals(mu), rounded_normal_mass(mu, SD_MARGIN))
    rng = np.random.default_rng(seed)
    base = simulate_game(mu_margin, 45.0, rng, CONFIG)
    return base, weights.apply(base, rng)


def test_the_lattice_lands_without_moving_the_spread() -> None:
    """The whole point: the drive sim bought its key numbers with dispersion.

    P(3) roughly doubles while the margin's standard deviation moves by under
    a fifth of a point, and the total is not touched at all.
    """
    base, tilted = _applied()
    def mass(sim, k): return float((np.abs(sim.margin) == k).mean())
    assert mass(tilted, 3) > 1.8 * mass(base, 3)
    assert mass(tilted, 7) > 1.3 * mass(base, 7)
    assert tilted.margin.std() == pytest.approx(float(base.margin.std()), abs=0.4)
    assert tilted.total.std() == pytest.approx(float(base.total.std()), abs=0.4)


def test_the_correction_reshapes_without_rerating() -> None:
    """A correction that moved the game's rating would be repricing it.

    The invariant that matters is the moneyline: reshaping WHERE the margin
    lands must not change how often the home team wins. The mean is allowed
    to drift a little — mass moving between football's lattice and the gaps
    around it is not symmetric — and the median is allowed to step a whole
    point, because a distribution with a lump on 3 and another on 7 has a
    median that sits on one of them.
    """
    for mu_margin in (0.0, 3.0, 7.0, 14.0):
        base, tilted = _applied(mu_margin)
        assert tilted.p_home_win() == pytest.approx(base.p_home_win(), abs=0.01)
        assert tilted.margin.mean() == pytest.approx(
            float(base.margin.mean()), abs=0.6)
        assert abs(tilted.fair_spread() - base.fair_spread()) <= 1.0
        # The total is not the correction's business at all.
        assert tilted.total.mean() == pytest.approx(
            float(base.total.mean()), abs=0.3)
        assert tilted.fair_total() == pytest.approx(base.fair_total(), abs=1.0)


def test_covering_a_key_number_gets_harder_which_is_the_whole_point() -> None:
    """This is not a cosmetic change to a histogram — it moves the rungs.

    A seven-point favourite lands on exactly 7 far more often than a normal
    says, so laying -7.5 is worth less than the shipped sim prices it. The
    measured move on the real NFL table is about three points of probability
    at that rung, which is the size of a whole market's edge.
    """
    base, tilted = _applied(7.0)
    assert tilted.prob_home_cover(-7.5) < base.prob_home_cover(-7.5) - 0.015
    # And the mass has to have gone ONTO the number, not past it.
    assert float((tilted.margin == 7).mean()) > float((base.margin == 7).mean())


def test_it_never_invents_a_margin_the_sim_did_not_draw() -> None:
    """Resampling, not smoothing: every output is one of the inputs."""
    base, tilted = _applied()
    assert set(np.unique(tilted.margin)).issubset(set(np.unique(base.margin)))
    assert set(np.unique(tilted.total)).issubset(set(np.unique(base.total)))
    assert tilted.n_sims == base.n_sims


def test_the_scores_stay_paired() -> None:
    """Whole (home, away) draws are carried, so no score pair is fabricated."""
    base, tilted = _applied()
    drawn = {(h, a) for h, a in zip(base.home_score, base.away_score, strict=True)}
    for pair in zip(tilted.home_score, tilted.away_score, strict=True):
        assert pair in drawn


def test_applying_it_is_deterministic() -> None:
    assert np.array_equal(_applied(seed=7)[1].margin, _applied(seed=7)[1].margin)
    assert not np.array_equal(_applied(seed=7)[1].margin, _applied(seed=8)[1].margin)


def test_weights_with_no_mass_to_move_leave_the_sim_alone() -> None:
    """The honest fallback: a correction with nowhere to put mass does not invent one."""
    rng = np.random.default_rng(7)
    base = simulate_game(0.0, 45.0, rng, CONFIG)
    same = LatticeWeights(np.zeros(DEFAULT_MAX_ABS + 1)).apply(base, rng)
    assert np.array_equal(same.home_score, base.home_score)
    assert np.array_equal(same.away_score, base.away_score)


# --- the promotion: the sim reads a banked table -----------------------------

def _bank_weights() -> LatticeWeights:
    mu = _games()
    return fit_lattice_weights(_lattice_actuals(mu), rounded_normal_mass(mu, SD_MARGIN))


def test_the_config_path_is_the_overlay_exactly() -> None:
    """``SimConfig.lattice`` is the lab's overlay, draw for draw.

    The gate (scripts/sim_lab.py) scored ``normal+keys`` as the base sim's
    draws resampled under the same generator; the promoted path has to be
    that sim and not a cousin of it, or the gate's verdict is about
    something the slate does not run.
    """
    weights = _bank_weights()
    promoted = replace(CONFIG, lattice=weights)
    a = simulate_game(3.0, 45.0, np.random.default_rng(7), promoted)
    rng = np.random.default_rng(7)
    b = weights.apply(simulate_game(3.0, 45.0, rng, CONFIG), rng)
    assert np.array_equal(a.home_score, b.home_score)
    assert np.array_equal(a.away_score, b.away_score)
    # And it is a correction, not a re-rating: the key number lands. (The
    # fixture's weight at 3 is 2.29 before the resample's own normalization,
    # which for a favourite sits above 1 — so the mass roughly doubles.)
    plain = simulate_game(3.0, 45.0, np.random.default_rng(7), CONFIG)
    assert np.mean(a.margin == 3) > 1.7 * np.mean(plain.margin == 3)
    assert abs(a.margin.mean() - plain.margin.mean()) < 0.5


def test_a_lattice_beside_a_residual_pool_is_refused() -> None:
    """The pool already carries the league's lattice; correcting it twice is a bug."""
    from velocity.models.residuals import ResidualPool

    rng = np.random.default_rng(2)
    pool = ResidualPool.from_residuals(rng.normal(size=200), rng.normal(size=200))
    with pytest.raises(ValueError, match="already carries"):
        replace(CONFIG, lattice=_bank_weights(), residuals=pool)


def test_the_banked_table_round_trips_and_loads(tmp_path: Path) -> None:
    weights = _bank_weights()
    frame = weights.to_frame()
    assert list(frame.columns) == LATTICE_COLUMNS
    assert LatticeWeights.from_frame(frame.sample(frac=1.0, random_state=1)) == weights
    # No bank, no correction — and never an invented one.
    assert load_lattice_weights("nfl", tmp_path) is None
    (tmp_path / "nfl").mkdir()
    frame.to_parquet(tmp_path / "nfl" / LATTICE_FILE, index=False)
    loaded = load_lattice_weights("nfl", tmp_path)
    assert loaded == weights
    # A table with a hole in it is not a table.
    with pytest.raises(ValueError, match="every absolute margin"):
        LatticeWeights.from_frame(frame[frame["abs_margin"] != 5])


def test_the_bank_fit_is_the_lab_fit() -> None:
    """One fit for the bank, the gate and the re-check."""
    mu = _games()
    actual = _lattice_actuals(mu)
    residuals = pd.DataFrame({"mu_margin": mu, "resid_margin": actual - mu})
    assert fit_lattice_from_residuals(residuals, SD_MARGIN) == fit_lattice_weights(
        actual, rounded_normal_mass(mu, SD_MARGIN))


@pytest.mark.parametrize("league, sd_margin", [("nfl", 13.0), ("ncaaf", 16.2)])
def test_the_committed_lattice_is_fresh(league: str, sd_margin: float) -> None:
    """``datasets/{league}/lattice.parquet`` is what the residual bank says it is.

    ``scripts/build_lattice.py`` is the generator; a residual bank rebuilt
    without its lattice fails here rather than shipping a stale correction.
    The sds are the promoted constants the script fits against.
    """
    from velocity.models.residuals import load_residual_frame

    residuals = load_residual_frame(league)
    banked = load_lattice_weights(league)
    if residuals is None or banked is None:
        pytest.skip(f"no {league} banks committed")
    fresh = fit_lattice_from_residuals(residuals, sd_margin)
    assert np.allclose(banked.weights, fresh.weights, atol=1e-9)


def test_the_committed_nfl_lattice_puts_the_key_numbers_where_football_does() -> None:
    banked = load_lattice_weights("nfl")
    if banked is None:
        pytest.skip("no NFL lattice committed")
    w = banked.weights
    assert w[3] > 2.0 and w[7] > 1.5 and w[14] > 1.2 and w[10] > 1.0
    # 4 is not a key number, 9 and 12 are where football rarely lands, and
    # the tail is left alone.
    assert w[4] < 1.0 and w[9] < 0.6 and w[12] < 0.7
    assert w[-1] == 1.0
