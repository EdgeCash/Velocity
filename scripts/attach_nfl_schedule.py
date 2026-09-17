"""Attach the nflverse schedule's extra columns to the committed NFL games.

    python scripts/attach_nfl_schedule.py --data datasets/nfl            # fetches
    python scripts/attach_nfl_schedule.py --data datasets/nfl --csv games.csv

The committed frame was built from play-by-play and kept two betting columns;
the schedules CSV has carried the announced starters, real moneyline closes,
the juice, rest days, the divisional flag, kickoff weather, kickoff time,
coaches and the referee all along (``velocity.ingest.nfl.SCHEDULE_EXTRA_COLUMNS``).
This joins them by ``game_id`` for every season on file; the weekly refresh
(``refresh_datasets.py``) carries them forward from here.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from velocity.ingest.nfl import NFLVERSE_SCHEDULE_URL, SCHEDULE_EXTRA_COLUMNS, schedule_extras


def attach(games: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    """``games`` with the schedule extras joined on ``game_id`` (existing copies replaced)."""
    stripped = games.drop(columns=[c for c in SCHEDULE_EXTRA_COLUMNS if c in games.columns])
    extras = schedule_extras(raw).drop_duplicates("game_id")
    out = stripped.copy()
    out["game_id"] = out["game_id"].astype(str)
    return out.merge(extras, on="game_id", how="left")


def main() -> None:
    parser = argparse.ArgumentParser(description="Attach nflverse schedule columns to games")
    parser.add_argument("--data", default="datasets/nfl")
    parser.add_argument("--csv", default=NFLVERSE_SCHEDULE_URL,
                        help="a local copy of the nflverse games.csv (default: fetch)")
    args = parser.parse_args()
    folder = Path(args.data)
    games = pd.read_parquet(folder / "games.parquet")
    raw = pd.read_csv(args.csv, low_memory=False)
    out = attach(games, raw)
    coverage = out.groupby("season")[["home_qb_id", "home_moneyline", "wind"]].apply(
        lambda d: d.notna().mean().round(2))
    print(coverage.to_string())
    out.to_parquet(folder / "games.parquet", index=False)
    print(f"wrote {len(out)} games with {len(SCHEDULE_EXTRA_COLUMNS)} schedule columns")


if __name__ == "__main__":
    main()
