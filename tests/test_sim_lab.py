"""The sim-shape gate scores each candidate sim against what happened."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
from velocity.models.drive import DriveConfig
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
    drive = DriveConfig(n_sims=2000)
    configs = _MOD.season_configs(train, base, drive)
    assert list(configs) == ["normal", "normal-hetero", "empirical",
                             "empirical-hetero", "normal+keys", "drive",
                             "drive-fit", "drive-fit+keys"]
    assert configs["normal"] == base
    assert configs["normal-hetero"].sd_anchor_total > 0
    assert configs["empirical"].residuals is not None
    assert len(configs["empirical"].residuals) == 2 * len(train)  # mirrored pairs
    assert configs["empirical-hetero"].residuals is not None
    assert configs["empirical-hetero"].sd_total_slope == configs["normal-hetero"].sd_total_slope
    # The unfitted drive sim is the league's own football, untouched.
    assert configs["drive"] == drive
    # The fitted one moves only what it is allowed to move: the two spread
    # mechanisms. The scoring lattice is not a dispersion knob (see
    # velocity/models/drive.py — this is the college run's lesson).
    fitted = configs["drive-fit"]
    assert fitted.fg_to_td == drive.fg_to_td and fitted.drives == drive.drives
    assert fitted.strength_sd > 0
    # An overlay corrects the sim it names and nothing else about it.
    assert configs["normal+keys"].base == base
    assert configs["normal+keys"].weights.weights.size > 1
    assert configs["drive-fit+keys"].base == fitted


def test_score_variant_returns_the_gate_columns_in_range() -> None:
    res = _residuals()
    test = res[res["season"] == 2022]
    cfg = SimConfig(n_sims=2000, sd_margin=13.0, sd_total=13.6)
    scored = _MOD.score_variant(test, cfg, seed=7)
    for key in ("ece", "brier", "spread_shoulder_max", "spread_tail_max",
                "total_shoulder_max", "total_tail_max", "spread_mean", "total_mean",
                "key_max", "key_mean", "grid_mean", "p3", "p7", "real_p3", "real_p7"):
        assert 0.0 <= scored[key] <= 1.0, key
    # The tail errors are bounded by the tail mass itself, which is small.
    assert scored["spread_tail_max"] < scored["spread_shoulder_max"] + 0.2
    # Deterministic under the seed.
    again = _MOD.score_variant(test, cfg, seed=7)
    assert again == scored


def test_the_gate_scores_the_drive_sim_through_the_same_path() -> None:
    """A DriveConfig is dispatched to the possession sampler, not the normal.

    The two must be scored by identical code on identical games or the
    comparison means nothing — so the only thing that changes is the config
    type. The key-number columns are what separate them: the possession
    lattice puts real mass on 3 and 7, and a rounded normal cannot.
    """
    test = _residuals()[lambda d: d["season"] == 2022]
    normal = _MOD.score_variant(
        test, SimConfig(n_sims=4000, sd_margin=13.0, sd_total=13.6), seed=7)
    drive = _MOD.score_variant(test, DriveConfig(n_sims=4000), seed=7)
    assert drive["p3"] > normal["p3"] and drive["p7"] > normal["p7"]
    # And it is still deterministic under the seed, like every other variant.
    assert _MOD.score_variant(test, DriveConfig(n_sims=4000), seed=7) == drive
