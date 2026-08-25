"""Does recency change the RANKING of starting pitchers? (walk-forward)

The showdown and classic backtests both found the pitcher projection running
about 9% hot against realized DK points while hitters, once the confirmed
card is known, come in within half a point (docs/DFS_FORMATS.md). A uniform
per-class rescale fixes that bias and does not help the rosters — it moves
every pitcher together, so it reorders nothing the optimizer compares.

This tests the obvious candidate that DOES change rankings: weighting a
starter's own history by recency. A start ``h`` days old counts half as much
as today's. Flat season rate is the incumbent; several half-lives are the
challengers.

Scored per slate, because a DFS projection ranks ONE night's pool — a
correlation pooled over a season flatters any model that merely knows Paul
Skenes is better than a swingman.

    python scripts/validate_mlb_pitcher_form.py --season 2026
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from velocity.dfs.backtest import prepare_banks
from velocity.models.dfs_mlb import DfsMlbModel

HALF_LIVES = (None, 45.0, 30.0, 21.0, 14.0)


def per_slate_correlation(frame: pd.DataFrame, column: str,
                          min_starters: int = 6) -> float:
    """Mean within-slate correlation of a projection against actual DK points."""
    values = []
    for _day, slate in frame.groupby(frame["kickoff"].dt.date):
        sub = slate[[column, "actual"]].dropna()
        if len(sub) < min_starters or sub[column].nunique() < 2:
            continue
        r = sub[column].corr(sub["actual"])
        if pd.notna(r):
            values.append(float(r))
    return float(np.mean(values)) if values else float("nan")


def top_k_by_slate(frame: pd.DataFrame, column: str, k: int) -> float:
    """Mean actual DK points of each slate's top-k arms by projection."""
    values = []
    for _day, slate in frame.groupby(frame["kickoff"].dt.date):
        sub = slate.dropna(subset=[column, "actual"])
        if len(sub) < k:
            continue
        values.append(float(sub.nlargest(k, column)["actual"].mean()))
    return float(np.mean(values)) if values else float("nan")


def walk_forward(bat, sp, played, *, step_days: int, min_train: int) -> pd.DataFrame:
    """Project every start in each window from a model fit only on its past."""
    windows = []
    start = sp["kickoff"].min().normalize()
    for cutoff in pd.date_range(start, sp["kickoff"].max(), freq=f"{step_days}D"):
        train = played[played["kickoff"] < cutoff]
        if len(train) < min_train:
            continue
        train_ids = set(train["game_id"])
        window = sp[(sp["kickoff"] >= cutoff)
                    & (sp["kickoff"] < cutoff + pd.Timedelta(days=step_days))]
        if window.empty:
            continue
        fitted = {
            half_life: DfsMlbModel.fit(
                bat[bat["game_id"].isin(train_ids)],
                sp[sp["game_id"].isin(train_ids)], train,
                pitcher_half_life=half_life)
            for half_life in HALF_LIVES
        }
        projections = {
            _column(half_life): [model.project_pitcher(str(pid))
                                 for pid in window["starter_id"]]
            for half_life, model in fitted.items()
        }
        windows.append(window.assign(**projections))
    return pd.concat(windows, ignore_index=True) if windows else pd.DataFrame()


def _column(half_life: float | None) -> str:
    return "p_flat" if half_life is None else f"p_hl{int(half_life)}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward pitcher recency")
    parser.add_argument("--batters", default="datasets/mlb/batters.parquet")
    parser.add_argument("--starters", default="datasets/mlb/starters.parquet")
    parser.add_argument("--games", default="datasets/mlb/games.parquet")
    parser.add_argument("--season", type=int, default=0)
    parser.add_argument("--step-days", type=int, default=14)
    parser.add_argument("--min-train-games", type=int, default=400)
    args = parser.parse_args()

    bat, sp, played = prepare_banks(
        pd.read_parquet(args.batters), pd.read_parquet(args.starters),
        pd.read_parquet(args.games))
    if args.season:
        seasons = set(played.loc[played["season"] == args.season, "game_id"])
        bat = bat[bat["game_id"].isin(seasons)]
        sp = sp[sp["game_id"].isin(seasons)]
        played = played[played["game_id"].isin(seasons)]

    graded = walk_forward(bat, sp, played, step_days=args.step_days,
                          min_train=args.min_train_games)
    if graded.empty:
        raise SystemExit("no windows produced")
    days = graded["kickoff"].dt.date.nunique()
    print(f"{len(graded):,} starts over {days} slates\n")

    print("--- mean WITHIN-SLATE correlation with actual DK points ---")
    base = per_slate_correlation(graded, "p_flat")
    for half_life in HALF_LIVES:
        column = _column(half_life)
        r = per_slate_correlation(graded, column)
        label = "flat season rate" if half_life is None else f"half-life {half_life:g}d"
        delta = "" if half_life is None else f"   ({r - base:+.4f})"
        print(f"  {label:<20} {r:+.4f}{delta}")

    print("\n--- actual DK points of each slate's top-k arms by projection ---")
    print(f"  every start                 {graded['actual'].mean():.2f}")
    for k in (2, 4):
        parts = []
        for half_life in HALF_LIVES:
            label = "flat" if half_life is None else f"{half_life:g}d"
            parts.append(f"{label} {top_k_by_slate(graded, _column(half_life), k):.2f}")
        print(f"  top {k:<3d} " + "   ".join(parts))

    print("\n--- level (projected vs actual) ---")
    for half_life in HALF_LIVES:
        column = _column(half_life)
        sub = graded.dropna(subset=[column])
        label = "flat season rate" if half_life is None else f"half-life {half_life:g}d"
        print(f"  {label:<20} projected {sub[column].mean():6.2f} "
              f"vs actual {sub['actual'].mean():6.2f} "
              f"({sub[column].mean() - sub['actual'].mean():+.2f})")


if __name__ == "__main__":
    main()
