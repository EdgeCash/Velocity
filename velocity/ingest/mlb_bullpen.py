"""Per-team bullpen rates — FanGraphs team reliever line → per-PA vector.

The sim finishes a game with a bullpen once the starter is pulled; this supplies
each club's aggregate reliever as the five shared per-PA rates (K / BB / HBP / HR /
in-play) the matchup engine consumes, replacing the old league-average finisher.

Source is the FanGraphs team **reliever** leaderboard (``stats=rel``), the same
unofficial JSON API the advanced-metrics adapter uses — so the same discipline
applies: pure ``normalize_team_bullpen`` (offline-testable), network ``load_*``
behind a pragma, tolerant parsing (rate fields preferred, counts/TBF as a
fallback), and graceful degradation — a club without data simply gets no bullpen
and the sim falls back to the starter's fresh rates.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Iterable, Mapping
from typing import Any

_FANGRAPHS = "https://www.fangraphs.com/api/leaders/major-league/data"
_FETCH_TIMEOUT = 60

# Feed abbreviation → the model's team code, where they differ (else identity).
_CODE_ALIASES: dict[str, str] = {
    "SFG": "SF", "TBR": "TB", "WSN": "WSH", "KCR": "KC", "SDP": "SD",
    "CHW": "CWS", "OAK": "ATH", "SAC": "ATH", "AZ": "ARI",
}

_PA_ORDER = ("k", "bb", "hbp", "hr", "in_play")


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


def _frac(value: Any) -> float | None:
    """A rate that may be a fraction (0.24) or a percent (24.0) → a fraction."""
    v = _f(value)
    if v is None:
        return None
    return v / 100.0 if v > 1.0 else v


def _rows(payload: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        payload = payload.get("data") or payload.get("rows") or []
    if isinstance(payload, list):
        yield from (r for r in payload if isinstance(r, Mapping))


def _team_key(row: Mapping[str, Any]) -> str | None:
    for field in ("teamabbrev", "Team", "TeamName", "team", "abbrev", "team_abbrev"):
        if row.get(field):
            return _code(row[field])
    return None


def _event_rate(
    row: Mapping[str, Any], pct_field: str, count_field: str, tbf: float
) -> float | None:
    """A per-PA rate from the percent field if present, else count / batters-faced."""
    pct = _frac(row.get(pct_field))
    if pct is not None:
        return pct
    count = _f(row.get(count_field))
    return None if count is None else count / tbf


def normalize_team_bullpen(payload: Any) -> dict[str, dict[str, float]]:
    """Flatten a FanGraphs team-reliever payload into ``{code: PA-rate dict}``.

    Each value maps the five ``PA_OUTCOMES`` to rates that sum to 1. A row without
    a team, batters faced, or enough fields to place K/BB is skipped.
    """
    out: dict[str, dict[str, float]] = {}
    for row in _rows(payload):
        code = _team_key(row)
        tbf = _f(row.get("TBF"))
        if not code or not tbf:
            continue
        k = _event_rate(row, "K%", "SO", tbf)
        bb = _event_rate(row, "BB%", "BB", tbf)
        hbp = (_f(row.get("HBP")) or 0.0) / tbf
        hr = _event_rate(row, "HR%", "HR", tbf)
        if k is None or bb is None or hr is None:
            continue
        reach = k + bb + hbp + hr
        if reach >= 1.0:  # degenerate row — skip rather than emit a bad vector
            continue
        vec = {"k": k, "bb": bb, "hbp": hbp, "hr": hr, "in_play": 1.0 - reach}
        total = sum(vec.values())
        out[code] = {o: vec[o] / total for o in _PA_ORDER}
    return out


def load_team_bullpen(season: int) -> dict[str, dict[str, float]]:  # pragma: no cover - network
    """Fetch and normalize the FanGraphs team reliever line for a season."""
    url = (
        f"{_FANGRAPHS}?pos=all&stats=rel&lg=all&qual=0"
        f"&season={season}&season1={season}&team=0,ts&type=8"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "velocity/1.0"})
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
        return normalize_team_bullpen(json.loads(resp.read()))
