"""PrizePicks ingest adapter — pick'em board snapshots → canonical projection lines.

PrizePicks posts one line per (player, stat) against fixed parlay payouts —
it is not a priced market, so these rows aren't ``Lines``; they are the
*pick'em board* our slip-EV engine compares against devigged sportsbook
props and our own prop model. There is no historical archive: a board line
exists only while it is live, so history is built the same way as the
BettingPros feed — scheduled snapshots banked as **private Actions
artifacts**, never committed to the public repo.

The board is served by PrizePicks' own unofficial JSON:API (no key, no
login). Two postures follow from that:

* **Tolerant normalizer** (same as the FantasyPros adapter): the schema is
  not ours to pin, so identity and attributes are read through alias keys
  and rows missing the essentials (a line, a player) are dropped, not
  crashed on.
* **Polite client**: a real User-Agent, one modest request per league per
  snapshot, backoff on throttles, and a clear error when bot protection
  answers instead of the API — the collector reports it rather than
  fighting it.

``normalize_projections`` is pure and offline-tested; :class:`PrizePicksClient`
is the network layer.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd

_BASE = "https://api.prizepicks.com"
_FETCH_TIMEOUT = 60
# A plain browser UA: the endpoint serves the PrizePicks web app and answers
# library default agents with bot-protection HTML instead of JSON.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_OUTPUT_COLUMNS = [
    "projection_id",
    "player_id",
    "player_name",
    "team",
    "position",
    "league",
    "stat_type",
    "line",
    "odds_type",
    "status",
    "start_time",
    "is_promo",
]

# Attribute aliases (the JSON:API spellings observed in the wild; read
# tolerantly in this order).
_LINE_KEYS = ("line_score", "line", "value")
_STAT_KEYS = ("stat_type", "stat_display_name", "stat")
_ODDS_TYPE_KEYS = ("odds_type",)
_PLAYER_NAME_KEYS = ("display_name", "name", "player_name")
_TEAM_KEYS = ("team", "team_name", "market")
_POSITION_KEYS = ("position",)


def _first(attrs: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in attrs and attrs[key] not in (None, ""):
            return attrs[key]
    return None


def _rel_id(row: Mapping[str, Any], name: str) -> str | None:
    """The related resource id for ``name`` (e.g. ``new_player``), if present."""
    rel = (row.get("relationships") or {}).get(name) or {}
    data = rel.get("data") or {}
    value = data.get("id")
    return None if value is None else str(value)


def normalize_projections(payload: Any) -> pd.DataFrame:
    """Melt a JSON:API projections payload into one row per board line.

    ``data`` carries the projections; ``included`` carries the player and
    league resources they reference. Rows without a line are dropped (a
    board entry that prices nothing compares to nothing); a missing player
    include degrades to null identity rather than dropping the row, so a
    partial payload still banks its lines.
    """
    if not isinstance(payload, Mapping):
        return pd.DataFrame(columns=_OUTPUT_COLUMNS)
    included = payload.get("included") or []
    players: dict[str, Mapping[str, Any]] = {}
    leagues: dict[str, str] = {}
    for res in included:
        if not isinstance(res, Mapping):
            continue
        kind = res.get("type")
        attrs = res.get("attributes") or {}
        if kind in ("new_player", "player"):
            players[str(res.get("id"))] = attrs
        elif kind == "league":
            name = attrs.get("name")
            if name:
                leagues[str(res.get("id"))] = str(name)

    rows: list[dict[str, object]] = []
    for row in payload.get("data") or []:
        if not isinstance(row, Mapping):
            continue
        attrs = row.get("attributes") or {}
        line = _first(attrs, _LINE_KEYS)
        try:
            line_value = float(line)
        except (TypeError, ValueError):
            continue
        player_id = _rel_id(row, "new_player") or _rel_id(row, "player")
        player = players.get(player_id or "", {})
        league_id = _rel_id(row, "league")
        rows.append({
            "projection_id": str(row.get("id")),
            "player_id": player_id,
            "player_name": _first(player, _PLAYER_NAME_KEYS),
            "team": _first(player, _TEAM_KEYS),
            "position": _first(player, _POSITION_KEYS),
            "league": leagues.get(league_id or "", player.get("league")),
            "stat_type": _first(attrs, _STAT_KEYS),
            "line": line_value,
            # "standard" lines are the EV target; "demon"/"goblin" alternates
            # carry shifted payouts and are kept but flagged for the engine.
            "odds_type": _first(attrs, _ODDS_TYPE_KEYS) or "standard",
            "status": attrs.get("status"),
            "start_time": attrs.get("start_time"),
            "is_promo": bool(attrs.get("is_promo", False)),
        })
    return pd.DataFrame(rows, columns=_OUTPUT_COLUMNS)


@dataclass
class PrizePicksClient:
    """Network client for the PrizePicks board (keyless; be polite).

    ``sleep_seconds`` spaces consecutive requests; ``max_pages`` bounds the
    JSON:API pagination walk so a surprise link loop can't run away.
    """

    sleep_seconds: float = 1.0
    max_pages: int = 10

    def _get(self, url: str) -> Any:  # pragma: no cover - network
        req = urllib.request.Request(
            url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"}
        )
        for attempt, delay in enumerate((0, 10, 30)):
            if delay:
                time.sleep(delay)
            try:
                with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
                    body = resp.read()
                try:
                    return json.loads(body)
                except json.JSONDecodeError as exc:
                    # HTML instead of JSON = bot protection answered. Say so.
                    raise RuntimeError(
                        "PrizePicks answered with non-JSON (bot protection?); "
                        f"first bytes: {body[:60]!r}"
                    ) from exc
            except urllib.error.HTTPError as exc:
                if exc.code not in (403, 429, 500, 502, 503) or attempt == 2:
                    raise
        raise RuntimeError("unreachable")

    def leagues(self) -> dict[str, str]:  # pragma: no cover - network
        """League name → id, from ``/leagues`` (names as PrizePicks spells them)."""
        payload = self._get(f"{_BASE}/leagues")
        out: dict[str, str] = {}
        for res in payload.get("data") or []:
            attrs = res.get("attributes") or {}
            name = attrs.get("name")
            if name:
                out[str(name)] = str(res.get("id"))
        return out

    def projections(self, league_id: str) -> Any:  # pragma: no cover - network
        """The raw projections payload for one league (JSON:API pages merged)."""
        query = urllib.parse.urlencode(
            {"league_id": league_id, "per_page": 250, "single_stat": "true"}
        )
        url: str | None = f"{_BASE}/projections?{query}"
        merged: dict[str, list] = {"data": [], "included": []}
        for _ in range(self.max_pages):
            if url is None:
                break
            page = self._get(url)
            merged["data"].extend(page.get("data") or [])
            merged["included"].extend(page.get("included") or [])
            url = (page.get("links") or {}).get("next")
            if url:
                time.sleep(self.sleep_seconds)
        return merged
