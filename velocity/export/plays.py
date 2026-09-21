"""``plays.csv`` — the curated card: A+ / A / B / Watch, each with its reason.

Thin by design. :mod:`velocity.wagering.plays` does the ranking and writes
the argument; this module owns only the file's shape, so the tiering can be
tested without a filesystem and the CSV contract without a slate.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from velocity.export.meta import EXPORT_DIR, ExportMeta, round_columns, write_csv
from velocity.wagering.plays import PlaysConfig, build_plays

PLAYS_COLUMNS: tuple[str, ...] = (
    "tier",
    "bet_type",
    "selection",
    "matchup",
    "kickoff",
    "market",
    "edge",
    "confidence",
    "stake",
    "reason",
    "generated_at",
    "season",
    "week",
)


def build_plays_export(  # noqa: PLR0913 - mirrors the engine it wraps
    slate: pd.DataFrame | None = None,
    *,
    props: pd.DataFrame | None = None,
    games: pd.DataFrame | None = None,
    projections: pd.DataFrame | None = None,
    intel: pd.DataFrame | None = None,
    bankroll: float = 100.0,
    config: PlaysConfig | None = None,
) -> pd.DataFrame:
    """The curated plays, reduced to the export's columns and ordering."""
    base = [c for c in PLAYS_COLUMNS if c not in ("generated_at", "season", "week")]
    frame = build_plays(
        slate, props=props, games=games, projections=projections,
        intel=intel, bankroll=bankroll, config=config,
    )
    if frame.empty:
        return pd.DataFrame(columns=base)
    frame = round_columns(frame, ("edge",), 4)
    frame = round_columns(frame, ("confidence", "stake"), 2)
    # One spelling of an instant, so the CSV is byte-stable across runners and
    # Power Query does not have to guess a locale. A game with no kickoff on
    # the board stays empty rather than becoming the epoch.
    if "kickoff" in frame.columns:
        kicks = pd.to_datetime(frame["kickoff"], errors="coerce", utc=True)
        frame["kickoff"] = kicks.dt.strftime("%Y-%m-%dT%H:%M:%SZ").where(kicks.notna())
    return frame.reindex(columns=base)


def export_plays(  # noqa: PLR0913 - mirrors the builder, plus where to write
    meta: ExportMeta,
    slate: pd.DataFrame | None = None,
    *,
    props: pd.DataFrame | None = None,
    games: pd.DataFrame | None = None,
    projections: pd.DataFrame | None = None,
    intel: pd.DataFrame | None = None,
    bankroll: float = 100.0,
    config: PlaysConfig | None = None,
    out_dir: str | Path = EXPORT_DIR,
) -> Path:
    """Build and write ``plays.csv``. Returns the path."""
    frame = build_plays_export(
        slate, props=props, games=games, projections=projections,
        intel=intel, bankroll=bankroll, config=config,
    )
    return write_csv(frame, Path(out_dir) / "plays.csv", PLAYS_COLUMNS, meta)
