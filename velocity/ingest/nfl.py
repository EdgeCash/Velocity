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
NFLVERSE_INJURIES_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "injuries/injuries_{year}.parquet"
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


# Official game-status designations that mean genuinely unavailable — the
# availability-adjustment trigger. "Questionable" deliberately excluded (most
# questionables play), matching the FantasyPros snapshot's OUT_STATUSES
# semantics so both feeds drive the same intel signals.
INJURY_OUT_STATUSES = frozenset({"out", "doubtful"})


def normalize_injury_reports(raw: pd.DataFrame) -> pd.DataFrame:
    """Map an nflverse injuries frame onto the intel layer's snapshot shape.

    One row per (season, week, player) game-status designation:
    ``season/week/player_id/player_name/team/position/status/practice_status/
    is_out/date_modified``. Rows with no ``report_status`` (practice notes
    only) are dropped — a report that assigns no game status adjusts nothing.
    ``date_modified`` is kept for point-in-time honesty: the designation's
    own timestamp, not ours.

    This is the historical backfill twin of the FantasyPros live snapshot
    (:func:`velocity.ingest.fantasypros.normalize_injuries`): nflverse banks
    every week's official reports back a decade, which is what makes the
    injury/veto channel *backtestable* instead of presumed
    (docs/EDGE_RESEARCH.md §7 item 6).
    """
    if "full_name" in raw.columns:
        names = raw["full_name"]
    elif "player_name" in raw.columns:
        names = raw["player_name"]
    else:
        raise ValueError("injuries frame needs full_name or player_name")
    status = raw["report_status"].astype("string")
    keep = status.notna() & (status.str.strip() != "")
    out = pd.DataFrame(
        {
            "season": pd.to_numeric(raw["season"], errors="coerce"),
            "week": pd.to_numeric(raw["week"], errors="coerce"),
            "player_id": raw.get("gsis_id", pd.Series(dtype=object)).astype("string"),
            "player_name": names.astype(str),
            "team": raw["team"].astype(str),
            "position": raw.get("position", pd.Series(dtype=object)).astype("string"),
            "status": status,
            "practice_status": raw.get(
                "practice_status", pd.Series(dtype=object)
            ).astype("string"),
            "is_out": status.str.strip().str.lower().isin(INJURY_OUT_STATUSES),
            "date_modified": (
                pd.to_datetime(
                    raw["date_modified"], errors="coerce", utc=True
                ).dt.tz_localize(None)
                if "date_modified" in raw.columns
                else pd.Series(pd.NaT, index=raw.index)
            ),
        }
    )
    out = out[keep].reset_index(drop=True)
    out["is_out"] = out["is_out"].fillna(False).astype(bool)
    return out


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


def load_injury_reports(years: Iterable[int]) -> pd.DataFrame:  # pragma: no cover - network
    """Fetch and normalize nflverse weekly injury reports for ``years`` (network)."""
    frames = [_read_parquet_url(NFLVERSE_INJURIES_URL.format(year=year)) for year in years]
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return normalize_injury_reports(combined)


# nflverse weekly player stats (the prop-grading actuals): one row per
# player-week with passing/rushing/receiving counting stats.
NFLVERSE_WEEKLY_STATS_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "stats_player/stats_player_week_{year}.parquet"
)

# nflverse weekly-stat column → canonical prop market (the stats
# velocity.models.props_football simulates and PropLines quotes).
_WEEKLY_STAT_COLUMNS = {
    "passing_yards": "pass_yards",
    "passing_tds": "pass_tds",
    "rushing_yards": "rush_yards",
    "receiving_yards": "receiving_yards",
    "receptions": "receptions",
}


def normalize_weekly_stats(raw: pd.DataFrame) -> pd.DataFrame:
    """Map an nflverse weekly player-stats frame onto the prop-market actuals.

    One row per player-week, columns named as the canonical prop markets;
    ``anytime_td`` is the rushing + receiving TD count (passing TDs don't count
    for an anytime-TD prop). Tolerates both the ``player_name`` and
    ``player_display_name`` spellings and ``team``/``recent_team``.
    """
    if "player_display_name" in raw.columns:
        names = raw["player_display_name"]
    elif "player_name" in raw.columns:
        names = raw["player_name"]
    else:
        raise ValueError("weekly stats frame needs player_display_name or player_name")
    team_col = "team" if "team" in raw.columns else "recent_team"

    out = pd.DataFrame(
        {
            "season": raw["season"],
            "week": raw["week"],
            "player_id": raw["player_id"].astype(str),
            "player_name": names.astype(str),
            "team": raw[team_col] if team_col in raw.columns else pd.NA,
            "position": raw["position"] if "position" in raw.columns else pd.NA,
        }
    )
    for src, market in _WEEKLY_STAT_COLUMNS.items():
        out[market] = pd.to_numeric(raw[src], errors="coerce") if src in raw.columns else 0.0
    rushing_tds = pd.to_numeric(
        raw["rushing_tds"], errors="coerce"
    ) if "rushing_tds" in raw.columns else 0.0
    receiving_tds = pd.to_numeric(
        raw["receiving_tds"], errors="coerce"
    ) if "receiving_tds" in raw.columns else 0.0
    out["anytime_td"] = pd.Series(rushing_tds, index=raw.index).fillna(0.0) + pd.Series(
        receiving_tds, index=raw.index
    ).fillna(0.0)
    return out


# The full DK scoring line per player-week, kept separate from the prop
# actuals above: that normalizer is shaped by the prop markets and adding
# columns to it would change what every prop grade reads.
_DFS_STAT_COLUMNS = {
    "pass_yards": ("passing_yards",),
    "pass_tds": ("passing_tds",),
    "interceptions": ("passing_interceptions", "interceptions"),
    "rush_yards": ("rushing_yards",),
    "rush_tds": ("rushing_tds",),
    "receptions": ("receptions",),
    "receiving_yards": ("receiving_yards",),
    "receiving_tds": ("receiving_tds",),
    "carries": ("carries",),
    "targets": ("targets",),
    "attempts": ("attempts",),
}
# Columns DK scores that nflverse splits across several of its own.
_DFS_SUMMED_COLUMNS = {
    "fumbles_lost": ("rushing_fumbles_lost", "receiving_fumbles_lost",
                     "sack_fumbles_lost"),
    "return_tds": ("special_teams_tds", "pt_return_tds"),
    "two_point_conversions": ("passing_2pt_conversions",
                              "rushing_2pt_conversions",
                              "receiving_2pt_conversions"),
}


def normalize_dfs_weeks(raw: pd.DataFrame) -> pd.DataFrame:
    """nflverse weekly player stats → the DK scoring line, one row per week.

    Everything :func:`velocity.models.dfs_nfl.nfl_dk_points` scores, plus the
    identity and opponent a projection needs. A column nflverse does not ship
    contributes zero rather than raising, so an older season with a thinner
    schema still banks.
    """
    if raw.empty:
        return pd.DataFrame(columns=[
            "season", "week", "season_type", "game_id", "player_id",
            "player_name", "team", "opponent", "position",
            *_DFS_STAT_COLUMNS, *_DFS_SUMMED_COLUMNS])

    def first(*names: str) -> pd.Series:
        for name in names:
            if name in raw.columns:
                return raw[name]
        return pd.Series(pd.NA, index=raw.index)

    def numeric(*names: str) -> pd.Series:
        return pd.to_numeric(first(*names), errors="coerce").fillna(0.0)

    out = pd.DataFrame({
        "season": pd.to_numeric(raw["season"], errors="coerce").astype("Int64"),
        "week": pd.to_numeric(raw["week"], errors="coerce").astype("Int64"),
        "season_type": first("season_type").astype(str),
        "game_id": first("game_id").astype(str),
        "player_id": raw["player_id"].astype(str),
        "player_name": first("player_display_name", "player_name").astype(str),
        "team": first("team", "recent_team").astype(str),
        "opponent": first("opponent_team").astype(str),
        "position": first("position").astype(str),
    })
    for target, sources in _DFS_STAT_COLUMNS.items():
        out[target] = numeric(*sources)
    for target, sources in _DFS_SUMMED_COLUMNS.items():
        total = pd.Series(0.0, index=raw.index)
        for source in sources:
            if source in raw.columns:
                total += pd.to_numeric(raw[source], errors="coerce").fillna(0.0)
        out[target] = total
    return out.reset_index(drop=True)


def load_dfs_weeks(years: Iterable[int]) -> pd.DataFrame:  # pragma: no cover - network
    """Fetch and normalize the DK scoring line for ``years`` (network)."""
    frames = [
        normalize_dfs_weeks(_read_parquet_url(NFLVERSE_WEEKLY_STATS_URL.format(year=year)))
        for year in years
    ]
    return pd.concat(frames, ignore_index=True) if frames else normalize_dfs_weeks(
        pd.DataFrame())


def load_weekly_stats(years: Iterable[int]) -> pd.DataFrame:  # pragma: no cover - network
    """Fetch and normalize nflverse weekly player stats for ``years`` (network)."""
    frames = [
        _read_parquet_url(NFLVERSE_WEEKLY_STATS_URL.format(year=year)) for year in years
    ]
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return normalize_weekly_stats(combined)
