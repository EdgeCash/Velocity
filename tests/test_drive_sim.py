"""The possession sampler — points arriving in sevens and threes.

The shipped sim draws a margin from a normal and rounds it, which puts a
three-point game and a four-point game at nearly the same probability. Since
2015 the NFL has landed on 3 in 14.8% of its games and on 4 in 4.6%. These
pin the structure that produces that asymmetry, the two mechanisms bolted
either side of it, and the arithmetic of the fit.

Reference figures are from the 3,045 NFL games in ``datasets/nfl/games.parquet``
from 2015 on, and from the walk-forward residuals over the same window
(margin sd 13.01, total sd 13.54).
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from velocity.models.drive import (
    EXTRA_POINT_RATE,
    FIELD_GOAL_POINTS,
    TOUCHDOWN_VALUE,
    DriveConfig,
    drive_probabilities,
    fit_drive_config,
    implied_score_sd,
    simulate_drives,
)

NFL = DriveConfig(drives=11.0, fg_to_td=0.65)
LEAGUE_TOTAL = 45.1  # the walk-forward's mean projected total


def _sim(mu_margin: float = 2.0, mu_total: float = LEAGUE_TOTAL,
         seed: int = 7, n: int = 120_000, **kw: object):
    config = replace(NFL, n_sims=n, **kw)  # type: ignore[arg-type]
    return simulate_drives(mu_margin, mu_total, np.random.default_rng(seed), config)


# --- the scoring-rate solve ------------------------------------------------

def test_the_rates_solve_back_to_the_points_they_were_asked_for() -> None:
    """The whole point of solving rather than assuming: μ comes back out."""
    for mu_team in (10.0, 17.0, 22.55, 31.0):
        p_td, p_fg = drive_probabilities(mu_team, NFL)
        per_drive = TOUCHDOWN_VALUE * p_td + FIELD_GOAL_POINTS * p_fg
        assert per_drive * NFL.drives == pytest.approx(mu_team, rel=1e-9)


def test_the_field_goal_mix_is_held_while_the_rate_moves() -> None:
    """A better offense kicks MORE field goals, not fewer."""
    weak = drive_probabilities(15.0, NFL)
    strong = drive_probabilities(32.0, NFL)
    assert strong[0] > weak[0] and strong[1] > weak[1]
    for p_td, p_fg in (weak, strong):
        assert float(p_fg) / float(p_td) == pytest.approx(NFL.fg_to_td)


def test_an_impossible_matchup_is_clamped_not_sampled() -> None:
    """A rate above one is not a probability; the tail loses a little μ instead."""
    p_td, p_fg = drive_probabilities(200.0, NFL)
    assert 0.0 <= p_td <= 1.0 and 0.0 <= p_fg <= 1.0
    assert float(p_td + p_fg) == pytest.approx(1.0)


def test_a_team_cannot_be_projected_to_negative_points() -> None:
    for mu in (0.0, -14.0):
        assert drive_probabilities(mu, NFL) == (pytest.approx(0.0), pytest.approx(0.0))


# --- the samples themselves ------------------------------------------------

def test_every_score_is_a_whole_number_of_points() -> None:
    """The reason the extra point is modelled rather than averaged in."""
    sim = _sim(n=40_000)
    for side in (sim.home_score, sim.away_score):
        assert np.all(side == np.rint(side))
        assert side.min() >= 0


def test_the_expected_margin_and_total_come_back_unbiased() -> None:
    """Overtime adds points to the games that need it, so the total runs a
    touch over μ — as real finals do, for exactly the same reason."""
    sim = _sim(n=200_000)
    assert sim.margin.mean() == pytest.approx(2.0, abs=0.1)
    assert sim.total.mean() == pytest.approx(LEAGUE_TOTAL, abs=0.4)
    assert sim.total.mean() > LEAGUE_TOTAL


def test_the_dispersion_falls_out_of_the_structure() -> None:
    """Nothing here was fitted: 11 drives and a 0.65 mix imply this.

    The lab measured an NFL walk-forward residual sd of 13.01 on the margin.
    The possession structure, told only how many points each side expects,
    produces one within half a point of it.
    """
    sim = _sim(n=200_000)
    assert 12.9 < sim.margin.std() < 14.0


def test_the_closed_form_matches_the_sampler() -> None:
    """Exact in regulation — which is what makes it usable in the fit."""
    config = replace(NFL, overtime_rounds=0, n_sims=300_000)
    sim = simulate_drives(0.0, LEAGUE_TOTAL, np.random.default_rng(5), config)
    assert implied_score_sd(LEAGUE_TOTAL / 2, config) == pytest.approx(
        float(sim.home_score.std()), abs=0.03)


def test_a_fractional_drive_count_is_actually_played() -> None:
    """11.4 drives is not 11 drives, and not 12 either.

    The drive count does not move the EXPECTED score — the rate solve divides
    μ by it, so a team asked for 22 points scores 22 however many possessions
    it gets. What it moves is the dispersion: more, smaller-probability drives
    is a wider team score. So that is where the fractional count has to show
    up, and a mean test would have passed on a sampler that ignored it.
    """
    sds = [_sim(n=120_000, drives=d).home_score.std() for d in (11.0, 11.4, 12.0)]
    assert sds[0] < sds[1] < sds[2]
    means = [_sim(n=120_000, drives=d).total.mean() for d in (11.0, 12.0)]
    assert means[0] == pytest.approx(means[1], abs=0.3)


def test_the_same_seed_gives_the_same_game() -> None:
    a, b = _sim(n=20_000), _sim(n=20_000)
    assert np.array_equal(a.home_score, b.home_score)
    assert np.array_equal(a.away_score, b.away_score)
    assert not np.array_equal(a.home_score, _sim(n=20_000, seed=8).home_score)


# --- what the module exists for -------------------------------------------

def test_three_and_seven_carry_the_mass_a_normal_flattens() -> None:
    """The margins football piles up on, against the ones next door.

    Real NFL since 2015: 3 → 14.8%, 4 → 4.6%, 7 → 8.7%, 5 → 4.4%. A rounded
    normal at this dispersion gives 3 and 4 within a tenth of a point of each
    other. This does not have to match football's spikes to be worth having;
    it has to put the spikes in the right places.
    """
    sim = _sim(n=200_000)
    mass = np.bincount(np.abs(sim.margin).astype(int), minlength=20) / sim.n_sims
    assert mass[3] > mass[2] and mass[3] > mass[5]
    assert mass[7] > mass[5] and mass[7] > mass[8]
    assert mass[10] > mass[9] and mass[14] > mass[13]
    # And the seven is not merely a local peak — it is football's own size.
    assert 0.07 < mass[7] < 0.11


def test_football_ends_level_sometimes_but_only_just() -> None:
    """Ten of 3,045 NFL games since 2015 finished tied: 0.33%.

    The sim scored 4.3% before it had an overtime, and every other sport in
    the repo forces the tie away entirely. Football is the one that needs a
    number in between.
    """
    assert (_sim(n=200_000).margin == 0).mean() == pytest.approx(0.004, abs=0.003)
    # Without overtime the defect is unmistakable, which is what fixing it fixed.
    assert (_sim(n=60_000, overtime_rounds=0).margin == 0).mean() > 0.03


# --- the two spread mechanisms --------------------------------------------

def test_projection_error_widens_the_margin_and_leaves_the_total() -> None:
    """The signature that makes it separable from the scoring environment."""
    base, wide = _sim(n=150_000), _sim(n=150_000, strength_sd=4.0)
    assert wide.margin.std() > base.margin.std() + 0.4
    assert wide.total.std() == pytest.approx(float(base.total.std()), abs=0.3)


def test_the_scoring_environment_widens_the_total_and_leaves_the_margin() -> None:
    base, wide = _sim(n=150_000), _sim(n=150_000, pace_sd=0.15)
    assert wide.total.std() > base.total.std() + 0.4
    assert wide.margin.std() == pytest.approx(float(base.margin.std()), abs=0.3)


def test_neither_mechanism_moves_the_expectation() -> None:
    """A fitted config prices the same game at the same number."""
    sim = _sim(n=200_000, strength_sd=4.0, pace_sd=0.15)
    assert sim.margin.mean() == pytest.approx(2.0, abs=0.15)
    assert sim.total.mean() == pytest.approx(LEAGUE_TOTAL, abs=0.6)


# --- the fit ---------------------------------------------------------------

def _residuals(sd_margin: float, sd_total: float, mu_total: float = LEAGUE_TOTAL,
               n: int = 40_000, seed: int = 3):
    rng = np.random.default_rng(seed)
    return (rng.normal(0.0, sd_margin, n), rng.normal(0.0, sd_total, n),
            np.full(n, mu_total))


def test_the_fit_reproduces_the_dispersion_it_was_given() -> None:
    """College's numbers, which is where this mattered."""
    base = DriveConfig(drives=12.0, fg_to_td=0.50, n_sims=150_000)
    fitted = fit_drive_config(*_residuals(16.9, 17.2, 58.0), base)
    sim = simulate_drives(0.0, 58.0, np.random.default_rng(7), fitted)
    assert sim.margin.std() == pytest.approx(16.9, abs=0.5)
    assert sim.total.std() == pytest.approx(17.2, abs=0.5)


def test_the_fit_never_touches_the_lattice() -> None:
    """The college run's lesson, pinned.

    An earlier fit used the field-goal mix as its dispersion control. Against
    college residuals it ran to the bound and returned a lattice with no
    field goals in it — 24% of the mass on a margin of exactly 7 and 0.02% on
    a margin of 3. Whatever dispersion a fit cannot reach, it does not get to
    take out of the scoring structure.
    """
    base = DriveConfig(drives=12.0, fg_to_td=0.50)
    for sd_margin, sd_total in ((16.9, 17.2), (30.0, 30.0), (5.0, 5.0)):
        fitted = fit_drive_config(*_residuals(sd_margin, sd_total), base)
        assert fitted.fg_to_td == base.fg_to_td
        assert fitted.drives == base.drives


def test_residuals_narrower_than_the_lattice_floor_at_zero() -> None:
    """There is no negative spread to fit — that is the structure being wrong."""
    fitted = fit_drive_config(*_residuals(4.0, 4.0), NFL)
    assert fitted.strength_sd == 0.0 and fitted.pace_sd == 0.0


def test_a_config_rejects_what_it_cannot_sample() -> None:
    for bad in ({"drives": 0.0}, {"fg_to_td": -0.1}, {"pace_sd": -0.1},
                {"strength_sd": -0.1}, {"overtime_rounds": -1}, {"n_sims": 0}):
        with pytest.raises(ValueError):
            replace(NFL, **bad)  # type: ignore[arg-type]


def test_the_extra_point_is_a_coin_and_not_an_average() -> None:
    """Rounding a 6.94-point touchdown biased the mean total up a quarter point."""
    assert 0.9 < EXTRA_POINT_RATE < 1.0
    sim = _sim(mu_margin=0.0, n=200_000, overtime_rounds=0)
    assert sim.total.mean() == pytest.approx(LEAGUE_TOTAL, abs=0.15)
