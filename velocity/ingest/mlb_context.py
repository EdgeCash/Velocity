"""MLB game context — StatsAPI schedule hydrate → header/context for the cards.

The descriptive header a matchup card needs that the *model* doesn't: each team's
id, name and W-L record, and each probable starter's id, name, throwing hand, and
season line (W-L / ERA / WHIP / IP). The team and player ids also key the official
MLB logo and headshot images the card shows.

One StatsAPI call supplies it all —
``/schedule?sportId=1&date=D&hydrate=team,probablePitcher(note,stats)``. Same
two-layer discipline as every adapter: ``normalize_context`` is pure and
offline-testable; ``load_context`` fetches. Everything is optional — a missing
record, starter, or stat line degrades to ``None`` and the card renders without
it, never crashing.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_STATSAPI = "https://statsapi.mlb.com/api/v1"
_FETCH_TIMEOUT = 60


@dataclass(frozen=True)
class TeamContext:
    """A team's id, name, and win-loss record ("W-L", or None if unknown)."""

    team_id: str | None
    name: str
    record: str | None


@dataclass(frozen=True)
class PitcherContext:
    """A probable starter's id, name, hand, and season display line."""

    player_id: str | None
    name: str
    hand: str | None
    line: str | None  # e.g. "9-1 · 2.28 ERA · 0.91 WHIP"


@dataclass(frozen=True)
class GameContext:
    """One game's team + starter context, keyed by StatsAPI gamePk."""

    game_pk: str
    away: TeamContext
    home: TeamContext
    away_sp: PitcherContext | None
    home_sp: PitcherContext | None


def _record(side: Mapping[str, Any]) -> str | None:
    rec = side.get("leagueRecord") or {}
    wins, losses = rec.get("wins"), rec.get("losses")
    return None if wins is None or losses is None else f"{wins}-{losses}"


def _pitcher(side: Mapping[str, Any]) -> PitcherContext | None:
    probable = side.get("probablePitcher")
    if not probable or probable.get("id") is None:
        return None
    hand = (probable.get("pitchHand") or {}).get("code")
    line: str | None = None
    for block in probable.get("stats") or []:
        if str((block.get("group") or {}).get("displayName")) != "pitching":
            continue
        s = block.get("stats") or {}
        era, whip = s.get("era"), s.get("whip")
        wins, losses = s.get("wins"), s.get("losses")
        parts = []
        if wins is not None and losses is not None:
            parts.append(f"{wins}-{losses}")
        if era is not None:
            parts.append(f"{era} ERA")
        if whip is not None:
            parts.append(f"{whip} WHIP")
        line = " · ".join(parts) or None
        break
    return PitcherContext(
        player_id=str(probable["id"]),
        name=str(probable.get("fullName", "")),
        hand=None if hand is None else str(hand),
        line=line,
    )


def _team(side: Mapping[str, Any]) -> TeamContext:
    team = side.get("team") or {}
    team_id = team.get("id")
    return TeamContext(
        team_id=None if team_id is None else str(team_id),
        name=str(team.get("name", "")),
        record=_record(side),
    )


def normalize_context(payload: Mapping[str, Any]) -> list[GameContext]:
    """Flatten a StatsAPI schedule-hydrate payload into per-game context objects."""
    out: list[GameContext] = []
    for date in payload.get("dates") or []:
        for game in date.get("games") or []:
            game_pk = game.get("gamePk")
            teams = game.get("teams") or {}
            home = teams.get("home") or {}
            away = teams.get("away") or {}
            if game_pk is None or not (home.get("team") and away.get("team")):
                continue
            out.append(
                GameContext(
                    game_pk=str(game_pk),
                    away=_team(away),
                    home=_team(home),
                    away_sp=_pitcher(away),
                    home_sp=_pitcher(home),
                )
            )
    return out


def load_context(date: str) -> list[GameContext]:  # pragma: no cover - network
    """Fetch and normalize game context for a date (ISO ``YYYY-MM-DD``)."""
    url = (
        f"{_STATSAPI}/schedule?sportId=1&date={date}"
        "&hydrate=team,probablePitcher(note,stats)"
    )
    with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
        payload = json.loads(resp.read())
    return normalize_context(payload)
