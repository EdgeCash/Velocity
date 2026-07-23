"""MLB rate projections (Phase M2) — counts shrink to per-PA outcome rates.

Pins the invariants the M3 sim relies on: every rate vector sums to 1, thin
samples regress hard toward the league prior while large samples sit near their
observed rate, and the HR park factor is multiplicative with a neutral identity.
Deterministic hand-built frames plus one integration pass through the M1
normalizer.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from velocity.features.baseball import (
    BIP_OUTCOMES,
    PA_OUTCOMES,
    RateConfig,
    apply_hr_park_factor,
    project_bip_profile,
    project_pa_rates,
)
from velocity.ingest.mlb import normalize_player_stats

FIXTURES = Path(__file__).parent / "fixtures"


def _stats(rows: list[dict]) -> pd.DataFrame:
    """A BaseballStats-shaped frame from partial rows (missing counts → 0)."""
    cols = ["player_id", "role", "pa", "k", "bb", "hbp", "singles", "doubles", "triples", "hr"]
    frame = pd.DataFrame(rows)
    for col in cols:
        if col not in frame.columns:
            frame[col] = 0
    return frame


def test_pa_rates_sum_to_one() -> None:
    stats = _stats(
        [
            {"player_id": "a", "role": "bat", "pa": 500, "k": 110, "bb": 70, "hbp": 5, "hr": 40},
            {"player_id": "b", "role": "pit", "pa": 600, "k": 180, "bb": 40, "hbp": 6, "hr": 18},
        ]
    )
    rates = project_pa_rates(stats)
    totals = rates[PA_OUTCOMES].sum(axis=1)
    assert np.allclose(totals, 1.0)
    # Every rate is a probability.
    assert (rates[PA_OUTCOMES] >= 0).all().all()
    assert (rates[PA_OUTCOMES] <= 1).all().all()


def test_pa_rate_known_value() -> None:
    stats = _stats([{"player_id": "a", "role": "bat", "pa": 500, "k": 110}])
    rates = project_pa_rates(stats)
    # (110 + 300*0.225) / (500 + 300) = 177.5 / 800.
    assert rates.loc[0, "k"] == pytest.approx(177.5 / 800.0)


def test_thin_sample_regresses_large_sample_does_not() -> None:
    stats = _stats(
        [
            {"player_id": "thin", "role": "bat", "pa": 4, "k": 4},
            {"player_id": "thick", "role": "bat", "pa": 6000, "k": 2400},
        ]
    )
    rates = project_pa_rates(stats).set_index("player_id")
    # 4-for-4 K in 4 PA is pulled almost all the way back to the ~0.225 league prior.
    assert rates.loc["thin", "k"] < 0.30
    # 0.40 K-rate over 6000 PA barely moves.
    assert rates.loc["thick", "k"] > 0.37


def test_prior_strength_controls_regression() -> None:
    stats = _stats([{"player_id": "a", "role": "bat", "pa": 200, "k": 100}])
    soft = project_pa_rates(stats, RateConfig(pa_prior_strength=50)).loc[0, "k"]
    hard = project_pa_rates(stats, RateConfig(pa_prior_strength=2000)).loc[0, "k"]
    # Observed 0.50 K-rate: a stronger prior pulls the estimate further down.
    assert 0.225 < hard < soft < 0.50


def test_bip_profile_sums_to_one_and_batters_only() -> None:
    stats = _stats(
        [
            {
                "player_id": "bat1", "role": "bat", "pa": 600, "k": 100, "bb": 50, "hbp": 0,
                "hr": 30, "singles": 120, "doubles": 40, "triples": 5,
            },
            {"player_id": "pit1", "role": "pit", "pa": 600, "k": 180, "hr": 18},
        ]
    )
    bip = project_bip_profile(stats)
    assert list(bip["player_id"]) == ["bat1"]  # the pitcher is excluded
    assert np.allclose(bip[BIP_OUTCOMES].sum(axis=1), 1.0)


def test_bip_known_value() -> None:
    stats = _stats(
        [{
            "player_id": "bat1", "role": "bat", "pa": 600, "k": 100, "bb": 50, "hbp": 0,
            "hr": 30, "singles": 120, "doubles": 40, "triples": 5,
        }]
    )
    bip = project_bip_profile(stats)
    # in_play = 600 - 100 - 50 - 0 - 30 = 420; single = (120 + 200*0.212) / (420 + 200).
    assert bip.loc[0, "single"] == pytest.approx((120 + 200 * 0.212) / 620.0)


def test_park_factor_neutral_is_identity() -> None:
    stats = _stats([{"player_id": "a", "role": "bat", "pa": 500, "k": 110, "hr": 40}])
    rates = project_pa_rates(stats)
    same = apply_hr_park_factor(rates, 1.0)
    pd.testing.assert_frame_equal(rates[PA_OUTCOMES], same[PA_OUTCOMES])


def test_park_factor_raises_hr_and_keeps_sum_one() -> None:
    stats = _stats([{"player_id": "a", "role": "bat", "pa": 500, "k": 110, "hr": 40}])
    rates = project_pa_rates(stats)
    hitter_park = apply_hr_park_factor(rates, 1.5)
    assert hitter_park.loc[0, "hr"] > rates.loc[0, "hr"]
    assert hitter_park.loc[0, "k"] < rates.loc[0, "k"]  # others renormalize down
    assert np.allclose(hitter_park[PA_OUTCOMES].sum(axis=1), 1.0)


def test_bad_config_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        RateConfig(pa_prior_strength=-1.0)


def test_integration_from_normalized_fixture() -> None:
    """The M1 normalizer output feeds project_pa_rates directly."""
    payload = json.loads((FIXTURES / "mlb_hitting.json").read_text())
    bat = normalize_player_stats(payload, "bat")
    rates = project_pa_rates(bat)
    assert np.allclose(rates[PA_OUTCOMES].sum(axis=1), 1.0)
    # Ohtani: 110 K in 500 PA → (110 + 300*0.225) / 800.
    ohtani = rates.set_index("player_id").loc["660271"]
    assert ohtani["k"] == pytest.approx(177.5 / 800.0)
