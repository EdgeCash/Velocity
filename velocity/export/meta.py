"""Export metadata and the CSV writer every exporter shares.

One place decides what an export file looks like on disk, so "Excel-ready"
is a property of the writer rather than a habit each module has to remember.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

# Where the exports land. Relative to the repo root; overridable per call.
EXPORT_DIR = Path("datasets/exports")

# The three columns every export row carries, in this order, at the end.
META_COLUMNS: tuple[str, ...] = ("generated_at", "season", "week")

# Excel reads a UTF-8 file correctly only when it is told, and a BOM is the
# only in-band way to tell it. Power Query handles the BOM transparently, so
# this costs the query nothing and saves a double-clicked file from mojibake.
_ENCODING = "utf-8-sig"

# Written explicitly rather than left to os.linesep: the file has to be
# byte-identical whichever runner produced it, or a diff of two exports is
# noise. Excel and Power Query both read bare LF.
_LINE_TERMINATOR = "\n"


def utc_now() -> pd.Timestamp:
    """This instant, in UTC. One call site, so tests can reason about it."""
    return pd.Timestamp(datetime.now(UTC))


def stamp_text(when: pd.Timestamp | str) -> str:
    """An instant as the exports spell it: ISO 8601, UTC, seconds precision.

    Power Query parses this without a locale guess, which is the whole point
    of not letting a viewer's regional settings decide what a timestamp means.
    """
    ts = pd.Timestamp(when)
    if ts.tzinfo is None:
        ts = ts.tz_localize(UTC)
    return ts.tz_convert(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class ExportMeta:
    """What stamps every exported row: when it was made, and for which week.

    ``season``/``week`` may be ``None``. An export made outside a season, or
    from frames that cannot say which week they belong to, carries an empty
    cell rather than a plausible integer — a wrong week silently mis-joins
    every workbook tab that keys on it.
    """

    generated_at: str
    season: int | None = None
    week: int | None = None

    @classmethod
    def now(cls, season: int | None = None, week: int | None = None) -> ExportMeta:
        return cls(stamp_text(utc_now()), season, week)

    @classmethod
    def from_games(
        cls,
        games: pd.DataFrame | None,
        *,
        generated_at: pd.Timestamp | str | None = None,
    ) -> ExportMeta:
        """Season and week read off a committed schedule frame.

        The week is :func:`velocity.models.level.next_week` — the week the
        latest season is about to play — because the committed frames carry
        completed games only. Anything the frame cannot answer stays ``None``.
        """
        when = stamp_text(generated_at if generated_at is not None else utc_now())
        if games is None or games.empty or "season" not in games.columns:
            return cls(when)
        try:
            from velocity.models.level import next_week

            season = int(games["season"].max())
            week = int(next_week(games))
        except Exception:  # noqa: BLE001 - a masthead label, never worth raising
            return cls(when)
        return cls(when, season, week)

    def apply(self, frame: pd.DataFrame) -> pd.DataFrame:
        """``frame`` with the three metadata columns set on every row."""
        out = frame.copy()
        out["generated_at"] = self.generated_at
        out["season"] = self.season
        out["week"] = self.week
        return out


def empty_export(columns: Sequence[str]) -> pd.DataFrame:
    """A zero-row frame with the export's exact columns."""
    return pd.DataFrame({c: pd.Series(dtype="object") for c in columns})


def finalize(
    frame: pd.DataFrame, columns: Sequence[str], meta: ExportMeta
) -> pd.DataFrame:
    """Stamp ``frame`` and reindex it onto the export's exact column contract.

    Reindexing rather than selecting is deliberate: a column the source frame
    never carried is written **empty**, in position, so the file's shape is a
    function of the contract alone and Excel's query never breaks because a
    league happened to have no weather that week.
    """
    stamped = meta.apply(frame if not frame.empty else empty_export(columns))
    return stamped.reindex(columns=list(columns))


def write_csv(
    frame: pd.DataFrame, dest: str | Path, columns: Sequence[str], meta: ExportMeta
) -> Path:
    """Write ``frame`` to ``dest`` as an Excel-ready CSV. Returns the path."""
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    finalize(frame, columns, meta).to_csv(
        path, index=False, encoding=_ENCODING, lineterminator=_LINE_TERMINATOR
    )
    return path


def round_columns(
    frame: pd.DataFrame, columns: Iterable[str], places: int
) -> pd.DataFrame:
    """Round the numeric columns that exist, leaving the rest alone.

    Exports are diffed run to run, so a float's sixteenth decimal place is
    churn. Rounding happens once, on the way out, never in the engine.
    """
    out = frame.copy()
    for col in columns:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").round(places)
    return out


def numeric_column(frame: pd.DataFrame, column: str) -> pd.Series:
    """``frame[column]`` as numbers, or an all-null column of the same length.

    ``DataFrame.get`` returns ``None`` for a missing column, which then has to
    be guarded at every call site or it reaches ``to_numeric`` and raises.
    One helper, so an export never has to care whether a run happened to
    produce a column.
    """
    if column not in frame.columns:
        return pd.Series([float("nan")] * len(frame), index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")
