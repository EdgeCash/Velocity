"""The read seam: stamped artifact families → the newest frame per league.

The pipeline writes one stamped parquet per frame kind per league per run
(``slate_nfl_20260920T175300Z.parquet``). Every reader downstream wants the
same thing from that pile — *the newest stamp per league* — and
``scripts/build_site_data.py`` has solved it for the Evidence site since the
site existed. This module is that rule, lifted into the package so the export
layer and the site build share one implementation instead of two that agree
until they don't.

Nothing here interprets a frame. It finds files, reads them, and tags each
row with the league and stamp it came from.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

# Every league the pipeline stamps artifacts for, in the site build's order.
LEAGUES: tuple[str, ...] = ("nfl", "ncaaf", "mlb", "wnba", "ncaab", "nhl")

# ``20260920T175300Z`` — the stamp the runner writes, which sorts
# chronologically as text, which is why "newest" below is a sort and not a
# parse.
STAMP = r"(\d{8}T\d{6}Z)"


def newest(folder: Path, pattern: str) -> Path | None:
    """Lexicographically-last match — the stamp format sorts chronologically."""
    if not Path(folder).exists():
        return None
    matches = sorted(p for p in Path(folder).rglob("*") if re.fullmatch(pattern, p.name))
    return matches[-1] if matches else None


def latest_frame(folder: Path, prefix: str, league: str) -> pd.DataFrame | None:
    """The newest ``{prefix}_{stamp}.parquet`` under ``folder``, or ``None``.

    ``{league}`` in the prefix is substituted, so ``slate_{league}_props``
    finds the props family and ``record_{league}`` the record family.
    """
    stem = prefix.format(league=league)
    path = newest(Path(folder), rf"{re.escape(stem)}_{STAMP}\.parquet")
    if path is None:
        return None
    frame = pd.read_parquet(path)
    stamp = re.search(STAMP, path.name)
    frame["league"] = league
    frame["stamp"] = stamp.group(1) if stamp else ""
    return frame


def collect(
    folder: Path, kind: str, leagues: tuple[str, ...] = LEAGUES
) -> pd.DataFrame:
    """Latest frame per league for ``kind``, concatenated (empty if none).

    ``kind`` is either a bare family (``record`` → ``record_{league}``) or a
    prefix template containing ``{league}``.
    """
    prefix = kind if "{league}" in kind else kind + "_{league}"
    frames = [
        f for lg in leagues if (f := latest_frame(Path(folder), prefix, lg)) is not None
    ]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def newest_stamp(frames: dict[str, pd.DataFrame]) -> str | None:
    """The newest stamp across a set of collected frames, or ``None``.

    The export's ``generated_at`` should say when the numbers were made, not
    when the CSV was written — those differ by however long the artifact sat
    in storage, and a dashboard that reports the latter calls stale data
    fresh.
    """
    stamps = [
        str(s)
        for frame in frames.values()
        if frame is not None and not frame.empty and "stamp" in frame.columns
        for s in frame["stamp"].dropna().tolist()
        if str(s)
    ]
    return max(stamps) if stamps else None


def stamp_to_timestamp(stamp: str) -> pd.Timestamp | None:
    """``20260920T175300Z`` → a UTC Timestamp, or ``None`` if it is not one."""
    try:
        ts = pd.Timestamp(stamp)
    except (ValueError, TypeError):
        return None
    if ts is pd.NaT:
        return None
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def load_board(
    odds_dir: Path | str | None,
    games: pd.DataFrame | None,
    *,
    max_snapshots: int = 24,
) -> pd.DataFrame:
    """The current market board for ``games``, from the hourly odds archive.

    The staked slate carries a price only for the bets that cleared the gate,
    so using it as the market side leaves every unbet game with no number at
    all — a betting card of projections with nothing to compare them to. The
    archive (``odds_lines_{stamp}.parquet``, written by
    ``scripts/collect_theoddsapi.py``) is the whole board, which is what the
    card actually wants.

    Rows are reduced to the freshest quote per ``(game_id, market, side,
    book)`` across the snapshots read, then canonicalized to
    ``home``/``away``/``over``/``under`` against the run's own games frame —
    the same exact per-event lookup
    :func:`velocity.wagering.live.canonicalize_sides` does for the slate, so
    a side that cannot be resolved is dropped rather than guessed.

    Empty whenever there is no archive, no games, or no overlap — the export
    then falls back to the slate and says so in its log.
    """
    if odds_dir is None or games is None or games.empty or "game_id" not in games.columns:
        return pd.DataFrame()
    folder = Path(odds_dir)
    if not folder.exists():
        return pd.DataFrame()
    snapshots = sorted(folder.rglob("odds_lines_*.parquet"))[-max_snapshots:]
    if not snapshots:
        return pd.DataFrame()

    wanted = set(games["game_id"].astype(str))
    frames: list[pd.DataFrame] = []
    for path in snapshots:
        try:
            snap = pd.read_parquet(path)
        except Exception:  # noqa: BLE001 - a corrupt snapshot is skipped, not fatal
            continue
        if "game_id" not in snap.columns:
            continue
        snap = snap[snap["game_id"].astype(str).isin(wanted)]
        if not snap.empty:
            frames.append(snap)
    if not frames:
        return pd.DataFrame()

    board = pd.concat(frames, ignore_index=True)
    stamp = "timestamp" if "timestamp" in board.columns else None
    if stamp is not None:
        board[stamp] = pd.to_datetime(board[stamp], errors="coerce", utc=True)
        board = board.sort_values(stamp)
    keys = [k for k in ("game_id", "market", "side", "book") if k in board.columns]
    if keys:
        board = board.drop_duplicates(subset=keys, keep="last")

    from velocity.wagering.live import canonicalize_sides

    events = games.drop_duplicates(subset=["game_id"])
    needed = {"game_id", "home_team", "away_team"}
    if not needed <= set(events.columns):
        return pd.DataFrame()
    return canonicalize_sides(board.reset_index(drop=True), events)
