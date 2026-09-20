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
