"""NFL ingest adapter — nflverse → canonical store.

nflverse is the free, deep source for NFL data: schedules, play-by-play with EPA
back to 1999, and weekly rosters. This adapter normalizes those three feeds onto
the canonical :class:`~velocity.store.schema.Games`,
:class:`~velocity.store.schema.Plays` and :class:`~velocity.store.schema.Players`
schemas.

Live data is fetched **directly** from nflverse's public files (a CSV for
schedules, parquet for play-by-play and rosters) rather than via ``nfl_data_py``,
which pins an incompatible ``pandas<2``. Fetching the files ourselves keeps the
project on modern pandas and avoids the dependency entirely. The ``normalize_*``
functions are pure and offline-testable; ``load_*`` fetch and hand off to them.

nflverse marks playoff games with distinct ``game_type`` codes (WC/DIV/CON/SB);
we collapse those to the canonical ``POST`` season type. Kickoff is assembled
from the separate game-day and game-time fields into a single point-in-time
anchor.
"""

from __future__ import annotations

import io
import urllib.request
from collections.abc import Iterable, Sequence

import numpy as np
import pandas as pd

from velocity.store.schema import Games, Players, Plays

# nflverse public data locations. Schedules live in the git tree (served by the
# raw CDN); play-by-play and rosters are release assets addressed per season.
NFLVERSE_SCHEDULE_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
NFLVERSE_PBP_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{year}.parquet"
)
NFLVERSE_ROSTER_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "weekly_rosters/roster_weekly_{year}.parquet"
)
_FETCH_TIMEOUT = 180

# nflverse game_type → canonical season_type.
GAME_TYPE_TO_SEASON_TYPE = {
    "PRE": "PRE",
    "REG": "REG",
    "WC": "POST",
    "DIV": "POST",
    "CON": "POST",
    "SB": "POST",
}

# Canonical play columns pulled from nflverse play-by-play (it has hundreds more).
_PBP_COLUMNS: Sequence[str] = (
    "play_id",
    "game_id",
    "season",
    "week",
    "posteam",
    "defteam",
    "play_type",
    "down",
    "yards_gained",
    "epa",
    "success",
)


def normalize_schedules(raw: pd.DataFrame) -> pd.DataFrame:
    """Map an nflverse schedules frame onto the canonical ``Games`` schema.

    Expects the nflverse columns ``game_id, season, game_type, week, gameday,
    gametime, home_team, away_team, home_score, away_score, location, roof,
    surface``. Unplayed games (null scores) are preserved; a neutral-site game is
    detected from ``location == "Neutral"``.
    """
    season_type = raw["game_type"].map(GAME_TYPE_TO_SEASON_TYPE)
    if season_type.isna().any():
        unknown = sorted(set(raw.loc[season_type.isna(), "game_type"]))
        raise ValueError(f"unrecognized nflverse game_type(s): {unknown}")

    gametime = raw["gametime"].fillna("00:00").astype(str)
    kickoff = pd.to_datetime(raw["gameday"].astype(str) + " " + gametime, errors="coerce")

    out = pd.DataFrame(
        {
            "game_id": raw["game_id"].astype(str),
            "league": "nfl",
            "season": raw["season"],
            "week": raw["week"],
            "season_type": season_type,
            "kickoff": kickoff,
            "home_team": raw["home_team"].astype(str),
            "away_team": raw["away_team"].astype(str),
            "neutral_site": raw["location"].astype(str).str.lower().eq("neutral"),
            "roof": raw["roof"],
            "surface": raw["surface"],
            "home_score": raw["home_score"],
            "away_score": raw["away_score"],
        }
    )
    return Games.validate(out)


def normalize_pbp(raw: pd.DataFrame) -> pd.DataFrame:
    """Map an nflverse play-by-play frame onto the canonical ``Plays`` schema.

    Only the canonical columns are retained; any missing optional column is
    filled with nulls so partial provider extracts still validate.
    """
    out = pd.DataFrame()
    for col in _PBP_COLUMNS:
        # A missing optional column becomes an all-null float column, which the
        # schema coerces cleanly (a pd.NA object column would not).
        out[col] = raw[col] if col in raw.columns else np.nan
    out["play_id"] = out["play_id"].astype(str)
    out["game_id"] = out["game_id"].astype(str)
    # nflverse encodes success as 1.0/0.0/NaN; route through pandas' nullable
    # boolean so a missing value stays missing instead of coercing NaN → True.
    out["success"] = out["success"].astype("boolean")
    return Plays.validate(out)


def normalize_rosters(raw: pd.DataFrame) -> pd.DataFrame:
    """Map an nflverse weekly/seasonal roster frame onto the ``Players`` schema.

    Accepts either ``player_name`` or nflverse's ``player_display_name`` for the
    name field.
    """
    if "player_name" in raw.columns:
        names = raw["player_name"]
    elif "player_display_name" in raw.columns:
        names = raw["player_display_name"]
    else:
        raise ValueError("roster frame needs player_name or player_display_name")

    out = pd.DataFrame(
        {
            "player_id": raw["player_id"].astype(str),
            "player_name": names.astype(str),
            "position": raw["position"] if "position" in raw.columns else pd.NA,
            "team": raw["team"] if "team" in raw.columns else pd.NA,
            "season": raw["season"],
        }
    )
    return Players.validate(out)


def _read_url_bytes(url: str) -> bytes:  # pragma: no cover - network
    with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
        return resp.read()


def _read_parquet_url(url: str) -> pd.DataFrame:  # pragma: no cover - network
    return pd.read_parquet(io.BytesIO(_read_url_bytes(url)))


def load_schedules(years: Iterable[int]) -> pd.DataFrame:  # pragma: no cover - network
    """Fetch and normalize nflverse schedules for ``years`` (network).

    Reads the nflverse games CSV (all seasons) and filters to ``years``.
    """
    raw = pd.read_csv(NFLVERSE_SCHEDULE_URL, low_memory=False)
    raw = raw[raw["season"].isin(set(years))]
    return normalize_schedules(raw)


def load_pbp(years: Iterable[int]) -> pd.DataFrame:  # pragma: no cover - network
    """Fetch and normalize nflverse play-by-play for ``years`` (network)."""
    frames = [_read_parquet_url(NFLVERSE_PBP_URL.format(year=year)) for year in years]
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return normalize_pbp(combined)


def load_rosters(years: Iterable[int]) -> pd.DataFrame:  # pragma: no cover - network
    """Fetch and normalize nflverse weekly rosters for ``years`` (network)."""
    frames = [_read_parquet_url(NFLVERSE_ROSTER_URL.format(year=year)) for year in years]
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return normalize_rosters(combined)
