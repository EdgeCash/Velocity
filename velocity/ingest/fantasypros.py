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


_INJURY_STATUS_KEYS = ("status", "injury_status", "designation", "game_status")

# Statuses that mean the player is genuinely unavailable (the adjustment
# trigger). "Questionable" deliberately excluded — most questionables play.
OUT_STATUSES = frozenset({"out", "ir", "injured reserve", "pup", "doubtful",
                          "suspended", "nfi"})


def normalize_injuries(payload: Any) -> pd.DataFrame:
    """Melt an ``/injuries`` response into ``player/team/position/status/is_out``.

    Tolerant like :func:`normalize_projections` — identity read through the
    alias keys, status through its own aliases; rows without a status are
    dropped (an injury report row that says nothing adjusts nothing).
    """
    if isinstance(payload, Mapping):
        rows_in = payload.get("injuries") or payload.get("players") or []
    else:
        rows_in = payload or []
    rows: list[dict[str, object]] = []
    for player in rows_in:
        if not isinstance(player, Mapping):
            continue
        status = _first(player, _INJURY_STATUS_KEYS)
        if status is None:
            continue
        rows.append({
            "player_id": _as_str(_first(player, _ID_KEYS)),
            "player_name": _as_str(_first(player, _NAME_KEYS)),
            "team": _as_str(_first(player, _TEAM_KEYS)),
            "position": _as_str(_first(player, _POSITION_KEYS)),
            "status": str(status),
            "is_out": str(status).strip().lower() in OUT_STATUSES,
        })
    return pd.DataFrame(
        rows, columns=["player_id", "player_name", "team", "position",
                       "status", "is_out"]
    )


def _as_str(value: Any) -> str | None:
    return None if value is None else str(value)


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

    def raw_injuries(
        self, sport: str, year: int, week: int | None = None
    ) -> Any:  # pragma: no cover - network
        """Fetch the raw ``/{sport}/injuries`` payload (documented public v2 path)."""
        return self._get(f"{sport.lower()}/injuries", year=year, week=week)

    def projections(
        self, sport: str, season: int, position: str = "ALL", week: int = 0
    ) -> pd.DataFrame:  # pragma: no cover - network
        """Fetch and normalize player projections into the long ``(player, stat, value)`` frame."""
        payload = self.raw_projections(sport, season, position=position, week=week)
        return normalize_projections(payload, season=season, week=week)


# --------------------------------------------------------------------------
# Stat-key coverage — which projections the feed serves, and which we read
# --------------------------------------------------------------------------
#
# The sibling of ``bettingpros.slug_coverage``, and it exists for the same
# reason: a stat key nothing maps contributes nothing, silently and correctly,
# so a feed we read five stats out of looks exactly like a feed that serves
# five. On 2026-09-16 two BettingPros prop slugs (``rushing-attempts``, 57 rows
# and the largest unmapped one, and ``passing-attempts``, 28) were blocked on a
# question nobody could answer from the sandbox: does FantasyPros project
# attempts at all? ``FP_API_KEY`` is an Actions secret, so the answer has to
# come out of a run log or a banked snapshot rather than a spec read — which is
# the habit #207 charged for.

_STAT_REPORT_COLUMNS = ["stat", "rows", "non_zero", "mean", "max", "market", "mapped"]

# Substrings that mark a key as a plausible attempt/completion volume stat.
# Deliberately loose: the point is to surface candidates for a human to read,
# not to pre-judge which spelling the feed uses.
VOLUME_HINTS = ("att", "cmp", "comp", "carr", "target", "tgt", "rush_a", "pass_a")


def looks_like_volume(stat: object) -> bool:
    """Whether a stat key reads like an attempt/completion count."""
    key = str(stat).lower()
    return any(hint in key for hint in VOLUME_HINTS)


def stat_key_census(projections: pd.DataFrame) -> pd.DataFrame:
    """Per stat key of a normalized projections frame, busiest first.

    Columns: the key, how many player rows carry it, how many are non-zero,
    its mean and max, the canonical prop market it feeds (empty when nothing
    reads it), and whether it is mapped at all.

    ``non_zero`` is the column that matters. A key the feed serves as a
    structural zero for every player is not a projection, it is a placeholder,
    and mapping a market onto it would abstain just as surely as leaving it
    unmapped — only less honestly.
    """
    if projections.empty or "stat" not in projections.columns:
        return pd.DataFrame(columns=_STAT_REPORT_COLUMNS)
    # Imported here rather than at module scope: the models package imports
    # this module's client, and a top-level import would close the loop.
    from velocity.models.props_football import FP_STAT_TO_MARKET  # noqa: PLC0415

    value = pd.to_numeric(projections["value"], errors="coerce")
    frame = projections.assign(_v=value)
    rows = []
    for stat, part in frame.groupby(projections["stat"].astype(str)):
        market = FP_STAT_TO_MARKET.get(str(stat), "")
        values = part["_v"]
        rows.append({
            "stat": str(stat),
            "rows": int(len(part)),
            "non_zero": int((values.fillna(0.0) != 0).sum()),
            "mean": round(float(values.mean()), 3) if len(values) else 0.0,
            "max": round(float(values.max()), 2) if len(values) else 0.0,
            "market": market,
            "mapped": bool(market),
        })
    out = pd.DataFrame(rows, columns=_STAT_REPORT_COLUMNS)
    return out.sort_values(["mapped", "non_zero"], ascending=[False, False]).reset_index(
        drop=True
    )


def describe_stat_keys(projections: pd.DataFrame, league: str = "") -> list[str]:
    """The census as printable lines — what a run log should say.

    Loud about the volume-like keys specifically, because those are the ones
    with an open question attached to them.
    """
    census = stat_key_census(projections)
    label = f"{league.upper()} " if league else ""
    if census.empty:
        return [f"  {label}stat keys: no projection rows to report"]

    mapped = census[census["mapped"]]
    lines = [
        f"  {label}stat-key census: {len(census)} key(s) served, "
        f"{len(mapped)} read by the props model"
    ]
    for r in census.to_dict("records"):
        mark = "read    " if r["mapped"] else "UNREAD  "
        target = f" -> {r['market']}" if r["market"] else ""
        lines.append(
            f"    {mark} {str(r['stat']):<22} {int(r['non_zero']):>5} non-zero "
            f"of {int(r['rows']):>5}  mean {r['mean']}  max {r['max']}{target}"
        )

    # The BettingPros slugs this unblocks are football markets, so that
    # guidance is only true under NFL. Printing it under MLB would read as a
    # finding about a feed it says nothing about.
    football = str(league).lower() in ("", "nfl")
    candidates = census[census["stat"].map(looks_like_volume)]
    lines.append(f"  {label}volume-like keys (the open question):")
    if candidates.empty:
        lines.append("    none — this feed serves no attempt/completion projection.")
        if football:
            lines.append("    If rush_att or pass_att has disappeared, the markets priced")
            lines.append("    off them are now abstaining: check before assuming a quiet slate.")
    else:
        for r in candidates.to_dict("records"):
            if r["mapped"]:
                state = f"already read as {r['market']}"
            elif r["non_zero"] == 0:
                state = "SERVED BUT ALL ZERO — a placeholder, not a projection"
            else:
                state = "AVAILABLE — nothing reads this yet"
            lines.append(f"    {str(r['stat']):<22} {int(r['non_zero']):>5} non-zero, "
                         f"mean {r['mean']}  [{state}]")
        if football:
            # The original open question — does this feed project attempts? —
            # was answered yes on 2026-09-16 (run 35108513721) and both markets
            # are priced. What remains is completions, and its blocker is on
            # OUR side, so the report should stop implying the feed decides it.
            lines.append("    rush_att / pass_att are priced (BP rushing-attempts,")
            lines.append("    passing-attempts). pass_cmp is served but NOT priced:")
            lines.append("    player_weeks has no completions column, so there is nothing")
            lines.append("    to fit dispersion on and nothing to settle against.")
            lines.append("    Anything else AVAILABLE here needs a model before a mapping.")
    return lines


def unmelted_stat_keys(payload: Any) -> list[str]:
    """Volume-like keys present on a player object that the melt would drop.

    ``normalize_projections`` keeps only values :func:`_as_number` can coerce,
    so a projection served as a compound string ("18/25" for completions over
    attempts) vanishes from the long frame without a trace. That is exactly the
    shape a completions or attempts projection might arrive in, so the census
    would otherwise answer "not served" to a feed that serves it.
    """
    seen: dict[str, None] = {}

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            if _first(node, _NAME_KEYS) is not None or _first(node, _ID_KEYS) is not None:
                for key, raw in _stat_items(node).items():
                    if _as_number(raw) is None and looks_like_volume(key):
                        seen.setdefault(str(key), None)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return sorted(seen)
