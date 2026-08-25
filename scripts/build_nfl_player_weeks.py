"""Bank the NFL DK scoring line per player-week (free, committable).

The football half of what the MLB box-score banks already give the DFS
layer: what every player actually scored, week by week, in DraftKings
points. nflverse publishes weekly player stats as a public release asset per
season — keyless and free, so the output commits like every other
``datasets/`` file.

NFL Showdown is the second-largest pool DK runs and Velocity's football DFS
surface has never been scored against anything; this is the substrate that
lets it be (docs/DFS_FORMATS.md).

    python scripts/build_nfl_player_weeks.py --from-season 2020
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from velocity.ingest.nfl import load_dfs_weeks
from velocity.models.dfs_nfl import nfl_dk_points

_KEYS = ["season", "week", "player_id"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Bank NFL DK player-weeks")
    parser.add_argument("--from-season", type=int, default=2020)
    parser.add_argument("--to-season", type=int, default=0,
                        help="last season to fetch (0 = the newest banked game)")
    parser.add_argument("--games", default="datasets/nfl/games.parquet")
    parser.add_argument("--out", default="datasets/nfl/player_weeks.parquet")
    args = parser.parse_args()

    last = args.to_season
    if not last:
        games = pd.read_parquet(args.games)
        last = int(pd.to_numeric(games["season"], errors="coerce").max())
    years = list(range(args.from_season, last + 1))
    print(f"fetching nflverse weekly stats for {years[0]}–{years[-1]}")

    weeks = load_dfs_weeks(years)
    if weeks.empty:
        raise SystemExit("no player-weeks fetched — nothing written")
    weeks["dk_points"] = nfl_dk_points(weeks)

    out = Path(args.out)
    existing = pd.read_parquet(out) if out.exists() else pd.DataFrame()
    combined = (pd.concat([existing, weeks], ignore_index=True)
                if not existing.empty else weeks)
    combined = combined.drop_duplicates(subset=_KEYS, keep="last")
    combined = combined.sort_values(_KEYS).reset_index(drop=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(out, index=False)

    scored = combined[combined["dk_points"] != 0]
    print(f"wrote {len(combined):,} player-weeks "
          f"({combined['season'].min()}–{combined['season'].max()}, "
          f"{combined['player_id'].nunique():,} players) to {out}")
    print(f"  {len(scored):,} weeks scored; mean {scored['dk_points'].mean():.2f} "
          f"DK points, best {combined['dk_points'].max():.1f}")
    top = combined.nlargest(3, "dk_points")
    for row in top.to_dict("records"):
        print(f"  {row['dk_points']:6.1f}  {row['player_name']} "
              f"({row['team']}) {row['season']} wk {row['week']}")


if __name__ == "__main__":
    main()
