"""Walk-forward the NFL DK projections — does the rate rank a week correctly?

The football counterpart of ``validate_dfs_mlb.py``, and the first time
Velocity's football DFS numbers have been scored against anything. For each
week the model is fit **only on weeks that finished before it**, then every
skill player who took the field is projected and compared with the DK points
he actually banked.

Reported per week, because a DFS projection ranks ONE slate's pool: a
correlation pooled across seasons flatters any model that merely knows a
starting quarterback outscores a third receiver.

Two comparisons:

* **player rate** — empirical-Bayes DK points per game, shrunk toward the
  player's own position mean. The incumbent.
* **+ defense** — the same rate times how many DK points the opponent allows
  to that position. Off in the model until it earns its place here.

    python scripts/validate_dfs_nfl.py
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from velocity.models.dfs_nfl import SKILL_POSITIONS, DfsNflModel


def per_week_correlation(frame: pd.DataFrame, column: str,
                         min_players: int = 30) -> float:
    """Mean within-week correlation of a projection against actual DK points."""
    values = []
    for _key, week in frame.groupby(["season", "week"]):
        sub = week[[column, "dk_points"]].dropna()
        if len(sub) < min_players or sub[column].nunique() < 2:
            continue
        r = sub[column].corr(sub["dk_points"])
        if pd.notna(r):
            values.append(float(r))
    return float(np.mean(values)) if values else float("nan")


def top_k_by_week(frame: pd.DataFrame, column: str, k: int) -> float:
    """Mean actual DK points of each week's top-k by projection."""
    values = []
    for _key, week in frame.groupby(["season", "week"]):
        sub = week.dropna(subset=[column, "dk_points"])
        if len(sub) < k:
            continue
        values.append(float(sub.nlargest(k, column)["dk_points"].mean()))
    return float(np.mean(values)) if values else float("nan")


def walk_forward(weeks: pd.DataFrame, *, min_train_weeks: int) -> pd.DataFrame:
    """Project each week from a model that has seen only its past."""
    frame = weeks[weeks["position"].isin(SKILL_POSITIONS)].copy()
    frame["season"] = pd.to_numeric(frame["season"], errors="coerce")
    frame["week"] = pd.to_numeric(frame["week"], errors="coerce")
    frame = frame.dropna(subset=["season", "week"])
    # A player who did not take the field has no DK line to be right about.
    played = frame[frame[["carries", "targets", "attempts"]].sum(axis=1) > 0]

    order = sorted({(int(s), int(w)) for s, w in
                    zip(frame["season"], frame["week"], strict=True)})
    graded = []
    for index, (season, week) in enumerate(order):
        if index < min_train_weeks:
            continue
        past = frame[(frame["season"] < season)
                     | ((frame["season"] == season) & (frame["week"] < week))]
        window = played[(played["season"] == season) & (played["week"] == week)]
        if window.empty:
            continue
        model = DfsNflModel.fit(past)
        flat, with_defense = [], []
        for row in window.to_dict("records"):
            pid = str(row["player_id"])
            flat.append(model.project(pid))
            with_defense.append(model.project(pid, opponent=str(row["opponent"]),
                                              use_defense=True))
        graded.append(window.assign(p_flat=flat, p_defense=with_defense))
    return pd.concat(graded, ignore_index=True) if graded else pd.DataFrame()


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward NFL DFS projections")
    parser.add_argument("--weeks", default="datasets/nfl/player_weeks.parquet")
    parser.add_argument("--min-train-weeks", type=int, default=17,
                        help="weeks of history required before a week is scored")
    args = parser.parse_args()

    graded = walk_forward(pd.read_parquet(args.weeks),
                          min_train_weeks=args.min_train_weeks)
    if graded.empty:
        raise SystemExit("no weeks produced")
    graded = graded.dropna(subset=["p_flat"])
    n_weeks = graded.groupby(["season", "week"]).ngroups
    print(f"{len(graded):,} player-weeks over {n_weeks} weeks\n")

    print("--- mean WITHIN-WEEK correlation with actual DK points ---")
    flat = per_week_correlation(graded, "p_flat")
    defense = per_week_correlation(graded, "p_defense")
    print(f"  player rate (incumbent)     {flat:+.4f}")
    print(f"  + defense (candidate)       {defense:+.4f}   ({defense - flat:+.4f})")

    print("\n--- actual DK points of each week's top-k by projection ---")
    print(f"  every player who played     {graded['dk_points'].mean():.2f}")
    for k in (9, 20, 50):
        f = top_k_by_week(graded, "p_flat", k)
        d = top_k_by_week(graded, "p_defense", k)
        print(f"  top {k:<3d} player rate {f:.2f}   + defense {d:.2f}   ({d - f:+.2f})")

    print("\n--- level (projected vs actual), by position ---")
    for position in SKILL_POSITIONS:
        sub = graded[graded["position"] == position]
        if sub.empty:
            continue
        print(f"  {position:<3} projected {sub['p_flat'].mean():6.2f} "
              f"vs actual {sub['dk_points'].mean():6.2f} "
              f"({sub['p_flat'].mean() - sub['dk_points'].mean():+.2f}, "
              f"n = {len(sub):,})")


if __name__ == "__main__":
    main()
