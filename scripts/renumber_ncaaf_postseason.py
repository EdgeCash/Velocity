"""Renumber postseason weeks in the committed NCAAF frames, in place.

``velocity.ingest.ncaaf.normalize_games`` puts postseason games on one ordinal
with the regular season, but that only applies to a fresh pull. The committed
frames were built before it and still carry 411 of 478 postseason games at week
1 -- which is how 55,007 plays of bowl football sat inside the walk-forward's
own training window for every NCAAF backtest.

This applies the same function to what is already on disk, driven by
``games.parquet``'s ``season_type`` and ``kickoff`` and joined to the other
frames by ``game_id``. It is idempotent: a second run finds nothing to move.

    python scripts/renumber_ncaaf_postseason.py [--data datasets/ncaaf] [--dry-run]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from velocity.ingest.ncaaf import postseason_week

# Every committed frame that carries a week alongside a game_id. prop_residuals
# and sp_ratings carry neither and are untouched.
FRAMES = ("games.parquet", "games_lines.parquet", "boxscores_2002_2025.parquet",
          "player_games.parquet", "plays.parquet", "sim_residuals.parquet")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="datasets/ncaaf")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would move, write nothing")
    args = parser.parse_args()
    folder = Path(args.data)

    games = pd.read_parquet(folder / "games.parquet")
    post = games["season_type"].astype(str) == "POST"
    if not post.any():
        print("no postseason games in the games frame; nothing to do")
        return

    # The new week for every postseason game, keyed by game_id. Computed once
    # here and applied everywhere, so the frames cannot drift apart.
    fixed = postseason_week(games.loc[post, "season"], games.loc[post, "kickoff"])
    new_week = dict(zip(games.loc[post, "game_id"].astype(str), fixed, strict=True))
    moving = {gid: w for gid, w in new_week.items()
              if int(games.loc[games["game_id"].astype(str) == gid, "week"].iloc[0]) != w}
    print(f"postseason games: {int(post.sum())}, of which {len(moving)} change week")
    if moving:
        before = games[post].set_index(games.loc[post, "game_id"].astype(str))
        sample = list(moving)[:3]
        for gid in sample:
            print(f"  {gid}: week {int(before.loc[gid, 'week'])} -> {moving[gid]} "
                  f"({before.loc[gid, 'kickoff'].date()})")

    for name in FRAMES:
        path = folder / name
        if not path.exists():
            print(f"  {name}: absent, skipped")
            continue
        frame = pd.read_parquet(path)
        if "week" not in frame.columns or "game_id" not in frame.columns:
            print(f"  {name}: no week/game_id, skipped")
            continue
        gid = frame["game_id"].astype(str)
        target = gid.map(new_week)
        touched = target.notna() & (frame["week"] != target)
        n = int(touched.sum())
        if n and not args.dry_run:
            frame.loc[touched, "week"] = target[touched].astype(frame["week"].dtype)
            frame.to_parquet(path, index=False)
        print(f"  {name}: {n:,} row(s) renumbered{' (dry run)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
