"""Advanced team metrics — FanGraphs (wRC+, xFIP) + Statcast (barrel%, xwOBA).

The metrics on the reference cards that neither the model nor StatsAPI carries.
They live at FanGraphs and Baseball Savant, which expose them as JSON/CSV
endpoints — unofficial, so the fetch is brittle and rate-limited. The two-layer
discipline earns its keep here: the ``normalize_*`` layer is pure and tolerant, and
every field is optional, so a throttled or reshaped feed contributes ``None`` for
its metrics and the card simply omits that row instead of breaking.

The sources label teams by abbreviation, and their abbreviations differ from the
card's three-letter code (``SFG``≠``SF``, ``TBR``≠``TB`` …). :data:`_CODE_ALIASES`
maps both feeds onto the card code so the join is exact.

Assumed feed shapes (documented so the network layer can be re-pointed if a feed
moves): FanGraphs ``/api/leaders/major-league/data`` returns ``{"data": [ {team,
"wRC+"/"xFIP", …} ]}``; Savant's team statcast leaderboard CSV yields one dict per
team with ``barrel_batted_rate`` + ``xwoba`` (or ``est_woba``).
"""

from __future__ import annotations

import csv
import io
import json
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

_FANGRAPHS = "https://www.fangraphs.com/api/leaders/major-league/data"
_SAVANT = "https://baseballsavant.mlb.com/leaderboard/statcast"
_FETCH_TIMEOUT = 60

# Feed abbreviation → the card's team code, where they differ (else identity).
_CODE_ALIASES: dict[str, str] = {
    "SFG": "SF", "TBR": "TB", "WSN": "WSH", "KCR": "KC", "SDP": "SD",
    "CHW": "CWS", "OAK": "ATH", "SAC": "ATH", "AZ": "ARI",
}


def _code(raw: Any) -> str | None:
    if raw is None:
        return None
    up = str(raw).strip().upper()
    return _CODE_ALIASES.get(up, up) or None


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class TeamAdvanced:
    """A club's advanced batting + pitching metrics (any field may be None)."""

    wrc_plus: int | None = None
    xfip: float | None = None
    barrel_pct: float | None = None
    xwoba: float | None = None


def _rows(payload: Any) -> Iterable[Mapping[str, Any]]:
    """Yield team dict rows from a FanGraphs payload (``{"data": [...]}`` or a list)."""
    if isinstance(payload, Mapping):
        payload = payload.get("data") or payload.get("rows") or []
    if isinstance(payload, list):
        yield from (r for r in payload if isinstance(r, Mapping))


def _team_key(row: Mapping[str, Any]) -> str | None:
    for field in ("teamabbrev", "Team", "TeamName", "team", "abbrev", "team_abbrev"):
        if row.get(field):
            return _code(row[field])
    return None


def normalize_fangraphs(
    batting: Any = None, pitching: Any = None
) -> dict[str, dict[str, float]]:
    """Merge FanGraphs batting (wRC+) + pitching (xFIP) payloads, keyed by card code."""
    out: dict[str, dict[str, float]] = {}
    for row in _rows(batting):
        code = _team_key(row)
        wrc = _f(row.get("wRC+") or row.get("wRCplus"))
        if code and wrc is not None:
            out.setdefault(code, {})["wrc_plus"] = wrc
    for row in _rows(pitching):
        code = _team_key(row)
        xfip = _f(row.get("xFIP"))
        if code and xfip is not None:
            out.setdefault(code, {})["xfip"] = xfip
    return out


def normalize_savant(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    """Flatten Savant team statcast rows → ``{code: {barrel_pct, xwoba}}``."""
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        code = _team_key(row)
        if not code:
            continue
        barrel = _f(row.get("barrel_batted_rate") or row.get("brl_percent"))
        xwoba = _f(row.get("xwoba") or row.get("est_woba"))
        vals = {k: v for k, v in (("barrel_pct", barrel), ("xwoba", xwoba)) if v is not None}
        if vals:
            out[code] = vals
    return out


def merge_advanced(
    fangraphs: Mapping[str, Mapping[str, float]] | None = None,
    savant: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, TeamAdvanced]:
    """Combine the source dicts into one :class:`TeamAdvanced` per team code."""
    codes = set(fangraphs or {}) | set(savant or {})
    index: dict[str, TeamAdvanced] = {}
    for code in codes:
        fg = (fangraphs or {}).get(code, {})
        sv = (savant or {}).get(code, {})
        wrc = fg.get("wrc_plus")
        index[code] = TeamAdvanced(
            wrc_plus=None if wrc is None else int(round(wrc)),
            xfip=fg.get("xfip"),
            barrel_pct=sv.get("barrel_pct"),
            xwoba=sv.get("xwoba"),
        )
    return index


def _get(url: str) -> bytes:  # pragma: no cover - network
    req = urllib.request.Request(url, headers={"User-Agent": "velocity/1.0"})
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
        return resp.read()


def _parse_csv(blob: bytes) -> list[dict[str, str]]:  # pragma: no cover - network
    return list(csv.DictReader(io.StringIO(blob.decode("utf-8-sig"))))


def load_advanced(season: int) -> dict[str, TeamAdvanced]:  # pragma: no cover - network
    """Fetch FanGraphs + Savant team metrics for a season; degrade per source.

    A failure in any one feed drops only its metrics — the others still populate.
    """
    fangraphs: dict[str, dict[str, float]] = {}
    savant: dict[str, dict[str, float]] = {}
    try:
        common = f"?pos=all&lg=all&qual=0&season={season}&season1={season}&team=0,ts"
        bat = json.loads(_get(f"{_FANGRAPHS}{common}&stats=bat"))
        pit = json.loads(_get(f"{_FANGRAPHS}{common}&stats=pit"))
        fangraphs = normalize_fangraphs(bat, pit)
    except Exception:  # noqa: BLE001 - unofficial feed; degrade to no advanced batting/pitching
        fangraphs = {}
    try:
        url = f"{_SAVANT}?type=batter-team&year={season}&min=q&csv=true"
        savant = normalize_savant(_parse_csv(_get(url)))
    except Exception:  # noqa: BLE001 - unofficial feed; degrade to no statcast metrics
        savant = {}
    return merge_advanced(fangraphs, savant)
