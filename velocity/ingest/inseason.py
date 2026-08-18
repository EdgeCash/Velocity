"""In-season league ingest — MLB (statsapi) and WNBA (wehoop) → canonical games.

The football dead-zone content feeds: both leagues run all summer, both have
free keyless schedule/score APIs, and the scores-fit + sim + card pipeline is
league-agnostic once a games frame exists. Same posture as every adapter —
``normalize_*`` is pure and offline-tested against frozen payload samples;
the network fetch lives in the build script.

Team keys are the providers' full display names ("New York Yankees",
"Las Vegas Aces"), which match The Odds API's event names — so live alias
resolution is an exact-name match, the NCAAF pattern.

Only **completed** games join the frames (the ratings fit on scores). The
``week`` column is a deterministic day-of-year bucket capped at the schema's
bound — these leagues play daily, so "week" is a label for slicing, not a
scheduling truth.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from velocity.store.schema import Games

# MLB gameType → canonical season_type. Spring/exhibition → PRE; the
# postseason ladder (wildcard/division/LCS/World Series) → POST.
_MLB_SEASON_TYPES = {
    "R": "REG",
    "S": "PRE", "E": "PRE", "A": "PRE",
    "F": "POST", "D": "POST", "L": "POST", "W": "POST", "P": "POST",
}
# ESPN season.type → canonical.
_ESPN_SEASON_TYPES = {1: "PRE", 2: "REG", 3: "POST"}


def _season_week(kickoff: pd.Timestamp) -> int:
    """A deterministic, date-monotone week label within the schema's 0–25."""
    return min(max(int((kickoff.dayofyear - 1) // 15), 0), 25)


def normalize_mlb_schedule(payload: Any, season: int) -> pd.DataFrame:
    """A statsapi ``/api/v1/schedule`` payload → canonical completed games.

    Tolerant: rows missing a game id, either team, or final scores are
    skipped, never guessed; unfamiliar game types are treated as REG.
    """
    rows: list[dict[str, object]] = []
    for date in (payload or {}).get("dates", []):
        for game in date.get("games", []):
            state = ((game.get("status") or {}).get("abstractGameState") or "")
            teams = game.get("teams") or {}
            home = (teams.get("home") or {})
            away = (teams.get("away") or {})
            home_name = ((home.get("team") or {}).get("name"))
            away_name = ((away.get("team") or {}).get("name"))
            if (
                game.get("gamePk") is None or not home_name or not away_name
                or str(state) != "Final"
                or home.get("score") is None or away.get("score") is None
            ):
                continue
            kickoff = pd.to_datetime(game.get("gameDate"), errors="coerce", utc=True)
            if pd.isna(kickoff):
                continue
            kickoff = kickoff.tz_localize(None)
            rows.append({
                "game_id": str(game["gamePk"]),
                "league": "mlb",
                "season": season,
                "week": _season_week(kickoff),
                "season_type": _MLB_SEASON_TYPES.get(str(game.get("gameType")), "REG"),
                "kickoff": kickoff,
                "home_team": str(home_name),
                "away_team": str(away_name),
                "neutral_site": False,
                "roof": None,
                "surface": None,
                "home_score": float(home["score"]),
                "away_score": float(away["score"]),
            })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    # statsapi lists a suspended/resumed game under both its original and its
    # completion date with the same gamePk — keep the last (completion) row.
    frame = frame.drop_duplicates(subset="game_id", keep="last").reset_index(drop=True)
    return Games.validate(frame)


def normalize_wehoop_schedule(raw: pd.DataFrame, season: int) -> pd.DataFrame:
    """A wehoop release schedule parquet → canonical completed WNBA games.

    wehoop (sportsdataverse) publishes one schedule parquet per season on the
    ``espn_wnba_schedules`` release of ``sportsdataverse/sportsdataverse-data``
    — the transport that actually works from CI (ESPN's own edge 403s
    datacenter IPs, proven on backfill runs 32181998406/32182552123; GitHub
    release assets are the nflverse pattern and always reachable). Columns
    used: ``game_id``, ``season_type`` (ESPN 1/2/3), ``game_date_time``
    (tz-aware ET), ``home_/away_display_name``, scores,
    ``status_type_completed``, ``neutral_site``.
    """
    if raw is None or raw.empty:
        return pd.DataFrame()
    rows: list[dict[str, object]] = []
    for g in raw.to_dict("records"):
        if not g.get("status_type_completed"):
            continue
        home, away = g.get("home_display_name"), g.get("away_display_name")
        if g.get("game_id") is None or not home or not away:
            continue
        kickoff = pd.to_datetime(
            g.get("game_date_time"), errors="coerce", utc=True  # type: ignore[call-overload]
        )
        if pd.isna(kickoff):
            continue
        try:
            home_score = float(g.get("home_score"))  # type: ignore[arg-type]
            away_score = float(g.get("away_score"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        kickoff = kickoff.tz_localize(None)
        rows.append({
            "game_id": str(g["game_id"]),
            "league": "wnba",
            "season": season,
            "week": _season_week(kickoff),
            "season_type": _ESPN_SEASON_TYPES.get(int(g.get("season_type") or 2), "REG"),
            "kickoff": kickoff,
            "home_team": str(home),
            "away_team": str(away),
            "neutral_site": bool(g.get("neutral_site", False)),
            "roof": None,
            "surface": None,
            "home_score": home_score,
            "away_score": away_score,
        })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame = frame.drop_duplicates(subset="game_id", keep="last").reset_index(drop=True)
    return Games.validate(frame)
