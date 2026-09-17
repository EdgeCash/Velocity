"""The scale — fitted out of sample on the residual bank, applied around the level.

The level fits the projection's intercept; this fits its slope. The tests pin
the recovery of a known slope, the leak gate on seasons, the identity below
the minimum sample, and that situational bonuses ride on top unscaled.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.models.level import (
    ScaleCalibration,
    ScaledModel,
    mean_projected_total,
    scale_model,
)
from velocity.models.simulate import SimConfig


def _bank(seed: int = 1, n: int = 1500, margin_slope: float = 0.8, total_slope: float = 0.5):
    """A residual bank whose actuals are a known slope of the projections plus noise."""
    rng = np.random.default_rng(seed)
    season = rng.integers(2015, 2025, n)
    mu_margin = rng.normal(0.0, 6.0, n)
    mu_total = rng.normal(45.0, 6.0, n)
    actual_margin = margin_slope * mu_margin + rng.normal(0.0, 13.0, n)
    actual_total = 45.0 + total_slope * (mu_total - 45.0) + rng.normal(0.0, 13.0, n)
    return pd.DataFrame({
        "season": season, "week": 1, "game_id": [f"g{i}" for i in range(n)],
        "mu_margin": mu_margin, "mu_total": mu_total,
        "resid_margin": actual_margin - mu_margin, "resid_total": actual_total - mu_total,
    })


def test_from_residuals_recovers_the_slopes() -> None:
    cal = ScaleCalibration.from_residuals(_bank(n=20000))
    assert cal.n == 20000
    assert cal.margin_slope == pytest.approx(0.8, abs=0.03)
    assert cal.total_slope == pytest.approx(0.5, abs=0.03)


def test_from_residuals_honours_the_leak_gate_and_the_minimum() -> None:
    bank = _bank()
    before = ScaleCalibration.from_residuals(bank, before_season=2018)
    assert before.n == int((bank["season"] < 2018).sum())
    # Too thin: the identity, never a slope on noise.
    thin = ScaleCalibration.from_residuals(bank, before_season=2016, min_games=10_000)
    assert thin == ScaleCalibration()
    assert ScaleCalibration.from_residuals(bank.iloc[0:0]) == ScaleCalibration()
    # Trailing seasons cut from the eligible ones, not the whole bank.
    trailing = ScaleCalibration.from_residuals(bank, before_season=2020, seasons=2)
    eligible = bank[(bank["season"] < 2020) & (bank["season"] >= 2018)]
    assert trailing.n == len(eligible)


def test_apply_scales_the_margin_and_pivots_the_total_on_the_anchor() -> None:
    cal = ScaleCalibration(margin_slope=0.5, total_slope=0.5, n=1)
    home, away = cal.apply(30.0, 20.0, anchor_total=44.0)
    # margin 10 → 5; total 50 → 44 + 0.5·6 = 47
    assert home - away == pytest.approx(5.0)
    assert home + away == pytest.approx(47.0)
    # The identity leaves everything alone.
    assert ScaleCalibration().apply(30.0, 20.0, anchor_total=44.0) == (30.0, 20.0)


class _Flat:
    """A stub model: fixed expected points, home-field aware."""

    def expected_points(self, home: str, away: str, *, neutral_site: bool = False):
        edge = 0.0 if neutral_site else 2.0
        return 28.0 + edge, 20.0


def test_scaled_model_adds_bonuses_after_the_scale_and_simulates() -> None:
    cal = ScaleCalibration(margin_slope=0.5, total_slope=0.5, n=1)
    model = ScaledModel(_Flat(), cal, anchor_total=44.0, sim=SimConfig(n_sims=2000))
    home, away = model.expected_points("H", "A", home_bonus=1.0, away_bonus=-1.0)
    # inner: 30 / 20 → scaled margin 5, total 47 → 26 / 21 → bonuses → 27 / 20.
    assert (home, away) == pytest.approx((27.0, 20.0))
    proj = model.project("H", "A", rng=np.random.default_rng(0))
    assert proj.mu_home == pytest.approx(26.0) and proj.mu_away == pytest.approx(21.0)
    assert 0.5 < proj.p_home_win() < 1.0


def test_scale_model_anchors_on_the_models_own_mean_total() -> None:
    games = pd.DataFrame({
        "season": [2024, 2024, 2025], "home_team": ["H", "H", "H"], "away_team": ["A", "A", "A"],
        "home_score": [1.0, 2.0, 3.0], "away_score": [0.0, 0.0, 0.0],
        "neutral_site": [False, True, False],
    })
    assert mean_projected_total(_Flat(), games) == pytest.approx((50 + 48 + 50) / 3)
    assert mean_projected_total(_Flat(), games, seasons=1) == pytest.approx(50.0)
    assert mean_projected_total(_Flat(), games.iloc[0:0]) is None
    scaled, cal = scale_model(_Flat(), _bank(n=3000), games, SimConfig(n_sims=100),
                              before_season=2030, anchor_seasons=1)
    assert cal.n == 3000 and scaled.anchor_total == pytest.approx(50.0)
    # No games to anchor on: the total is left unscaled, the margin still is.
    scaled, cal = scale_model(_Flat(), _bank(n=3000), games.iloc[0:0], SimConfig(n_sims=100))
    assert cal.total_slope == 1.0 and cal.margin_slope != 1.0
