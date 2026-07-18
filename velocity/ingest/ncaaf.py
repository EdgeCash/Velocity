"""NCAAF ingest adapter — CollegeFootballData (CFBD) → canonical store.

CFBD is the free source for college football: games, play-by-play (with a PPA
field that plays the role EPA does for the NFL), recruiting rankings, and
returning-production. College data is materially messier than the NFL's — 130+
FBS teams, uneven play-by-play parsing for smaller programs, and occasional
malformed rows — so this adapter is deliberately **tolerant**: numeric fields are
coerced with ``errors="coerce"`` (bad values become null rather than raising),
and rows missing an essential key (a game id, both teams) are dropped instead of
crashing the ingest.

As with the NFL adapter, ``normalize_*`` is pure and offline-testable; ``load_*``
lazily import the ``cfbd`` client (network) and are not part of the test gate.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from velocity.store.schema import Games, Plays

# CFBD season_type → canonical season_type.
SEASON_TYPE_MAP = {
    "regular": "REG",
    "postseason": "POST",
    "preseason": "PRE",
    "both": "REG",
}


def normalize_games(raw: pd.DataFrame) -> pd.DataFrame:
    """Map a CFBD games frame onto the canonical ``Games`` schema (tolerant).

    Expects CFBD columns ``id, season, week, season_type, start_date,
    home_team, away_team, home_points, away_points, neutral_site`` (``venue``,
    ``grass`` optional). Rows without an id or either team are dropped.
    """
    raw = raw.copy()
    essential = raw["id"].notna() & raw["home_team"].notna() & raw["away_team"].notna()
    raw = raw[essential]

    season_type = (
        raw["season_type"].astype(str).str.lower().map(SEASON_TYPE_MAP).fillna("REG")
    )
    if "neutral_site" in raw.columns:
        neutral_site = raw["neutral_site"].fillna(False).astype(bool)
    else:
        neutral_site = pd.Series(False, index=raw.index)
    if "grass" in raw.columns:
        surface = raw["grass"].map({True: "grass", False: "turf"})
    else:
        surface = pd.Series(pd.NA, index=raw.index)
    kickoff = pd.to_datetime(raw["start_date"], errors="coerce", utc=True)
    out = pd.DataFrame(
        {
            "game_id": raw["id"].astype("int64").astype(str),
            "league": "ncaaf",
            "season": pd.to_numeric(raw["season"], errors="coerce"),
            "week": pd.to_numeric(raw["week"], errors="coerce").fillna(0),
            "season_type": season_type,
            "kickoff": kickoff.dt.tz_localize(None),
            "home_team": raw["home_team"].astype(str),
            "away_team": raw["away_team"].astype(str),
            "neutral_site": neutral_site,
            "roof": None,
            "surface": surface,
            "home_score": pd.to_numeric(raw["home_points"], errors="coerce"),
            "away_score": pd.to_numeric(raw["away_points"], errors="coerce"),
        }
    )
    return Games.validate(out)


def normalize_plays(raw: pd.DataFrame) -> pd.DataFrame:
    """Map a CFBD play-by-play frame onto the canonical ``Plays`` schema (tolerant).

    CFBD's ``ppa`` (predicted points added) is the EPA analogue. Non-numeric
    ``ppa``/``down``/``yards_gained`` coerce to null rather than raising. Rows
    without a game id are dropped.
    """
    raw = raw.copy()
    if "game_id" not in raw.columns and "gameId" in raw.columns:
        raw = raw.rename(columns={"gameId": "game_id"})
    raw = raw[raw["game_id"].notna()]

    def num(name: str) -> pd.Series:
        if name in raw.columns:
            return pd.to_numeric(raw[name], errors="coerce")
        return pd.Series(np.nan, index=raw.index)

    def obj(name: str) -> pd.Series:
        return raw[name] if name in raw.columns else pd.Series(pd.NA, index=raw.index)

    epa = num("ppa")
    # missing EPA → success unknown (NA), not False
    success = (epa > 0).astype("boolean").mask(epa.isna())

    play_id = raw["id"].astype(str) if "id" in raw.columns else raw.index.astype(str)
    down = num("down").where(num("down").between(1, 4))  # out-of-range → null
    out = pd.DataFrame(
        {
            "play_id": play_id,
            "game_id": raw["game_id"].astype("int64").astype(str),
            "season": num("season"),
            "week": num("week"),
            "posteam": obj("offense"),
            "defteam": obj("defense"),
            "play_type": obj("play_type"),
            "down": down,
            "yards_gained": num("yards_gained"),
            "epa": epa,
            "success": success,
        }
    )
    return Plays.validate(out)


def _import_cfbd():  # type: ignore[no-untyped-def]
    """Lazily import the ``cfbd`` client, with a helpful error if it is absent."""
    try:
        import cfbd  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - exercised only without the dep
        raise ImportError(
            "cfbd is required for live NCAAF ingest; install it with "
            "`pip install 'velocity[ingest]'` and set a CFBD API key"
        ) from exc
    return cfbd


def load_games(years: Iterable[int], api_key: str) -> pd.DataFrame:  # pragma: no cover
    """Fetch and normalize CFBD games for ``years`` (network)."""
    cfbd = _import_cfbd()
    config = cfbd.Configuration(access_token=api_key)
    with cfbd.ApiClient(config) as client:
        api = cfbd.GamesApi(client)
        rows = [g.to_dict() for year in years for g in api.get_games(year=year)]
    return normalize_games(pd.DataFrame(rows))
