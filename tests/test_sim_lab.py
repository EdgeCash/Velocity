"""The sim-shape gate scores an empirical draw against what happened."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
from velocity.models.simulate import SimConfig

_SPEC = importlib.util.spec_from_file_location(
    "sim_lab", Path(__file__).resolve().parents[1] / "scripts" / "sim_lab.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def _residuals(n: int = 600, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    mu_total = rng.uniform(40.0, 60.0, size=n)
    mu_margin = rng.normal(0.0, 6.0, size=n)
    return pd.DataFrame({
        "season": np.repeat([2019, 2020, 2021, 2022], n // 4),
        "week": np.tile(np.arange(1, 1 + n // 4), 4) % 17 + 1,
        "game_id": [f"g{i}" for i in range(n)],
        "mu_margin": mu_margin, "mu_total": mu_total,
        "resid_margin": rng.standard_t(6, size=n) * 12.0,
        "resid_total": rng.normal(0.0, 13.0, size=n),
    })


def test_season_configs_fit_on_train_only_and_span_the_grid() -> None:
    res = _residuals()
    train = res[res["season"] < 2022]
    base = SimConfig(n_sims=2000, sd_margin=13.0, sd_total=13.6)
    configs = _MOD.season_configs(train, base)
    assert list(configs) == ["normal", "normal-hetero", "empirical", "empirical-hetero"]
    assert configs["normal"] == base
    assert configs["normal-hetero"].sd_anchor_total > 0
    assert configs["empirical"].residuals is not None
    assert len(configs["empirical"].residuals) == 2 * len(train)  # mirrored pairs
    assert configs["empirical-hetero"].residuals is not None
    assert configs["empirical-hetero"].sd_total_slope == configs["normal-hetero"].sd_total_slope


def test_score_variant_returns_the_gate_columns_in_range() -> None:
    res = _residuals()
    test = res[res["season"] == 2022]
    cfg = SimConfig(n_sims=2000, sd_margin=13.0, sd_total=13.6)
    scored = _MOD.score_variant(test, cfg, seed=7)
    for key in ("ece", "brier", "spread_shoulder_max", "spread_tail_max",
                "total_shoulder_max", "total_tail_max", "spread_mean", "total_mean"):
        assert 0.0 <= scored[key] <= 1.0, key
    # The tail errors are bounded by the tail mass itself, which is small.
    assert scored["spread_tail_max"] < scored["spread_shoulder_max"] + 0.2
    # Deterministic under the seed.
    again = _MOD.score_variant(test, cfg, seed=7)
    assert again == scored
