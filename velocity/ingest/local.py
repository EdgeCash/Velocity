"""Local dataset harness — read committed real data into the canonical store.

When live provider access is blocked, the real inputs are committed under
``datasets/`` (see ``datasets/README.md``). This module reads those CSV/parquet
files and maps them onto the canonical :class:`~velocity.store.schema.Games` and
:class:`~velocity.store.schema.Plays` schemas.

Two design points make it robust to whatever a dataset looks like:

* **Format-agnostic read** — :func:`read_data_file` picks CSV vs parquet by
  extension.
* **Column bridging** — real datasets rarely use our exact names, so every
  loader takes a ``rename`` map (source → canonical). Missing *optional* fields
  are filled with sensible defaults (``league`` injected, ``season_type`` → REG,
  ``neutral_site`` → False, ``success`` derived from ``epa > 0``) rather than
  forcing the caller to reshape the file first.

Use :func:`describe` to inspect an unfamiliar file's columns before wiring a map.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pandas as pd

from velocity.store.schema import Games, Plays


def read_data_file(path: str | Path) -> pd.DataFrame:
    """Read a ``.csv`` or ``.parquet`` file into a DataFrame (by extension)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, low_memory=False)
    if suffix in (".parquet", ".pq"):
        return pd.read_parquet(path)
    raise ValueError(f"unsupported data file type {suffix!r} (use .csv or .parquet)")


def describe(path: str | Path) -> pd.DataFrame:
    """Return a columns × (dtype, non-null, sample) summary — for wiring renames."""
    df = read_data_file(path)
    return pd.DataFrame(
        {
            "dtype": df.dtypes.astype(str),
            "non_null": df.notna().sum(),
            "sample": [df[c].dropna().iloc[0] if df[c].notna().any() else None for c in df.columns],
        }
    )


def _apply_rename(df: pd.DataFrame, rename: Mapping[str, str] | None) -> pd.DataFrame:
    return df.rename(columns=dict(rename)) if rename else df.copy()


def load_games(
    path: str | Path,
    *,
    league: str,
    rename: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Load a games file onto the canonical ``Games`` schema.

    ``rename`` maps source column names to canonical ones. ``league`` is injected;
    ``season_type``/``neutral_site`` default when absent; ``kickoff`` is parsed to
    datetime. Extra columns (e.g. betting lines) are preserved.
    """
    df = _apply_rename(read_data_file(path), rename)
    df["league"] = league
    if "season_type" not in df.columns:
        df["season_type"] = "REG"
    if "neutral_site" not in df.columns:
        df["neutral_site"] = False
    for optional in ("roof", "surface"):
        if optional not in df.columns:
            df[optional] = None
    if "kickoff" not in df.columns:
        raise ValueError("games file needs a 'kickoff' column (or a rename to it)")
    df["kickoff"] = pd.to_datetime(df["kickoff"], errors="coerce")
    return Games.validate(df)


def load_plays(
    path: str | Path,
    *,
    rename: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Load a plays file onto the canonical ``Plays`` schema.

    ``rename`` maps source columns to canonical ones. ``success`` is derived from
    ``epa > 0`` when the column is absent; ``play_id``/``game_id`` are stringified.
    """
    df = _apply_rename(read_data_file(path), rename)
    for required in ("play_id", "game_id", "season", "week"):
        if required not in df.columns:
            raise ValueError(f"plays file missing required column {required!r}")
    df["play_id"] = df["play_id"].astype(str)
    df["game_id"] = df["game_id"].astype(str)
    if "epa" in df.columns and "success" not in df.columns:
        epa = pd.to_numeric(df["epa"], errors="coerce")
        df["success"] = (epa > 0).astype("boolean").mask(epa.isna())
    for optional in ("posteam", "defteam", "play_type", "down", "yards_gained", "epa"):
        if optional not in df.columns:
            df[optional] = np.nan
    return Plays.validate(df)
