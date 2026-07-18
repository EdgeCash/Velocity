"""NFL ingest adapter — nflverse (``nfl_data_py``) → canonical store.

nflverse is the free, deep source for NFL data: schedules, play-by-play with EPA
back to 1999, and weekly rosters. This adapter normalizes those three feeds onto
the canonical :class:`~velocity.store.schema.Games`,
:class:`~velocity.store.schema.Plays` and :class:`~velocity.store.schema.Players`
schemas.

The provider column names it depends on are declared as constants below, so if
nflverse changes a field there is a single place to update. The ``normalize_*``
functions are pure and offline-testable; ``load_*`` fetch live data and are thin
by design (a fetch plus the matching normalize).

nflverse marks playoff games with distinct ``game_type`` codes (WC/DIV/CON/SB);
we collapse those to the canonical ``POST`` season type. Kickoff is assembled
from the separate game-day and game-time fields into a single point-in-time
anchor.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np
import pandas as pd

from velocity.store.schema import Games, Players, Plays

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


def _import_nfl_data_py():  # type: ignore[no-untyped-def]
    """Lazily import ``nfl_data_py``, with a helpful error if it is absent."""
    try:
        import nfl_data_py  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - exercised only without the dep
        raise ImportError(
            "nfl_data_py is required for live NFL ingest; install it with "
            "`pip install 'velocity[ingest]'`"
        ) from exc
    return nfl_data_py


def load_schedules(years: Iterable[int]) -> pd.DataFrame:
    """Fetch and normalize nflverse schedules for ``years`` (network)."""
    nfl = _import_nfl_data_py()
    return normalize_schedules(nfl.import_schedules(list(years)))


def load_pbp(years: Iterable[int]) -> pd.DataFrame:
    """Fetch and normalize nflverse play-by-play for ``years`` (network)."""
    nfl = _import_nfl_data_py()
    return normalize_pbp(nfl.import_pbp_data(list(years)))


def load_rosters(years: Iterable[int]) -> pd.DataFrame:
    """Fetch and normalize nflverse weekly rosters for ``years`` (network)."""
    nfl = _import_nfl_data_py()
    return normalize_rosters(nfl.import_weekly_rosters(list(years)))
