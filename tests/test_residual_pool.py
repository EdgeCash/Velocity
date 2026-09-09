"""The sim's empirical shape and heteroscedastic dispersion (SYSTEM_REVIEW §2)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.models.residuals import (
    ResidualPool,
    fit_sd_slope,
    load_residual_pool,
    residuals_from_projections,
)
from velocity.models.simulate import SimConfig, simulate_game
from velocity.util.seed import make_rng


def _football_pool(n: int = 4000, seed: int = 3) -> ResidualPool:
    # A leptokurtic margin with mass on 3 and 7, and a mildly correlated total —
    # the shape a normal cannot draw. Mirrored so the pool's mean is exactly
    # zero and centering moves nothing (the spikes stay on ±3 / ±7).
    rng = np.random.default_rng(seed)
    base = rng.standard_t(df=5, size=n) * 11.0
    keys = rng.choice([-7.0, -3.0, 3.0, 7.0], size=n)
    margin = np.where(rng.random(n) < 0.25, keys, np.rint(base))
    total = np.rint(0.3 * margin + rng.normal(0.0, 12.5, size=n))
    margin = np.concatenate([margin, -margin])
    total = np.concatenate([total, -total])
    return ResidualPool.from_residuals(margin, total)


def test_pool_is_centered_and_standardized() -> None:
    pool = _football_pool()
    assert pool.margin_z.mean() == pytest.approx(0.0, abs=1e-9)
    assert pool.total_z.mean() == pytest.approx(0.0, abs=1e-9)
    assert pool.margin_z.std(ddof=1) == pytest.approx(1.0)
    assert pool.total_z.std(ddof=1) == pytest.approx(1.0)
    assert pool.sd_margin > 0 and pool.sd_total > 0
    assert 0.1 < pool.correlation < 0.5


def test_pool_rejects_degenerate_input() -> None:
    with pytest.raises(ValueError):
        ResidualPool.from_residuals([1.0], [2.0])
    with pytest.raises(ValueError):
        ResidualPool.from_residuals([1.0, 1.0, 1.0], [2.0, 3.0, 4.0])


def test_empirical_draw_is_deterministic_and_keeps_the_moments() -> None:
    pool = _football_pool()
    cfg = SimConfig(n_sims=100_000, sd_margin=13.0, sd_total=13.6,
                    round_scores=False, residuals=pool)
    # A high total keeps the zero-floor on team scores out of play, so the
    # sample moments read the draw itself (the floor is §2.3's censoring,
    # deliberately unchanged here).
    a = simulate_game(4.0, 90.0, make_rng(), cfg)
    b = simulate_game(4.0, 90.0, make_rng(), cfg)
    assert np.array_equal(a.home_score, b.home_score)
    # The pool supplies shape; the config's sds still set the width and the
    # model's μ the location.
    assert a.margin.mean() == pytest.approx(4.0, abs=0.2)
    assert a.total.mean() == pytest.approx(90.0, abs=0.2)
    assert a.margin.std() == pytest.approx(13.0, rel=0.03)
    assert a.total.std() == pytest.approx(13.6, rel=0.03)
    # And the dependence is the pool's, not a correlation knob's.
    assert np.corrcoef(a.margin, a.total)[0, 1] == pytest.approx(pool.correlation, abs=0.03)


def test_empirical_draw_lands_on_the_pools_numbers() -> None:
    # A normal spreads mass smoothly; the pool has 25% of its games on ±3/±7
    # (scaled by 13/pool.sd here, so the spikes sit at fixed z's). The sim
    # inherits the spikes: its margin distribution has more mass at the
    # pool's modal values than the normal puts there.
    pool = _football_pool()
    normal = SimConfig(n_sims=100_000, sd_margin=pool.sd_margin, round_scores=False)
    empirical = SimConfig(n_sims=100_000, sd_margin=pool.sd_margin,
                          round_scores=False, residuals=pool)
    n_sim = simulate_game(0.0, 45.0, make_rng(), normal)
    e_sim = simulate_game(0.0, 45.0, make_rng(), empirical)

    def at3(sim: object) -> float:
        return float(np.mean(np.abs(np.abs(sim.margin) - 3.0) < 0.01))  # type: ignore[attr-defined]

    assert at3(e_sim) > 0.05
    assert at3(e_sim) > 20 * at3(n_sim)


def test_heteroscedastic_sd_widens_with_the_expected_total() -> None:
    cfg = SimConfig(n_sims=100_000, sd_margin=18.2, sd_total=16.7,
                    sd_total_slope=0.13, sd_margin_slope=0.05,
                    sd_anchor_total=55.0, round_scores=False)
    assert cfg.effective_sds(55.0) == (18.2, 16.7)
    lo_m, lo_t = cfg.effective_sds(44.0)
    hi_m, hi_t = cfg.effective_sds(66.0)
    assert lo_t == pytest.approx(16.7 - 0.13 * 11)
    assert hi_t == pytest.approx(16.7 + 0.13 * 11)
    assert lo_m < 18.2 < hi_m
    # Floored: an absurd μ cannot invert the noise.
    assert cfg.effective_sds(-1000.0) == (0.5 * 18.2, 0.5 * 16.7)
    low = simulate_game(0.0, 44.0, make_rng(), cfg)
    high = simulate_game(0.0, 66.0, make_rng(), cfg)
    assert low.total.std() == pytest.approx(lo_t, rel=0.03)
    assert high.total.std() == pytest.approx(hi_t, rel=0.03)


def test_slopes_without_an_anchor_are_refused_and_zero_slopes_change_nothing() -> None:
    with pytest.raises(ValueError):
        SimConfig(sd_total_slope=0.1)
    plain = SimConfig(n_sims=20_000)
    anchored = SimConfig(n_sims=20_000, sd_anchor_total=45.0)
    a = simulate_game(3.0, 45.0, make_rng(), plain)
    b = simulate_game(3.0, 45.0, make_rng(), anchored)
    assert np.array_equal(a.home_score, b.home_score)


def test_fit_sd_slope_recovers_a_planted_slope() -> None:
    rng = np.random.default_rng(11)
    mu = rng.uniform(40.0, 70.0, size=20_000)
    true_sd = 16.7 + 0.13 * (mu - 55.0)
    resid = rng.normal(0.0, true_sd)
    sd_at, slope, anchor = fit_sd_slope(mu, resid)
    assert anchor == pytest.approx(55.0, abs=0.3)
    assert slope == pytest.approx(0.13, abs=0.03)
    assert sd_at == pytest.approx(16.7, abs=0.4)


def test_residuals_from_projections_uses_the_models_own_mu() -> None:
    projections = pd.DataFrame([
        {"season": 2025, "week": 1, "game_id": "g1", "p_home_win": 0.6,
         "home_win": 1.0, "fair_spread": -6.0, "fair_total": 44.0},
        {"season": 2025, "week": 1, "game_id": "g2", "p_home_win": 0.4,
         "home_win": 0.0, "fair_spread": 3.0, "fair_total": 50.0},
        {"season": 2025, "week": 2, "game_id": "g3", "p_home_win": 0.5,
         "home_win": 1.0, "fair_spread": 0.0, "fair_total": 41.0},
    ])
    games = pd.DataFrame([
        {"game_id": "g1", "home_score": 27, "away_score": 17},
        {"game_id": "g2", "home_score": 20, "away_score": 24},
        {"game_id": "g3", "home_score": None, "away_score": None},
    ])
    out = residuals_from_projections(projections, games)
    assert out["game_id"].tolist() == ["g1", "g2"]  # the unplayed game drops
    g1 = out.iloc[0]
    assert g1["mu_margin"] == 6.0 and g1["resid_margin"] == pytest.approx(4.0)
    assert g1["resid_total"] == pytest.approx(0.0)
    g2 = out.iloc[1]
    assert g2["mu_margin"] == -3.0 and g2["resid_margin"] == pytest.approx(-1.0)
    assert g2["resid_total"] == pytest.approx(-6.0)


def test_load_residual_pool_is_none_without_a_bank(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert load_residual_pool("nfl", tmp_path) is None
    (tmp_path / "nfl").mkdir()
    pd.DataFrame({
        "season": [2024] * 4, "week": [1, 1, 2, 2], "game_id": list("abcd"),
        "mu_margin": [1.0, 2.0, 3.0, 4.0], "mu_total": [44.0] * 4,
        "resid_margin": [3.0, -7.0, 10.0, -3.0], "resid_total": [5.0, -2.0, 8.0, -11.0],
    }).to_parquet(tmp_path / "nfl" / "sim_residuals.parquet", index=False)
    pool = load_residual_pool("nfl", tmp_path)
    assert pool is not None and len(pool) == 8  # four games, mirrored
    raw = ResidualPool.from_frame(pd.read_parquet(tmp_path / "nfl" / "sim_residuals.parquet"),
                                  symmetric=False)
    assert len(raw) == 4


def test_a_symmetric_pool_prices_a_pickem_at_exactly_half() -> None:
    rng = np.random.default_rng(9)
    skewed = rng.gamma(2.0, 6.0, size=3000) - 12.0  # right-skewed residuals
    pool = ResidualPool.from_residuals(skewed, rng.normal(size=3000))
    assert np.median(pool.margin_z) == pytest.approx(0.0, abs=1e-9)
    assert float(np.mean(pool.margin_z > 0)) == pytest.approx(0.5, abs=1e-3)
    cfg = SimConfig(n_sims=200_000, residuals=pool, round_scores=False)
    assert simulate_game(0.0, 60.0, make_rng(), cfg).p_home_win() == pytest.approx(0.5, abs=0.005)
    raw = ResidualPool.from_residuals(skewed, rng.normal(size=3000), symmetric=False)
    assert abs(float(np.mean(raw.margin_z > 0)) - 0.5) > 0.02
