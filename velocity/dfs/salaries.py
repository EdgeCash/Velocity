"""DraftKings salary ingest — draft groups + draftables → canonical salary rows.

DraftKings exposes its lobby unauthenticated: ``getcontests?sport=NFL`` lists
the draft groups (slates), and the draftables endpoint lists every player in a
group with salary, position, team, and game. Two layers, kept strictly separate
so the test gate stays offline (the same pattern as the odds ingest):

* :func:`normalize_draftables` — **pure**: flattens a draftables payload onto
  the canonical :class:`Salaries` schema, one row per player per draft group
  (DK repeats a player once per eligible roster slot — deduped here).
* :class:`DraftKingsClient` — the network layer. No key needed; a browser-ish
  User-Agent is set because DK rejects the default urllib one.

Salary snapshots are banked to PRIVATE Actions artifacts alongside the odds
archives (docs/FOOTBALL_CUTOVER.md §5a) — salary-vs-projection history is the
DFS equivalent of the CLV archive, so it starts accruing before the season.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd
import pandera.pandas as pa
from pandera.pandas import Field
from pandera.typing import Series

LOBBY_URL = "https://www.draftkings.com/lobby/getcontests?sport={sport}"
DRAFTABLES_URL = (
    "https://api.draftkings.com/draftgroups/v1/draftgroups/{group_id}/draftables?format=json"
)
_FETCH_TIMEOUT = 60
# DK serves these endpoints to browsers; the default urllib UA gets rejected.
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"

# DK sport codes for the football leagues.
SPORT_CODES = {"nfl": "NFL", "ncaaf": "CFB"}


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

    class Config:
        coerce = True


_COLUMNS = [
    "draft_group_id", "player_id", "player_name", "position", "salary",
    "team", "competition", "kickoff", "status",
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
        }
    )
    return Salaries.validate(empty)


def normalize_draftables(payload: Mapping[str, Any], draft_group_id: str) -> pd.DataFrame:
    """Flatten a DK draftables payload onto the canonical :class:`Salaries` schema.

    One row per player: DK lists a player once per eligible roster slot (an RB
    appears again for FLEX), so rows are deduped on the player id at the same
    salary. A row without a name or salary is dropped, never guessed.
    """
    rows: list[dict[str, object]] = []
    for d in payload.get("draftables") or []:
        name = d.get("displayName")
        salary = d.get("salary")
        if name is None or salary is None:
            continue
        player_id = d.get("playerDkId") or d.get("playerId") or name
        competition = d.get("competition") or {}
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
        .drop_duplicates(subset=["draft_group_id", "player_id"])
        .reset_index(drop=True)
    )
    return Salaries.validate(df[_COLUMNS])


def normalize_draft_groups(lobby: Mapping[str, Any]) -> pd.DataFrame:
    """The lobby's draft groups as a small frame: id, contest type, start, games.

    ``contest_type_id`` distinguishes the game styles (classic vs showdown
    etc.); the collector banks them all and downstream picks what it prices.
    """
    rows = [
        {
            "draft_group_id": str(g.get("DraftGroupId")),
            "contest_type_id": g.get("ContestTypeId"),
            "start": g.get("StartDate"),
            "game_count": g.get("GameCount"),
            "tag": g.get("DraftGroupTag"),
        }
        for g in lobby.get("DraftGroups") or []
        if g.get("DraftGroupId") is not None
    ]
    df = pd.DataFrame(rows, columns=["draft_group_id", "contest_type_id", "start",
                                     "game_count", "tag"])
    if not df.empty:
        start = pd.to_datetime(df["start"], errors="coerce", utc=True)
        df["start"] = start.dt.tz_localize(None)
    return df


@dataclass
class DraftKingsClient:
    """Network client for the unauthenticated DK lobby + draftables endpoints."""

    def _get(self, url: str) -> Any:  # pragma: no cover - network
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
            return json.loads(resp.read())

    def lobby(self, sport: str) -> Any:  # pragma: no cover - network
        """Raw lobby payload for a DK sport code (contests + draft groups)."""
        return self._get(LOBBY_URL.format(sport=sport))

    def draftables(self, group_id: str) -> Any:  # pragma: no cover - network
        """Raw draftables payload for one draft group."""
        return self._get(DRAFTABLES_URL.format(group_id=group_id))
