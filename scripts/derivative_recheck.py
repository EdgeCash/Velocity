#!/usr/bin/env python3
"""Does the key-number overlay hold up everywhere the sim is read?

    python3 scripts/derivative_recheck.py --league nfl

The lattice round (``docs/MODEL_LAB.md``) won the sim-shape gate and stopped
there on purpose: eight columns scored on the game markets are not a licence
to change a sim that props, ladders, DFS and the correlation model all price
off. This is that wider check.

Three families, each scored against what actually happened rather than
against the shipped sim:

1. **The ladder gate** (``velocity/eval/ladders.py``), which is the one that
   matters most and the one easiest to miss. That gate is not a consumer of
   the sim — it is a HARDCODED CORRECTION FOR THE SIM'S SHAPE BEING WRONG,
   banked as a table of per-offset probability errors and used to refuse
   rungs the sim cannot price honestly. Change the shape and the correction
   is measuring a sim that no longer exists. So this re-measures the same
   table against each candidate and reports how many rungs each would let
   through.
2. **Ladder pricing**, over the whole board rather than the gate's summary:
   the probability error at every rung, and the log score of the actual
   outcome under each sim.
3. **Everything downstream of an exact score** — team totals and the modal
   score the site shows. These read the joint distribution rather than the
   margin's tails, so they are where a resample could do damage the gate
   never sees, and a resample carries whole (home, away) pairs precisely so
   it cannot.
4. **Same-game two-leg parlays** (``velocity/wagering/parlay.py``), which
   price a side and a total off ONE set of draws so the correlation between
   them is the sim's own. That is the sharpest test of whether a resample
   damaged the joint: a correction applied to the margin alone could leave
   both marginals right and the pair wrong, and nothing else here would
   see it.

Honest by construction: the lattice is fitted on seasons strictly before
each scored season, exactly as in ``scripts/sim_lab.py``.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from velocity.eval.ladders import residual_calibration
from velocity.models.keynumbers import (
    LatticeWeights,
    fit_lattice_weights,
    rounded_normal_mass,
)
from velocity.models.residuals import residuals_from_projections
from velocity.models.simulate import (
    DEFAULT_SD_MARGIN,
    DEFAULT_SD_TOTAL,
    NCAAF_SD_MARGIN,
    NCAAF_SD_TOTAL,
    GameSim,
    SimConfig,
    simulate_game,
)
from velocity.util.seed import make_rng

LEAGUE_SDS = {
    "nfl": (DEFAULT_SD_MARGIN, DEFAULT_SD_TOTAL),
    "ncaaf": (NCAAF_SD_MARGIN, NCAAF_SD_TOTAL),
}
OFFSETS = np.arange(0.5, 29.0, 1.0)
# The gate's own bar, from velocity/eval/ladders.py.
DEFAULT_TOLERANCE = 0.02


def league_frames(league: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    games = pd.read_parquet(f"datasets/{league}/games.parquet")
    residuals = residuals_from_projections(
        pd.read_parquet(f"datasets/{league}/projections_promoted.parquet"), games)
    return games, residuals


def fit_weights(train: pd.DataFrame, sd_margin: float) -> LatticeWeights:
    return fit_lattice_weights(
        (train["mu_margin"] + train["resid_margin"]).to_numpy(),
        rounded_normal_mass(train["mu_margin"].to_numpy(), sd_margin))


def _sim(mu_m: float, mu_t: float, config: SimConfig,
         weights: LatticeWeights | None, seed: int) -> GameSim:
    rng = make_rng(seed)
    sim = simulate_game(mu_m, mu_t, rng, config)
    return sim if weights is None else weights.apply(sim, rng)


# --- 1. the gate's own table, re-measured ---------------------------------

def gate_table(
    games: pd.DataFrame, market: str, config: SimConfig,
    weights: LatticeWeights | None, seed: int = 7,
) -> pd.DataFrame:
    """The gate's per-offset error, but with a SIM in place of its normal.

    The banked table compares the empirical tail past each offset with a
    continuous normal fitted to the residuals. That normal is a stand-in for
    the sim, and a loose one: the real sim rounds, and under the overlay it
    is not normal at all. So this asks each candidate directly — simulate
    every game at the market's own numbers and count how often the draws land
    past each offset — and scores it against the same empirical tails the
    gate uses.

    The market's close stands in for μ here, exactly as the banked table has
    it: it is the sharpest per-game expectation available, so this isolates
    the shape of outcomes rather than the model's aim.
    """
    frame = games.dropna(
        subset=["home_score", "away_score", "spread_line", "total_line"])
    outcome = (frame["home_score"] - frame["away_score"] if market == "spread"
               else frame["home_score"] + frame["away_score"])
    # ``spread_line`` in the games frames is the expected HOME MARGIN —
    # positive when the home team is favoured, so the residual is
    # ``margin - spread_line`` and its sd comes out at the 12.98 the ladder
    # module quotes. That is the OPPOSITE sign from the sim's own
    # ``fair_spread``, where -6 means home by 6, and from the ``mu_margin``
    # that `residuals_from_projections` derives by negating it. Applying the
    # sim's convention here reported the shipped sim missing the spread tails
    # by fourteen points of probability, which is not a result, it is a
    # sign error — the one thing velocity/eval/ladders.py warns about.
    centre = (frame["spread_line"] if market == "spread"
              else frame["total_line"]).astype(float).to_numpy()
    residual = outcome.astype(float).to_numpy() - centre
    over_emp = np.array([(residual > o).mean() for o in OFFSETS])
    under_emp = np.array([(residual < -o).mean() for o in OFFSETS])

    mu_margins = frame["spread_line"].astype(float).to_numpy()
    mu_totals = frame["total_line"].astype(float).to_numpy()
    over_sim = np.zeros(OFFSETS.size)
    under_sim = np.zeros(OFFSETS.size)
    for i, (mu_m, mu_t) in enumerate(zip(mu_margins, mu_totals, strict=True)):
        sim = _sim(float(mu_m), float(mu_t), config, weights, seed + i % 64)
        draw = np.sort(sim.margin if market == "spread" else sim.total)
        mid = float(centre[i])
        n = float(sim.n_sims)
        # One sort, then every offset's tail by bisection.
        over_sim += 1.0 - np.searchsorted(draw, mid + OFFSETS, side="right") / n
        under_sim += np.searchsorted(draw, mid - OFFSETS, side="left") / n
    over_sim /= len(frame)
    under_sim /= len(frame)
    return pd.DataFrame({
        "offset": OFFSETS,
        "over_bias": over_sim - over_emp,
        "under_bias": under_sim - under_emp,
        "error": np.maximum(np.abs(over_sim - over_emp),
                            np.abs(under_sim - under_emp)),
    })


def banked_gate_table(games: pd.DataFrame, market: str) -> pd.DataFrame:
    """What the shipped gate believes today, in the same shape.

    :func:`~velocity.eval.ladders.residual_calibration` is the generator
    behind the committed ``OFFSET_BIAS`` literal, so this is the row the
    other candidates have to beat.
    """
    table = residual_calibration(games, market, max_offset=float(OFFSETS.max()))
    return table[["offset", "over_bias", "under_bias", "error"]]


def gate_summary(table: pd.DataFrame, tolerance: float = DEFAULT_TOLERANCE) -> dict:
    """What the gate would do with this table: how many sides it lets through.

    A rung passes when the sim does not OVERSTATE that side by more than the
    bar — a negative bias is safe, because an edge this bias cannot have
    invented is an edge worth having (``rung_is_honest``). Counting open
    SIDES rather than offsets is the same asymmetry the shipped gate reads.
    """
    sides = np.concatenate([table["over_bias"].to_numpy(),
                            table["under_bias"].to_numpy()])
    near = table["offset"] <= 13.5
    return {
        "worst_error": float(table["error"].max()),
        "mean_error": float(table["error"].mean()),
        "shoulder_error": float(table.loc[near, "error"].max()),
        "sides_open": int(np.count_nonzero(sides <= tolerance)),
        "sides": int(sides.size),
    }


# --- 2. ladder pricing, scored against what happened ----------------------

def ladder_scores(
    test: pd.DataFrame, config: SimConfig, weights: LatticeWeights | None,
    seed: int = 7,
) -> dict[str, float]:
    """Every rung on the board, priced and then graded.

    ``rung_error`` is the calibration error over the whole ladder: bucket
    every (game, rung) pair by the probability quoted and compare it with how
    often that side actually landed. ``log_score`` is the exact-margin score
    — the probability each sim put on the margin the game actually finished
    on — which is the one number that sees the lattice and the dispersion at
    once, and cannot be gamed by getting either alone right.
    """
    quoted: list[np.ndarray] = []
    landed: list[np.ndarray] = []
    exact: list[float] = []
    team_quoted: list[np.ndarray] = []
    team_landed: list[np.ndarray] = []
    modal_hits = 0
    for i, row in enumerate(test.itertuples(index=False)):
        mu_m, mu_t = float(row.mu_margin), float(row.mu_total)
        sim = _sim(mu_m, mu_t, config, weights, seed + i % 64)
        actual_m = mu_m + float(row.resid_margin)
        actual_t = mu_t + float(row.resid_total)
        rungs = np.round(mu_m) + np.arange(-14.0, 14.5, 1.0) + 0.5
        margin = np.sort(sim.margin)
        n = float(sim.n_sims)
        quoted.append(1.0 - np.searchsorted(margin, rungs, side="right") / n)
        landed.append((actual_m > rungs).astype(float))
        # The exact margin, which is where a lattice either pays or does not.
        exact.append(float(np.count_nonzero(sim.margin == np.round(actual_m))) / n)
        # Team totals: the home side's own number, which reads the joint
        # rather than the margin and so is free to be damaged by a resample.
        home_rungs = np.round((mu_t + mu_m) / 2.0) + np.arange(-7.0, 7.5, 1.0) + 0.5
        home = np.sort(sim.home_score)
        team_quoted.append(
            1.0 - np.searchsorted(home, home_rungs, side="right") / n)
        team_landed.append(((actual_t + actual_m) / 2.0 > home_rungs).astype(float))
        modal = np.bincount(
            np.clip(sim.margin.astype(np.int64) + 60, 0, 120)).argmax() - 60
        modal_hits += int(modal == round(actual_m))
    q, y = np.concatenate(quoted), np.concatenate(landed)
    tq, ty = np.concatenate(team_quoted), np.concatenate(team_landed)
    probs = np.clip(np.array(exact), 1e-6, None)
    return {
        "rung_calib": _bucket_error(q, y),
        "rung_brier": float(np.mean((q - y) ** 2)),
        "team_calib": _bucket_error(tq, ty),
        "exact_logscore": float(np.mean(np.log(probs))),
        "exact_mass": float(np.mean(probs)),
        "modal_hit": modal_hits / float(len(test)),
    }


def parlay_scores(
    test: pd.DataFrame, config: SimConfig, weights: LatticeWeights | None,
    seed: int = 7,
) -> dict[str, float]:
    """Same-game side+total pairs, priced off one set of draws and graded.

    ``joint_calib`` is the calibration error over every pair; ``lift_error``
    is how far the sim's correlation is from the games' own, measured as the
    joint minus what independence would give. A sim can get both legs right
    and the pair wrong, and only this column would notice.
    """
    quoted: list[np.ndarray] = []
    landed: list[np.ndarray] = []
    lift_sim: list[np.ndarray] = []
    lift_real: list[np.ndarray] = []
    for i, row in enumerate(test.itertuples(index=False)):
        mu_m, mu_t = float(row.mu_margin), float(row.mu_total)
        sim = _sim(mu_m, mu_t, config, weights, seed + i % 64)
        actual_m = mu_m + float(row.resid_margin)
        actual_t = mu_t + float(row.resid_total)
        # Rungs either side of the fair numbers, which is where a same-game
        # parlay is actually struck.
        spreads = np.round(mu_m) + np.array([-3.5, -0.5, 2.5])
        totals = np.round(mu_t) + np.array([-3.5, -0.5, 2.5])
        covers = sim.margin[:, None] > spreads          # (n_sims, 3)
        overs = sim.total[:, None] > totals
        # Every (side, total) pair, both directions on the total.
        joint = np.concatenate([
            (covers[:, :, None] & overs[:, None, :]).mean(axis=0).ravel(),
            (covers[:, :, None] & ~overs[:, None, :]).mean(axis=0).ravel(),
        ])
        p_cover = covers.mean(axis=0)
        p_over = overs.mean(axis=0)
        independent = np.concatenate([
            (p_cover[:, None] * p_over[None, :]).ravel(),
            (p_cover[:, None] * (1.0 - p_over)[None, :]).ravel(),
        ])
        hit_cover = actual_m > spreads
        hit_over = actual_t > totals
        outcome = np.concatenate([
            (hit_cover[:, None] & hit_over[None, :]).ravel(),
            (hit_cover[:, None] & ~hit_over[None, :]).ravel(),
        ]).astype(float)
        quoted.append(joint)
        landed.append(outcome)
        lift_sim.append(joint - independent)
        lift_real.append(outcome - independent)
    q, y = np.concatenate(quoted), np.concatenate(landed)
    return {
        "joint_calib": _bucket_error(q, y),
        "joint_brier": float(np.mean((q - y) ** 2)),
        "lift_error": float(abs(np.mean(np.concatenate(lift_sim))
                                - np.mean(np.concatenate(lift_real)))),
    }


def _bucket_error(quoted: np.ndarray, landed: np.ndarray, bins: int = 20) -> float:
    """Mean |quoted − landed| over equal-width probability buckets."""
    edges = np.linspace(0.0, 1.0, bins + 1)
    index = np.clip(np.digitize(quoted, edges) - 1, 0, bins - 1)
    total = 0.0
    weight = 0.0
    for b in range(bins):
        mask = index == b
        k = int(np.count_nonzero(mask))
        if k < 30:
            continue
        total += k * abs(float(quoted[mask].mean()) - float(landed[mask].mean()))
        weight += k
    return total / weight if weight else float("nan")


def _weighted(scored: list[tuple[int, dict[str, float]]], key: str) -> float:
    """Games-weighted mean over seasons, skipping the ones with no answer.

    A season too thin to fill a calibration bucket returns NaN for that
    column — the in-progress season does, at sixteen games. Weighting it in
    would turn every other season's answer into NaN too, which is how a
    working measurement comes back blank and gets dropped instead of fixed.
    """
    pairs = [(n, s[key]) for n, s in scored if np.isfinite(s[key])]
    weight = float(sum(n for n, _ in pairs))
    if not weight:
        return float("nan")
    return sum(n * v for n, v in pairs) / weight


def main() -> None:
    parser = argparse.ArgumentParser(description="Derivative re-check")
    parser.add_argument("--league", default="nfl", choices=sorted(LEAGUE_SDS))
    parser.add_argument("--eval-from", type=int, default=None)
    parser.add_argument("--n-sims", type=int, default=4000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--skip-gate", action="store_true")
    args = parser.parse_args()

    eval_from = args.eval_from or (2015 if args.league == "nfl" else 2022)
    games, residuals = league_frames(args.league)
    sd_m, sd_t = LEAGUE_SDS[args.league]
    base = SimConfig(n_sims=args.n_sims, sd_margin=sd_m, sd_total=sd_t)
    # One lattice for the gate pass, fitted on everything before the scored
    # window — the gate's table is a single banked constant, so its
    # replacement has to be one too.
    gate_weights = fit_weights(residuals[residuals["season"] < eval_from], sd_m)

    if not args.skip_gate:
        print(f"=== 1. the ladder gate, re-measured ({args.league}, "
              f"{args.n_sims} sims) ===")
        print("The banked row is what velocity/eval/ladders.py believes today.")
        for market in ("spread", "total"):
            rows = {
                "banked normal": banked_gate_table(games, market),
                "shipped sim": gate_table(games, market, base, None, args.seed),
                "sim + keys": gate_table(games, market, base, gate_weights,
                                         args.seed),
            }
            print(f"\n  {args.league} {market}")
            for name, table in rows.items():
                s = gate_summary(table)
                print(f"    {name:14s} worst {s['worst_error']:.4f} "
                      f" shoulder {s['shoulder_error']:.4f} "
                      f" mean {s['mean_error']:.4f} "
                      f" sides open {s['sides_open']:3d}/{s['sides']}")

    print(f"\n=== 2. the board, priced and graded ({args.league}, "
          f"{eval_from}+) ===")
    seasons = sorted(s for s in residuals["season"].unique() if s >= eval_from)
    parts: dict[str, list[tuple[int, dict[str, float]]]] = {}
    for season in seasons:
        train = residuals[residuals["season"] < season]
        test = residuals[residuals["season"] == season]
        if len(train) < 400 or test.empty:
            continue
        weights = fit_weights(train, sd_m)
        for name, w in (("shipped sim", None), ("sim + keys", weights)):
            scored = ladder_scores(test, base, w, args.seed)
            scored.update(parlay_scores(test, base, w, args.seed))
            parts.setdefault(name, []).append((len(test), scored))
    columns = ("rung_calib", "rung_brier", "team_calib", "exact_logscore",
               "exact_mass", "modal_hit", "joint_calib", "lift_error")
    print(f"{'variant':14s}" + "".join(f"{c:>15s}" for c in columns))
    for name, scored in parts.items():
        agg = {k: _weighted(scored, k) for k in scored[0][1]}
        print(f"{name:14s}" + "".join(f"{agg[c]:15.4f}" for c in columns))


if __name__ == "__main__":
    main()
