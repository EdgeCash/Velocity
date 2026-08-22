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


def test_clv_by_market_summarizes_and_flags_trust() -> None:
    import numpy as np
    import pandas as pd
    from velocity.eval.metrics import clv_by_market

    ledger = pd.DataFrame([
        {"market": "spread", "price_clv": 0.02, "line_clv": 1.0},
        {"market": "spread", "price_clv": -0.01, "line_clv": -0.5},
        {"market": "team_total_home", "price_clv": 0.05, "line_clv": np.nan},
        {"market": "total", "price_clv": np.nan, "line_clv": 2.0},  # line CLV fallback
    ])
    table = clv_by_market(ledger).set_index("market")
    assert table.loc["spread", "n_bets"] == 2
    assert table.loc["spread", "mean_price_clv"] == pytest.approx(0.005)
    assert table.loc["spread", "pct_beat_close"] == pytest.approx(0.5)
    assert bool(table.loc["spread", "clv_trusted"])
    assert bool(table.loc["total", "clv_trusted"])
    assert table.loc["total", "pct_beat_close"] == pytest.approx(1.0)
    # Team totals close on numbers few sharps price: CLV is not the yardstick.
    assert not bool(table.loc["team_total_home", "clv_trusted"])


def test_clv_by_market_empty_and_missing_columns() -> None:
    import pandas as pd
    from velocity.eval.metrics import clv_by_market

    assert clv_by_market(pd.DataFrame()).empty
    bare = clv_by_market(pd.DataFrame({"market": ["spread"]}))
    assert bare.loc[0, "n_bets"] == 1
    assert pd.isna(bare.loc[0, "pct_beat_close"])


def test_benjamini_hochberg_textbook_case() -> None:
    import numpy as np
    from velocity.eval.metrics import benjamini_hochberg

    # Classic worked example: n=6, alpha=0.25 — thresholds k/6·0.25.
    p = np.array([0.009, 0.013, 0.014, 0.19, 0.35, 0.5])
    survive = benjamini_hochberg(p, alpha=0.25)
    assert survive.tolist() == [True, True, True, False, False, False]
    # Order-independence: shuffling the inputs shuffles the mask identically.
    perm = np.array([3, 0, 5, 1, 4, 2])
    assert benjamini_hochberg(p[perm], alpha=0.25).tolist() == survive[perm].tolist()
    # Nothing significant → nothing survives.
    assert not benjamini_hochberg([0.5, 0.9], alpha=0.05).any()
    with pytest.raises(ValueError, match="p-values"):
        benjamini_hochberg([1.5])
