"""The scale's home-margin intercept (velocity.models.level.ScaleCalibration.margin_shift).

Offline. The college scale's margin slope (1.35) fitted without its intercept
inflated the home edge by two points on every home-and-away game
(docs/MODEL_LAB.md, the home-margin round); with ``shift`` the intercept is
kept, fitted on home-and-away rows, and applied to those games only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.models.level import ScaleCalibration, ScaledModel, scale_model
from velocity.models.simulate import SimConfig


def _bank(n: int = 600, slope: float = 1.3, shift: float = -2.0, seed: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    mu_m = rng.normal(5.0, 10.0, n)
    mu_t = rng.normal(50.0, 6.0, n)
    game_id = [f"g{i}" for i in range(n)]
    neutral = np.arange(n) % 10 == 0  # every tenth game on a neutral field
    # Sited games carry the home bias; neutral ones do not.
    act_m = np.where(neutral, slope * mu_m, shift + slope * mu_m) + rng.normal(0.0, 0.5, n)
    act_t = mu_t + rng.normal(0.0, 0.5, n)
    return pd.DataFrame({
        "season": 2024, "week": 1 + np.arange(n) % 12, "game_id": game_id,
        "mu_margin": mu_m, "mu_total": mu_t,
        "resid_margin": act_m - mu_m, "resid_total": act_t - mu_t,
    }), {g for g, f in zip(game_id, neutral, strict=True) if f}


def test_shift_keeps_the_intercept_fitted_on_sited_games_only() -> None:
    bank, neutral_ids = _bank()
    plain = ScaleCalibration.from_residuals(bank)
    shifted = ScaleCalibration.from_residuals(bank, shift=True, neutral_ids=neutral_ids)
    assert plain.margin_shift == 0.0
    assert shifted.margin_shift == pytest.approx(-2.0, abs=0.15)
    assert shifted.margin_slope == pytest.approx(1.3, abs=0.02)
    assert shifted.n == plain.n == len(bank)
    # Fitting on every row (neutral games included) drags the intercept
    # toward zero — the point of excluding them.
    mixed = ScaleCalibration.from_residuals(bank, shift=True)
    assert mixed.margin_shift > shifted.margin_shift
    # The identity, and the shift with it, under the game floor.
    assert ScaleCalibration.from_residuals(bank.head(5), shift=True).margin_shift == 0.0


def test_apply_shifts_sited_games_and_leaves_a_neutral_field_alone() -> None:
    cal = ScaleCalibration(margin_slope=1.2, total_slope=1.0, n=500, margin_shift=-2.0)
    home, away = cal.apply(30.0, 20.0, anchor_total=50.0)
    assert home - away == pytest.approx(10.0 * 1.2 - 2.0)
    assert home + away == pytest.approx(50.0)
    n_home, n_away = cal.apply(30.0, 20.0, anchor_total=50.0, neutral_site=True)
    assert n_home - n_away == pytest.approx(12.0)


class _Even:
    def expected_points(self, home: str, away: str, *, neutral_site: bool = False):  # type: ignore[no-untyped-def]
        return (27.0, 23.0) if not neutral_site else (25.0, 25.0)


def test_scale_model_reads_the_neutral_flags_off_the_games_frame() -> None:
    bank, neutral_ids = _bank()
    games = pd.DataFrame({
        "game_id": bank["game_id"], "season": 2024, "week": bank["week"],
        "home_team": "A", "away_team": "B", "home_score": 24.0, "away_score": 21.0,
        "neutral_site": bank["game_id"].isin(neutral_ids),
    })
    sim = SimConfig(n_sims=100)
    model, cal = scale_model(_Even(), bank, games, sim, shift=True)
    assert isinstance(model, ScaledModel)
    assert cal.margin_shift == pytest.approx(-2.0, abs=0.15)
    sited = model.expected_points("A", "B")
    neutral = model.expected_points("A", "B", neutral_site=True)
    assert sited[0] - sited[1] == pytest.approx(4.0 * cal.margin_slope + cal.margin_shift)
    assert neutral[0] - neutral[1] == pytest.approx(0.0)
    # Without the flag the calibration is the old one.
    _model, plain = scale_model(_Even(), bank, games, sim)
    assert plain.margin_shift == 0.0
