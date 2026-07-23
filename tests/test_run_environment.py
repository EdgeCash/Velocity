"""Per-game run environment (velocity.models.run_environment) — pure factors.

Covers the temperature HR multiplier, the non-HR park tilt (residual after HR),
the roof gate, and the combined RunEnvironment. Magnitudes are first-order and
calibration-pending; these pin direction + the neutral identity.
"""

from __future__ import annotations

import pytest
from velocity.models.run_environment import (
    NEUTRAL_TEMP_F,
    game_run_environment,
    park_non_hr_tilt,
    temperature_hr_multiplier,
)


def test_temperature_multiplier_direction_and_neutral() -> None:
    assert temperature_hr_multiplier(NEUTRAL_TEMP_F) == pytest.approx(1.0)
    assert temperature_hr_multiplier(90.0) > 1.0  # hot air carries
    assert temperature_hr_multiplier(45.0) < 1.0  # cold air drags
    # Symmetric around the reference.
    hot_gain = temperature_hr_multiplier(80.0) - 1.0
    cold_loss = 1.0 - temperature_hr_multiplier(60.0)
    assert hot_gain == pytest.approx(cold_loss)


def test_temperature_multiplier_is_clamped() -> None:
    assert temperature_hr_multiplier(200.0) == pytest.approx(1.15)
    assert temperature_hr_multiplier(-50.0) == pytest.approx(0.85)


def test_non_hr_tilt_separates_doubles_parks_from_hr_parks() -> None:
    # Fenway: 108 runs / 96 HR — run boost is doubles/BABIP, not HR → positive tilt.
    assert park_non_hr_tilt(108, 96) > 0
    # Yankee Stadium: 100 runs / 110 HR — HR-driven, no extra non-HR runs → <= 0.
    assert park_non_hr_tilt(100, 110) < 0
    # A neutral park is neutral.
    assert park_non_hr_tilt(100, 100) == pytest.approx(0.0)


def test_game_environment_combines_park_and_weather() -> None:
    hot = game_run_environment(park_hr_index=110, park_runs_index=112, temp_f=90.0)
    cold = game_run_environment(park_hr_index=110, park_runs_index=112, temp_f=45.0)
    # Same park, warmer air → bigger HR factor.
    assert hot.hr_factor > cold.hr_factor
    # HR factor folds park (1.10) and temperature together.
    assert hot.hr_factor > 1.10


def test_roof_gate_neutralizes_weather() -> None:
    indoors = game_run_environment(
        park_hr_index=100, park_runs_index=100, temp_f=95.0, indoors=True
    )
    # Indoors ignores temperature entirely — neutral park stays neutral.
    assert indoors.hr_factor == pytest.approx(1.0)
    assert indoors.tilt == pytest.approx(0.0)


def test_no_weather_is_park_only() -> None:
    env = game_run_environment(park_hr_index=90, park_runs_index=95)
    assert env.hr_factor == pytest.approx(0.90)  # Oracle, HR-only park view


def test_default_is_neutral() -> None:
    env = game_run_environment()
    assert env.hr_factor == pytest.approx(1.0) and env.tilt == pytest.approx(0.0)
