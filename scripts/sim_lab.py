"""The sim-shape gate: normal vs empirical draw, constant vs sloped sd.

    python scripts/sim_lab.py --league ncaaf \\
        --projections /tmp/lab_ncaaf/projections_blend-epa50.parquet --eval-from 2022

Re-prices every out-of-sample game from the shipped model's own μ (the lab
projections' fair spread/total) under four sims and scores each the way
NCAAF Round 3 gated 18.2/16.7 (docs/MODEL_LAB.md): expected calibration
error and Brier on the moneyline, plus E8's yardstick — the probability
error at every half-point offset from the fair line, on both spreads and
totals, which is the shape a ladder rung is priced from
(docs/SYSTEM_REVIEW.md §2, M1's definition of done).

Honest by construction: each test season's residual pool and dispersion
slopes are fitted on the seasons before it, so the empirical draw never sees
the games it is scored on. The league's promoted sd constants are held fixed
across variants so the comparison isolates shape and slope.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from velocity.eval.metrics import brier_score, expected_calibration_error
from velocity.models.residuals import ResidualPool, fit_sd_slope, residuals_from_projections
from velocity.models.simulate import (
    DEFAULT_SD_MARGIN,
    DEFAULT_SD_TOTAL,
    NCAAF_SD_MARGIN,
    NCAAF_SD_TOTAL,
    SimConfig,
    simulate_game,
)
from velocity.util.seed import make_rng

LEAGUE_SDS = {
    "nfl": (DEFAULT_SD_MARGIN, DEFAULT_SD_TOTAL),
    "ncaaf": (NCAAF_SD_MARGIN, NCAAF_SD_TOTAL),
}
OFFSETS = np.arange(0.5, 29.0, 1.0)
SHOULDER = OFFSETS <= 13.5


def season_configs(
    train: pd.DataFrame, base: SimConfig
) -> dict[str, SimConfig]:
    """The four variants for one test season, fitted on ``train`` only."""
    pool = ResidualPool.from_frame(train)
    _, slope_t, anchor = fit_sd_slope(train["mu_total"], train["resid_total"])
    _, slope_m, _ = fit_sd_slope(train["mu_total"], train["resid_margin"])
    hetero = {"sd_total_slope": slope_t, "sd_margin_slope": slope_m,
              "sd_anchor_total": anchor}
    return {
        "normal": base,
        "normal-hetero": replace(base, **hetero),
        "empirical": replace(base, residuals=pool),
        "empirical-hetero": replace(base, residuals=pool, **hetero),
    }


def score_variant(
    test: pd.DataFrame, config: SimConfig, seed: int
) -> dict[str, float]:
    """ECE / Brier on the moneyline and the offset-error profile, one seed."""
    p_home: list[float] = []
    y: list[float] = []
    k = len(OFFSETS)
    sim_tail = np.zeros((4, k))  # over/under margin, over/under total — sim
    real_tail = np.zeros((4, k))  # the same four, what actually happened
    for row in test.itertuples(index=False):
        rng = make_rng(seed + int(row.week))
        mu_m, mu_t = float(row.mu_margin), float(row.mu_total)
        sim = simulate_game(mu_m, mu_t, rng, config)
        p_home.append(sim.p_home_win())
        actual_m = mu_m + float(row.resid_margin)
        actual_t = mu_t + float(row.resid_total)
        y.append(1.0 if actual_m > 0 else 0.0)
        # One sort per game, then every offset's tail mass by bisection:
        # P(x > mu + k) and P(x < mu - k) for all k at once.
        n = float(sim.n_sims)
        margin = np.sort(sim.margin)
        total = np.sort(sim.total)
        sim_tail[0] += 1.0 - np.searchsorted(margin, mu_m + OFFSETS, side="right") / n
        sim_tail[1] += np.searchsorted(margin, mu_m - OFFSETS, side="left") / n
        sim_tail[2] += 1.0 - np.searchsorted(total, mu_t + OFFSETS, side="right") / n
        sim_tail[3] += np.searchsorted(total, mu_t - OFFSETS, side="left") / n
        real_tail[0] += actual_m > mu_m + OFFSETS
        real_tail[1] += actual_m < mu_m - OFFSETS
        real_tail[2] += actual_t > mu_t + OFFSETS
        real_tail[3] += actual_t < mu_t - OFFSETS
    games = float(len(test))
    err = np.abs(sim_tail - real_tail) / games
    err_m = np.maximum(err[0], err[1])
    err_t = np.maximum(err[2], err[3])
    p = np.array(p_home)
    yy = np.array(y)
    return {
        "ece": expected_calibration_error(p, yy),
        "brier": brier_score(p, yy),
        "spread_shoulder_max": float(err_m[SHOULDER].max()),
        "spread_tail_max": float(err_m[~SHOULDER].max()),
        "spread_mean": float(err_m.mean()),
        "total_shoulder_max": float(err_t[SHOULDER].max()),
        "total_tail_max": float(err_t[~SHOULDER].max()),
        "total_mean": float(err_t.mean()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sim-shape gate")
    parser.add_argument("--league", required=True, choices=sorted(LEAGUE_SDS))
    parser.add_argument("--projections", required=True)
    parser.add_argument("--games", default=None)
    parser.add_argument("--eval-from", type=int, default=2022)
    parser.add_argument("--n-sims", type=int, default=10_000)
    parser.add_argument("--seeds", default="7,101,2027")
    parser.add_argument("--variants", default="")
    parser.add_argument("--out", default=None, help="optional parquet for the table")
    args = parser.parse_args()

    games = pd.read_parquet(args.games or f"datasets/{args.league}/games.parquet")
    residuals = residuals_from_projections(pd.read_parquet(args.projections), games)
    sd_m, sd_t = LEAGUE_SDS[args.league]
    base = SimConfig(n_sims=args.n_sims, sd_margin=sd_m, sd_total=sd_t)
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    wanted = [v.strip() for v in args.variants.split(",") if v.strip()]

    test_seasons = sorted(s for s in residuals["season"].unique() if s >= args.eval_from)
    print(f"{args.league}: {len(residuals)} walk-forward games; testing "
          f"{test_seasons[0]}–{test_seasons[-1]} at {args.n_sims} sims × seeds {seeds}")
    rows = []
    for seed in seeds:
        per_variant: dict[str, list[pd.DataFrame]] = {}
        # Score season by season so each pool is point-in-time, then pool
        # the per-game arrays across seasons before the metrics.
        for season in test_seasons:
            train = residuals[residuals["season"] < season]
            test = residuals[residuals["season"] == season]
            if len(train) < 200 or test.empty:
                continue
            for name, cfg in season_configs(train, base).items():
                if wanted and name not in wanted:
                    continue
                scored = score_variant(test, cfg, seed)
                scored.update(variant=name, seed=seed, season=season, n=len(test))
                per_variant.setdefault(name, []).append(pd.DataFrame([scored]))
        for name, parts in per_variant.items():
            frame = pd.concat(parts, ignore_index=True)
            w = frame["n"] / frame["n"].sum()
            row = {"variant": name, "seed": seed, "n": int(frame["n"].sum())}
            for col in ("ece", "brier", "spread_shoulder_max", "spread_tail_max",
                        "spread_mean", "total_shoulder_max", "total_tail_max", "total_mean"):
                row[col] = float((frame[col] * w).sum())
            rows.append(row)
    table = pd.DataFrame(rows)
    summary = (table.drop(columns=["seed"]).groupby("variant", sort=False)
               .mean(numeric_only=True))
    order = ["normal", "normal-hetero", "empirical", "empirical-hetero"]
    summary = summary.reindex([v for v in order if v in summary.index])
    with pd.option_context("display.width", 200, "display.max_columns", None):
        print("\n=== Sim-shape gate (out-of-sample, mean over seeds; "
              "offset errors are worst |sim − real| probability at any half-point "
              "offset, shoulder ≤ 13.5 / tail 14.5–28.5) ===")
        print(summary.round(4).to_string())
        print("\nper seed:")
        print(table.round(4).to_string(index=False))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        table.to_parquet(args.out, index=False)


if __name__ == "__main__":
    main()
