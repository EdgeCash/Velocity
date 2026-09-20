"""The sim-shape gate: normal vs empirical draw, constant vs sloped sd, vs drives, vs lattice.

    python scripts/sim_lab.py --league ncaaf \\
        --projections /tmp/lab_ncaaf/projections_blend-epa50.parquet --eval-from 2022

Re-prices every out-of-sample game from the shipped model's own μ (the lab
projections' fair spread/total) under four sims and scores each the way
NCAAF Round 3 gated 18.2/16.7 (docs/MODEL_LAB.md): expected calibration
error and Brier on the moneyline, plus E8's yardstick — the probability
error at every half-point offset from the fair line, on both spreads and
totals, which is the shape a ladder rung is priced from
(docs/SYSTEM_REVIEW.md §2, M1's definition of done).

``normal+skew`` re-shapes the TOTAL's draw to carry the right skew football
actually has (:mod:`velocity.models.skew`); ``normal+keys`` does the same job
for the MARGIN's lattice. The two are orthogonal and the combination is the
row worth reading.

``normal+keys`` is the shipped normal with football's own margin lattice
measured off the training seasons and reapplied by resampling
(:mod:`velocity.models.keynumbers`). It exists because the drive round left
the two sims failing in opposite directions — the normal has the dispersion
and no key numbers, the possession sampler the reverse — and this is the
third option: take the lattice from the data and leave the dispersion alone.

The last two variants are the possession sampler
(:mod:`velocity.models.drive`). They are the only ones whose dispersion is
not set from the league constants at all — it falls out of the scoring rates
— so they are the ones that can be *structurally* wrong rather than merely
mis-tuned, and the ``key_*`` columns exist to catch exactly that.

Honest by construction: each test season's residual pool and dispersion
slopes are fitted on the seasons before it, so the empirical draw never sees
the games it is scored on. The league's promoted sd constants are held fixed
across variants so the comparison isolates shape and slope.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd
from velocity.eval.metrics import brier_score, expected_calibration_error
from velocity.models.drive import DriveConfig, fit_drive_config, simulate_drives
from velocity.models.keynumbers import (
    LatticeWeights,
    fit_lattice_weights,
    rounded_normal_mass,
    simulated_margin_mass,
)
from velocity.models.residuals import ResidualPool, fit_sd_slope, residuals_from_projections
from velocity.models.simulate import (
    DEFAULT_SD_MARGIN,
    DEFAULT_SD_TOTAL,
    NCAAF_SD_MARGIN,
    NCAAF_SD_TOTAL,
    GameSim,
    SimConfig,
    simulate_game,
)
from velocity.models.skew import fit_epsilon
from velocity.util.seed import make_rng

LEAGUE_SDS = {
    "nfl": (DEFAULT_SD_MARGIN, DEFAULT_SD_TOTAL),
    "ncaaf": (NCAAF_SD_MARGIN, NCAAF_SD_TOTAL),
}
# Per-team possessions and field-goal-to-touchdown ratio by league, from each
# sport's own box scores rather than fitted here — college plays slightly more
# possessions than the NFL and converts more of them into touchdowns rather
# than field goals, which is a coarser scoring lattice.
DRIVE_CONFIGS = {
    "nfl": DriveConfig(drives=11.0, fg_to_td=0.65),
    "ncaaf": DriveConfig(drives=12.0, fg_to_td=0.50),
}


class Overlay(NamedTuple):
    """A base sim plus the lattice correction applied to its draws.

    A NamedTuple rather than a dataclass on purpose: this module is loaded
    by path in ``tests/test_sim_lab.py``, and a dataclass under
    ``from __future__ import annotations`` cannot resolve its own field
    types when the module was never registered in ``sys.modules``.
    """

    base: SimConfig | DriveConfig
    weights: LatticeWeights


def draw(
    config: SimConfig | DriveConfig, mu_margin: float, mu_total: float,
    rng: np.random.Generator,
) -> GameSim:
    """One game from whichever sampler the config names."""
    if isinstance(config, DriveConfig):
        return simulate_drives(mu_margin, mu_total, rng, config)
    return simulate_game(mu_margin, mu_total, rng, config)


OFFSETS = np.arange(0.5, 29.0, 1.0)
SHOULDER = OFFSETS <= 13.5
# The integers a football margin piles up on, plus one that it does not. A
# half-point offset profile cannot see any of them: its offsets are measured
# from each game's own μ, which is not an integer, so no offset ever isolates
# "the margin was exactly three". These are absolute margins, which is what a
# half-point through 3 is bought and sold on.
#
# 4 is in the list deliberately and is NOT a key number — the NFL lands on it
# in 4.6% of games against 14.8% on 3. A sim that earns its key numbers by
# smearing mass over every small margin would improve on 3 and 7 and get 4
# wrong by the same amount, and scoring only the spikes would call that a win.
KEY_NUMBERS = np.array([3, 7, 10, 14, 6, 4])
MARGIN_GRID = np.arange(0, 29)


def season_configs(
    train: pd.DataFrame, base: SimConfig, drive: DriveConfig
) -> dict[str, SimConfig | DriveConfig | Overlay]:
    """The six variants for one test season, fitted on ``train`` only.

    ``drive`` is the same for every season by construction: its dispersion
    falls out of football's own drive count and scoring mix, with nothing in
    it to fit. ``drive-fit`` is the same lattice — the drive count and the
    scoring mix are NOT fitted, deliberately — with the two mechanisms
    sitting outside it solved against the training seasons' residual
    moments: the model's own projection error and the shared scoring
    environment. The pair separates "the possession structure is right" from
    "the possession structure is right once what it omits is accounted for".
    """
    pool = ResidualPool.from_frame(train)
    _, slope_t, anchor = fit_sd_slope(train["mu_total"], train["resid_total"])
    _, slope_m, _ = fit_sd_slope(train["mu_total"], train["resid_margin"])
    hetero = {"sd_total_slope": slope_t, "sd_margin_slope": slope_m,
              "sd_anchor_total": anchor}
    actual = (train["mu_margin"] + train["resid_margin"]).to_numpy()
    skewed = replace(base, total_skew=fit_epsilon(train["resid_total"].to_numpy()))
    drive_fitted = fit_drive_config(
        train["resid_margin"].to_numpy(), train["resid_total"].to_numpy(),
        train["mu_total"].to_numpy(), drive)
    # Measuring a 22-bin histogram does not need the scoring run's sim count.
    drive_reference = replace(drive_fitted, n_sims=2000)
    return {
        "normal": base,
        "normal-hetero": replace(base, **hetero),
        "empirical": replace(base, residuals=pool),
        "empirical-hetero": replace(base, residuals=pool, **hetero),
        "normal+keys": Overlay(base, fit_lattice_weights(
            actual, rounded_normal_mass(
                train["mu_margin"].to_numpy(), base.sd_margin))),
        # The other asymmetry: total residuals are right-skewed and the sim
        # is symmetric. Orthogonal to the lattice — one re-shapes the total,
        # the other the margin — so the pair is the interesting row.
        "normal+skew": skewed,
        "normal+skew+keys": Overlay(skewed, fit_lattice_weights(
            actual, rounded_normal_mass(
                train["mu_margin"].to_numpy(), base.sd_margin))),
        "drive": drive,
        "drive-fit": drive_fitted,
        # The synthesis: the possession sampler's dispersion and totals, with
        # whatever lattice it still misses measured off the training seasons
        # and put back. Its reference mass has no closed form, so it is
        # simulated — under a fixed generator, so the fit is reproducible.
        "drive-fit+keys": Overlay(drive_fitted, fit_lattice_weights(
            actual, simulated_margin_mass(
                train["mu_margin"].to_numpy(), train["mu_total"].to_numpy(),
                lambda m, t, r: simulate_drives(m, t, r, drive_reference),
                make_rng(20260920)))),
    }


def score_variant(
    test: pd.DataFrame, config: SimConfig | DriveConfig | Overlay, seed: int
) -> dict[str, float]:
    """ECE / Brier on the moneyline, the offset profile, and key-number mass."""
    p_home: list[float] = []
    y: list[float] = []
    k = len(OFFSETS)
    sim_tail = np.zeros((4, k))  # over/under margin, over/under total — sim
    real_tail = np.zeros((4, k))  # the same four, what actually happened
    sim_grid = np.zeros(MARGIN_GRID.size)  # P(|margin| == m), summed over games
    real_grid = np.zeros(MARGIN_GRID.size)
    for row in test.itertuples(index=False):
        rng = make_rng(seed + int(row.week))
        mu_m, mu_t = float(row.mu_margin), float(row.mu_total)
        if isinstance(config, Overlay):
            # Same generator for the draw and the resample, so the corrected
            # variant is as deterministic as the one it corrects.
            sim = config.weights.apply(draw(config.base, mu_m, mu_t, rng), rng)
        else:
            sim = draw(config, mu_m, mu_t, rng)
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
        # Absolute-margin mass, which is where the key numbers live. The
        # normal path is rounded so its margins are integers too; bincount
        # over the grid is exact for both.
        abs_margin = np.abs(sim.margin).astype(np.int64)
        sim_grid += np.bincount(
            np.clip(abs_margin, 0, MARGIN_GRID.size - 1),
            minlength=MARGIN_GRID.size) / n
        # Rounded, not truncated: ``actual_m`` is μ plus a residual that was
        # computed by subtracting that same μ, so it is an integer in exact
        # arithmetic and need not be one in floating point. A truncation there
        # silently moves a three-point game onto 2.
        real_grid[min(int(round(abs(actual_m))), MARGIN_GRID.size - 1)] += 1.0
    games = float(len(test))
    err = np.abs(sim_tail - real_tail) / games
    err_m = np.maximum(err[0], err[1])
    err_t = np.maximum(err[2], err[3])
    grid_err = np.abs(sim_grid - real_grid) / games
    p = np.array(p_home)
    yy = np.array(y)
    return {
        "ece": expected_calibration_error(p, yy),
        "brier": brier_score(p, yy),
        # How far the sim's mass at each absolute margin is from how often
        # that margin actually happened: over the key integers, and over the
        # whole 0–28 grid (which also catches mass invented elsewhere).
        "key_max": float(grid_err[KEY_NUMBERS].max()),
        "key_mean": float(grid_err[KEY_NUMBERS].mean()),
        "grid_mean": float(grid_err.mean()),
        "p3": float(sim_grid[3] / games),
        "p7": float(sim_grid[7] / games),
        # The yardstick, identical across variants within a season: how often
        # these games really landed on 3 and on 7.
        "real_p3": float(real_grid[3] / games),
        "real_p7": float(real_grid[7] / games),
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
    parser.add_argument("--by-season", action="store_true",
                        help="also print each variant season by season — a "
                             "mean that one season carries is not a result")
    args = parser.parse_args()

    games = pd.read_parquet(args.games or f"datasets/{args.league}/games.parquet")
    residuals = residuals_from_projections(pd.read_parquet(args.projections), games)
    sd_m, sd_t = LEAGUE_SDS[args.league]
    base = SimConfig(n_sims=args.n_sims, sd_margin=sd_m, sd_total=sd_t)
    drive = replace(DRIVE_CONFIGS[args.league], n_sims=args.n_sims)
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    wanted = [v.strip() for v in args.variants.split(",") if v.strip()]

    test_seasons = sorted(s for s in residuals["season"].unique() if s >= args.eval_from)
    print(f"{args.league}: {len(residuals)} walk-forward games; testing "
          f"{test_seasons[0]}–{test_seasons[-1]} at {args.n_sims} sims × seeds {seeds}")
    rows = []
    seasonal: list[pd.DataFrame] = []
    for seed in seeds:
        per_variant: dict[str, list[pd.DataFrame]] = {}
        # Score season by season so each pool is point-in-time, then pool
        # the per-game arrays across seasons before the metrics.
        for season in test_seasons:
            train = residuals[residuals["season"] < season]
            test = residuals[residuals["season"] == season]
            if len(train) < 200 or test.empty:
                continue
            for name, cfg in season_configs(train, base, drive).items():
                if wanted and name not in wanted:
                    continue
                scored = score_variant(test, cfg, seed)
                scored.update(variant=name, seed=seed, season=season, n=len(test))
                per_variant.setdefault(name, []).append(pd.DataFrame([scored]))
                seasonal.append(pd.DataFrame([scored]))
        for name, parts in per_variant.items():
            frame = pd.concat(parts, ignore_index=True)
            w = frame["n"] / frame["n"].sum()
            row = {"variant": name, "seed": seed, "n": int(frame["n"].sum())}
            for col in ("ece", "brier", "key_max", "key_mean", "grid_mean",
                        "p3", "p7", "real_p3", "real_p7",
                        "spread_shoulder_max", "spread_tail_max",
                        "spread_mean", "total_shoulder_max", "total_tail_max", "total_mean"):
                row[col] = float((frame[col] * w).sum())
            rows.append(row)
    table = pd.DataFrame(rows)
    summary = (table.drop(columns=["seed"]).groupby("variant", sort=False)
               .mean(numeric_only=True))
    order = ["normal", "normal-hetero", "empirical", "empirical-hetero",
             "normal+keys", "normal+skew", "normal+skew+keys",
             "drive", "drive-fit", "drive-fit+keys"]
    summary = summary.reindex([v for v in order if v in summary.index])
    with pd.option_context("display.width", 200, "display.max_columns", None):
        print("\n=== Sim-shape gate (out-of-sample, mean over seeds; "
              "offset errors are worst |sim − real| probability at any half-point "
              "offset, shoulder ≤ 13.5 / tail 14.5–28.5; key_* are absolute-margin "
              "mass errors, p3/p7 against real_p3/real_p7) ===")
        print(summary.round(4).to_string())
        print("\nper seed:")
        print(table.round(4).to_string(index=False))
        if args.by_season and seasonal:
            by_season = (pd.concat(seasonal, ignore_index=True)
                         .groupby(["season", "variant"], sort=True)
                         .mean(numeric_only=True)
                         .reset_index()
                         .pivot(index="season", columns="variant",
                                values=["key_mean", "spread_mean", "ece"]))
            print("\n=== season by season (mean over seeds) ===")
            print(by_season.round(4).to_string())
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        table.to_parquet(args.out, index=False)


if __name__ == "__main__":
    main()
