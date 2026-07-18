"""Build compact canonical NFL datasets from nflfastR play-by-play CSVs.

The uploaded pbp files are full nflfastR exports (~370 columns, ~117 MB/season).
This script distills each season into two small, committable parquet files under
``datasets/nfl/``:

* ``plays.parquet`` — the canonical :class:`~velocity.store.schema.Plays` columns,
  keeping only real offensive plays (non-null ``posteam`` and ``epa``), which is
  all the EPA ratings need. This drops kickoffs/timeouts/etc. and shrinks the data
  by ~an order of magnitude.
* ``games.parquet`` — one row per game with the canonical
  :class:`~velocity.store.schema.Games` columns plus the closing ``spread_line`` /
  ``total_line`` carried through for the against-the-spread evaluation.

Run from the repo root, pointing at a folder of ``pbp-YYYY.csv`` files::

    python scripts/build_nfl_pbp_datasets.py --src /tmp/pbp_csvs --out datasets/nfl
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

PLAY_COLS = [
    "play_id", "game_id", "season", "week",
    "posteam", "defteam", "play_type", "down", "yards_gained", "epa", "success",
]
GAME_COLS = [
    "game_id", "season", "week", "season_type", "game_date",
    "home_team", "away_team", "home_score", "away_score",
    "location", "roof", "surface", "spread_line", "total_line",
]
_USECOLS = sorted(set(PLAY_COLS) | set(GAME_COLS))


def _season_frames(csv: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.read_csv(csv, usecols=lambda c: c in _USECOLS, low_memory=False)

    plays = df[PLAY_COLS]
    plays = plays[plays["posteam"].notna() & plays["epa"].notna()].copy()

    games = df[GAME_COLS].drop_duplicates("game_id").copy()
    games["neutral_site"] = games["location"].astype(str).str.lower().eq("neutral")
    games["kickoff"] = pd.to_datetime(games["game_date"], errors="coerce")
    games = games.drop(columns=["location", "game_date"])
    return plays, games


def build(src: Path, out: Path) -> tuple[int, int]:
    csvs = sorted(src.glob("pbp-*.csv"))
    if not csvs:
        raise SystemExit(f"no pbp-*.csv files in {src}")
    play_frames, game_frames = [], []
    for csv in csvs:
        plays, games = _season_frames(csv)
        play_frames.append(plays)
        game_frames.append(games)
        print(f"  {csv.name}: {len(plays):>6} plays, {len(games):>3} games")

    all_plays = pd.concat(play_frames, ignore_index=True)
    all_games = pd.concat(game_frames, ignore_index=True)
    out.mkdir(parents=True, exist_ok=True)
    all_plays.to_parquet(out / "plays.parquet", index=False)
    all_games.to_parquet(out / "games.parquet", index=False)
    return len(all_plays), len(all_games)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build compact NFL datasets from pbp CSVs")
    parser.add_argument("--src", required=True, help="folder containing pbp-YYYY.csv files")
    parser.add_argument("--out", default="datasets/nfl", help="output folder")
    args = parser.parse_args()
    n_plays, n_games = build(Path(args.src), Path(args.out))
    print(f"wrote {n_plays} plays and {n_games} games to {args.out}")


if __name__ == "__main__":
    main()
