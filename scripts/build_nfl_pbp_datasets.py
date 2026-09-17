"""Build compact canonical NFL datasets from nflverse play-by-play.

Full nflverse pbp exports run ~370 columns / ~117 MB a season. This script
distills each season into two small, committable parquet files under
``datasets/nfl/``:

* ``plays.parquet`` — the canonical :class:`~velocity.store.schema.Plays`
  columns **plus ``passer_player_id``** (the QB-adjustment feature: which
  passer ran each dropback) **plus the play context**
  (:data:`velocity.ingest.nfl.PBP_CONTEXT_COLUMNS`: win probability, clock
  and score state, turnover flags, QB EPA, CPOE, penalty and aborted-snap
  markers), keeping only real offensive plays (non-null ``posteam`` and
  ``epa``). This drops kickoffs/timeouts/etc. and shrinks the data by ~an
  order of magnitude.
* ``games.parquet`` — one row per game with the canonical
  :class:`~velocity.store.schema.Games` columns plus the closing
  ``spread_line`` / ``total_line`` carried through for the
  against-the-spread evaluation.

Two source modes::

    # local nflfastR CSV exports
    python scripts/build_nfl_pbp_datasets.py --src /tmp/pbp_csvs --out datasets/nfl

    # straight from the nflverse release parquets (one download per season)
    python scripts/build_nfl_pbp_datasets.py --seasons 2011 2025 --out datasets/nfl

    # the plays alone, leaving a games.parquet that carries attached lines and
    # schedule extras untouched; --cache reads already-downloaded releases
    python scripts/build_nfl_pbp_datasets.py --seasons 2011 2026 --plays-only \
        --cache /path/to/pbp --out datasets/nfl

``--plays-only`` distills through :func:`velocity.ingest.nfl.normalize_pbp`,
the same path the daily refresh's current-season top-up takes, so the
committed file and the refreshed rows share one schema.
"""

from __future__ import annotations

import argparse
import tempfile
import urllib.request
from pathlib import Path

import pandas as pd
from velocity.ingest.nfl import PBP_CONTEXT_COLUMNS, normalize_pbp

PLAY_COLS = [
    "play_id", "game_id", "season", "week",
    "posteam", "defteam", "play_type", "down", "yards_gained", "epa", "success",
    "passer_player_id",
    *PBP_CONTEXT_COLUMNS,
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


def _read_release(path: str | Path, columns: list[str]) -> pd.DataFrame:
    """The release parquet at ``path``, restricted to the ``columns`` it carries."""
    import pyarrow.parquet as pq

    available = set(pq.ParquetFile(str(path)).schema_arrow.names)
    return pd.read_parquet(str(path), columns=[c for c in columns if c in available])


def _fetch_release(year: int, cache: Path | None) -> Path:  # pragma: no cover - network
    """The season's release parquet on disk: the cached copy, else downloaded there.

    Without ``cache`` a temporary file is used and deleted by the caller.
    """
    name = f"play_by_play_{year}.parquet"
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / name
        if target.exists():
            return target
    else:
        target = Path(tempfile.mkstemp(suffix=".parquet")[1])
    url = _PBP_URL.format(year=year)
    with urllib.request.urlopen(url, timeout=120) as resp, target.open("wb") as fh:  # noqa: S310
        fh.write(resp.read())
    return target


def _download_season(year: int) -> tuple[pd.DataFrame, pd.DataFrame]:  # pragma: no cover
    path = _fetch_release(year, None)
    try:
        return _distill(_read_release(path, _USECOLS))
    finally:
        path.unlink(missing_ok=True)


def distill_plays(raw: pd.DataFrame) -> pd.DataFrame:
    """One season's raw release frame → the committed plays rows.

    The canonical normalization (ids stringified, ``success`` nullable-boolean,
    the context columns numeric, anything the release lacks null) followed by
    the same non-null ``posteam``/``epa`` filter as the CSV path.
    """
    plays = normalize_pbp(raw)
    return plays[plays["posteam"].notna() & plays["epa"].notna()].reset_index(drop=True)


def build_plays_from_releases(
    first: int, last: int, out: Path, cache: Path | None = None,
) -> int:  # pragma: no cover - network
    """Rebuild ``out/plays.parquet`` alone from the release parquets."""
    frames = []
    for year in range(first, last + 1):
        path = _fetch_release(year, cache)
        try:
            plays = distill_plays(_read_release(path, PLAY_COLS))
        finally:
            if cache is None:
                path.unlink(missing_ok=True)
        frames.append(plays)
        print(f"  {year}: {len(plays):>6} plays", flush=True)
    all_plays = pd.concat(frames, ignore_index=True)
    out.mkdir(parents=True, exist_ok=True)
    all_plays.to_parquet(out / "plays.parquet", index=False)
    return len(all_plays)


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
    parser.add_argument("--plays-only", action="store_true",
                        help="with --seasons: rewrite plays.parquet alone, through the "
                             "canonical normalization; games.parquet is left as it is")
    parser.add_argument("--cache", help="with --plays-only: folder of downloaded release "
                                        "parquets (missing seasons are fetched into it)")
    args = parser.parse_args()
    if args.seasons and args.plays_only:
        n_plays = build_plays_from_releases(
            args.seasons[0], args.seasons[1], Path(args.out),
            Path(args.cache) if args.cache else None)
        print(f"wrote {n_plays} plays to {args.out} (games untouched)")
        return
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
