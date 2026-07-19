"""FantasyPros ingest adapter — consensus player projections → projection prior.

FantasyPros publishes consensus **player projections** (expert-aggregated
per-player stat lines). They don't price a market, so they aren't lines; instead
they feed the props model (:mod:`velocity.models.props`) as an external *prior* to
blend against our own ``volume × share × efficiency`` decomposition — a cheap
sanity anchor on soft player-prop numbers.

Unlike the BettingPros (OpenAPI spec) and The Odds API (well-documented) feeds,
we don't have a frozen FantasyPros schema to code against, so the normalizer is
deliberately **tolerant** (same posture as the CFBD adapter): it discovers the
player list and stat keys at runtime and melts them into a long
``(player, stat, value)`` frame, rather than hard-coding field names that a real
response might spell differently. The exact live shape is confirmed by the CI
dry-run (the ``FP_API_KEY`` secret isn't visible to the dev sandbox).

Two layers, kept strictly separate so the test gate stays offline:

* ``normalize_projections`` — **pure**, tolerant, offline-tested against frozen
  samples of both the nested-``stats`` and flat-key response shapes.
* :class:`FantasyProsClient` — the network layer, keyed from ``FP_API_KEY`` only.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd

_BASE = "https://api.fantasypros.com/public/v2/json"
_FETCH_TIMEOUT = 60

# Identity fields are pulled by trying these keys in order; whatever stat keys
# remain become the melted (stat, value) rows. Kept as aliases because the live
# spelling isn't pinned.
_NAME_KEYS = ("name", "player_name", "player", "fantasypros_name")
_TEAM_KEYS = ("team", "team_id", "player_team_id", "tm")
_POSITION_KEYS = ("position", "position_id", "player_position_id", "pos")
_ID_KEYS = ("fpid", "player_id", "id", "mpid", "player_filename")

# Non-stat containers/labels to never treat as a numeric stat.
_NON_STAT_KEYS = frozenset(
    {*_NAME_KEYS, *_TEAM_KEYS, *_POSITION_KEYS, *_ID_KEYS, "stats", "player_page_url", "url"}
)

_OUTPUT_COLUMNS = [
    "season",
    "week",
    "player_id",
    "player_name",
    "team",
    "position",
    "stat",
    "value",
    "source",
]


def _first(player: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in player and player[key] not in (None, ""):
            return player[key]
    return None


def _as_number(value: Any) -> float | None:
    """Coerce a stat value to float, tolerating strings like '1,234.5'; else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _stat_items(player: Mapping[str, Any]) -> dict[str, Any]:
    """Return the player's stat mapping, whether nested under 'stats' or flat."""
    stats = player.get("stats")
    if isinstance(stats, Mapping):
        return dict(stats)
    return {k: v for k, v in player.items() if k not in _NON_STAT_KEYS}


def normalize_projections(
    payload: Any, season: int, week: int, source: str = "fantasypros"
) -> pd.DataFrame:
    """Melt a FantasyPros projections response into a long ``(player, stat, value)`` frame.

    ``payload`` is the decoded JSON (a ``{"players": [...]}`` object, or a bare
    list of player rows). Player identity is read from whatever alias key is
    present; every remaining numeric field becomes one ``stat``/``value`` row.
    Non-numeric fields are skipped. The result is tagged with ``season``,
    ``week`` and ``source`` for point-in-time blending.
    """
    if isinstance(payload, Mapping):
        players = payload.get("players") or payload.get("data") or []
    else:
        players = payload or []

    rows: list[dict[str, object]] = []
    for player in players:
        if not isinstance(player, Mapping):
            continue
        pid = _first(player, _ID_KEYS)
        name = _first(player, _NAME_KEYS)
        team = _first(player, _TEAM_KEYS)
        position = _first(player, _POSITION_KEYS)
        for stat, raw in _stat_items(player).items():
            value = _as_number(raw)
            if value is None:
                continue
            rows.append(
                {
                    "season": season,
                    "week": week,
                    "player_id": None if pid is None else str(pid),
                    "player_name": None if name is None else str(name),
                    "team": None if team is None else str(team),
                    "position": None if position is None else str(position),
                    "stat": str(stat),
                    "value": value,
                    "source": source,
                }
            )
    return pd.DataFrame(rows, columns=_OUTPUT_COLUMNS)


@dataclass
class FantasyProsClient:
    """Network client for the FantasyPros projections API. Build with :meth:`from_env`.

    The key comes from ``FP_API_KEY`` only — never a literal — sent as the
    ``x-api-key`` header, so the collector reads it from a GitHub Actions secret
    and the sandbox never sees it.
    """

    api_key: str

    @classmethod
    def from_env(cls) -> FantasyProsClient:
        api_key = os.environ.get("FP_API_KEY", "")
        if not api_key:
            raise RuntimeError("FP_API_KEY is not set (FantasyPros API key)")
        return cls(api_key=api_key)

    def _get(self, path: str, **params: object) -> Any:  # pragma: no cover - network
        query = {k: v for k, v in params.items() if v is not None}
        url = f"{_BASE}/{path.lstrip('/')}?{urllib.parse.urlencode(query, doseq=True)}"
        req = urllib.request.Request(url, headers={"x-api-key": self.api_key})
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
            return json.loads(resp.read())

    def raw_projections(
        self, sport: str, season: int, position: str = "ALL", week: int = 0
    ) -> Any:  # pragma: no cover - network
        """Fetch the raw projections payload (week 0 = full-season; used by the dry-run)."""
        return self._get(
            f"{sport.lower()}/{season}/projections", position=position, week=week
        )

    def projections(
        self, sport: str, season: int, position: str = "ALL", week: int = 0
    ) -> pd.DataFrame:  # pragma: no cover - network
        """Fetch and normalize player projections into the long ``(player, stat, value)`` frame."""
        payload = self.raw_projections(sport, season, position=position, week=week)
        return normalize_projections(payload, season=season, week=week)
