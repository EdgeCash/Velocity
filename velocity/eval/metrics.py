"""Evaluation metrics — proper scores, calibration, and bankroll statistics.

Each function has a closed-form answer on textbook inputs, so the evaluation
layer is as testable as the wagering math. Probabilities are model win/cover
probabilities; outcomes are 0/1 indicators of the event occurring.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

_EPS = 1e-15


def _as_arrays(probs: object, outcomes: object) -> tuple[np.ndarray, np.ndarray]:
    p = np.asarray(probs, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    if p.shape != y.shape:
        raise ValueError("probs and outcomes must have the same shape")
    if p.size == 0:
        raise ValueError("need at least one observation")
    return p, y


def brier_score(probs: object, outcomes: object) -> float:
    """Mean squared error of probabilistic forecasts (lower is better; 0 is perfect)."""
    p, y = _as_arrays(probs, outcomes)
    return float(np.mean((p - y) ** 2))


def log_loss(probs: object, outcomes: object) -> float:
    """Mean negative log-likelihood (cross-entropy) of the forecasts."""
    p, y = _as_arrays(probs, outcomes)
    p = np.clip(p, _EPS, 1.0 - _EPS)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def calibration_table(probs: object, outcomes: object, n_bins: int = 10) -> pd.DataFrame:
    """Reliability table: per probability bin, the mean forecast vs observed rate.

    A well-calibrated model has ``mean_pred ≈ obs_rate`` in every populated bin.
    Empty bins are omitted.
    """
    p, y = _as_arrays(probs, outcomes)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        mask = idx == b
        count = int(mask.sum())
        if count == 0:
            continue
        rows.append(
            {
                "bin_lower": edges[b],
                "bin_upper": edges[b + 1],
                "mean_pred": float(p[mask].mean()),
                "obs_rate": float(y[mask].mean()),
                "count": count,
            }
        )
    return pd.DataFrame(rows)


def expected_calibration_error(probs: object, outcomes: object, n_bins: int = 10) -> float:
    """Count-weighted mean gap between forecast and outcome across bins (0 is perfect)."""
    table = calibration_table(probs, outcomes, n_bins)
    if table.empty:
        return float("nan")
    weights = table["count"] / table["count"].sum()
    return float((weights * (table["mean_pred"] - table["obs_rate"]).abs()).sum())


def roi(profit: object, staked: object) -> float:
    """Return on turnover: total profit divided by total amount staked."""
    profit_total = float(np.nansum(np.asarray(profit, dtype=float)))
    staked_total = float(np.nansum(np.asarray(staked, dtype=float)))
    if staked_total == 0:
        return 0.0
    return profit_total / staked_total


def hit_rate(results: Iterable[str]) -> float:
    """Win rate among decided bets (pushes excluded)."""
    decided = [r for r in results if r in ("win", "loss")]
    if not decided:
        return float("nan")
    return sum(r == "win" for r in decided) / len(decided)


def max_drawdown(bankroll: object) -> float:
    """Largest peak-to-trough fractional decline of a bankroll trajectory."""
    curve = np.asarray(bankroll, dtype=float)
    if curve.size == 0:
        return 0.0
    running_peak = np.maximum.accumulate(curve)
    drawdowns = np.where(running_peak > 0, 1.0 - curve / running_peak, 0.0)
    return float(np.max(drawdowns))


def clv_stats(clv: object) -> dict[str, float]:
    """Mean CLV and the fraction of bets that beat the close."""
    arr = np.asarray(clv, dtype=float)
    arr = arr[~np.isnan(arr)]
    if arr.size == 0:
        return {"mean_clv": float("nan"), "pct_positive": float("nan")}
    return {
        "mean_clv": float(arr.mean()),
        "pct_positive": float(np.mean(arr > 0)),
    }
