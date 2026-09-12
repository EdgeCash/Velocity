"""The low-scoring count sim — baseball simulated as baseball.

The rounded normal got three things wrong about a baseball game at once: it
scored ties, which do not exist; it was a third under-dispersed; and it was
symmetric in home and away, which the unbatted ninth inning is not. These pin
all three, plus the endgame rules that produce the asymmetry.

The league-profile numbers below come from the 7,149 games banked in
``datasets/mlb/games.parquet`` (2024–2026).
"""

from __future__ import annotations

import numpy as np
import pytest
from velocity.models.counts import MLB_COUNTS, CountSimConfig, simulate_counts
from velocity.models.simulate import SimConfig, simulate_game

# The league's own averages, which is what the sim is calibrated against.
LEAGUE_HOME = 4.456
LEAGUE_AWAY = 4.414


def _sim(n: int = 200_000, seed: int = 3, **kw):
    home, away = simulate_counts(
        kw.pop("mu_home", LEAGUE_HOME), kw.pop("mu_away", LEAGUE_AWAY),
        np.random.default_rng(seed), kw.pop("config", MLB_COUNTS), n,
    )
    return home, away


def test_no_baseball_game_ends_level() -> None:
    """Zero ties in 7,149 banked games; the normal sim scored 13.5%."""
    home, away = _sim()
    assert np.count_nonzero(home == away) == 0


def test_every_score_is_a_whole_number_of_runs() -> None:
    home, away = _sim(n=20_000)
    assert np.all(home == np.rint(home)) and np.all(away == np.rint(away))
    assert home.min() >= 0 and away.min() >= 0


def test_the_dispersion_is_the_leagues_own() -> None:
    """A team-game's runs are overdispersed against Poisson: var ≈ 2.2 × mean.

    The shipped normal used a margin sd of 3.2 against a walk-forward residual
    sd of 4.53 — it priced baseball as a third more predictable than it is.
    """
    home, away = _sim()
    assert 4.3 < (home - away).std() < 4.8, "the margin spread is the league's"
    assert 4.3 < (home + away).std() < 4.8
    # Each side's own spread, which is what a team total is priced off.
    assert 3.0 < home.std() < 3.3
    assert 3.1 < away.std() < 3.4


def test_the_home_team_wins_as_often_as_home_teams_do() -> None:
    # 53.1% in the banked games. The rounded normal, splitting its ties,
    # produced 50.9% — every home team priced nearly as a coin flip.
    home, away = _sim()
    assert 0.520 < np.mean(home > away) < 0.540


def test_the_unbatted_ninth_shows_up_as_a_one_run_asymmetry() -> None:
    """The signature of censoring by winning, and the reason a normal cannot do it.

    A home team ahead going into the bottom of the ninth does not bat, and a
    walk-off ends the instant the lead is taken — so it wins by exactly one far
    more often than the away team does (32.7% of home wins against 23.1% in the
    banked games). A symmetric margin distribution has no way to say this.
    """
    home, away = _sim()
    margin = home - away
    home_by_one = np.mean(margin[margin > 0] == 1)
    away_by_one = np.mean(-margin[margin < 0] == 1)
    assert home_by_one > away_by_one + 0.05
    assert 0.30 < home_by_one < 0.36
    assert 0.20 < away_by_one < 0.27


def test_the_home_team_scores_fewer_runs_when_it_wins_than_the_away_team_does() -> None:
    # The same censoring, seen from the other side: a winning home team stops
    # batting, so it banks less than a winning away team despite being better.
    home, away = _sim()
    assert home[home > away].mean() < away[away > home].mean()


def test_the_one_run_game_is_the_key_number() -> None:
    """28.2% of banked games end by one — baseball's 3."""
    home, away = _sim()
    assert 0.26 < np.mean(np.abs(home - away) == 1) < 0.31


def test_a_stronger_team_scores_more_and_wins_more() -> None:
    strong, weak = simulate_counts(6.5, 3.0, np.random.default_rng(4), MLB_COUNTS, 60_000)
    assert strong.mean() > weak.mean() + 2.5
    assert np.mean(strong > weak) > 0.75


def test_the_same_seed_gives_the_same_game() -> None:
    a = simulate_counts(4.5, 4.2, np.random.default_rng(17), MLB_COUNTS, 5_000)
    b = simulate_counts(4.5, 4.2, np.random.default_rng(17), MLB_COUNTS, 5_000)
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])


def test_an_extreme_projection_does_not_break_the_draw() -> None:
    # A rating can project a non-positive expectation; zero runs is already the
    # floor the count distribution supplies, so it must not raise.
    home, away = simulate_counts(0.0, -2.0, np.random.default_rng(1), MLB_COUNTS, 5_000)
    assert home.min() >= 0 and away.min() >= 0
    assert np.count_nonzero(home == away) == 0


def test_extra_innings_can_be_turned_off_to_end_by_exactly_one() -> None:
    config = CountSimConfig(dispersion=3.25, extra_margin_tail=0.0)
    home, away = _sim(n=50_000, config=config)
    assert np.count_nonzero(home == away) == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"dispersion": 0.0},
        {"dispersion": 3.0, "home_uncensored": 0.9},
        {"dispersion": 3.0, "walkoff_spill": 1.4},
        {"dispersion": 3.0, "extra_home_win": -0.1},
        {"dispersion": 3.0, "extra_margin_tail": 1.0},
        {"dispersion": 3.0, "innings": 0},
    ],
)
def test_a_nonsense_config_is_refused(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        CountSimConfig(**kwargs)


# --- the wiring: a config carrying counts routes through the shared sim ------


def test_the_shared_sim_dispatches_to_counts_when_asked() -> None:
    config = SimConfig(sd_margin=4.5, sd_total=4.5, n_sims=80_000, counts=MLB_COUNTS)
    sim = simulate_game(
        mu_margin=LEAGUE_HOME - LEAGUE_AWAY, mu_total=LEAGUE_HOME + LEAGUE_AWAY,
        rng=np.random.default_rng(8), config=config,
    )
    assert np.count_nonzero(sim.margin == 0) == 0
    assert 0.52 < sim.p_home_win() < 0.54
    # The two expected scores survive the (margin, total) round trip.
    assert abs(sim.home_score.mean() - LEAGUE_HOME) < 0.15
    assert abs(sim.away_score.mean() - LEAGUE_AWAY) < 0.15


def test_the_run_line_prices_where_the_league_actually_lands() -> None:
    """The market this fix is worth the most on.

    Over 5,636 walk-forward games the rounded normal put the home −1.5 at 31.8%
    against an actual 35.6%, and the home +1.5 at 68.7% against 63.9% — a four-
    to five-point bias, in the same direction every time.
    """
    config = SimConfig(sd_margin=4.5, sd_total=4.5, n_sims=200_000, counts=MLB_COUNTS)
    sim = simulate_game(
        mu_margin=LEAGUE_HOME - LEAGUE_AWAY, mu_total=LEAGUE_HOME + LEAGUE_AWAY,
        rng=np.random.default_rng(12), config=config,
    )
    assert 0.335 < sim.prob_home_cover(-1.5) < 0.375
    assert 0.625 < sim.prob_home_cover(1.5) < 0.665


def test_the_normal_path_is_untouched() -> None:
    # Football must simulate exactly as it did; the count path is opt-in.
    plain = SimConfig(n_sims=20_000)
    assert plain.counts is None
    sim = simulate_game(mu_margin=3.0, mu_total=44.0,
                        rng=np.random.default_rng(2), config=plain)
    assert np.count_nonzero(sim.margin == 0) > 0, "football ties are real"
    assert 11.0 < sim.margin.std() < 15.0
