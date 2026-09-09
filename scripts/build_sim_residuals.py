"""Bank the shipped model's walk-forward residuals — the sim's empirical shape.

    python scripts/model_lab.py --league nfl --variants qb-recency-17-q300 \\
        --train-window 4 --out /tmp/lab_nfl
    python scripts/build_sim_residuals.py --league nfl \\
        --projections /tmp/lab_nfl/projections_qb-recency-17-q300.parquet

Reads a lab projections frame (the engine's per-game ``fair_spread`` /
``fair_total``, every row projected before its week was trained on), joins
the final scores, and writes ``datasets/{league}/sim_residuals.parquet``
(:data:`velocity.models.residuals.RESIDUAL_COLUMNS`). The live sim draws its
(margin, total) residual pairs from this pool; :mod:`scripts.sim_lab` is the
gate that decides whether it should (docs/SYSTEM_REVIEW.md §2, M1).

Prints the pool's shape — sd, excess kurtosis, the margin/total dependence —
and the fitted dispersion slopes, so the promotion record has its table.
Derived from public final scores and our own projections: no odds data, so
the bank is safe to commit.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from velocity.models.residuals import (
    RESIDUAL_COLUMNS,
    ResidualPool,
    fit_sd_slope,
    residuals_from_projections,
)


def _excess_kurtosis(x: np.ndarray) -> float:
    z = (x - x.mean()) / x.std(ddof=0)
    return float((z**4).mean() - 3.0)


def describe(residuals: pd.DataFrame) -> dict[str, float]:
    """The pool's shape in numbers — what the promotion record quotes."""
    pool = ResidualPool.from_frame(residuals)
    margin = residuals["resid_margin"].to_numpy(dtype=float)
    total = residuals["resid_total"].to_numpy(dtype=float)
    sd_t_at, slope_t, anchor = fit_sd_slope(residuals["mu_total"], residuals["resid_total"])
    sd_m_at, slope_m, _ = fit_sd_slope(residuals["mu_total"], residuals["resid_margin"])
    return {
        "n": float(len(residuals)),
        "sd_margin": pool.sd_margin,
        "sd_total": pool.sd_total,
        "mean_margin": float(margin.mean()),
        "mean_total": float(total.mean()),
        "kurtosis_margin": _excess_kurtosis(margin),
        "kurtosis_total": _excess_kurtosis(total),
        "correlation": pool.correlation,
        "anchor_total": anchor,
        "sd_total_at_anchor": sd_t_at,
        "sd_total_slope": slope_t,
        "sd_margin_at_anchor": sd_m_at,
        "sd_margin_slope": slope_m,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bank the sim's residual pool")
    parser.add_argument("--league", required=True, choices=["nfl", "ncaaf"])
    parser.add_argument("--projections", required=True,
                        help="a model_lab.py projections_{variant}.parquet")
    parser.add_argument("--games", default=None,
                        help="games frame with finals (default datasets/{league}/games.parquet)")
    parser.add_argument("--seasons-from", type=int, default=None,
                        help="keep residuals from this season on (thin early training)")
    parser.add_argument("--out", default=None,
                        help="default datasets/{league}/sim_residuals.parquet")
    args = parser.parse_args()

    games_path = Path(args.games or f"datasets/{args.league}/games.parquet")
    out = Path(args.out or f"datasets/{args.league}/sim_residuals.parquet")
    projections = pd.read_parquet(args.projections)
    games = pd.read_parquet(games_path)
    residuals = residuals_from_projections(projections, games)
    if args.seasons_from is not None:
        residuals = residuals[residuals["season"] >= args.seasons_from].reset_index(drop=True)
    if residuals.empty:
        raise SystemExit("no residuals — do the projections and games share game ids?")

    stats = describe(residuals)
    print(f"{args.league}: {int(stats['n'])} walk-forward games, "
          f"seasons {residuals['season'].min()}–{residuals['season'].max()}")
    print(f"  sd margin {stats['sd_margin']:.2f} / total {stats['sd_total']:.2f}; "
          f"mean {stats['mean_margin']:+.2f} / {stats['mean_total']:+.2f} (centered away)")
    print(f"  excess kurtosis margin {stats['kurtosis_margin']:+.2f} / "
          f"total {stats['kurtosis_total']:+.2f}; margin·total correlation "
          f"{stats['correlation']:+.3f}")
    print(f"  dispersion vs expected total (anchor {stats['anchor_total']:.1f}): "
          f"sd_total {stats['sd_total_at_anchor']:.2f} {stats['sd_total_slope']:+.3f}/pt, "
          f"sd_margin {stats['sd_margin_at_anchor']:.2f} {stats['sd_margin_slope']:+.3f}/pt")
    by_bucket = residuals.assign(
        bucket=pd.qcut(residuals["mu_total"], 5, labels=False)
    ).groupby("bucket").agg(
        mu_total=("mu_total", "mean"), n=("game_id", "size"),
        sd_total=("resid_total", "std"), sd_margin=("resid_margin", "std"),
    )
    print("\n  by expected-total quintile:")
    print(by_bucket.round(2).to_string())

    out.parent.mkdir(parents=True, exist_ok=True)
    residuals[RESIDUAL_COLUMNS].to_parquet(out, index=False)
    print(f"\nwrote {len(residuals)} residual rows to {out}")


if __name__ == "__main__":
    main()
