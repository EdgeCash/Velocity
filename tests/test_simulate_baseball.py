"""MLB Monte Carlo (Phase M3) — the per-PA baseball engine.

Pins the properties the wagering stack will trust: determinism under seed, a
correct base-out state machine (an all-strikeout inning scores nothing in exactly
three outs), a discrete right-skewed run distribution calibrated to a realistic
MLB run environment, first-5-innings runs bounded by the full game, and internal
stat consistency (a pitcher's strikeouts equal the strikeouts of the batters he
faces). The full real-data holdout calibration is an acceptance check, run on
committed history, not part of this offline gate.
"""

from __future__ import annotations

import numpy as np
import pytest
from velocity.features.baseball import DEFAULT_BAT_PRIOR, DEFAULT_BIP_PRIOR, DEFAULT_PIT_PRIOR
from velocity.models.simulate_baseball import (
    N_OUTCOMES,
    BaseballSimConfig,
    Team,
    batter_from_rates,
    combine_matchup,
    matchup_distribution,
    pitcher_from_rates,
    simulate_game,
    simulate_half_inning,
)


def _avg_team(prefix: str) -> Team:
    lineup = [
        batter_from_rates(f"{prefix}{i}", DEFAULT_BAT_PRIOR, DEFAULT_BIP_PRIOR) for i in range(9)
    ]
    return Team(lineup=lineup, pitcher=pitcher_from_rates(f"{prefix}_p", DEFAULT_PIT_PRIOR))


def _sim(seed: int, n: int):
    """Simulate ``n`` games between two league-average teams under ``seed``."""
    return simulate_game(
        _avg_team("h"), _avg_team("a"), np.random.default_rng(seed), BaseballSimConfig(n_sims=n)
    )


# --- matchup math -----------------------------------------------------------


def test_combine_matchup_sums_to_one() -> None:
    b = np.array([DEFAULT_BAT_PRIOR[o] for o in ["k", "bb", "hbp", "hr", "in_play"]])
    p = np.array([DEFAULT_PIT_PRIOR[o] for o in ["k", "bb", "hbp", "hr", "in_play"]])
    assert combine_matchup(b, p).sum() == pytest.approx(1.0)


def test_league_average_batter_yields_pitcher_rates() -> None:
    from velocity.models.simulate_baseball import _LEAGUE_PA_VEC

    pitcher = np.array([0.30, 0.05, 0.01, 0.05, 0.59])
    out = combine_matchup(_LEAGUE_PA_VEC, pitcher)
    # A league-average batter can't shift the matchup off the pitcher's rates.
    assert np.allclose(out, pitcher / pitcher.sum())


def test_matchup_distribution_is_a_distribution() -> None:
    dist = matchup_distribution(
        batter_from_rates("b", DEFAULT_BAT_PRIOR, DEFAULT_BIP_PRIOR),
        pitcher_from_rates("p", DEFAULT_PIT_PRIOR),
    )
    assert dist.shape == (N_OUTCOMES,)
    assert (dist >= 0).all()
    assert dist.sum() == pytest.approx(1.0)


# --- half-inning state machine ----------------------------------------------


def test_all_strikeout_inning_scores_nothing_in_three_outs() -> None:
    all_k = np.zeros(N_OUTCOMES)
    all_k[0] = 1.0  # 100% strikeout
    cum = [np.cumsum(all_k)] * 9
    result = simulate_half_inning(cum, start_index=0, rng=np.random.default_rng(1))
    assert result.runs == 0
    assert result.outs == 3
    assert result.next_index == 3  # exactly three batters faced
    assert all(outcome == 0 for _, outcome in result.events)


def test_bases_loaded_walk_forces_a_run() -> None:
    all_walk = np.zeros(N_OUTCOMES)
    all_walk[1] = 1.0  # 100% walk — loads the bases, then forces runs in
    cum = [np.cumsum(all_walk)] * 9
    # Walk-off after 4 walks: 3 to load the bases, the 4th forces a run.
    result = simulate_half_inning(cum, 0, np.random.default_rng(1), runs_to_win=1)
    assert result.runs == 1
    assert result.outs == 0  # ended on the run, not on outs
    assert result.next_index == 4


# --- full game --------------------------------------------------------------


def test_determinism_under_seed() -> None:
    home, away = _avg_team("h"), _avg_team("a")
    cfg = BaseballSimConfig(n_sims=200)
    r1 = simulate_game(home, away, np.random.default_rng(42), cfg)
    r2 = simulate_game(home, away, np.random.default_rng(42), cfg)
    assert np.array_equal(r1.full.home_score, r2.full.home_score)
    assert np.array_equal(r1.full.away_score, r2.full.away_score)
    assert np.array_equal(r1.pitcher_strikeouts["h_p"], r2.pitcher_strikeouts["h_p"])


def test_runs_are_discrete_and_non_negative() -> None:
    res = _sim(3, 500)
    for scores in (res.full.home_score, res.full.away_score):
        assert (scores >= 0).all()
        assert np.array_equal(scores, np.round(scores))


def test_run_distribution_is_right_skewed() -> None:
    res = _sim(5, 3000)
    total = res.full.home_score + res.full.away_score
    # Baseball run totals pile up low with a long high tail: mean exceeds median.
    assert total.mean() > np.median(total)


def test_run_environment_is_realistic() -> None:
    """Analytic/sanity calibration: league-average inputs → a real MLB run rate."""
    res = _sim(11, 2500)
    total = (res.full.home_score + res.full.away_score).mean()
    per_team = res.full.away_score.mean()
    assert 8.0 <= total <= 10.5  # recent MLB ~8.6–9.5 runs/game
    assert 4.0 <= per_team <= 5.3
    assert 0.44 <= res.full.p_home_win() <= 0.56  # no HFA modeled → ~coin flip


def test_f5_runs_bounded_by_full_game() -> None:
    res = _sim(9, 800)
    # First-5-innings runs can never exceed the final; F5 is a prefix of the game.
    assert (res.f5.home_score <= res.full.home_score).all()
    assert (res.f5.away_score <= res.full.away_score).all()


def test_pitcher_strikeouts_match_batters_faced() -> None:
    """Internal consistency: the home pitcher's Ks are the away batters' Ks."""
    res = _sim(2, 400)
    away_batter_k = sum(res.batter_strikeouts[f"a{i}"] for i in range(9))
    assert np.array_equal(res.pitcher_strikeouts["h_p"], away_batter_k)


def test_all_strikeout_batter_has_zero_total_bases() -> None:
    all_k_bat = {"k": 1.0, "bb": 0.0, "hbp": 0.0, "hr": 0.0, "in_play": 0.0}
    whiffer = batter_from_rates("whiff", all_k_bat, DEFAULT_BIP_PRIOR)
    home = Team([whiffer] * 9, pitcher_from_rates("h_p", DEFAULT_PIT_PRIOR))
    res = simulate_game(
        home, _avg_team("a"), np.random.default_rng(4), BaseballSimConfig(n_sims=200)
    )
    assert (res.batter_total_bases["whiff"] == 0).all()
    assert (res.batter_hits["whiff"] == 0).all()


def test_home_field_advantage_favors_home() -> None:
    home, away = _avg_team("h"), _avg_team("a")
    cfg = lambda hfa: BaseballSimConfig(n_sims=4000, hfa=hfa)  # noqa: E731
    neutral = simulate_game(home, away, np.random.default_rng(7), cfg(0.0)).full
    with_hfa = simulate_game(home, away, np.random.default_rng(7), cfg(0.02)).full
    # HFA tilts the win probability toward home and lifts home's run share...
    assert with_hfa.p_home_win() > neutral.p_home_win()
    assert with_hfa.home_score.mean() > neutral.home_score.mean()
    assert with_hfa.away_score.mean() < neutral.away_score.mean()
    # ...while leaving the total roughly unchanged (a margin shift, not more runs).
    neutral_total = (neutral.home_score + neutral.away_score).mean()
    hfa_total = (with_hfa.home_score + with_hfa.away_score).mean()
    assert abs(hfa_total - neutral_total) < 0.5


def test_park_hr_factor_moves_the_total() -> None:
    home, away = _avg_team("h"), _avg_team("a")
    cfg = BaseballSimConfig(n_sims=4000)
    neutral = simulate_game(home, away, np.random.default_rng(11), cfg).full
    hitter = simulate_game(home, away, np.random.default_rng(11), cfg, park_hr_factor=1.15).full
    pitcher = simulate_game(home, away, np.random.default_rng(11), cfg, park_hr_factor=0.85).full

    def total(sim):  # noqa: ANN001, ANN202
        return (sim.home_score + sim.away_score).mean()

    def margin(sim):  # noqa: ANN001, ANN202
        return (sim.home_score - sim.away_score).mean()

    # A hitter's park lifts the total, a pitcher's park suppresses it...
    assert total(hitter) > total(neutral) > total(pitcher)
    # ...symmetrically for both lineups (both bat in the home park), so the margin
    # is roughly unchanged — this is a total effect, not a home edge.
    assert abs(margin(hitter) - margin(neutral)) < 0.4


def test_neutral_park_factor_is_identity() -> None:
    home, away = _avg_team("h"), _avg_team("a")
    cfg = BaseballSimConfig(n_sims=1500)
    base = simulate_game(home, away, np.random.default_rng(3), cfg).full
    same = simulate_game(home, away, np.random.default_rng(3), cfg, park_hr_factor=1.0).full
    assert base.home_score.tolist() == same.home_score.tolist()
    assert base.away_score.tolist() == same.away_score.tolist()


def test_run_env_tilt_moves_the_total_symmetrically() -> None:
    home, away = _avg_team("h"), _avg_team("a")
    cfg = BaseballSimConfig(n_sims=4000)
    neutral = simulate_game(home, away, np.random.default_rng(21), cfg).full
    up = simulate_game(home, away, np.random.default_rng(21), cfg, run_env_tilt=0.05).full
    down = simulate_game(home, away, np.random.default_rng(21), cfg, run_env_tilt=-0.05).full

    def total(sim):  # noqa: ANN001, ANN202
        return (sim.home_score + sim.away_score).mean()

    def margin(sim):  # noqa: ANN001, ANN202
        return (sim.home_score - sim.away_score).mean()

    assert total(up) > total(neutral) > total(down)  # a run-env tilt moves the total
    assert abs(margin(up) - margin(neutral)) < 0.4  # symmetric — not a home edge


def test_tto_penalty_raises_the_total() -> None:
    """The times-through-the-order penalty lets tiring starters allow more runs."""
    home, away = _avg_team("h"), _avg_team("a")
    base = BaseballSimConfig(n_sims=4000, starter_outs=18)
    tto = BaseballSimConfig(n_sims=4000, starter_outs=18, tto_penalty=(0.05, 0.10))
    no_tto = simulate_game(home, away, np.random.default_rng(31), base).full
    with_tto = simulate_game(home, away, np.random.default_rng(31), tto).full
    total = lambda s: (s.home_score + s.away_score).mean()  # noqa: E731
    assert total(with_tto) > total(no_tto)


def test_tto_concentrated_in_later_innings() -> None:
    """The penalty escalates through the order, so it grows the full-minus-F5 gap.

    F5 (starter's first ~2 turns) moves little; the full game (2nd/3rd turn) moves
    more — exactly the F5-vs-full split the penalty is meant to fix.
    """
    home, away = _avg_team("h"), _avg_team("a")
    base = BaseballSimConfig(n_sims=5000, starter_outs=18)
    tto = BaseballSimConfig(n_sims=5000, starter_outs=18, tto_penalty=(0.05, 0.10))
    b = simulate_game(home, away, np.random.default_rng(41), base)
    t = simulate_game(home, away, np.random.default_rng(41), tto)
    full = lambda r: (r.full.home_score + r.full.away_score).mean()  # noqa: E731
    f5 = lambda r: (r.f5.home_score + r.f5.away_score).mean()  # noqa: E731
    full_delta = full(t) - full(b)
    f5_delta = f5(t) - f5(b)
    assert full_delta > f5_delta  # the penalty lands more on the back of the game


def test_bullpen_swaps_in_after_the_cap() -> None:
    """A dominant bullpen suppresses runs after the starter is pulled — a back-of-
    game effect, so the full game moves but F5 (starter innings) barely does."""
    import dataclasses

    from velocity.models.simulate_baseball import Pitcher

    # A shutdown bullpen: all strikeouts (no baserunners → no runs allowed).
    lights_out = Pitcher("pen", np.array([1.0, 0.0, 0.0, 0.0, 0.0]))
    home, away = _avg_team("h"), _avg_team("a")
    # Give the HOME team a lights-out bullpen; it faces the AWAY lineup late.
    home_pen = dataclasses.replace(home, bullpen=lights_out)
    cfg = BaseballSimConfig(n_sims=4000, starter_outs=18)

    base = simulate_game(home, away, np.random.default_rng(51), cfg)
    penned = simulate_game(home_pen, away, np.random.default_rng(51), cfg)
    # Away scoring falls once the shutdown pen takes over (full game)...
    assert penned.full.away_score.mean() < base.full.away_score.mean()
    # ...while the first five innings (starter's) are essentially unchanged.
    assert penned.f5.away_score.mean() == pytest.approx(base.f5.away_score.mean(), abs=0.15)


def test_no_bullpen_is_identity() -> None:
    home, away = _avg_team("h"), _avg_team("a")
    cfg = BaseballSimConfig(n_sims=1500, starter_outs=18)
    a = simulate_game(home, away, np.random.default_rng(9), cfg).full
    b = simulate_game(home, away, np.random.default_rng(9), cfg).full
    assert a.home_score.tolist() == b.home_score.tolist()


def test_tto_zero_penalty_is_identity() -> None:
    home, away = _avg_team("h"), _avg_team("a")
    cfg = BaseballSimConfig(n_sims=1500, starter_outs=18)
    base = simulate_game(home, away, np.random.default_rng(5), cfg).full
    same = simulate_game(
        home, away, np.random.default_rng(5),
        BaseballSimConfig(n_sims=1500, starter_outs=18, tto_penalty=(0.0, 0.0)),
    ).full
    assert base.home_score.tolist() == same.home_score.tolist()


def test_config_validation() -> None:
    with pytest.raises(ValueError, match="n_sims"):
        BaseballSimConfig(n_sims=0)
    with pytest.raises(ValueError, match="max_innings"):
        BaseballSimConfig(max_innings=8)
    with pytest.raises(ValueError, match="hfa"):
        BaseballSimConfig(hfa=1.5)
    with pytest.raises(ValueError, match="tto_penalty"):
        BaseballSimConfig(tto_penalty=(1.5, 0.0))
