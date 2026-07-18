"""Evaluation metrics — closed-form checks on textbook inputs."""

from __future__ import annotations

import math

import pytest
from velocity.eval.metrics import (
    brier_score,
    calibration_table,
    clv_stats,
    expected_calibration_error,
    hit_rate,
    log_loss,
    max_drawdown,
    roi,
)


def test_brier_score() -> None:
    assert brier_score([1.0, 0.0], [1, 0]) == 0.0  # perfect
    assert brier_score([0.5, 0.5], [1, 0]) == pytest.approx(0.25)  # coin flip


def test_log_loss() -> None:
    assert log_loss([0.5, 0.5], [1, 0]) == pytest.approx(math.log(2))
    assert log_loss([0.9], [1]) == pytest.approx(-math.log(0.9))


def test_calibration_table_bins() -> None:
    # 10 forecasts at 0.30, three of which hit → the 0.3 bin is perfectly calibrated.
    probs = [0.30] * 10
    outcomes = [1, 1, 1, 0, 0, 0, 0, 0, 0, 0]
    table = calibration_table(probs, outcomes, n_bins=10)
    assert len(table) == 1
    row = table.iloc[0]
    assert row["mean_pred"] == pytest.approx(0.30)
    assert row["obs_rate"] == pytest.approx(0.30)
    assert row["count"] == 10


def test_expected_calibration_error_zero_when_calibrated() -> None:
    probs = [0.30] * 10
    outcomes = [1, 1, 1, 0, 0, 0, 0, 0, 0, 0]
    assert expected_calibration_error(probs, outcomes) == pytest.approx(0.0)


def test_expected_calibration_error_detects_miscalibration() -> None:
    # Forecasts 0.9 but only half hit → a big gap.
    probs = [0.9] * 10
    outcomes = [1, 1, 1, 1, 1, 0, 0, 0, 0, 0]
    assert expected_calibration_error(probs, outcomes) == pytest.approx(0.4)


def test_roi() -> None:
    assert roi([10.0, -5.0], [10.0, 10.0]) == pytest.approx(0.25)
    assert roi([0.0], [0.0]) == 0.0  # nothing staked


def test_hit_rate_excludes_pushes() -> None:
    assert hit_rate(["win", "loss", "win", "push"]) == pytest.approx(2 / 3)


def test_max_drawdown() -> None:
    # 120 → 60 is the worst peak-to-trough: 50%.
    assert max_drawdown([100, 120, 60, 80]) == pytest.approx(0.5)
    assert max_drawdown([100, 110, 120]) == pytest.approx(0.0)  # monotone up


def test_clv_stats() -> None:
    stats = clv_stats([0.1, -0.2, 0.3])
    assert stats["mean_clv"] == pytest.approx(0.2 / 3 + 0.0, abs=1e-9)
    assert stats["pct_positive"] == pytest.approx(2 / 3)


def test_metrics_reject_mismatched_shapes() -> None:
    with pytest.raises(ValueError):
        brier_score([0.5, 0.5], [1])
