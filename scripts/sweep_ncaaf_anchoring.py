"""The NCAAF staking sweep: does the claimed edge match the realized one?

    python scripts/sweep_ncaaf_anchoring.py \\
        --projections /tmp/lab_ncaaf/projections_blend-epa50.parquet

At the promoted totals filter (bet only when the model and the close
disagree by ≥ 6 points), selection does not depend on the anchoring
weight — the weight sets the *claimed* probability, which is what Kelly
stakes on. For each weight the sweep reports the mean claimed edge
(belief − fair, the market's side at 0.5 after the vig) beside the
realized one (hit rate − 0.5) and the ROI at −110, out of sample by
construction (walk-forward projections vs the committed closes). The
right weight is the one where the claim and the realization agree: any
higher over-stakes, any lower under-stakes (docs/STRATEGY_REVIEW.md S3.2).
"""

from __future__ import annotations

import argparse
import math

import numpy as np
import pandas as pd
from velocity.models.simulate import NCAAF_SD_TOTAL


def _normal_cdf(x: np.ndarray) -> np.ndarray:
    return 0.5 * (1.0 + np.vectorize(math.erf)(x / math.sqrt(2.0)))


def sweep(
    projections: pd.DataFrame, games: pd.DataFrame, *, min_gap: float, weights: list[float],
    price: float = -110.0,
) -> pd.DataFrame:
    frame = projections.merge(
        games[["game_id", "total_line", "home_score", "away_score"]], on="game_id", how="inner"
    ).dropna(subset=["total_line", "fair_total", "home_score", "away_score"])
    gap = frame["fair_total"] - frame["total_line"]
    picked = frame[gap.abs() >= min_gap].copy()
    if picked.empty:
        return pd.DataFrame()
    side_over = (picked["fair_total"] > picked["total_line"]).to_numpy()
    actual = (picked["home_score"] + picked["away_score"]).to_numpy(dtype=float)
    line = picked["total_line"].to_numpy(dtype=float)
    won = np.where(side_over, actual > line, actual < line)
    push = actual == line
    # The sim's probability of the picked side at the close, from the
    # promoted dispersion — a normal at sd_total around the model's total.
    z = (picked["fair_total"].to_numpy(dtype=float) - line) / NCAAF_SD_TOTAL
    p_model = np.where(side_over, _normal_cdf(z), 1.0 - _normal_cdf(z))
    payout = 100.0 / abs(price)
    rows = []
    decided = ~push
    for w in weights:
        belief = 0.5 + w * (p_model - 0.5)
        claimed = belief - 0.5
        rows.append({
            "weight": w, "n": int(decided.sum()),
            "claimed_edge": float(claimed[decided].mean()),
            "realized_edge": float(won[decided].mean() - 0.5),
            "hit_rate": float(won[decided].mean()),
            "roi": float(np.where(won[decided], payout, -1.0).mean()),
            "claimed_ev": float((belief[decided] * payout - (1 - belief[decided])).mean()),
        })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="NCAAF anchoring sweep at the totals filter")
    parser.add_argument("--projections", required=True)
    parser.add_argument("--games", default="datasets/ncaaf/games.parquet")
    parser.add_argument("--min-gap", type=float, default=6.0)
    parser.add_argument("--eval-from", type=int, default=2018)
    parser.add_argument("--weights", default="0.1,0.2,0.3,0.5,1.0")
    args = parser.parse_args()

    projections = pd.read_parquet(args.projections)
    projections["game_id"] = projections["game_id"].astype(str)
    projections = projections[projections["season"] >= args.eval_from]
    games = pd.read_parquet(args.games)
    games["game_id"] = games["game_id"].astype(str)
    weights = [float(w) for w in args.weights.split(",")]
    table = sweep(projections, games, min_gap=args.min_gap, weights=weights)
    print(f"totals at ≥ {args.min_gap:g} points of disagreement, seasons ≥ {args.eval_from}")
    with pd.option_context("display.width", 160):
        print(table.round(4).to_string(index=False))
    if not table.empty:
        by_season = []
        for season, part in projections.groupby("season"):
            row = sweep(part, games, min_gap=args.min_gap, weights=[0.2])
            if not row.empty:
                by_season.append({"season": int(season), **row.iloc[0].to_dict()})
        print("\nby season at w = 0.2:")
        print(pd.DataFrame(by_season).round(4).to_string(index=False))


if __name__ == "__main__":
    main()
