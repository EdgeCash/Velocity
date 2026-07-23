"""MLB ingest adapter — MLB StatsAPI → canonical store.

MLB StatsAPI (``https://statsapi.mlb.com``) is the free, official source for
schedules, scores and player season stats. This adapter normalizes two of its
feeds onto the canonical store: the ``/schedule`` response onto
:class:`~velocity.store.schema.Games`, and the ``/stats`` season splits onto
:class:`~velocity.store.schema.BaseballStats`.

Same two-layer discipline as the football adapters: the ``normalize_*`` functions
are **pure** and offline-testable — they take already-parsed JSON (a
:class:`~collections.abc.Mapping`, exactly what ``json.loads`` returns) and flatten
it — while the ``load_*`` functions do the network fetch and hand off. The
StatsAPI JSON is deeply nested, so the flattening (where the bugs live) sits in
the tested pure layer.

Like the NCAAF adapter, this is deliberately **tolerant**: numeric fields coerce
with ``errors="coerce"`` (a bad value becomes null, not an exception) and a split
missing an essential key (a game id, both teams, a player id) is dropped rather
than crashing the ingest. Baseball has no week concept, so ``week`` is a constant
0 — ``kickoff`` is the real point-in-time anchor — and unplayed games keep null
scores, which is the ingest-level guard against leaking a result into a
projection.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from velocity.store.schema import BASEBALL_ROLES, BaseballStats, Games

_STATSAPI = "https://statsapi.mlb.com/api/v1"
_FETCH_TIMEOUT = 60

# MLB StatsAPI ``gameType`` code → canonical season_type. Spring/exhibition/
# all-star are treated as non-counting (PRE); every postseason round is POST.
GAME_TYPE_TO_SEASON_TYPE = {
    "R": "REG",
    "S": "PRE",  # spring training
    "E": "PRE",  # exhibition
    "A": "PRE",  # all-star
    "F": "POST",  # wild card
    "D": "POST",  # division series
    "L": "POST",  # league championship series
    "W": "POST",  # world series
    "P": "POST",  # playoffs (generic)
}

_GAMES_COLUMNS = [
    "game_id",
    "league",
    "season",
    "week",
    "season_type",
    "kickoff",
    "home_team",
    "away_team",
    "neutral_site",
    "roof",
    "surface",
    "home_score",
    "away_score",
]

_STATS_COLUMNS = [
    "player_id",
    "player_name",
    "team",
    "season",
    "role",
    "pa",
    "k",
    "bb",
    "hbp",
    "singles",
    "doubles",
    "triples",
    "hr",
]


def _empty_games() -> pd.DataFrame:
    empty = pd.DataFrame(
        {
            "game_id": pd.Series(dtype=str),
            "league": pd.Series(dtype=str),
            "season": pd.Series(dtype="int64"),
            "week": pd.Series(dtype="int64"),
            "season_type": pd.Series(dtype=str),
            "kickoff": pd.Series(dtype="datetime64[ns]"),
            "home_team": pd.Series(dtype=str),
            "away_team": pd.Series(dtype=str),
            "neutral_site": pd.Series(dtype=bool),
            "roof": pd.Series(dtype=object),
            "surface": pd.Series(dtype=object),
            "home_score": pd.Series(dtype=float),
            "away_score": pd.Series(dtype=float),
        }
    )
    return Games.validate(empty)


def _empty_stats() -> pd.DataFrame:
    empty = pd.DataFrame(
        {
            "player_id": pd.Series(dtype=str),
            "player_name": pd.Series(dtype=str),
            "team": pd.Series(dtype=object),
            "season": pd.Series(dtype="int64"),
            "role": pd.Series(dtype=str),
            "pa": pd.Series(dtype=float),
            "k": pd.Series(dtype=float),
            "bb": pd.Series(dtype=float),
            "hbp": pd.Series(dtype=float),
            "singles": pd.Series(dtype=float),
            "doubles": pd.Series(dtype=float),
            "triples": pd.Series(dtype=float),
            "hr": pd.Series(dtype=float),
        }
    )
    return BaseballStats.validate(empty)


def normalize_schedule(payload: Mapping[str, Any]) -> pd.DataFrame:
    """Flatten a StatsAPI ``/schedule`` payload onto the canonical ``Games`` schema.

    Walks ``dates → games``. A game without a ``gamePk`` or either team name is
    dropped (tolerant). Scores are read only where present, so an unplayed game
    keeps null scores — the point-in-time guard against leaking a result.
    """
    rows: list[dict[str, Any]] = []
    for date in payload.get("dates") or []:
        for game in date.get("games") or []:
            game_pk = game.get("gamePk")
            teams = game.get("teams") or {}
            home = teams.get("home") or {}
            away = teams.get("away") or {}
            home_team = (home.get("team") or {}).get("name")
            away_team = (away.get("team") or {}).get("name")
            if game_pk is None or not home_team or not away_team:
                continue
            code = str(game.get("gameType", "R"))
            rows.append(
                {
                    "game_id": str(game_pk),
                    "league": "mlb",
                    "season": game.get("season"),
                    "week": 0,  # baseball has no weeks; kickoff is the PIT anchor
                    "season_type": GAME_TYPE_TO_SEASON_TYPE.get(code, "REG"),
                    "kickoff": game.get("gameDate"),
                    "home_team": str(home_team),
                    "away_team": str(away_team),
                    "neutral_site": False,
                    "roof": None,
                    "surface": None,
                    "home_score": home.get("score"),
                    "away_score": away.get("score"),
                }
            )
    if not rows:
        return _empty_games()

    df = pd.DataFrame(rows)
    df["season"] = pd.to_numeric(df["season"], errors="coerce")
    kickoff = pd.to_datetime(df["kickoff"], errors="coerce", utc=True)
    df["kickoff"] = kickoff.dt.tz_localize(None)
    df["home_score"] = pd.to_numeric(df["home_score"], errors="coerce")
    df["away_score"] = pd.to_numeric(df["away_score"], errors="coerce")
    return Games.validate(df[_GAMES_COLUMNS])


def normalize_player_stats(payload: Mapping[str, Any], role: str) -> pd.DataFrame:
    """Flatten a StatsAPI ``/stats`` season-splits payload onto ``BaseballStats``.

    ``role`` is ``"bat"`` or ``"pit"`` and tags every row. Walks ``stats →
    splits``; a split without a player id is dropped. Counts coerce to null on bad
    values. For batters, singles are derived (``hits − 2B − 3B − HR``, floored at
    0); for pitchers the ball-in-play breakdown is unavailable, so
    singles/doubles/triples are left null (see :class:`BaseballStats`).
    """
    if role not in BASEBALL_ROLES:
        raise ValueError(f"role must be one of {BASEBALL_ROLES}, got {role!r}")

    rows: list[dict[str, Any]] = []
    for block in payload.get("stats") or []:
        for split in block.get("splits") or []:
            player = split.get("player") or {}
            player_id = player.get("id")
            if player_id is None:
                continue
            stat = split.get("stat") or {}
            team = split.get("team") or {}
            # Pitchers count batters faced; batters count plate appearances.
            pa = stat.get("battersFaced") if role == "pit" else stat.get("plateAppearances")
            rows.append(
                {
                    "player_id": str(player_id),
                    "player_name": str(player.get("fullName", "")),
                    "team": team.get("name"),
                    "season": split.get("season"),
                    "role": role,
                    "pa": pa,
                    "k": stat.get("strikeOuts"),
                    "bb": stat.get("baseOnBalls"),
                    "hbp": stat.get("hitByPitch"),
                    "hits": stat.get("hits"),
                    "doubles": stat.get("doubles"),
                    "triples": stat.get("triples"),
                    "hr": stat.get("homeRuns"),
                }
            )
    if not rows:
        return _empty_stats()

    df = pd.DataFrame(rows)
    for col in ("pa", "k", "bb", "hbp", "hits", "doubles", "triples", "hr"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["season"] = pd.to_numeric(df["season"], errors="coerce")

    if role == "bat":
        singles = (
            df["hits"] - df["doubles"].fillna(0) - df["triples"].fillna(0) - df["hr"].fillna(0)
        )
        df["singles"] = singles.clip(lower=0)
    else:
        # Season pitching splits carry no 1B/2B/3B-allowed breakdown.
        df["singles"] = np.nan
        df["doubles"] = np.nan
        df["triples"] = np.nan
    return BaseballStats.validate(df[_STATS_COLUMNS])


def _get_json(url: str) -> Any:  # pragma: no cover - network
    with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
        return json.loads(resp.read())


def load_schedule(start_date: str, end_date: str) -> pd.DataFrame:  # pragma: no cover - network
    """Fetch and normalize the StatsAPI schedule for a date range (ISO ``YYYY-MM-DD``)."""
    url = f"{_STATSAPI}/schedule?sportId=1&startDate={start_date}&endDate={end_date}"
    return normalize_schedule(_get_json(url))


def load_player_stats(season: int, role: str) -> pd.DataFrame:  # pragma: no cover - network
    """Fetch and normalize season player stats for ``role`` (``"bat"`` or ``"pit"``)."""
    if role not in BASEBALL_ROLES:
        raise ValueError(f"role must be one of {BASEBALL_ROLES}, got {role!r}")
    group = "pitching" if role == "pit" else "hitting"
    url = (
        f"{_STATSAPI}/stats?stats=season&group={group}"
        f"&season={season}&sportId=1&playerPool=all&limit=2000"
    )
    return normalize_player_stats(_get_json(url), role)


@dataclass(frozen=True)
class GameLineups:
    """One game's probable starters and batting orders (player ids), by side.

    Fields may be empty early — a starter not yet announced leaves the pitcher id
    ``None``; an unposted lineup leaves the tuple empty. Downstream team assembly
    fills those gaps with league-average players, so a partial board still runs.
    """

    game_id: str
    home_team: str
    away_team: str
    home_pitcher_id: str | None
    away_pitcher_id: str | None
    home_lineup: tuple[str, ...]
    away_lineup: tuple[str, ...]


def _player_ids(players: Any) -> tuple[str, ...]:
    """Pull the ordered player ids from a StatsAPI lineup player array."""
    return tuple(
        str(p["id"])
        for p in (players or [])
        if isinstance(p, Mapping) and p.get("id") is not None
    )


def normalize_lineups(payload: Mapping[str, Any]) -> list[GameLineups]:
    """Flatten a StatsAPI ``/schedule?hydrate=lineups,probablePitcher`` payload.

    Walks ``dates → games`` and pulls each side's probable-pitcher id and batting
    order. A game missing a ``gamePk`` or either team name is dropped; missing
    pitchers/lineups become ``None``/empty rather than raising.
    """
    out: list[GameLineups] = []
    for date in payload.get("dates") or []:
        for game in date.get("games") or []:
            game_pk = game.get("gamePk")
            teams = game.get("teams") or {}
            home = teams.get("home") or {}
            away = teams.get("away") or {}
            home_team = (home.get("team") or {}).get("name")
            away_team = (away.get("team") or {}).get("name")
            if game_pk is None or not home_team or not away_team:
                continue
            lineups = game.get("lineups") or {}
            home_pitcher = (home.get("probablePitcher") or {}).get("id")
            away_pitcher = (away.get("probablePitcher") or {}).get("id")
            out.append(
                GameLineups(
                    game_id=str(game_pk),
                    home_team=str(home_team),
                    away_team=str(away_team),
                    home_pitcher_id=None if home_pitcher is None else str(home_pitcher),
                    away_pitcher_id=None if away_pitcher is None else str(away_pitcher),
                    home_lineup=_player_ids(lineups.get("homePlayers")),
                    away_lineup=_player_ids(lineups.get("awayPlayers")),
                )
            )
    return out


def load_lineups(date: str) -> list[GameLineups]:  # pragma: no cover - network
    """Fetch and normalize probable lineups for a date (ISO ``YYYY-MM-DD``)."""
    url = f"{_STATSAPI}/schedule?sportId=1&date={date}&hydrate=lineups,probablePitcher"
    return normalize_lineups(_get_json(url))
