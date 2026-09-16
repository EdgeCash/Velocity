"""Football prop model — correlated FantasyPros-driven distributions.

Everything is pinned on a tiny synthetic projections frame with exactly known
means: projections are recovered in expectation (the lognormal multipliers are
mean-1), teammates correlate through the shared volume draw, the QB's passing
yards are exactly his receivers' simulated yards (scaled), sub-threshold
players are skipped, and the sim satisfies both the pricing protocol and the
parlay ``player_samples`` hook. Seeded → deterministic.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from velocity.models.props_football import (
    FootballPropConfig,
    game_props,
    name_index_from_fp,
    player_key,
    simulate_team_props,
    team_player_means,
)


def _fp_frame() -> pd.DataFrame:
    """A two-team long projections frame in the FantasyPros normalized shape."""
    rows = [
        # KC: QB + two pass catchers + a runner.
        ("kc_qb", "Patrick Mahomes", "KC", "QB", "pass_yds", 280.0),
        ("kc_qb", "Patrick Mahomes", "KC", "QB", "pass_tds", 2.1),
        ("kc_qb", "Patrick Mahomes", "KC", "QB", "rush_yds", 18.0),
        ("kc_qb", "Patrick Mahomes", "KC", "QB", "pass_int", 0.72),
        ("kc_qb", "Patrick Mahomes", "KC", "QB", "pass_att", 34.0),
        ("kc_qb", "Patrick Mahomes", "KC", "QB", "pass_cmp", 22.1),
        ("kc_rb", "Isiah Pacheco", "KC", "RB", "rush_att", 14.0),
        ("kc_te", "Travis Kelce", "KC", "TE", "rec", 6.5),
        ("kc_te", "Travis Kelce", "KC", "TE", "rec_yds", 72.0),
        ("kc_te", "Travis Kelce", "KC", "TE", "rec_tds", 0.55),
        ("kc_wr", "Rashee Rice", "KC", "WR", "rec", 5.5),
        ("kc_wr", "Rashee Rice", "KC", "WR", "rec_yds", 68.0),
        ("kc_rb", "Isiah Pacheco", "KC", "RB", "rush_yds", 62.0),
        ("kc_rb", "Isiah Pacheco", "KC", "RB", "rush_tds", 0.45),
        ("kc_rb", "Isiah Pacheco", "KC", "RB", "rec", 2.5),
        ("kc_rb", "Isiah Pacheco", "KC", "RB", "rec_yds", 18.0),
        # A fringe player under every threshold: no markets simulated.
        ("kc_x", "Deep Reserve", "KC", "WR", "rec", 0.2),
        ("kc_x", "Deep Reserve", "KC", "WR", "rec_yds", 1.5),
        # BUF: a QB and one receiver.
        ("buf_qb", "Josh Allen", "BUF", "QB", "pass_yds", 250.0),
        ("buf_wr", "Khalil Shakir", "BUF", "WR", "rec", 5.0),
        ("buf_wr", "Khalil Shakir", "BUF", "WR", "rec_yds", 55.0),
    ]
    return pd.DataFrame(
        rows, columns=["player_id", "player_name", "team", "position", "stat", "value"]
    )


CFG = FootballPropConfig(n_sims=20_000)


def test_team_player_means_collapses_and_folds_tds() -> None:
    players = {p.key: p for p in team_player_means(_fp_frame(), "KC")}
    assert players["kc_te"].means["receptions"] == 6.5
    assert players["kc_te"].td_rate == pytest.approx(0.55)
    assert players["kc_rb"].td_rate == pytest.approx(0.45)
    assert players["kc_qb"].means["pass_yards"] == 280.0


def test_projections_are_recovered_in_expectation() -> None:
    samples = simulate_team_props(
        team_player_means(_fp_frame(), "KC"), np.random.default_rng(7), CFG
    )
    assert np.mean(samples[("kc_te", "receptions")]) == pytest.approx(6.5, rel=0.05)
    assert np.mean(samples[("kc_te", "receiving_yards")]) == pytest.approx(72.0, rel=0.05)
    assert np.mean(samples[("kc_rb", "rush_yards")]) > 0
    # Rushing yards are clipped at zero, which lifts the mean above the raw
    # normal's — allow a looser band on the high side only.
    assert np.mean(samples[("kc_rb", "rush_yards")]) == pytest.approx(62.0, rel=0.15)
    assert np.mean(samples[("kc_qb", "pass_yards")]) == pytest.approx(280.0, rel=0.05)
    assert np.mean(samples[("kc_qb", "pass_tds")]) == pytest.approx(2.1, rel=0.06)


def test_sub_threshold_players_are_skipped() -> None:
    samples = simulate_team_props(
        team_player_means(_fp_frame(), "KC"), np.random.default_rng(7), CFG
    )
    assert ("kc_x", "receptions") not in samples
    assert ("kc_x", "receiving_yards") not in samples


def test_teammates_correlate_through_shared_volume() -> None:
    samples = simulate_team_props(
        team_player_means(_fp_frame(), "KC"), np.random.default_rng(11), CFG
    )
    kelce = samples[("kc_te", "receiving_yards")]
    rice = samples[("kc_wr", "receiving_yards")]
    corr = float(np.corrcoef(kelce, rice)[0, 1])
    # The shared pass-volume draw moves them together — a little. Measured
    # on 525 top-receiver pairs (2020–2025) the week-to-week correlation of
    # teammates' receiving yards is −0.02: the pie is not a common
    # multiplier, target share trades off. The fitted σ (0.118) implies
    # ~0.035 here; the old prior (0.18) implied 0.075.
    assert 0.0 < corr < 0.07


def test_qb_pass_yards_are_exactly_the_receiving_sum_scaled() -> None:
    samples = simulate_team_props(
        team_player_means(_fp_frame(), "KC"), np.random.default_rng(3), CFG
    )
    total = (
        samples[("kc_te", "receiving_yards")]
        + samples[("kc_wr", "receiving_yards")]
        + samples[("kc_rb", "receiving_yards")]
    )
    qb = samples[("kc_qb", "pass_yards")]
    # Perfectly correlated by construction: one is a scalar multiple of the other.
    assert float(np.corrcoef(total, qb)[0, 1]) == pytest.approx(1.0)


def test_anytime_td_prices_a_probability() -> None:
    samples = simulate_team_props(
        team_player_means(_fp_frame(), "KC"), np.random.default_rng(5), CFG
    )
    td = samples[("kc_rb", "anytime_td")]
    p_any = float(np.mean(td > 0.5))  # P(count ≥ 1), the anytime-TD over at 0.5
    # Poisson(0.45) → P(≥1) = 1 − e^−0.45 ≈ 0.362 (the multiplier widens it a touch).
    assert p_any == pytest.approx(1 - np.exp(-0.45), abs=0.03)


def test_game_props_satisfies_both_protocols() -> None:
    sim = game_props(_fp_frame(), "KC", "BUF", np.random.default_rng(1), CFG)
    assert sim.has("kc_te", "receptions")
    assert sim.has("buf_wr", "receiving_yards")
    assert not sim.has("kc_x", "receptions")
    p_over = sim.prob_over("kc_te", "receptions", 5.5)
    p_under = sim.prob_under("kc_te", "receptions", 5.5)
    assert 0.0 < p_over < 1.0
    # over + under < 1 only by the push mass at integer counts... 5.5 can't push:
    assert p_over + p_under == pytest.approx(1.0)
    assert sim.player_samples("kc_te", "receptions").shape == (CFG.n_sims,)


def test_determinism_under_a_fixed_seed() -> None:
    a = game_props(_fp_frame(), "KC", "BUF", np.random.default_rng(42), CFG)
    b = game_props(_fp_frame(), "KC", "BUF", np.random.default_rng(42), CFG)
    assert np.array_equal(
        a.player_samples("kc_te", "receiving_yards"),
        b.player_samples("kc_te", "receiving_yards"),
    )


def test_name_index_and_player_key() -> None:
    index = name_index_from_fp(_fp_frame())
    assert index["patrickmahomes"] == "kc_qb"
    assert player_key(None, "Josh Allen") == "joshallen"  # id-less rows fall back to name
    assert player_key("x1", "Josh Allen") == "x1"


def test_dispersion_is_the_fitted_per_position_table() -> None:
    cfg = FootballPropConfig()
    assert cfg.per_catch_sd("WR") == pytest.approx(10.57)
    assert cfg.per_catch_sd("TE") == pytest.approx(7.92)
    assert cfg.per_catch_sd("FB") == cfg.yards_sd_per_reception  # the fallback
    assert cfg.phi("RB") > cfg.phi("WR") > cfg.phi("TE") == 0.0
    assert cfg.rush_cv("QB") > cfg.rush_cv("RB") == pytest.approx(0.72)
    assert cfg.pass_volume_sigma == pytest.approx(0.118)


def test_receptions_carry_the_negative_binomial_overdispersion() -> None:
    from velocity.models.props_football import _PlayerMeans

    rng = np.random.default_rng(3)
    back = _PlayerMeans(key="rb", name="Back", position="RB",
                        means={"receptions": 4.0, "receiving_yards": 30.0})
    end = _PlayerMeans(key="te", name="End", position="TE",
                       means={"receptions": 4.0, "receiving_yards": 40.0})
    cfg = FootballPropConfig(n_sims=100_000, rush_pool={})
    samples = simulate_team_props([back, end], rng, cfg)
    for key in ("rb", "te"):
        assert samples[(key, "receptions")].mean() == pytest.approx(4.0, abs=0.05)
    # var = μ + μ²·(φ_player + e^{σ²} − 1): the back's extra share shows.
    var_rb = samples[("rb", "receptions")].var()
    var_te = samples[("te", "receptions")].var()
    assert var_rb > var_te + 0.5
    assert var_te == pytest.approx(4.0 + 16.0 * (np.exp(0.118**2) - 1.0), rel=0.05)


def test_rushing_takes_the_banked_shape_and_falls_back_to_a_normal() -> None:
    from velocity.models.props_football import _PlayerMeans, load_rush_pool

    rng = np.random.default_rng(5)
    back = _PlayerMeans(key="rb", name="Back", position="RB", means={"rush_yards": 60.0})
    # A right-skewed synthetic pool: long runs make the upper tail.
    pool_z = np.random.default_rng(1).gamma(2.0, 1.0, 20_000)
    pool_z = (pool_z - pool_z.mean()) / pool_z.std()
    skewed = FootballPropConfig(n_sims=100_000, rush_pool={"RB": pool_z})
    normal = FootballPropConfig(n_sims=100_000, rush_pool={})
    rush_skewed = simulate_team_props([back], rng, skewed)[("rb", "rush_yards")]
    rush_normal = simulate_team_props(
        [back], np.random.default_rng(5), normal)[("rb", "rush_yards")]
    # The pool's bounded left tail keeps the zero clip out of play; the
    # normal at this width loses 9% of its mass below zero and the clip
    # lifts its mean — the §2.3 censoring, as before, now visible.
    assert rush_skewed.mean() == pytest.approx(60.0, abs=1.5)
    assert rush_normal.mean() == pytest.approx(60.0, abs=3.0)
    # Same width (the fitted CV), different shape: the pool's skew survives
    # into the deep upper tail where alt-line overs are priced.
    assert rush_skewed.std() == pytest.approx(rush_normal.std(), rel=0.1)
    assert np.mean(rush_skewed > 160.0) > 1.5 * np.mean(rush_normal > 160.0)
    # The committed bank loads, is right-skewed, and prices the mean back.
    bank = load_rush_pool()
    assert bank is not None and "RB" in bank and "QB" in bank
    z = bank["RB"]
    assert abs(z.mean()) < 0.05 and float(((z - z.mean()) ** 3).mean()) > 0.3
    banked = simulate_team_props([back], np.random.default_rng(5),
                                 FootballPropConfig(n_sims=100_000))[("rb", "rush_yards")]
    assert banked.mean() == pytest.approx(60.0, abs=1.5)


def test_a_missing_bank_is_the_normal_not_an_error(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from velocity.models.props_football import load_rush_pool

    assert load_rush_pool(tmp_path / "absent.parquet") is None


# --- rush + receiving yards, and interceptions -------------------------------
#
# Both markets were added on 2026-09-16 after the BettingPros coverage report
# showed their boards being served and abstained on. Each was measured against
# the banked player-weeks before it was added rather than reasoned about, which
# is the lesson #207 charged for: a test that pins a guess keeps the guess.


def test_combined_yards_is_the_per_sim_sum_of_the_two_legs() -> None:
    """Not a convolution of two marginals — the identity has to hold per draw.

    This is the whole reason the market is cheap to add: the legs are already
    simulated jointly, so summing them is exact whatever their dependence.
    """
    samples = simulate_team_props(
        team_player_means(_fp_frame(), "KC"), np.random.default_rng(5), CFG
    )
    combined = samples[("kc_rb", "rush_rec_yards")]
    legs = samples[("kc_rb", "rush_yards")] + samples[("kc_rb", "receiving_yards")]
    assert np.array_equal(combined, legs)


def test_combined_yards_recovers_the_summed_projection() -> None:
    samples = simulate_team_props(
        team_player_means(_fp_frame(), "KC"), np.random.default_rng(5), CFG
    )
    # Pacheco: 62 rushing + 18 receiving. Rushing is clipped at zero, which
    # lifts its mean, so the band matches the rush_yards test's.
    assert np.mean(samples[("kc_rb", "rush_rec_yards")]) == pytest.approx(80.0, rel=0.15)


def test_combined_yards_is_gated_on_the_combined_projection() -> None:
    """A back under both single-leg floors still prices if the sum clears.

    Gating on either leg alone would drop exactly the receiving backs this
    market exists for.
    """
    rows = [
        ("split", "Split Back", "NE", "RB", "rush_yds", 4.0),   # under the 5.0 floor
        ("split", "Split Back", "NE", "RB", "rec", 3.0),
        ("split", "Split Back", "NE", "RB", "rec_yds", 24.0),
        ("tiny", "Tiny Role", "NE", "RB", "rush_yds", 6.0),
        ("tiny", "Tiny Role", "NE", "RB", "rec", 0.6),
        ("tiny", "Tiny Role", "NE", "RB", "rec_yds", 5.0),      # 11.0 combined
    ]
    fp = pd.DataFrame(
        rows, columns=["player_id", "player_name", "team", "position", "stat", "value"]
    )
    samples = simulate_team_props(
        team_player_means(fp, "NE"), np.random.default_rng(5), CFG
    )
    # 4 + 24 = 28 clears the 20.0 combined floor even though the rushing leg
    # never cleared its own and contributes nothing.
    assert ("split", "rush_yards") not in samples
    assert ("split", "rush_rec_yards") in samples
    # 6 + 5 = 11 does not clear it.
    assert ("tiny", "rush_rec_yards") not in samples


def test_the_two_yardage_legs_are_near_independent_in_the_sim() -> None:
    """Pins the ~1% of spread this market knowingly gives up.

    The legs ride separate team multipliers, so the sim puts a player's own
    rushing against his own receiving at r~0. The banked RB games say the
    within-player-season residual correlation is 0.0255 (the pooled 0.080 is
    mostly player quality, which the projection already carries), so the sum's
    sd comes out ~1% narrow. That was measured and declined, not missed — and
    if someone later induces the correlation, this test should fail and be
    updated rather than quietly keep passing.
    """
    samples = simulate_team_props(
        team_player_means(_fp_frame(), "KC"), np.random.default_rng(13), CFG
    )
    rush = samples[("kc_rb", "rush_yards")]
    rec = samples[("kc_rb", "receiving_yards")]
    corr = float(np.corrcoef(rush, rec)[0, 1])
    assert abs(corr) < 0.03, f"the legs correlate at {corr:.4f} — the ~1% note is stale"


def test_interceptions_recover_the_projection_and_stay_poisson() -> None:
    """Observed variance/mean on 3,219 banked QB games is 1.020.

    Poisson times the lognormal pass multiplier gives 1.012, which is why this
    market rides the structure pass_tds already uses — it is in fact a closer
    fit than pass_tds itself, which is mildly UNDERdispersed at 0.886.
    """
    samples = simulate_team_props(
        team_player_means(_fp_frame(), "KC"), np.random.default_rng(17),
        FootballPropConfig(n_sims=200_000),
    )
    ints = samples[("kc_qb", "interceptions")]
    assert np.mean(ints) == pytest.approx(0.72, rel=0.05)
    assert float(np.var(ints) / np.mean(ints)) == pytest.approx(1.012, abs=0.02)
    # A count market: whole numbers only, never negative.
    assert np.all(ints >= 0)
    assert np.array_equal(ints, np.round(ints))


def test_interceptions_read_either_spelling() -> None:
    """``pass_int`` and ``pass_ints`` both appear in the wild (dfs/scoring.py)."""
    for key in ("pass_int", "pass_ints"):
        fp = pd.DataFrame(
            [("qb", "A QB", "NE", "QB", key, 0.9)],
            columns=["player_id", "player_name", "team", "position", "stat", "value"],
        )
        players = team_player_means(fp, "NE")
        assert players[0].means["interceptions"] == 0.9, key


def test_a_qb_without_an_interception_projection_abstains() -> None:
    """Josh Allen carries no ``pass_int`` row — no projection, no market."""
    samples = simulate_team_props(
        team_player_means(_fp_frame(), "BUF"), np.random.default_rng(5), CFG
    )
    assert ("buf_qb", "interceptions") not in samples


# --- attempt counts -----------------------------------------------------------
#
# Added once the FantasyPros stat-key census (run 35108513721) confirmed the
# feed serves rush_att and pass_att. Dispersion is FITTED from the banked
# player-weeks, not assumed, and these pin the fit rather than the happy path.


def test_attempt_projections_are_recovered_in_expectation() -> None:
    samples = simulate_team_props(
        team_player_means(_fp_frame(), "KC"), np.random.default_rng(23),
        FootballPropConfig(n_sims=200_000),
    )
    assert np.mean(samples[("kc_qb", "pass_attempts")]) == pytest.approx(34.0, rel=0.02)
    assert np.mean(samples[("kc_rb", "rush_attempts")]) == pytest.approx(14.0, rel=0.02)


def test_attempts_are_counts_not_continuous() -> None:
    samples = simulate_team_props(
        team_player_means(_fp_frame(), "KC"), np.random.default_rng(23), CFG
    )
    for market in ("pass_attempts", "rush_attempts"):
        key = "kc_qb" if market == "pass_attempts" else "kc_rb"
        draws = samples[(key, market)]
        assert np.all(draws >= 0)
        assert np.array_equal(draws, np.round(draws))


def test_attempt_dispersion_matches_the_banked_fit() -> None:
    """The reason these markets could be added at all.

    Within player-season on the banked weeks, QB pass attempts run
    variance/mean 2.332 at a median 31.7 attempts. The structure — a
    gamma-mixed Poisson on the passing multiplier, phi 0.0260 — reproduces it.
    A bare Poisson would say 1.0 and price the tails far too tight.
    """
    samples = simulate_team_props(
        team_player_means(_fp_frame(), "KC"), np.random.default_rng(29),
        FootballPropConfig(n_sims=400_000),
    )
    draws = samples[("kc_qb", "pass_attempts")]
    ratio = float(np.var(draws) / np.mean(draws))
    assert ratio == pytest.approx(2.39, abs=0.15), f"var/mean {ratio:.3f}"
    assert ratio > 2.0, "a bare Poisson (1.0) would under-price every tail"


def test_carries_ride_the_rushing_multiplier_not_the_passing_one() -> None:
    """Crossing them would be backwards.

    The script that lifts a passing game suppresses the running game. If
    carries were drawn off ``pass_mult`` a back's workload would rise with his
    quarterback's, so this pins that carries move with a teammate's RUSHING
    yards more than with the team's passing volume.
    """
    samples = simulate_team_props(
        team_player_means(_fp_frame(), "KC"), np.random.default_rng(31),
        FootballPropConfig(n_sims=200_000),
    )
    carries = samples[("kc_rb", "rush_attempts")]
    with_rush = float(np.corrcoef(carries, samples[("kc_rb", "rush_yards")])[0, 1])
    with_pass = float(np.corrcoef(carries, samples[("kc_qb", "pass_yards")])[0, 1])
    assert with_rush > with_pass, (
        f"carries correlate {with_rush:.3f} with rushing and {with_pass:.3f} "
        "with passing — they are riding the wrong multiplier"
    )


def test_a_receiver_end_around_is_not_a_rushing_attempts_prop() -> None:
    """No book posts one, and the bank barely has the data.

    WR carries are 24 player-seasons against the backs' 365, so the floor
    keeps this market to players who actually carry it.
    """
    rows = [
        ("wr", "A Receiver", "NE", "WR", "rec", 5.0),
        ("wr", "A Receiver", "NE", "WR", "rec_yds", 60.0),
        ("wr", "A Receiver", "NE", "WR", "rush_att", 0.4),
    ]
    fp = pd.DataFrame(
        rows, columns=["player_id", "player_name", "team", "position", "stat", "value"]
    )
    samples = simulate_team_props(
        team_player_means(fp, "NE"), np.random.default_rng(3), CFG
    )
    assert ("wr", "rush_attempts") not in samples


def test_a_player_without_an_attempt_projection_abstains() -> None:
    samples = simulate_team_props(
        team_player_means(_fp_frame(), "BUF"), np.random.default_rng(5), CFG
    )
    assert ("buf_qb", "pass_attempts") not in samples


def test_the_shipped_dispersion_is_what_the_fitter_measures() -> None:
    """These constants are a measurement, so they must stay regenerable.

    If the fitter and the config drift apart, one of them is lying about the
    bank — and the config is the one that prices bets.
    """
    import pandas as _pd
    from scripts.fit_prop_dispersion import fit

    weeks = Path("datasets/nfl/player_weeks.parquet")
    if not weeks.exists():  # pragma: no cover - the bank is committed
        pytest.skip("player_weeks bank not present")
    fitted, _ = fit(_pd.read_parquet(weeks))
    config = FootballPropConfig()
    assert fitted["rush_attempts_phi"]["RB"] == pytest.approx(
        config.rush_att_phi("RB"), abs=5e-4)
    assert fitted["rush_attempts_phi"]["QB"] == pytest.approx(
        config.rush_att_phi("QB"), abs=5e-4)
    assert fitted["pass_attempts_phi"] == pytest.approx(
        config.pass_attempts_phi, abs=5e-4)


# --- completions: a binomial on attempts, not a count of its own --------------


def test_completions_can_never_exceed_attempts() -> None:
    """A separate Poisson would happily print 30 completions on 25 attempts.

    The constraint is structural, so the sim has to honour it in every draw,
    not on average.
    """
    samples = simulate_team_props(
        team_player_means(_fp_frame(), "KC"), np.random.default_rng(41),
        FootballPropConfig(n_sims=100_000),
    )
    attempts = samples[("kc_qb", "pass_attempts")]
    completions = samples[("kc_qb", "pass_completions")]
    assert np.all(completions <= attempts)
    assert np.all(completions >= 0)
    assert np.array_equal(completions, np.round(completions))


def test_completions_recover_the_projection() -> None:
    samples = simulate_team_props(
        team_player_means(_fp_frame(), "KC"), np.random.default_rng(41),
        FootballPropConfig(n_sims=200_000),
    )
    assert np.mean(samples[("kc_qb", "pass_completions")]) == pytest.approx(22.1, rel=0.02)


def test_completions_are_strongly_tied_to_attempts() -> None:
    """The reason this is a binomial and not an independent count.

    Within player-season the banked QB games put the correlation at 0.844.
    Drawn independently, an "over attempts + over completions" parlay would
    price as two bets when it is nearly one — so a LOW correlation here is the
    failure, not a high one.
    """
    samples = simulate_team_props(
        team_player_means(_fp_frame(), "KC"), np.random.default_rng(43),
        FootballPropConfig(n_sims=200_000),
    )
    corr = float(np.corrcoef(samples[("kc_qb", "pass_attempts")],
                             samples[("kc_qb", "pass_completions")])[0, 1])
    assert corr > 0.8, f"completions correlate only {corr:.3f} with attempts"


def test_completion_dispersion_lands_near_the_banked_figure() -> None:
    """Banked var/mean is 1.753 at a median 20.3 completions.

    The binomial-on-attempts structure returns ~1.85 — about 6% wide, which is
    the safe direction for a price. Real completion% wobbles game to game by
    more than binomial noise allows; that gap is left uncorrected rather than
    papered over, and this pins the size of it.
    """
    samples = simulate_team_props(
        team_player_means(_fp_frame(), "KC"), np.random.default_rng(47),
        FootballPropConfig(n_sims=400_000),
    )
    draws = samples[("kc_qb", "pass_completions")]
    ratio = float(np.var(draws) / np.mean(draws))
    assert ratio == pytest.approx(1.85, abs=0.2), f"var/mean {ratio:.3f}"


def test_completions_abstain_without_an_attempts_projection() -> None:
    """No denominator, no market — never an invented one."""
    fp = pd.DataFrame(
        [("qb", "A QB", "NE", "QB", "pass_cmp", 15.0),
         ("qb", "A QB", "NE", "QB", "pass_yds", 200.0)],
        columns=["player_id", "player_name", "team", "position", "stat", "value"],
    )
    samples = simulate_team_props(
        team_player_means(fp, "NE"), np.random.default_rng(3), CFG
    )
    assert ("qb", "pass_completions") not in samples


def test_the_completion_rate_comes_from_the_players_own_projection() -> None:
    """A checkdown passer and a deep thrower must not share a league rate."""
    rows = []
    for key, att, cmp_ in (("dink", 30.0, 22.5), ("bomb", 30.0, 16.5)):
        rows += [
            (key, key, "NE", "QB", "pass_att", att),
            (key, key, "NE", "QB", "pass_cmp", cmp_),
        ]
    fp = pd.DataFrame(
        rows, columns=["player_id", "player_name", "team", "position", "stat", "value"]
    )
    samples = simulate_team_props(
        team_player_means(fp, "NE"), np.random.default_rng(7),
        FootballPropConfig(n_sims=100_000),
    )
    assert np.mean(samples[("dink", "pass_completions")]) == pytest.approx(22.5, rel=0.03)
    assert np.mean(samples[("bomb", "pass_completions")]) == pytest.approx(16.5, rel=0.03)
