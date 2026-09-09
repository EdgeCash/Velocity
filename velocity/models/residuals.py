"""The sim's empirical shape: banked walk-forward residuals per league.

The engine in :mod:`velocity.models.simulate` draws a game's (margin, total)
from a bivariate normal around the model's expectation. E8 measured how far
that normal misses at every offset from the fair line: a football residual
is leptokurtic in the shoulders and fatter than normal past ~15 points, so
the normal overstates the shoulders by ~3 points of probability for NFL
spreads and understates the deep tail (docs/SYSTEM_REVIEW.md §2.2). The
ladder gate refuses rungs where that error exceeds a tolerance — a bandaid
over a distribution known to be the wrong shape.

The fix at the source is a **nonparametric residual draw**: bank the
walk-forward residuals of the shipped model — actual minus the model's own
projection, the same run that produced the sd constants — and draw
``(margin, total)`` residual *pairs* from that pool, scaled by the league's
sd, instead of from a normal. Real football margins then land on real
football numbers (3, 7, 10) by construction, the tails are the tails the
league produces, and the margin/total dependence is the empirical one
rather than a correlation parameter.

Two disciplines. The pool is **centered**: the model's μ is the location,
the pool supplies shape only, so a level bias in a training window (the NFL
totals cold-start gap, say) cannot be baked into every future draw. And the
pool is **standardized** by its own sd, so the league's promoted
``sd_margin`` / ``sd_total`` — and the heteroscedastic slope, where fitted —
still set the dispersion; swapping the shape does not silently re-tune the
width the lab gated.

Residuals are measured against the **model's** projection, never the
market's close (docs/MODEL_LAB.md NCAAF Round 3's trap): the market is
sharper, its residuals narrower, and a pool built on it would make the sim
overconfident.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

DATASETS = Path("datasets")
RESIDUALS_FILE = "sim_residuals.parquet"

# The banked frame's columns: one row per walk-forward game.
RESIDUAL_COLUMNS = [
    "season", "week", "game_id", "mu_margin", "mu_total",
    "resid_margin", "resid_total",
]


@dataclass(frozen=True)
class ResidualPool:
    """Standardized, centered (margin, total) residual pairs — the shape."""

    margin_z: np.ndarray
    total_z: np.ndarray
    # The sds the pool was standardized by, for the record (and the lab).
    sd_margin: float
    sd_total: float

    def __post_init__(self) -> None:
        if self.margin_z.shape != self.total_z.shape or self.margin_z.ndim != 1:
            raise ValueError("margin_z and total_z must be one-dimensional and the same length")
        if len(self.margin_z) < 2:
            raise ValueError("a residual pool needs at least two games")

    def __len__(self) -> int:
        return int(self.margin_z.shape[0])

    @property
    def correlation(self) -> float:
        """The empirical margin/total dependence the pool carries."""
        return float(np.corrcoef(self.margin_z, self.total_z)[0, 1])

    @classmethod
    def from_residuals(
        cls, resid_margin: object, resid_total: object, *, symmetric: bool = True
    ) -> ResidualPool:
        """Center and standardize raw residual pairs into a pool.

        ``symmetric`` (the default) mirrors every pair — ``(m, t)`` and
        ``(−m, −t)`` — so the pool is even around the model's μ: the mean
        and the median both sit at zero, and a home team projected to win
        by nothing wins exactly half the samples. A raw pool carries
        whatever skew its training window had, which shifted every
        moneyline by a point or two in the first gate and cost calibration
        for no gain in shape; the mirror keeps the kurtosis, the key-number
        mass, and the margin/total dependence (``corr(−m, −t) = corr(m, t)``).
        """
        margin = np.asarray(resid_margin, dtype=float)
        total = np.asarray(resid_total, dtype=float)
        keep = np.isfinite(margin) & np.isfinite(total)
        margin, total = margin[keep], total[keep]
        if len(margin) < 2:
            raise ValueError("a residual pool needs at least two finite games")
        if margin.std(ddof=1) <= 0 or total.std(ddof=1) <= 0:
            raise ValueError("residuals have no dispersion to standardize by")
        if symmetric:
            margin = np.concatenate([margin, -margin])
            total = np.concatenate([total, -total])
        sd_m = float(margin.std(ddof=1))
        sd_t = float(total.std(ddof=1))
        return cls(
            margin_z=(margin - margin.mean()) / sd_m,
            total_z=(total - total.mean()) / sd_t,
            sd_margin=sd_m,
            sd_total=sd_t,
        )

    @classmethod
    def from_frame(cls, frame: pd.DataFrame, *, symmetric: bool = True) -> ResidualPool:
        """A pool from a banked residual frame (``RESIDUAL_COLUMNS``)."""
        return cls.from_residuals(
            frame["resid_margin"], frame["resid_total"], symmetric=symmetric
        )

    def draw(self, rng: np.random.Generator, n: int) -> tuple[np.ndarray, np.ndarray]:
        """``n`` standardized (margin, total) pairs, drawn jointly with replacement.

        One index per sample keeps each pair together, which is what carries
        the empirical margin/total dependence into the sim.
        """
        idx = rng.integers(0, len(self), size=n)
        return self.margin_z[idx], self.total_z[idx]


def residuals_from_projections(
    projections: pd.DataFrame, games: pd.DataFrame
) -> pd.DataFrame:
    """Walk-forward residuals (``RESIDUAL_COLUMNS``) from a lab projections frame.

    ``projections`` is the engine's per-game output (``fair_spread`` /
    ``fair_total`` are the sim's medians, which for the symmetric normal are
    the model's μ to within rounding); ``games`` carries the final scores.
    Out-of-sample by construction: every row was projected before its week
    was trained on.
    """
    cols = ["game_id", "home_score", "away_score"]
    merged = projections.merge(games[cols], on="game_id", how="inner")
    merged = merged.dropna(subset=["home_score", "away_score", "fair_spread", "fair_total"])
    out = pd.DataFrame({
        "season": merged["season"].astype(int),
        "week": merged["week"].astype(int),
        "game_id": merged["game_id"].astype(str),
        # fair_spread is the HOME spread: -6 means home by 6.
        "mu_margin": -merged["fair_spread"].astype(float),
        "mu_total": merged["fair_total"].astype(float),
    })
    actual_margin = (merged["home_score"] - merged["away_score"]).astype(float)
    actual_total = (merged["home_score"] + merged["away_score"]).astype(float)
    out["resid_margin"] = (actual_margin - out["mu_margin"]).to_numpy()
    out["resid_total"] = (actual_total - out["mu_total"]).to_numpy()
    return out[RESIDUAL_COLUMNS].reset_index(drop=True)


def fit_sd_slope(
    mu: object, resid: object, *, n_bins: int = 5
) -> tuple[float, float, float]:
    """``(sd_at_anchor, slope, anchor)`` — how the residual sd moves with μ.

    Buckets the games by quantile of ``mu`` (the expected total, usually),
    measures the residual sd inside each bucket, and fits a line through
    the bucket points weighted by their counts. ``anchor`` is the overall
    mean of ``mu``; ``sd_at_anchor`` is the fitted sd there, so a config
    built from this keeps the league's constant at a typical game and
    widens or narrows away from it (docs/SYSTEM_REVIEW.md §2.1).
    """
    x = np.asarray(mu, dtype=float)
    r = np.asarray(resid, dtype=float)
    keep = np.isfinite(x) & np.isfinite(r)
    x, r = x[keep], r[keep]
    if len(x) < 2 * n_bins:
        raise ValueError("too few games to fit a dispersion slope")
    edges = np.quantile(x, np.linspace(0.0, 1.0, n_bins + 1))
    idx = np.clip(np.searchsorted(edges[1:-1], x, side="right"), 0, n_bins - 1)
    centers, sds, weights = [], [], []
    for b in range(n_bins):
        mask = idx == b
        if mask.sum() < 2:
            continue
        centers.append(float(x[mask].mean()))
        sds.append(float(r[mask].std(ddof=1)))
        weights.append(float(mask.sum()))
    if len(centers) < 2:
        raise ValueError("too few populated buckets to fit a slope")
    slope, intercept = np.polyfit(np.array(centers), np.array(sds), 1, w=np.sqrt(weights))
    anchor = float(x.mean())
    return float(intercept + slope * anchor), float(slope), anchor


def load_residual_pool(
    league: str, datasets: Path | None = None
) -> ResidualPool | None:
    """The banked pool for ``league``, or ``None`` when none is committed."""
    path = (datasets or DATASETS) / league / RESIDUALS_FILE
    if not path.exists():
        return None
    frame = pd.read_parquet(path)
    if frame.empty:
        return None
    return ResidualPool.from_frame(frame)
