"""Build compact canonical NFL datasets from nflverse play-by-play.

Full nflverse pbp exports run ~370 columns / ~117 MB a season. This script
distills each season into two small, committable parquet files under
``datasets/nfl/``:

* ``plays.parquet`` — the canonical :class:`~velocity.store.schema.Plays`
  columns **plus ``passer_player_id``** (the QB-adjustment feature: which
  passer ran each dropback), keeping only real offensive plays (non-null
  ``posteam`` and ``epa``). This drops kickoffs/timeouts/etc. and shrinks the
  data by ~an order of magnitude.
* ``games.parquet`` — one row per game with the canonical
  :class:`~velocity.store.schema.Games` columns plus the closing
  ``spread_line`` / ``total_line`` carried through for the
  against-the-spread evaluation.

Two source modes::

    # local nflfastR CSV exports
    python scripts/build_nfl_pbp_datasets.py --src /tmp/pbp_csvs --out datasets/nfl

    # straight from the nflverse release parquets (one download per season)
    python scripts/build_nfl_pbp_datasets.py --seasons 2011 2025 --out datasets/nfl
"""

from __future__ import annotations

import argparse
import tempfile
import urllib.request
from pathlib import Path

import pandas as pd

PLAY_COLS = [
    "play_id", "game_id", "season", "week",
    "posteam", "defteam", "play_type", "down", "yards_gained", "epa", "success",
    "passer_player_id",
]
GAME_COLS = [
    "game_id", "season", "week", "season_type", "game_date",
    "home_team", "away_team", "home_score", "away_score",
    "location", "roof", "surface", "spread_line", "total_line",
]
_USECOLS = sorted(set(PLAY_COLS) | set(GAME_COLS))


def _distill(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    for col in _USECOLS:  # tolerate seasons missing an optional column
        if col not in df.columns:
            df[col] = pd.NA
    plays = df[PLAY_COLS]
    plays = plays[plays["posteam"].notna() & plays["epa"].notna()].copy()

    games = df[GAME_COLS].drop_duplicates("game_id").copy()
    games["neutral_site"] = games["location"].astype(str).str.lower().eq("neutral")
    games["kickoff"] = pd.to_datetime(games["game_date"], errors="coerce")
    games = games.drop(columns=["location", "game_date"])
    return plays, games


def _season_frames(csv: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    return _distill(pd.read_csv(csv, usecols=lambda c: c in _USECOLS, low_memory=False))


_PBP_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/pbp/"
    "play_by_play_{year}.parquet"
)


def _download_season(year: int) -> tuple[pd.DataFrame, pd.DataFrame]:  # pragma: no cover
    url = _PBP_URL.format(year=year)
    with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
        with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 - fixed host
            tmp.write(resp.read())
        tmp.flush()
        import pyarrow.parquet as pq

        available = set(pq.ParquetFile(tmp.name).schema_arrow.names)
        df = pd.read_parquet(
            tmp.name, columns=[c for c in _USECOLS if c in available]
        )
    return _distill(df)


def _write(play_frames: list, game_frames: list, out: Path) -> tuple[int, int]:
    all_plays = pd.concat(play_frames, ignore_index=True)
    all_games = pd.concat(game_frames, ignore_index=True)
    out.mkdir(parents=True, exist_ok=True)
    all_plays.to_parquet(out / "plays.parquet", index=False)
    all_games.to_parquet(out / "games.parquet", index=False)
    return len(all_plays), len(all_games)


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
    return _write(play_frames, game_frames, out)


def build_from_releases(first: int, last: int, out: Path) -> tuple[int, int]:  # pragma: no cover
    play_frames, game_frames = [], []
    for year in range(first, last + 1):
        plays, games = _download_season(year)
        play_frames.append(plays)
        game_frames.append(games)
        print(f"  {year}: {len(plays):>6} plays, {len(games):>3} games", flush=True)
    return _write(play_frames, game_frames, out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build compact NFL datasets from pbp")
    parser.add_argument("--src", help="folder containing pbp-YYYY.csv files")
    parser.add_argument("--seasons", nargs=2, type=int, metavar=("FIRST", "LAST"),
                        help="download nflverse release parquets for this range")
    parser.add_argument("--out", default="datasets/nfl", help="output folder")
    args = parser.parse_args()
    if args.seasons:
        n_plays, n_games = build_from_releases(args.seasons[0], args.seasons[1],
                                               Path(args.out))
    elif args.src:
        n_plays, n_games = build(Path(args.src), Path(args.out))
    else:
        raise SystemExit("pass --src or --seasons")
    print(f"wrote {n_plays} plays and {n_games} games to {args.out}")


if __name__ == "__main__":
    main()
