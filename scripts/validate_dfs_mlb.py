"""Walk-forward the MLB DFS projections — contextual vs the flat-rate model.

The gate the flat-rate projections never had to clear. For each window, fit
only on games completed before it, project every batter who started and every
starting pitcher, then score against the DK points they actually banked.

The comparison that matters is against the model we are replacing: a flat
season rate with no matchup, park, or lineup-slot term. If context does not
beat flat, it does not ship.

Reported per slate, because a DFS projection is used to rank ONE night's
pool — a correlation pooled across a whole season flatters any model that
merely knows Aaron Judge is better than a utility infielder.

    python scripts/validate_dfs_mlb.py --season 2026
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from velocity.models.dfs_mlb import DfsMlbModel, hitter_dk_points, pitcher_dk_points

sys.path.insert(0, str(Path(__file__).parent))


def per_slate_correlation(frame: pd.DataFrame, column: str) -> float:
    """Mean within-slate correlation of a projection against actual DK points."""
    values = []
    for _day, slate in frame.groupby(frame["kickoff"].dt.date):
        sub = slate[[column, "actual"]].dropna()
        if len(sub) < 8 or sub[column].nunique() < 2:
            continue
        r = sub[column].corr(sub["actual"])
        if pd.notna(r):
            values.append(float(r))
    return float(np.mean(values)) if values else float("nan")


def top_k_by_slate(frame: pd.DataFrame, column: str, k: int) -> float:
    """Mean actual DK points of each slate's top-k by projection."""
    values = []
    for _day, slate in frame.groupby(frame["kickoff"].dt.date):
        sub = slate.dropna(subset=[column, "actual"])
        if len(sub) < k:
            continue
        values.append(float(sub.nlargest(k, column)["actual"].mean()))
    return float(np.mean(values)) if values else float("nan")


def walk_forward(batters, starters, games, *, season, step_days=14,
                 min_train_games=400) -> pd.DataFrame:
    games = games.copy()
    games["game_id"] = games["game_id"].astype(str)
    games["kickoff"] = pd.to_datetime(games["kickoff"], errors="coerce")
    games = games.dropna(subset=["kickoff"]).sort_values("kickoff")
    played = games[games["home_score"].notna()]

    bat = batters.copy()
    bat["game_id"] = bat["game_id"].astype(str)
    bat["batter_id"] = bat["batter_id"].astype(str)
    bat = bat.merge(played[["game_id", "kickoff", "season", "home_team", "away_team"]],
                    on="game_id", how="inner")
    bat = bat[bat["started"].astype(bool)]
    bat["actual"] = hitter_dk_points(bat)

    sp = starters.copy()
    sp["game_id"] = sp["game_id"].astype(str)
    sp["starter_id"] = sp["starter_id"].astype(str)
    sp = sp.merge(played[["game_id", "kickoff", "home_team", "away_team"]],
                  on="game_id", how="inner")
    sp["actual"] = pitcher_dk_points(sp)
    # Each batter faces the OTHER side's starter.
    opposing = sp.assign(side=sp["side"].map({"home": "away", "away": "home"}))[
        ["game_id", "side", "starter_id"]]
    bat = bat.merge(opposing, on=["game_id", "side"], how="left")

    start = bat[bat["season"] == season]["kickoff"].min()
    rows = []
    for cutoff in pd.date_range(start, bat["kickoff"].max(), freq=f"{step_days}D"):
        train_ids = set(played[played["kickoff"] < cutoff]["game_id"])
        if len(train_ids) < min_train_games:
            continue
        window = bat[(bat["kickoff"] >= cutoff)
                     & (bat["kickoff"] < cutoff + pd.Timedelta(days=step_days))]
        if window.empty:
            continue
        model = DfsMlbModel.fit(
            bat[bat["game_id"].isin(train_ids)],
            sp[sp["game_id"].isin(train_ids)],
            played[played["game_id"].isin(train_ids)])
        # The baseline we are replacing: flat season rate x a constant PA.
        flat, context = [], []
        for row in window.to_dict("records"):
            pid = str(row["batter_id"])
            rate = model.batter_rate.get(pid)
            flat.append(np.nan if rate is None else rate * model.default_pa)
            context.append(model.project_hitter(
                pid,
                opposing_starter=None if pd.isna(row.get("starter_id"))
                else str(row["starter_id"]),
                venue=str(row["home_team"]),
                lineup_slot=int(row["lineup_slot"]) or None))
        rows.append(window.assign(p_flat=flat, p_context=context))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward MLB DFS projections")
    parser.add_argument("--batters", default="datasets/mlb/batters.parquet")
    parser.add_argument("--starters", default="datasets/mlb/starters.parquet")
    parser.add_argument("--games", default="datasets/mlb/games.parquet")
    parser.add_argument("--season", type=int, default=0)
    parser.add_argument("--step-days", type=int, default=14)
    args = parser.parse_args()

    batters = pd.read_parquet(args.batters)
    starters = pd.read_parquet(args.starters)
    games = pd.read_parquet(args.games)
    season = args.season or int(games["season"].max())

    graded = walk_forward(batters, starters, games, season=season,
                          step_days=args.step_days)
    if graded.empty:
        raise SystemExit("no windows produced")
    graded = graded.dropna(subset=["p_flat", "p_context"])
    days = graded["kickoff"].dt.date.nunique()
    print(f"{len(graded):,} hitter-games over {days} slates\n")

    print("--- mean WITHIN-SLATE correlation with actual DK points ---")
    flat = per_slate_correlation(graded, "p_flat")
    ctx = per_slate_correlation(graded, "p_context")
    print(f"  flat season rate (current)  {flat:+.4f}")
    print(f"  contextual (proposed)       {ctx:+.4f}")
    print(f"  -> {ctx - flat:+.4f} absolute"
          + (f" ({(ctx - flat) / abs(flat):+.1%} relative)" if flat else ""))

    print("\n--- actual DK points of each slate's top-k by projection ---")
    field = float(graded["actual"].mean())
    print(f"  field average               {field:.2f}")
    for k in (10, 20, 50):
        f = top_k_by_slate(graded, "p_flat", k)
        c = top_k_by_slate(graded, "p_context", k)
        print(f"  top {k:<3d}  flat {f:.2f}   contextual {c:.2f}   ({c - f:+.2f})")


if __name__ == "__main__":
    main()
