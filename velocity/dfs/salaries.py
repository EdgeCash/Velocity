"""DraftKings salary ingest — draft groups + draftables → canonical salary rows.

DraftKings exposes its lobby unauthenticated: ``getcontests?sport=NFL`` lists
the draft groups (slates), and the draftables endpoint lists every player in a
group with salary, position, team, and game. Two layers, kept strictly separate
so the test gate stays offline (the same pattern as the odds ingest):

* :func:`legacy_players_to_draftables` — **pure**: the older lineup
  endpoint's payload (``www.draftkings.com/lineup/getavailableplayers``)
  rewritten into the draftables shape, so one normalizer serves both. The
  client falls back to it when the draftables API refuses a request: from
  2026-09-16 every ``api.draftkings.com`` draftables call from the Actions
  runners came back 403 while the lobby on ``www`` kept answering, and the
  salary archive went silently empty for two days.
* :func:`normalize_draftables` — **pure**: flattens a draftables payload onto
  the canonical :class:`Salaries` schema, one row per player per draft group
  (DK repeats a player once per eligible roster slot — deduped here).
* :class:`DraftKingsClient` — the network layer. No key needed; a browser-ish
  User-Agent is set because DK rejects the default urllib one.

Salary snapshots are banked to Actions artifacts alongside the odds
archives (docs/FOOTBALL_CUTOVER.md §5a) — salary-vs-projection history is the
DFS equivalent of the CLV archive, so it starts accruing before the season.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import pandera.pandas as pa
from pandera.pandas import Field
from pandera.typing import Series

LOBBY_URL = "https://www.draftkings.com/lobby/getcontests?sport={sport}"
DRAFTABLES_URL = (
    "https://api.draftkings.com/draftgroups/v1/draftgroups/{group_id}/draftables?format=json"
)
# The older lineup-builder endpoint on the www host — the same host as the
# lobby, and the fallback when the draftables API refuses a request.
LEGACY_PLAYERS_URL = (
    "https://www.draftkings.com/lineup/getavailableplayers?draftGroupId={group_id}"
)
_FETCH_TIMEOUT = 60
# DK serves these endpoints to browsers and rejects the default urllib
# identity. The headers are what a browser's own fetch of the lobby sends:
# a full user-agent string (the truncated one without the Chrome/Safari
# tokens was accepted until 2026-09-15), an Accept for JSON, a language,
# and the site as referer and origin.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.draftkings.com/lobby",
    "Origin": "https://www.draftkings.com",
}
# HTTP statuses on the draftables API that send the client to the legacy
# endpoint: a refusal, not an absence. A 404 (no such group) is final.
_FALLBACK_STATUSES = frozenset({401, 403, 429})

# DK sport codes per league. Every covered vertical banks salary history
# (the DFS analog of the CLV archive); the optimizer prices the leagues it
# has a roster spec + projections for (velocity.dfs.pipeline.LEAGUE_SPECS).
SPORT_CODES = {
    "nfl": "NFL",
    "ncaaf": "CFB",
    "mlb": "MLB",
    "wnba": "WNBA",
    "nba": "NBA",
    "ncaab": "CBB",
    "nhl": "NHL",
}


class Salaries(pa.DataFrameModel):
    """One row per player per draft group: the salary-cap input for a slate."""

    draft_group_id: Series[str] = Field()
    player_id: Series[str] = Field()
    player_name: Series[str] = Field()
    position: Series[str] = Field(nullable=True)
    salary: Series[int] = Field(ge=0)
    team: Series[str] = Field(nullable=True)
    competition: Series[str] = Field(nullable=True)
    kickoff: Series[pa.DateTime] = Field(nullable=True)
    status: Series[str] = Field(nullable=True)
    # DK's probable-pitcher marker (playerGameAttributes id 1) — true on
    # exactly the announced starters, absent on every other pitcher and on
    # hitters. The optimizer's P pool must respect it: DK lists EVERY rostered
    # pitcher on the board (their app hides them behind a "remove non
    # probables" toggle), and a non-probable never starts.
    probable: Series[bool] = Field()
    # DK's roster-slot id, as DK labelled the surviving row. Showdown boards
    # list every player TWICE — once at the captain slot (1.5x salary) and
    # once at flex/utility — and this names which entry a row came from.
    roster_slot_id: Series[str] = Field(nullable=True)

    class Config:
        coerce = True


_COLUMNS = [
    "draft_group_id", "player_id", "player_name", "position", "salary",
    "team", "competition", "kickoff", "status", "probable", "roster_slot_id",
]


def _empty_salaries() -> pd.DataFrame:
    empty = pd.DataFrame(
        {
            "draft_group_id": pd.Series(dtype=str),
            "player_id": pd.Series(dtype=str),
            "player_name": pd.Series(dtype=str),
            "position": pd.Series(dtype=str),
            "salary": pd.Series(dtype="int64"),
            "team": pd.Series(dtype=str),
            "competition": pd.Series(dtype=str),
            "kickoff": pd.Series(dtype="datetime64[ns]"),
            "status": pd.Series(dtype=str),
            "probable": pd.Series(dtype=bool),
            "roster_slot_id": pd.Series(dtype=str),
        }
    )
    return Salaries.validate(empty)


def normalize_draftables(payload: Mapping[str, Any], draft_group_id: str) -> pd.DataFrame:
    """Flatten a DK draftables payload onto the canonical :class:`Salaries` schema.

    One row per player **per price**. DK lists a player once per eligible
    roster slot, and the slot tells you which of two things that means: on a
    classic board an RB's RB and FLEX entries carry the SAME salary and are
    one row here; on a showdown board the captain entry is a genuinely
    different, dearer price for the same player and both rows survive
    (:func:`velocity.dfs.showdown.showdown_board` pairs them back up). Hence
    the dedupe key is the price, not the roster slot. A row without a name or
    salary is dropped, never guessed.
    """
    rows: list[dict[str, object]] = []
    for d in payload.get("draftables") or []:
        name = d.get("displayName")
        salary = d.get("salary")
        if name is None or salary is None:
            continue
        player_id = d.get("playerDkId") or d.get("playerId") or name
        competition = d.get("competition") or {}
        probable = any(
            attr.get("id") == 1 and str(attr.get("value")).lower() == "true"
            for attr in d.get("playerGameAttributes") or []
        )
        rows.append(
            {
                "draft_group_id": str(draft_group_id),
                "player_id": str(player_id),
                "player_name": str(name),
                "position": d.get("position"),
                "salary": salary,
                "team": d.get("teamAbbreviation"),
                "competition": competition.get("name"),
                "kickoff": competition.get("startTime"),
                "status": d.get("status"),
                "probable": probable,
                "roster_slot_id": None if d.get("rosterSlotId") is None
                else str(d.get("rosterSlotId")),
            }
        )
    if not rows:
        return _empty_salaries()
    df = pd.DataFrame(rows)
    kickoff = pd.to_datetime(df["kickoff"], errors="coerce", utc=True)
    df["kickoff"] = kickoff.dt.tz_localize(None)
    df["salary"] = pd.to_numeric(df["salary"], errors="coerce")
    df = (
        df.dropna(subset=["salary"])
        .drop_duplicates(subset=["draft_group_id", "player_id", "salary"])
        .reset_index(drop=True)
    )
    return Salaries.validate(df[_COLUMNS])


def _dotnet_date(value: Any) -> str | None:
    """DK's ``/Date(1789676100000)/`` (or a bare epoch-ms) → ISO-8601 UTC."""
    if value is None:
        return None
    if isinstance(value, int | float):
        ms = int(value)
    else:
        match = re.search(r"-?\d+", str(value))
        if match is None:
            return None
        ms = int(match.group())
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def legacy_players_to_draftables(
    payload: Mapping[str, Any], draft_group_id: str, *, start: Any = None,
) -> dict[str, Any]:
    """The legacy lineup endpoint's payload, rewritten in the draftables shape.

    ``getavailableplayers`` lists one entry per player with the short keys
    the old lineup builder used, read off the real payload on 2026-09-18:
    ``fn``/``ln`` (name), ``pn`` (position), ``s`` (salary, 0 on the
    salary-free Tiers and Single Stat boards), ``pdkid`` (DK's player id,
    the API's ``playerDkId``), ``tid`` (the player's team id) beside
    ``htid``/``atid`` and ``htabbr``/``atabbr`` (the game's two teams),
    ``i`` (the injury designation, empty when healthy), ``pp`` (DK's
    probable-pitcher flag — the API's ``playerGameAttributes`` id 1),
    ``rosposid`` (the roster-slot id; on a Tiers board the tier), ``ppg``
    (the number the lobby shows beside a player — the API's draft-stat
    attribute 408), and ``tsid``, the game's key into ``teamList``, whose
    ``tz`` is the game's start as a .NET epoch. So the kickoff is the game's
    own, not the slate's; ``start`` (the slate's, from the lobby) is only
    the last resort. What the endpoint lacks is a showdown board's captain
    rows — each player appears once, at the flex price, and
    :func:`velocity.dfs.showdown.showdown_board` prices the captain at 1.5x
    from a single row as it already does for any player without one.
    Nothing is guessed: a player without a name or a price is dropped by
    :func:`normalize_draftables` exactly as before, and a salary of 0 is
    read as "no salary" so those boards take the tiered path. The raw
    payload rides along under ``_legacy`` so the banked JSON keeps DK's
    actual shape.
    """
    games_raw = payload.get("teamList") or {}
    games: dict[str, Mapping[str, Any]] = (
        {str(k): v for k, v in games_raw.items()} if isinstance(games_raw, Mapping)
        else {str(g.get("tsid")): g for g in games_raw if isinstance(g, Mapping)}
    )
    draftables: list[dict[str, Any]] = []
    for p in payload.get("playerList") or []:
        name = " ".join(str(part) for part in (p.get("fn"), p.get("ln")) if part).strip()
        game = games.get(str(p.get("tsid"))) or {}
        tid = None if p.get("tid") is None else str(p.get("tid"))
        home_id = game.get("htid", p.get("htid"))
        away_id = game.get("atid", p.get("atid"))
        home = game.get("ht") or p.get("htabbr")
        away = game.get("at") or p.get("atabbr")
        team = home if tid is not None and tid == str(home_id) else (
            away if tid is not None and tid == str(away_id) else None)
        competition = f"{away} @ {home}" if home and away else None
        salary = p.get("s")
        if salary in (0, "0"):
            salary = None  # a salary-free board, not a free player
        game_attrs = [{"id": 1, "value": "true"}] if p.get("pp") else []
        stat_attrs = ([{"id": 408, "value": p.get("ppg")}]
                      if p.get("ppg") is not None else [])
        draftables.append({
            "playerDkId": p.get("pdkid") or p.get("pid"),
            "playerId": p.get("pid"),
            "displayName": name or None,
            "position": p.get("pn"),
            "salary": salary,
            "teamAbbreviation": team,
            # The API spells a healthy player "None"; the legacy field is "".
            "status": p.get("i") or "None",
            "isDisabled": bool(p.get("IsDisabledFromDrafting")),
            "rosterSlotId": p.get("rosposid"),
            "playerGameAttributes": game_attrs,
            "draftStatAttributes": stat_attrs,
            "competition": {
                "name": competition,
                "startTime": _dotnet_date(game.get("tz")) or _dotnet_date(p.get("dgst"))
                or start,
            },
        })
    return {"draftables": draftables, "_source": "legacy", "_legacy": dict(payload)}


def _clean_suffix(suffix: Any) -> str:
    """DK's ``ContestStartTimeSuffix`` (" (Turbo)", " (Night)") → "Turbo"."""
    if not suffix:
        return ""
    return str(suffix).strip().strip("()").strip()


def game_type_names(lobby: Mapping[str, Any]) -> dict[str, str]:
    """Draft group id → DK's own game-type NAME, read off the contest list.

    The lobby names every format in plain English on its contests
    ("Classic", "Showdown Captain Mode", "Tiers", "Single Stat - Home Runs",
    "Snake Showdown"). That name is the honest format key: ``ContestTypeId``
    is a parallel id space that agrees with the game-type ids for the newer
    formats but not the oldest ones (MLB classic ships as 28, not 2), and DK
    also runs look-alike simulated formats ("Madden Showdown Captain Mode")
    that a numeric filter would have to know about separately.
    """
    names: dict[str, str] = {}
    for contest in lobby.get("Contests") or []:
        group = contest.get("dg")
        name = contest.get("gameType")
        if group is None or not name:
            continue
        names.setdefault(str(group), str(name))
    return names


def normalize_draft_groups(lobby: Mapping[str, Any]) -> pd.DataFrame:
    """The lobby's draft groups as a small frame: id, contest type, start, games.

    ``contest_type_id`` and ``game_type`` both distinguish the game styles
    (classic vs showdown etc.) — the id from the draft group, the name from
    the contests that reference it (:func:`game_type_names`). The collector
    banks them all and downstream picks what it prices. ``suffix`` is DK's
    own slate label ("Turbo", "Night", "Early", or a showdown's matchup;
    empty for the plain slates) — how the lobby tells groupings apart.
    """
    names = game_type_names(lobby)
    rows = [
        {
            "draft_group_id": str(g.get("DraftGroupId")),
            "contest_type_id": g.get("ContestTypeId"),
            "game_type": names.get(str(g.get("DraftGroupId"))),
            "start": g.get("StartDate"),
            "game_count": g.get("GameCount"),
            "tag": g.get("DraftGroupTag"),
            "suffix": _clean_suffix(g.get("ContestStartTimeSuffix")),
        }
        for g in lobby.get("DraftGroups") or []
        if g.get("DraftGroupId") is not None
    ]
    df = pd.DataFrame(rows, columns=["draft_group_id", "contest_type_id",
                                     "game_type", "start", "game_count", "tag",
                                     "suffix"])
    if not df.empty:
        start = pd.to_datetime(df["start"], errors="coerce", utc=True)
        df["start"] = start.dt.tz_localize(None)
    return df


def assign_draft_groups(
    groups_by_league: Mapping[str, Iterable[str]],
) -> dict[str, set[str]]:
    """Give each draft group to exactly one league — the most specific lobby.

    ``getcontests?sport=X`` does not reliably honour its own filter. Asked for
    WNBA on a night the league had a couple of playoff games, the lobby came
    back with 75 draft groups and 13,192 draftables — NFL and MLB boards, which
    the collector then stamped ``league="wnba"`` and banked as women's
    basketball salary history. NBA, entirely out of season, banked 2,826 tiered
    rows the same way. Downstream that is not merely noise: the salary archive
    is the DFS analogue of the closing-line archive, and a lineup built from it
    would price the wrong sport.

    No field in the payload says which sport a group belongs to, so ownership is
    decided by specificity instead: a lobby that lists a group *and* few others
    is a better claim than one that lists it among hundreds. A sport whose
    filter works keeps everything it returned; a sport whose lobby is really
    somebody else's keeps only what is unique to it. Ties break on the league
    name so the result never depends on iteration order.
    """
    ids_by_league = {league: set(ids) for league, ids in groups_by_league.items()}
    owner: dict[str, str] = {}
    for league, ids in ids_by_league.items():
        for group_id in ids:
            held = owner.get(group_id)
            if held is None or (len(ids), league) < (len(ids_by_league[held]), held):
                owner[group_id] = league
    out: dict[str, set[str]] = {league: set() for league in ids_by_league}
    for group_id, league in owner.items():
        out[league].add(group_id)
    return out


@dataclass
class DraftKingsClient:
    """Network client for the unauthenticated DK lobby + draftables endpoints."""

    def _get(self, url: str) -> Any:  # pragma: no cover - network
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
            return json.loads(resp.read())

    def lobby(self, sport: str) -> Any:  # pragma: no cover - network
        """Raw lobby payload for a DK sport code (contests + draft groups)."""
        return self._get(LOBBY_URL.format(sport=sport))

    def draftables(self, group_id: str, *, start: Any = None) -> Any:
        """One draft group's board in the draftables shape, whichever host served it.

        The draftables API first; when it refuses (:data:`_FALLBACK_STATUSES`)
        the legacy lineup endpoint on the www host answers instead, rewritten
        by :func:`legacy_players_to_draftables` with ``start`` (the slate's
        start, from the lobby) as the kickoff of last resort. The payload names its source
        under ``_source`` so the collector's log says which host is carrying
        the archive. A refusal on both hosts raises the API's error, with the
        legacy one chained.
        """
        try:
            payload = self._get(DRAFTABLES_URL.format(group_id=group_id))
        except urllib.error.HTTPError as exc:
            if exc.code not in _FALLBACK_STATUSES:
                raise
            try:
                legacy = self._get(LEGACY_PLAYERS_URL.format(group_id=group_id))
            except Exception as legacy_exc:
                raise exc from legacy_exc
            return legacy_players_to_draftables(legacy, str(group_id), start=start)
        if isinstance(payload, dict):
            payload["_source"] = "api"
        return payload
