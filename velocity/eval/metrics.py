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


# Markets whose closing lines are efficient enough for CLV to be the skill
# yardstick. Props, team totals, and thin derivatives close on numbers few
# sharp participants ever price, so beating them means little — those
# strategies are judged on longer-window P/L and process instead
# (docs/EDGE_RESEARCH.md §1.1).
CLV_TRUSTED_MARKETS = frozenset({"spread", "total", "moneyline"})


def clv_by_market(ledger: pd.DataFrame) -> pd.DataFrame:
    """Per-market CLV table with a trust flag — the monitor's one-glance read.

    ``ledger`` is any frame with ``market``, ``price_clv``, and ``line_clv``
    columns (the backtest engine's ledger, a settled :class:`BetLog`). For
    each market: bet count, mean price/line CLV, the share of bets beating
    the close (price CLV where present, line CLV as fallback), and
    ``clv_trusted`` — whether that CLV means anything. An untrusted market's
    row is still reported; the flag tells the reader to weigh P/L instead.
    """
    columns = ["market", "n_bets", "mean_price_clv", "mean_line_clv",
               "pct_beat_close", "clv_trusted"]
    if ledger is None or ledger.empty or "market" not in ledger.columns:
        return pd.DataFrame(columns=columns)
    rows = []
    for market, part in ledger.groupby("market", sort=True):
        empty = pd.Series(dtype=float, index=part.index)
        price = pd.to_numeric(
            part["price_clv"] if "price_clv" in part.columns else empty, errors="coerce"
        )
        line = pd.to_numeric(
            part["line_clv"] if "line_clv" in part.columns else empty, errors="coerce"
        )
        beat = price.where(price.notna(), line)
        known = beat.dropna()
        rows.append({
            "market": str(market),
            "n_bets": len(part),
            "mean_price_clv": float(price.mean()) if price.notna().any() else float("nan"),
            "mean_line_clv": float(line.mean()) if line.notna().any() else float("nan"),
            "pct_beat_close": float((known > 0).mean()) if len(known) else float("nan"),
            "clv_trusted": str(market) in CLV_TRUSTED_MARKETS,
        })
    return pd.DataFrame(rows, columns=columns)


def benjamini_hochberg(p_values: object, alpha: float = 0.05) -> np.ndarray:
    """Which hypotheses survive FDR control — the sweep-family discipline.

    Every re-tune sweep tests many variants at once, and with ~5 years of
    data more than a few dozen variants virtually guarantees a false
    discovery (docs/EDGE_RESEARCH.md §1.3, Bailey/López de Prado). The
    Benjamini–Hochberg step-up procedure bounds the *false discovery rate*
    at ``alpha``: sort the p-values, find the largest k with
    ``p_(k) ≤ k·alpha/n``, and reject exactly the k smallest. Returns a
    boolean array aligned with the input — True = the finding survives.
    """
    p = np.asarray(p_values, dtype=float)
    if p.size == 0:
        return np.zeros(0, dtype=bool)
    if np.any((p < 0) | (p > 1) | ~np.isfinite(p)):
        raise ValueError("p-values must be finite and inside [0, 1]")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be inside (0, 1)")
    n = p.size
    order = np.argsort(p, kind="stable")
    ranked = p[order]
    thresholds = alpha * (np.arange(1, n + 1) / n)
    passing = np.nonzero(ranked <= thresholds)[0]
    survive = np.zeros(n, dtype=bool)
    if passing.size:
        survive[order[: passing[-1] + 1]] = True
    return survive
