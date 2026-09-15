"""ESPN ingest adapter — the keyless injury report, for every league we price.

Injuries reached this system from one place: FantasyPros, NFL only. So the
intel layer's availability signals — the ones that veto a bet when the player
it depends on is not playing — abstained on **every non-NFL league**. MLB,
NCAAF, WNBA, NCAAB and NHL cards were priced with no idea who was unavailable,
which on a baseball card means no idea that a listed starter is on the 60-day
IL.

ESPN publishes an injury report per sport at
``site.api.espn.com/apis/site/v2/sports/{sport}/{league}/injuries``. It needs
no key, no account and no quota, and it covers all six leagues. The repo had
written ESPN off — ``build_wnba_box.py`` records that "ESPN's own edge 403s
datacenter IPs" and routes around it through a mirror — but that is true of the
bulk box-score downloads, not of this API, which answers a datacenter IP
normally. Probed across all six leagues on 2026-09-14: HTTP 200 everywhere.

Two layers, kept strictly separate so the test gate stays offline:

* ``normalize_injuries`` / ``resolve_injury_teams`` — **pure** functions over a
  frozen payload shape.
* :class:`ESPNClient` — the network layer, and it is genuinely all it is:
  there is no credential to configure.

Convention notes:

* **Status vocabulary is per sport.** Football says ``Out`` / ``Questionable``
  / ``Injured Reserve``; baseball says ``10-Day-IL`` / ``15-Day-IL`` /
  ``60-Day-IL``; basketball says ``Day-To-Day``. :data:`OUT_STATUSES` and
  :data:`_IL_PATTERN` cover all of it, and a status we have never seen is
  treated as **available** — an unknown word is not evidence that someone is
  out, and inventing an out would veto a good bet.
* **Teams arrive in ESPN's own key space.** The payload carries both an
  abbreviation (``ARI``) and a display name ("Arizona Diamondbacks"), because
  our leagues key differently — NFL by nflverse abbreviation, MLB and WNBA by
  full name, college by school. :func:`resolve_injury_teams` maps both onto a
  model's rating keys through the same alias machinery every other venue uses,
  and reports what it could not resolve rather than guessing.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd

_BASE = "https://site.api.espn.com/apis/site/v2/sports"
# Depth charts live on the core API, not the site one, and are keyed by season.
_CORE_BASE = "https://sports.core.api.espn.com/v2"
_FETCH_TIMEOUT = 60
# An HONEST, identifying User-Agent — and this matters more than it looks.
#
# ESPN's edge refuses browser-impersonating agents from a datacenter IP: a
# "Mozilla/5.0 ..." string 403s every time, because a real browser running in
# a data center is exactly the shape of a scraper. A UA that says what it is
# and where to complain is served normally. Measured 2026-09-14, same host,
# same second: "Mozilla/5.0 (X11; Linux x86_64) velocity-datasets" -> 403,
# this string -> 200.
#
# That is almost certainly the whole of the "ESPN's own edge 403s datacenter
# IPs" note in build_wnba_box.py, which sets exactly that Mozilla string. The
# block was never about the IP. It was about pretending to be a browser.
#
# So: do not "fix" a future 403 here by making this look more like a browser.
# That is the arms race docs/DATA_PROVIDERS.md declines to enter, and it is
# the thing being refused.
_USER_AGENT = "velocity-datasets/1.0 (+https://github.com/EdgeCash/Velocity)"

# Our league key → ESPN's ``{sport}/{league}`` path segment.
ESPN_LEAGUE_PATHS: Mapping[str, str] = {
    "nfl": "football/nfl",
    "ncaaf": "football/college-football",
    "mlb": "baseball/mlb",
    "wnba": "basketball/wnba",
    "ncaab": "basketball/mens-college-basketball",
    "nhl": "hockey/nhl",
}

# Statuses that mean the player is genuinely unavailable. Deliberately does NOT
# include "questionable" or "day-to-day": those are *probably playing*, and the
# intel layer's own contract is that an availability signal vetoes rather than
# nudges. Treating a game-time decision as an out would veto bets on players
# who take the field.
OUT_STATUSES = frozenset({
    "out",
    "doubtful",
    "injured reserve",
    "ir",
    "suspension",
    "suspended",
    "bereavement",
    "personal",
    "not with team",
})
# Baseball's injured list, whose length varies ("7-Day IL", "10-Day-IL",
# "15-Day-IL", "60-Day-IL"). Matched on shape so a new list length — MLB has
# changed these repeatedly — does not quietly read as available.
_IL_TOKENS = ("-day-il", "-day il", "day-to-day-il")

INJURY_COLUMNS = [
    "league", "player_id", "player_name", "position",
    "team_abbreviation", "team_name", "status", "is_out",
    "injury_type", "injury_location", "injury_detail", "return_date",
    "updated", "comment",
]


def is_out_status(status: object) -> bool:
    """Whether ``status`` means genuinely unavailable (see :data:`OUT_STATUSES`).

    An unrecognized status reads as **available**. That asymmetry is
    deliberate: a missing out costs us a signal, an invented one vetoes a bet
    on a player who is starting.
    """
    text = str(status or "").strip().lower()
    if not text:
        return False
    if text in OUT_STATUSES:
        return True
    return any(token in text for token in _IL_TOKENS)


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_injuries(payload: Any, league: str = "") -> pd.DataFrame:
    """Flatten an ESPN ``/injuries`` response into one tidy row per player.

    The payload nests ``injuries[team].injuries[entry].athlete``; this walks it
    into the flat frame the intel layer consumes, carrying the clinical detail
    ESPN supplies alongside the designation — type, body location, and the
    expected return date, which is the part a weekly report never has.

    Rows with no athlete name are dropped (an entry naming nobody adjusts
    nothing). Every other field is optional and lands as ``None``.
    """
    teams = payload.get("injuries") if isinstance(payload, Mapping) else payload
    rows: list[dict[str, object]] = []
    for team in teams or []:
        if not isinstance(team, Mapping):
            continue
        team_fallback = _text(team.get("displayName"))
        for entry in team.get("injuries") or []:
            if not isinstance(entry, Mapping):
                continue
            athlete = entry.get("athlete") or {}
            if not isinstance(athlete, Mapping):
                continue
            name = _text(athlete.get("displayName"))
            if name is None:
                continue
            position = athlete.get("position") or {}
            team_block = athlete.get("team") or {}
            details = entry.get("details") or {}
            status = _text(entry.get("status"))
            rows.append({
                "league": league,
                "player_id": _text(entry.get("id")),
                "player_name": name,
                "position": _text(
                    position.get("abbreviation") if isinstance(position, Mapping) else None
                ),
                "team_abbreviation": _text(
                    team_block.get("abbreviation") if isinstance(team_block, Mapping) else None
                ),
                "team_name": _text(
                    team_block.get("displayName") if isinstance(team_block, Mapping) else None
                ) or team_fallback,
                "status": status,
                "is_out": is_out_status(status),
                "injury_type": _text(
                    details.get("type") if isinstance(details, Mapping) else None
                ),
                "injury_location": _text(
                    details.get("location") if isinstance(details, Mapping) else None
                ),
                "injury_detail": _text(
                    details.get("detail") if isinstance(details, Mapping) else None
                ),
                "return_date": _text(
                    details.get("returnDate") if isinstance(details, Mapping) else None
                ),
                "updated": _text(entry.get("date")),
                "comment": _text(entry.get("shortComment")),
            })
    return pd.DataFrame(rows, columns=INJURY_COLUMNS)


def resolve_injury_teams(
    injuries: pd.DataFrame,
    known_teams: Iterable[str],
    league: str | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Add a ``team`` column in the model's rating keys; report what did not resolve.

    Our leagues key teams differently — NFL by nflverse abbreviation, MLB and
    WNBA by full name, college by school — and ESPN supplies both spellings, so
    both are offered to the same alias machinery every venue board uses
    (:func:`~velocity.wagering.live.exchange_aliases`). The abbreviation is
    tried first because it is exact where it works at all.

    ``league`` scopes the report to one league's rows before resolving, and a
    caller with a mixed frame should always pass it. Team keys are only unique
    *within* a league: ESPN calls the Cardinals ``ARI`` and the Diamondbacks
    ``ARI`` too, so resolving a multi-league bank in one pass hands one of them
    the other's injury list — with no collision to detect, because there is
    only one ``ARI`` to go around.

    Resolution is **collision-checked**. The alias machinery falls back to
    prefix matching, which is what lets "San Jose St." find "San Jose State" —
    and also what makes "Arizona Cardinals" match "Arizona Diamondbacks" when a
    report is measured against the wrong league's universe. Two distinct ESPN
    teams mapping to one model team is that failure's signature, so both are
    refused rather than one of them silently inheriting the other's injury
    list. Same-city pairs (Cubs/White Sox, Yankees/Mets, Giants/Jets) resolve
    exactly and are untouched by this.

    Returns ``(frame, unresolved)``. Unresolved rows are **dropped**: the intel
    layer looks up outs by team, so a row under a team name no model knows is
    invisible anyway — but silently invisible is how a whole league's report
    goes missing without anyone noticing, hence the list.
    """
    from velocity.wagering.live import exchange_aliases

    if league is not None and "league" in injuries.columns:
        injuries = injuries[injuries["league"].astype(str).str.lower() == league.lower()]
    if injuries.empty:
        return injuries.assign(team=pd.Series(dtype=str)), []

    known = list(known_teams)
    abbrevs = {a: a for a in injuries["team_abbreviation"].dropna().astype(str).unique()}
    names = {n: n for n in injuries["team_name"].dropna().astype(str).unique()}
    by_abbrev = _without_collisions(exchange_aliases(abbrevs, known))
    by_name = _without_collisions(exchange_aliases(names, known))

    # ``to_dict("records")`` hands back Hashable keys, so the row type matches
    # the rest of the codebase's per-row helpers rather than narrowing to str.
    def _team(row: Mapping[Any, Any]) -> str | None:
        abbrev = row.get("team_abbreviation")
        name = row.get("team_name")
        return (by_abbrev.get(str(abbrev)) if abbrev else None) or (
            by_name.get(str(name)) if name else None
        )

    out = injuries.copy()
    out["team"] = [_team(row) for row in out.to_dict("records")]
    unresolved = sorted({
        str(row["team_name"] or row["team_abbreviation"])
        for row in out[out["team"].isna()].to_dict("records")
    })
    return out[out["team"].notna()].reset_index(drop=True), unresolved


def _without_collisions(aliases: Mapping[str, str]) -> dict[str, str]:
    """Drop every mapping whose target is claimed by more than one source name.

    One model team cannot be two ESPN teams. When it looks like it is, a prefix
    match has fired where an exact one should have, and there is no way to tell
    from here which source was the real one — so neither is kept.
    """
    claimed: dict[str, list[str]] = {}
    for source, team in aliases.items():
        claimed.setdefault(team, []).append(source)
    return {
        source: team
        for team, sources in claimed.items()
        if len(sources) == 1
        for source in sources
    }


@dataclass
class ESPNClient:
    """Network client for ESPN's public site API. No key, no account, no quota.

    Kept as a class for symmetry with the keyed adapters, and so a timeout or
    base URL can be overridden in one place.
    """

    base: str = _BASE
    timeout: float = _FETCH_TIMEOUT

    def _get(self, path: str, **params: object) -> Any:  # pragma: no cover - network
        query = {k: v for k, v in params.items() if v is not None}
        url = f"{self.base}/{path.lstrip('/')}"
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            return json.loads(resp.read())

    def raw_injuries(self, league: str) -> Any:  # pragma: no cover - network
        """The raw ``/injuries`` payload for one of :data:`ESPN_LEAGUE_PATHS`."""
        try:
            path = ESPN_LEAGUE_PATHS[league]
        except KeyError:
            raise ValueError(
                f"no ESPN path for league {league!r}; known: {sorted(ESPN_LEAGUE_PATHS)}"
            ) from None
        return self._get(f"{path}/injuries")

    def injuries(self, league: str) -> pd.DataFrame:  # pragma: no cover - network
        """One league's injury report, normalized."""
        return normalize_injuries(self.raw_injuries(league), league)

    def teams(self, league: str) -> pd.DataFrame:  # pragma: no cover - network
        """The league's teams — the ids the roster and depth-chart routes take."""
        return normalize_teams(self._get(f"{self._path(league)}/teams"))

    def roster(self, league: str, team_id: object) -> pd.DataFrame:  # pragma: no cover - network
        """One team's roster, normalized."""
        return normalize_roster(
            self._get(f"{self._path(league)}/teams/{team_id}/roster"), league
        )

    def depth_chart(  # pragma: no cover - network
        self, league: str, team_id: object, season: int,
        roster: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """One team's depth chart, named from ``roster``.

        Lives on the **core** API rather than the site one, and is keyed by
        season. Leagues without the route (WNBA 500s, college football 400s)
        raise; the collector reports that rather than inventing an ordering.
        """
        try:
            sport, league_path = ESPN_LEAGUE_PATHS[league].split("/", 1)
        except KeyError:
            raise ValueError(f"no ESPN path for league {league!r}") from None
        url = (f"{_CORE_BASE}/sports/{sport}/leagues/{league_path}/seasons/{season}"
               f"/teams/{team_id}/depthcharts")
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            payload = json.loads(resp.read())
        return normalize_depth_chart(payload, roster, league)

    def _path(self, league: str) -> str:
        try:
            return ESPN_LEAGUE_PATHS[league]
        except KeyError:
            raise ValueError(
                f"no ESPN path for league {league!r}; known: {sorted(ESPN_LEAGUE_PATHS)}"
            ) from None


# ---------------------------------------------------------------------------
# Rosters and depth charts — who is on the team, and who is ahead of whom
# ---------------------------------------------------------------------------
#
# The NFL starter map (velocity/features/starters.py) works out each team's QB1
# by *inference*: it reads FantasyPros' projected passing yards and takes the
# busiest passer. That is a good proxy and it fails in the obvious place —
# whenever the projections are stale or a backup is projected high, the
# inference disagrees with the depth chart, and the depth chart is right.
#
# ESPN states it outright. Two endpoints, joined locally:
#
# * the **roster** (site API, one call per team) — every athlete with an id,
#   name, position and jersey;
# * the **depth chart** (core API, one call per team) — per position, athlete
#   ids in rank order, and *ids only*: every athlete is a ``$ref`` link. Left to
#   the API that is one fetch per player, hundreds a team. Joined against the
#   roster it is free.
#
# So a league costs 2 calls per team plus a teams listing — 65 for the NFL,
# against no quota at all.
#
# Depth charts exist for NFL, MLB and NHL. WNBA 500s and college football 400s
# on that route, so those leagues get rosters only; ``depth_chart`` reports it
# rather than inventing an ordering.

ROSTER_COLUMNS = [
    "league", "team_id", "team_abbreviation", "team_name",
    "athlete_id", "player_name", "position", "jersey", "status_group",
]
DEPTH_COLUMNS = [
    "league", "team_id", "team_abbreviation", "team_name",
    "unit", "position", "position_name", "rank", "athlete_id", "player_name",
]

# Depth-chart formations that are not a team's ordinary ordering. ESPN lists
# several per team ("Base 4-3 D", "Nickel", "3WR 1TE"); the plain one is what a
# starter question means, and taking the first item blindly picks whichever
# formation happens to be first in the payload.
_PREFERRED_UNITS = ("depth chart", "3wr 1te", "base 4-3 d", "base 3-4 d")


def normalize_teams(payload: Any) -> pd.DataFrame:
    """The league's teams — ``team_id``/``abbreviation``/``displayName``.

    The id is what the roster and depth-chart routes are keyed by, so this is
    the listing every other call in this section starts from.
    """
    rows: list[dict[str, object]] = []
    sports = (payload or {}).get("sports") if isinstance(payload, Mapping) else None
    for sport in sports or []:
        for league in sport.get("leagues") or []:
            for entry in league.get("teams") or []:
                team = entry.get("team") if isinstance(entry, Mapping) else None
                if not isinstance(team, Mapping) or team.get("id") is None:
                    continue
                rows.append({
                    "team_id": str(team["id"]),
                    "team_abbreviation": _text(team.get("abbreviation")),
                    "team_name": _text(team.get("displayName")),
                })
    return pd.DataFrame(rows, columns=["team_id", "team_abbreviation", "team_name"])


def normalize_roster(payload: Any, league: str = "") -> pd.DataFrame:
    """One team's roster → a row per athlete.

    ``status_group`` carries ESPN's own bucketing ("offense", "defense",
    "injuredReserveOrOut", "practiceSquad"), which is coarse availability
    evidence in its own right and is exactly what a depth chart does not say.
    """
    if not isinstance(payload, Mapping):
        return pd.DataFrame(columns=ROSTER_COLUMNS)
    team = payload.get("team") or {}
    team_id = _text(team.get("id")) if isinstance(team, Mapping) else None
    team_abbr = _text(team.get("abbreviation")) if isinstance(team, Mapping) else None
    team_name = _text(team.get("displayName")) if isinstance(team, Mapping) else None

    rows: list[dict[str, object]] = []
    for group in payload.get("athletes") or []:
        if not isinstance(group, Mapping):
            continue
        status = _text(group.get("position"))
        # A flat roster (no groups) puts athletes at the top level instead.
        items = group.get("items") if "items" in group else [group]
        for athlete in items or []:
            if not isinstance(athlete, Mapping) or athlete.get("id") is None:
                continue
            name = _text(athlete.get("displayName")) or _text(athlete.get("fullName"))
            if name is None:
                continue
            position = athlete.get("position") or {}
            rows.append({
                "league": league,
                "team_id": team_id,
                "team_abbreviation": team_abbr,
                "team_name": team_name,
                "athlete_id": str(athlete["id"]),
                "player_name": name,
                "position": _text(
                    position.get("abbreviation") if isinstance(position, Mapping) else None
                ),
                "jersey": _text(athlete.get("jersey")),
                "status_group": status,
            })
    return pd.DataFrame(rows, columns=ROSTER_COLUMNS)


def _athlete_id_from_ref(ref: object) -> str | None:
    """The trailing athlete id out of a core-API ``$ref`` URL."""
    match = re.search(r"/athletes/(\d+)", str(ref or ""))
    return match.group(1) if match else None


def normalize_depth_chart(
    payload: Any, roster: pd.DataFrame | None = None, league: str = ""
) -> pd.DataFrame:
    """One team's depth chart → a row per (unit, position, rank, athlete).

    The payload names athletes only by ``$ref``, so ``roster`` (that team's
    :func:`normalize_roster` frame) supplies the names. Without it the rows
    still carry ids and ranks — usable for a join, and honest about what is
    missing — rather than being dropped.

    Rows are returned in ESPN's own rank order per position; rank 1 is the
    starter.
    """
    if not isinstance(payload, Mapping):
        return pd.DataFrame(columns=DEPTH_COLUMNS)
    names: Mapping[str, str] = {}
    team_id = team_abbr = team_name = None
    if roster is not None and not roster.empty:
        names = dict(
            zip(roster["athlete_id"].astype(str), roster["player_name"].astype(str),
                strict=False)
        )
        first = roster.iloc[0]
        team_id = _text(first.get("team_id"))
        team_abbr = _text(first.get("team_abbreviation"))
        team_name = _text(first.get("team_name"))

    rows: list[dict[str, object]] = []
    for item in payload.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        unit = _text(item.get("name"))
        for key, block in (item.get("positions") or {}).items():
            if not isinstance(block, Mapping):
                continue
            position = block.get("position") or {}
            for entry in block.get("athletes") or []:
                if not isinstance(entry, Mapping):
                    continue
                athlete_id = _athlete_id_from_ref(
                    (entry.get("athlete") or {}).get("$ref")
                    if isinstance(entry.get("athlete"), Mapping) else None
                )
                if athlete_id is None:
                    continue
                rows.append({
                    "league": league,
                    "team_id": team_id,
                    "team_abbreviation": team_abbr,
                    "team_name": team_name,
                    "unit": unit,
                    "position": str(key).upper(),
                    "position_name": _text(
                        position.get("displayName") if isinstance(position, Mapping) else None
                    ),
                    "rank": entry.get("rank"),
                    "athlete_id": athlete_id,
                    "player_name": names.get(athlete_id),
                })
    frame = pd.DataFrame(rows, columns=DEPTH_COLUMNS)
    if not frame.empty:
        frame["rank"] = pd.to_numeric(frame["rank"], errors="coerce")
    return frame


def depth_by_position(
    depth: pd.DataFrame, position: str, *, team_column: str = "team_abbreviation"
) -> dict[str, list[str]]:
    """``{team: [player names]}`` in depth order for one position.

    The shape :func:`velocity.features.starters.starter_map` already consumes,
    so an ESPN depth chart can stand in for the FantasyPros workload inference
    without touching anything downstream of it.

    A team listing several formations contributes its **ordinary** one — ESPN
    files "Nickel" and "3WR 1TE" beside the plain chart, and taking whichever
    came first in the payload would silently answer a different question.
    Players the roster could not name are skipped.
    """
    if depth.empty:
        return {}
    wanted = depth[depth["position"].astype(str).str.upper() == position.upper()]
    wanted = wanted[wanted["player_name"].notna()]
    if wanted.empty:
        return {}
    out: dict[str, list[str]] = {}
    for team, rows in wanted.groupby(team_column):
        units = list(rows["unit"].dropna().unique())
        preferred = next(
            (u for p in _PREFERRED_UNITS for u in units if str(u).strip().lower() == p),
            units[0] if units else None,
        )
        scoped = rows[rows["unit"] == preferred] if preferred is not None else rows
        ordered = scoped.sort_values("rank", na_position="last")
        names = [str(n) for n in ordered["player_name"] if str(n).strip()]
        if names:
            out[str(team)] = names
    return out
