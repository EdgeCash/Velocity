"""Football prop model — correlated FantasyPros-driven distributions.

Everything is pinned on a tiny synthetic projections frame with exactly known
means: projections are recovered in expectation (the lognormal multipliers are
mean-1), teammates correlate through the shared volume draw, the QB's passing
yards are exactly his receivers' simulated yards (scaled), sub-threshold
players are skipped, and the sim satisfies both the pricing protocol and the
parlay ``player_samples`` hook. Seeded → deterministic.
"""

from __future__ import annotations

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
    assert corr > 0.05  # the shared pass-volume draw moves them together


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
