"""NHL ingest — the official NHL API (api-web.nhle.com, free, keyless).

Games come from ``/v1/club-schedule-season/{club}/{season}`` (32 club
calls per season, deduped by game id — far cheaper than a date crawl) and
carry final scores plus the OT/SO indicator. Starting goalies come from
``/v1/gamecenter/{id}/boxscore``, which flags the starter explicitly —
the goalie is the model's starting-pitcher analog, decomposed out of the
team defense by the same machinery MLB uses.

Team keys are the NHL's own abbreviations (TOR, MTL, …);
``NHL_TEAM_ALIASES`` bridges The Odds API's full names onto them.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.request import Request, urlopen

import pandas as pd

BASE = "https://api-web.nhle.com/v1"

# The 32 active clubs (2024-25 onward). ARI (Coyotes) appears in older
# seasons' schedules via opponents and needs no separate crawl.
NHL_CLUBS = (
    "ANA", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ", "DAL", "DET",
    "EDM", "FLA", "LAK", "MIN", "MTL", "NSH", "NJD", "NYI", "NYR", "OTT",
    "PHI", "PIT", "SJS", "SEA", "STL", "TBL", "TOR", "UTA", "VAN", "VGK",
    "WSH", "WPG",
)

# The Odds API full names → NHL abbreviations (both Utah identities and
# the pre-2024 Coyotes included so older archives resolve).
NHL_TEAM_ALIASES: dict[str, str] = {
    "Anaheim Ducks": "ANA", "Arizona Coyotes": "ARI", "Boston Bruins": "BOS",
    "Buffalo Sabres": "BUF", "Calgary Flames": "CGY",
    "Carolina Hurricanes": "CAR", "Chicago Blackhawks": "CHI",
    "Colorado Avalanche": "COL", "Columbus Blue Jackets": "CBJ",
    "Dallas Stars": "DAL", "Detroit Red Wings": "DET",
    "Edmonton Oilers": "EDM", "Florida Panthers": "FLA",
    "Los Angeles Kings": "LAK", "Minnesota Wild": "MIN",
    "Montreal Canadiens": "MTL", "Montréal Canadiens": "MTL",
    "Nashville Predators": "NSH", "New Jersey Devils": "NJD",
    "New York Islanders": "NYI", "New York Rangers": "NYR",
    "Ottawa Senators": "OTT", "Philadelphia Flyers": "PHI",
    "Pittsburgh Penguins": "PIT", "San Jose Sharks": "SJS",
    "Seattle Kraken": "SEA", "St Louis Blues": "STL",
    "St. Louis Blues": "STL", "Tampa Bay Lightning": "TBL",
    "Toronto Maple Leafs": "TOR", "Utah Hockey Club": "UTA",
    "Utah Mammoth": "UTA", "Vancouver Canucks": "VAN",
    "Vegas Golden Knights": "VGK", "Washington Capitals": "WSH",
    "Winnipeg Jets": "WPG",
}

# gameType: 1 = preseason (excluded), 2 = regular, 3 = playoffs.
_SEASON_TYPE = {2: "REG", 3: "POST"}


def _season_week(kickoff: pd.Timestamp, start_year: int) -> int:
    """A date-monotone 15-day bucket within the schema's 0–25.

    NHL seasons span the calendar boundary (Oct–Jun), so the bucket counts
    days since Sep 15 of the season's start year rather than day-of-year
    (which would wrap non-monotonically at New Year).
    """
    origin = pd.Timestamp(year=start_year, month=9, day=15)
    return min(max(int((kickoff - origin).days // 15), 0), 25)


def fetch_json(url: str, timeout: float = 20.0) -> Any:  # pragma: no cover - network
    # The NHL edge 403s urllib's default user agent; a plain tool UA passes.
    request = Request(url, headers={"User-Agent": "velocity/1.0",
                                    "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as resp:
        return json.load(resp)


def club_season_url(club: str, start_year: int) -> str:
    return f"{BASE}/club-schedule-season/{club}/{start_year}{start_year + 1}"


def boxscore_url(game_id: str | int) -> str:
    return f"{BASE}/gamecenter/{game_id}/boxscore"


def score_day_url(day: str) -> str:
    """Finals for one calendar day (YYYY-MM-DD) — the grading feed."""
    return f"{BASE}/score/{day}"


def normalize_nhl_games(
    payloads: list[dict], start_year: int
) -> pd.DataFrame:
    """Club-season payloads → one Games-shaped frame (deduped by game id).

    ``season`` is the start year (2024 = the 2024-25 season). Unplayed
    games keep null scores; ``last_period_type`` (REG/OT/SO) rides along
    as an extra column for played games.
    """
    rows: dict[str, dict] = {}
    for payload in payloads:
        for game in payload.get("games", []):
            season_type = _SEASON_TYPE.get(game.get("gameType"))
            if season_type is None:
                continue
            gid = str(game["id"])
            if gid in rows:
                continue
            home, away = game.get("homeTeam", {}), game.get("awayTeam", {})
            outcome = game.get("gameOutcome") or {}
            final = game.get("gameState") in ("OFF", "FINAL")
            kickoff = pd.Timestamp(game["startTimeUTC"]).tz_localize(None)
            rows[gid] = {
                "game_id": gid,
                "league": "nhl",
                "season": start_year,
                "week": _season_week(kickoff, start_year),
                "season_type": season_type,
                "kickoff": kickoff,
                "home_team": str(home.get("abbrev", "")),
                "away_team": str(away.get("abbrev", "")),
                "neutral_site": bool(game.get("neutralSite", False)),
                "roof": "dome",
                "surface": "ice",
                "home_score": float(home["score"]) if final and "score" in home
                else None,
                "away_score": float(away["score"]) if final and "score" in away
                else None,
                "last_period_type": outcome.get("lastPeriodType"),
            }
    frame = pd.DataFrame(rows.values())
    return frame.sort_values("kickoff").reset_index(drop=True) if not frame.empty else frame


def extract_goalie_starts(payload: dict, game_id: str) -> list[dict]:
    """Boxscore payload → one row per side's STARTING goalie.

    The API flags the starter explicitly; a side with no flagged starter
    (data gaps) contributes nothing rather than a guess.
    """
    rows = []
    stats = payload.get("playerByGameStats", {})
    for side in ("home", "away"):
        for goalie in stats.get(f"{side}Team", {}).get("goalies", []):
            if not goalie.get("starter"):
                continue
            name = goalie.get("name")
            rows.append({
                "game_id": str(game_id),
                "side": side,
                "starter_id": str(goalie.get("playerId", "")),
                "starter_name": (name or {}).get("default", "")
                if isinstance(name, dict) else str(name or ""),
                "shots_against": _num(goalie.get("shotsAgainst")),
                "saves": _num(goalie.get("saves")),
                "goals_against": _num(goalie.get("goalsAgainst")),
            })
            break  # one starter per side
    return rows


def _num(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def normalize_day_scores(payload: dict) -> pd.DataFrame:
    """The ``/v1/score/{date}`` payload → a finals frame for grading.

    Only games in a final state contribute; teams keyed by abbreviation.
    """
    rows = []
    for game in payload.get("games", []):
        if game.get("gameState") not in ("OFF", "FINAL"):
            continue
        home, away = game.get("homeTeam", {}), game.get("awayTeam", {})
        rows.append({
            "game_id": str(game["id"]),
            "kickoff": pd.Timestamp(game["startTimeUTC"]).tz_localize(None),
            "home_team": str(home.get("abbrev", "")),
            "away_team": str(away.get("abbrev", "")),
            "home_score": float(home.get("score", 0)),
            "away_score": float(away.get("score", 0)),
        })
    return pd.DataFrame(rows)
