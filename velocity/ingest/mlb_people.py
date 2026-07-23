"""Player handedness — StatsAPI ``/people`` → bat side / throw hand by id.

The platoon matchup in the sim needs each batter's batting side and each pitcher's
throwing hand; the season-stats and lineup feeds don't carry them, so this pulls
them from the StatsAPI ``/people`` endpoint (one call for the players in today's
games). Same two-layer discipline: pure ``normalize_player_hands`` (offline) +
network ``load_player_hands`` behind a pragma. A missing side degrades to ``None``
and the sim treats that matchup as platoon-neutral.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Iterable, Mapping
from typing import Any

_STATSAPI = "https://statsapi.mlb.com/api/v1"
_FETCH_TIMEOUT = 60


def normalize_player_hands(payload: Mapping[str, Any]) -> dict[str, dict[str, str | None]]:
    """Flatten a StatsAPI ``/people`` payload into ``{id: {"bat": code, "pit": code}}``.

    ``bat`` is the batting side (``L``/``R``/``S``), ``pit`` the throwing hand
    (``L``/``R``); either is ``None`` when the person doesn't carry it. A record
    without an id is skipped.
    """
    out: dict[str, dict[str, str | None]] = {}
    for person in payload.get("people") or []:
        pid = person.get("id")
        if pid is None:
            continue
        bat = (person.get("batSide") or {}).get("code")
        pit = (person.get("pitchHand") or {}).get("code")
        out[str(pid)] = {
            "bat": None if bat is None else str(bat),
            "pit": None if pit is None else str(pit),
        }
    return out


def load_player_hands(  # pragma: no cover - network
    player_ids: Iterable[str],
) -> dict[str, dict[str, str | None]]:
    """Fetch bat/throw hands for the given player ids (one ``/people`` call)."""
    ids = ",".join(sorted({str(pid) for pid in player_ids if pid}))
    if not ids:
        return {}
    url = f"{_STATSAPI}/people?personIds={ids}&fields=people,id,batSide,pitchHand,code"
    with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
        return normalize_player_hands(json.loads(resp.read()))
