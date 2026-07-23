"""MLB team stats + situational splits + locally-computed league ranks.

The descriptive numbers a matchup card shows that the *model* doesn't carry: each
team's season batting and pitching line, its platoon and recent-form splits, and
— the honest part — **where those numbers rank in the league**, computed here from
the all-30-teams pull rather than scraped from anyone's leaderboard.

Three StatsAPI feeds, same two-layer discipline as every adapter (pure
``normalize_*`` + network ``load_*`` behind a pragma):

* ``/teams/stats?group=hitting|pitching`` — one call returns all 30 clubs; the
  season line + the basis for the ranks.
* ``/teams/{id}/stats?stats=statSplits&sitCodes=vl,vr`` — a club's OPS vs LHP / RHP.
* ``/teams/{id}/stats?stats=lastXGames`` — recent form (last-N runs per game).

Everything is optional: a missing stat coerces to ``None`` and the card renders
the row without it, never crashing.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

_STATSAPI = "https://statsapi.mlb.com/api/v1"
_FETCH_TIMEOUT = 60


def _f(value: Any) -> float | None:
    """Coerce a StatsAPI stat (often a string like ``".271"`` or ``"3.14"``) to float."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return numerator / denominator


@dataclass(frozen=True)
class TeamHitting:
    """A club's season batting line (rates already derived)."""

    team_id: str
    name: str
    ops: float | None
    avg: float | None
    obp: float | None
    slg: float | None
    runs_per_game: float | None
    home_runs: int | None
    k_pct: float | None
    bb_pct: float | None


@dataclass(frozen=True)
class TeamPitching:
    """A club's season staff line."""

    team_id: str
    name: str
    era: float | None
    whip: float | None
    k_per_9: float | None
    runs_allowed_per_game: float | None


@dataclass(frozen=True)
class TeamSplits:
    """A club's situational splits used on the card (any field may be None)."""

    vs_lhp_ops: float | None = None
    vs_rhp_ops: float | None = None
    last_n: int | None = None
    last_n_runs_per_game: float | None = None


def _team_stat_splits(payload: Mapping[str, Any]) -> Iterable[tuple[dict, dict]]:
    """Yield ``(team, stat)`` pairs from a ``/teams/stats`` payload's splits."""
    for block in payload.get("stats") or []:
        for split in block.get("splits") or []:
            team = split.get("team") or {}
            stat = split.get("stat") or {}
            if team.get("id") is not None:
                yield team, stat


def normalize_team_hitting(payload: Mapping[str, Any]) -> list[TeamHitting]:
    """Flatten a ``/teams/stats?group=hitting&stats=season`` payload (all clubs)."""
    out: list[TeamHitting] = []
    for team, stat in _team_stat_splits(payload):
        games = _f(stat.get("gamesPlayed"))
        pa = _f(stat.get("plateAppearances"))
        hr = _f(stat.get("homeRuns"))
        out.append(
            TeamHitting(
                team_id=str(team["id"]),
                name=str(team.get("name", "")),
                ops=_f(stat.get("ops")),
                avg=_f(stat.get("avg")),
                obp=_f(stat.get("obp")),
                slg=_f(stat.get("slg")),
                runs_per_game=_pct(_f(stat.get("runs")), games),
                home_runs=None if hr is None else int(hr),
                k_pct=_pct(_f(stat.get("strikeOuts")), pa),
                bb_pct=_pct(_f(stat.get("baseOnBalls")), pa),
            )
        )
    return out


def normalize_team_pitching(payload: Mapping[str, Any]) -> list[TeamPitching]:
    """Flatten a ``/teams/stats?group=pitching&stats=season`` payload (all clubs)."""
    out: list[TeamPitching] = []
    for team, stat in _team_stat_splits(payload):
        games = _f(stat.get("gamesPlayed"))
        k9 = _f(stat.get("strikeoutsPer9Inn"))
        if k9 is None:  # derive from K and IP if the rate field is absent
            k9 = _pct(_f(stat.get("strikeOuts")), _f(stat.get("inningsPitched")))
            k9 = None if k9 is None else round(k9 * 9.0, 2)
        out.append(
            TeamPitching(
                team_id=str(team["id"]),
                name=str(team.get("name", "")),
                era=_f(stat.get("era")),
                whip=_f(stat.get("whip")),
                k_per_9=k9,
                runs_allowed_per_game=_pct(_f(stat.get("runs")), games),
            )
        )
    return out


def _rank(
    items: Iterable[Any], key: str, *, ascending: bool
) -> dict[str, int]:
    """Rank objects 1..N by an attribute, best = 1; ``None`` values are unranked.

    ``ascending`` True ranks smaller-is-better (ERA); False ranks larger-is-better
    (OPS). Ties share the lower rank number, dense-style, so no rank is skipped.
    """
    scored = [(o.team_id, getattr(o, key)) for o in items]
    present = [(tid, v) for tid, v in scored if v is not None]
    present.sort(key=lambda pair: pair[1], reverse=not ascending)
    ranks: dict[str, int] = {}
    last_val: float | None = None
    rank = 0
    for tid, val in present:
        if val != last_val:
            rank += 1
            last_val = val
        ranks[tid] = rank
    return ranks


def hitting_ranks(teams: Iterable[TeamHitting]) -> dict[str, dict[str, int]]:
    """League ranks (1 = best) for OPS and runs/game, keyed by team id."""
    teams = list(teams)
    ops = _rank(teams, "ops", ascending=False)
    rpg = _rank(teams, "runs_per_game", ascending=False)
    return {t.team_id: {"ops": ops.get(t.team_id, 0), "rpg": rpg.get(t.team_id, 0)}
            for t in teams}


def pitching_ranks(teams: Iterable[TeamPitching]) -> dict[str, dict[str, int]]:
    """League ranks (1 = best) for ERA and WHIP, keyed by team id (lower = better)."""
    teams = list(teams)
    era = _rank(teams, "era", ascending=True)
    whip = _rank(teams, "whip", ascending=True)
    return {t.team_id: {"era": era.get(t.team_id, 0), "whip": whip.get(t.team_id, 0)}
            for t in teams}


def normalize_team_splits(
    platoon: Mapping[str, Any] | None = None,
    recent: Mapping[str, Any] | None = None,
) -> TeamSplits:
    """Build one club's :class:`TeamSplits` from its statSplits + lastXGames payloads.

    ``platoon`` is a ``stats=statSplits&sitCodes=vl,vr`` payload (split labels carry
    the ``sitCode``); ``recent`` is a ``stats=lastXGames`` payload. Both optional.
    """
    vs_lhp = vs_rhp = None
    for block in (platoon or {}).get("stats") or []:
        for split in block.get("splits") or []:
            code = str(split.get("split", {}).get("code", "")).lower()
            ops = _f((split.get("stat") or {}).get("ops"))
            if code == "vl":
                vs_lhp = ops
            elif code == "vr":
                vs_rhp = ops

    last_n = last_rpg = None
    for block in (recent or {}).get("stats") or []:
        for split in block.get("splits") or []:
            stat = split.get("stat") or {}
            games = _f(stat.get("gamesPlayed"))
            if games:
                last_n = int(games)
                last_rpg = _pct(_f(stat.get("runs")), games)
            break  # lastXGames returns a single aggregated split
    return TeamSplits(
        vs_lhp_ops=vs_lhp, vs_rhp_ops=vs_rhp,
        last_n=last_n, last_n_runs_per_game=last_rpg,
    )


def _get_json(url: str) -> Any:  # pragma: no cover - network
    with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
        return json.loads(resp.read())


def load_team_hitting(season: int) -> list[TeamHitting]:  # pragma: no cover - network
    """Fetch and normalize all 30 clubs' season batting line."""
    url = f"{_STATSAPI}/teams/stats?season={season}&sportId=1&group=hitting&stats=season"
    return normalize_team_hitting(_get_json(url))


def load_team_pitching(season: int) -> list[TeamPitching]:  # pragma: no cover - network
    """Fetch and normalize all 30 clubs' season pitching line."""
    url = f"{_STATSAPI}/teams/stats?season={season}&sportId=1&group=pitching&stats=season"
    return normalize_team_pitching(_get_json(url))


def load_team_splits(  # pragma: no cover - network
    team_id: str, season: int, *, last_games: int = 15
) -> TeamSplits:
    """Fetch a club's platoon (vs LHP/RHP) + last-N form splits."""
    base = f"{_STATSAPI}/teams/{team_id}/stats?season={season}&group=hitting"
    platoon = _get_json(f"{base}&stats=statSplits&sitCodes=vl,vr")
    recent = _get_json(f"{base}&stats=lastXGames&limit={last_games}")
    return normalize_team_splits(platoon, recent)
