"""The derivative re-check — does the overlay hold up off the game markets?

The lattice round won the sim-shape gate on eight columns scored against the
game markets. Props, ladders, DFS and the correlation model all price off the
same samples, so this harness scores the wider surface. These pin its
arithmetic; the measurement itself lives in ``docs/MODEL_LAB.md``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "derivative_recheck",
    Path(__file__).resolve().parents[1] / "scripts" / "derivative_recheck.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def test_a_perfectly_calibrated_board_scores_zero() -> None:
    rng = np.random.default_rng(5)
    quoted = rng.uniform(0.05, 0.95, size=40_000)
    landed = (rng.random(40_000) < quoted).astype(float)
    assert _MOD._bucket_error(quoted, landed) < 0.01


def test_a_board_quoting_everything_too_high_is_caught() -> None:
    rng = np.random.default_rng(5)
    truth = rng.uniform(0.05, 0.85, size=40_000)
    landed = (rng.random(40_000) < truth).astype(float)
    assert _MOD._bucket_error(truth + 0.10, landed) == pytest.approx(0.10, abs=0.02)


def test_a_bucket_too_thin_to_measure_is_not_measured() -> None:
    """Twenty games in a bucket is a coin-flip estimate, not a calibration."""
    quoted = np.concatenate([np.full(500, 0.52), np.full(5, 0.97)])
    landed = np.concatenate([(np.arange(500) % 2).astype(float), np.zeros(5)])
    # The thin bucket is wrong by 0.97 and must not reach the answer.
    assert _MOD._bucket_error(quoted, landed) < 0.05


def test_no_measurable_bucket_says_so_rather_than_guessing() -> None:
    assert np.isnan(_MOD._bucket_error(np.array([0.5, 0.5]), np.array([1.0, 0.0])))


def test_a_season_with_no_answer_does_not_erase_the_others() -> None:
    """The bug this caught: one thin season turned every column blank.

    The in-progress season is sixteen games, too few to fill a calibration
    bucket, so it scores NaN. Weighting it in propagates that NaN through the
    games-weighted mean and the whole measurement comes back empty — which
    reads like "no result" rather than "one season abstained".
    """
    scored = [(267, {"x": 0.02}), (285, {"x": 0.04}), (16, {"x": float("nan")})]
    assert _MOD._weighted(scored, "x") == pytest.approx(
        (267 * 0.02 + 285 * 0.04) / 552)
    assert np.isnan(_MOD._weighted([(16, {"x": float("nan")})], "x"))


def test_the_gate_summary_counts_sides_the_way_the_gate_does() -> None:
    """A negative bias is SAFE — the sim understates that side.

    `rung_is_honest` passes anything the sim does not overstate by more than
    the bar, because an edge this bias cannot have invented is one worth
    having. A summary that scored |bias| would refuse the safe half of every
    ladder.
    """
    table = pd.DataFrame({
        "offset": [0.5, 1.5, 2.5],
        # over tail overstated badly, under tail understated badly
        "over_bias": [0.05, 0.05, 0.05],
        "under_bias": [-0.05, -0.05, -0.05],
        "error": [0.05, 0.05, 0.05],
    })
    summary = _MOD.gate_summary(table, tolerance=0.02)
    assert summary["sides"] == 6
    assert summary["sides_open"] == 3, "every understated side stays open"
    assert summary["worst_error"] == pytest.approx(0.05)


def test_the_shoulder_is_reported_apart_from_the_deep_tail() -> None:
    """Near the line is where the money is and where the gate bites."""
    table = pd.DataFrame({
        "offset": [0.5, 13.5, 14.5, 28.5],
        "over_bias": [0.01, 0.01, 0.09, 0.09],
        "under_bias": [0.0, 0.0, 0.0, 0.0],
        "error": [0.01, 0.01, 0.09, 0.09],
    })
    summary = _MOD.gate_summary(table)
    assert summary["shoulder_error"] == pytest.approx(0.01)
    assert summary["worst_error"] == pytest.approx(0.09)
