"""Basketball's sim repair — a smaller fix than baseball's, and a different one.

Baseball needed rebuilding from the ground up: counts, overdispersion, and the
asymmetry of an unbatted ninth. Basketball needed two things, and porting the
rest would have been pattern-matching — eighty-odd points is normal enough, and
both teams play all forty minutes whatever the score.

What it needed: the right dispersion on the TOTAL, which was a fifth too
narrow, and an end to scoring ties in a sport that plays an extra period.
"""

from __future__ import annotations

import numpy as np
import pytest
from velocity.models.overtime import (
    WNBA_OVERTIME,
    OvertimeConfig,
    resolve_ties,
)
from velocity.models.simulate import SimConfig, simulate_game

# The league's own averages, from the 875 banked games.
LEAGUE_MARGIN = 1.93
LEAGUE_TOTAL = 167.09


def _wnba(n_sims: int = 200_000, **kw) -> SimConfig:
    return SimConfig(sd_margin=12.93, sd_total=17.6, n_sims=n_sims,
                     overtime=WNBA_OVERTIME, **kw)


def _sim(config: SimConfig, seed: int = 7):
    return simulate_game(mu_margin=LEAGUE_MARGIN, mu_total=LEAGUE_TOTAL,
                         rng=np.random.default_rng(seed), config=config)


def test_no_basketball_game_ends_level() -> None:
    """Zero ties in 875 banked games; the shipped normal scored 3.2%."""
    assert np.count_nonzero(_sim(_wnba()).margin == 0) == 0


def test_the_short_spreads_stop_differing_by_an_impossible_outcome() -> None:
    """The fix's real payoff, and it lands exactly.

    A lump of probability at exactly zero made covering −0.5 and covering +0.5
    differ by that whole 3.2%. In a sport with no ties they should differ by
    nothing at all, and now they do.
    """
    sim = _sim(_wnba())
    assert sim.prob_home_cover(-0.5) == sim.prob_home_cover(0.5)
    # The defect, for contrast: the same game on the old config.
    old = _sim(SimConfig(sd_margin=12.5, sd_total=15.0, n_sims=200_000))
    assert old.prob_home_cover(0.5) - old.prob_home_cover(-0.5) > 0.02


def test_the_total_carries_the_leagues_own_dispersion() -> None:
    """15.0 against a walk-forward residual sd of 18.15 — a fifth too narrow.

    The margin was nearly right, which is why this is the fix that matters:
    every WNBA total and team total was priced overconfident.
    """
    sim = _sim(_wnba())
    assert 17.8 < sim.total.std() < 18.6
    assert 12.5 < sim.margin.std() < 13.4


def test_an_extra_period_puts_points_on_the_board_for_both_sides() -> None:
    """What separates this from baseball's tie resolution, where one run ends it."""
    home = np.array([80.0, 80.0, 70.0])
    away = np.array([80.0, 80.0, 65.0])
    out_home, out_away = resolve_ties(home, away, np.random.default_rng(3),
                                      WNBA_OVERTIME)
    assert np.all(out_home != out_away), "no sample may come back level"
    # Both tied games scored again; the decided one is untouched.
    assert out_home[0] > 80.0 and out_away[0] > 80.0
    assert (out_home[2], out_away[2]) == (70.0, 65.0)


def test_close_games_carry_a_higher_total_than_blowouts() -> None:
    """The gradient a tie-scoring sim cannot produce, because it ends early.

    In the banked games the ones decided by three or fewer average 170.5
    against 164.8 for those decided by thirteen or more. The sim recovers the
    sign and part of the size — see docs/BUILD_WNBA_SIM.md on why only part.
    """
    sim = _sim(_wnba())
    margin, total = sim.margin, sim.total
    close = total[np.abs(margin) <= 3].mean()
    blowout = total[np.abs(margin) >= 13].mean()
    assert close > blowout + 1.0


def test_the_moneyline_barely_moves_and_that_is_expected() -> None:
    """Honest about what this fix is NOT worth.

    ``p_home_win`` already split the tie mass evenly, and an extra period is
    close to a coin flip, so the split was nearly right by accident. The gain
    is in the short spreads and the total, not here.
    """
    fixed = _sim(_wnba()).p_home_win()
    old = _sim(SimConfig(sd_margin=12.5, sd_total=15.0, n_sims=200_000)).p_home_win()
    assert abs(fixed - old) < 0.01


def test_the_same_seed_gives_the_same_game() -> None:
    a, b = _sim(_wnba(20_000)), _sim(_wnba(20_000))
    assert np.array_equal(a.home_score, b.home_score)
    assert np.array_equal(a.away_score, b.away_score)


def test_a_tie_that_survives_a_bounded_run_of_periods_is_still_decided() -> None:
    # A degenerate config that can never break a tie by scoring must still not
    # return one — the single thing this module exists to rule out.
    stubborn = OvertimeConfig(points=0.0, points_sd=0.0, margin_sd=1e-9)
    home, away = resolve_ties(np.full(500, 80.0), np.full(500, 80.0),
                              np.random.default_rng(1), stubborn)
    assert np.count_nonzero(home == away) == 0


@pytest.mark.parametrize(
    "kwargs",
    [{"points": -1.0, "points_sd": 1.0, "margin_sd": 4.0},
     {"points": 21.0, "points_sd": -1.0, "margin_sd": 4.0},
     {"points": 21.0, "points_sd": 6.0, "margin_sd": 0.0}],
)
def test_a_nonsense_config_is_refused(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        OvertimeConfig(**kwargs)


def test_football_is_untouched() -> None:
    # Football games CAN end level, and its config carries no overtime rule.
    plain = SimConfig(n_sims=20_000)
    assert plain.overtime is None
    sim = simulate_game(mu_margin=3.0, mu_total=44.0,
                        rng=np.random.default_rng(2), config=plain)
    assert np.count_nonzero(sim.margin == 0) > 0
