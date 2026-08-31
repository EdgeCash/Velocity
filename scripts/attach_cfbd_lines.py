"""Attach CFBD closing lines to backfilled (fit-only) rows in games.parquet.

The 2025 backfill (``backfill_games_from_boxscores.py``) restored the season's
finals but not its lines — those arrive later via ``pull_cfbd_lines.py`` as
``games_lines.parquet``, in a different game-id space (CFBD/ESPN ids vs the
boxscore builder's synthetic ids). This script closes the loop for one season:

* a backfilled lineless row that matches a pulled row — same season and team
  pair, kickoff within two days, either orientation — takes its
  ``spread_line``/``total_line`` (sign flipped when the orientation is
  reversed; totals are orientation-free);
* a pulled game with no counterpart in the games file is appended whole
  (CFBD coverage exceeds the boxscore subset);
* rows that already carry lines are never touched.

    python scripts/attach_cfbd_lines.py --data datasets/ncaaf --season 2025
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

GAMES_COLUMNS = ["game_id", "league", "season", "week", "season_type", "kickoff",
                 "home_team", "away_team", "neutral_site", "roof", "surface",
                 "home_score", "away_score", "spread_line", "total_line"]


def attach(games: pd.DataFrame, pulled: pd.DataFrame, season: int,
           max_days: int = 2) -> tuple[pd.DataFrame, dict[str, int]]:
    """Games with the season's lines attached; plus counts for the report."""
    pulled = pulled[pulled["season"] == season].copy()
    if pulled.empty:
        raise SystemExit(f"games_lines carries no season {season}")
    pulled["kickoff"] = pd.to_datetime(pulled["kickoff"])
    games = games.copy()
    games["kickoff"] = pd.to_datetime(games["kickoff"])

    index: dict[tuple, list] = {}
    for i, row in enumerate(pulled.to_dict("records")):
        index.setdefault((row["home_team"], row["away_team"]), []).append((i, False))
        index.setdefault((row["away_team"], row["home_team"]), []).append((i, True))

    matched_pull_rows: set[int] = set()
    attached = 0
    target = (games["season"] == season) & games["spread_line"].isna() \
        & games["total_line"].isna()
    for gi in games.index[target]:
        row = games.loc[gi]
        candidates = index.get((row["home_team"], row["away_team"]), [])
        best = None
        for pi, flipped in candidates:
            gap = abs((pulled.iloc[pi]["kickoff"] - row["kickoff"]).days)
            if gap <= max_days and (best is None or gap < best[2]):
                best = (pi, flipped, gap)
        if best is None:
            continue
        pi, flipped, _gap = best
        spread = pulled.iloc[pi]["spread_line"]
        games.loc[gi, "spread_line"] = -spread if flipped and pd.notna(spread) else spread
        games.loc[gi, "total_line"] = pulled.iloc[pi]["total_line"]
        matched_pull_rows.add(pi)
        attached += 1

    extra = pulled.iloc[[i for i in range(len(pulled)) if i not in matched_pull_rows]]
    merged = pd.concat([games, extra[GAMES_COLUMNS]], ignore_index=True)
    merged = merged.sort_values(["kickoff", "game_id"]).reset_index(drop=True)
    lineless = ((merged["season"] == season) & merged["spread_line"].isna()
                & merged["total_line"].isna())
    counts = {"attached": attached, "appended": len(extra),
              "still_lineless": int(lineless.sum())}
    return merged, counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Attach pulled CFBD lines to one season")
    parser.add_argument("--data", default="datasets/ncaaf")
    parser.add_argument("--season", type=int, required=True)
    args = parser.parse_args()

    folder = Path(args.data)
    games = pd.read_parquet(folder / "games.parquet")
    pulled = pd.read_parquet(folder / "games_lines.parquet")
    merged, counts = attach(games, pulled, args.season)
    merged.to_parquet(folder / "games.parquet", index=False)
    print(f"season {args.season}: attached lines to {counts['attached']} backfilled "
          f"rows, appended {counts['appended']} CFBD-only games; "
          f"{counts['still_lineless']} rows remain fit-only")


if __name__ == "__main__":
    main()
