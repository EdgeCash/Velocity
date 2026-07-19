"""Live-slate orchestration — a provider snapshot → staked recommendations.

The backtest proved the engine on a line *archive*; this runs the identical
engine on **today's board**. Given one internally-consistent provider snapshot
(events + their book lines, e.g. from The Odds API ``/odds``), it:

1. **canonicalizes sides** — a provider names spread/moneyline sides by team and
   totals by ``Over``/``Under``; :func:`build_slate` speaks ``home``/``away`` and
   ``over``/``under``. Because the event names its own home/away teams in the same
   snapshot, this remap is an exact, reliable lookup — no fuzzy matching.
2. **resolves teams to the model's universe** — the provider spells a team
   (``"Kansas City Chiefs"``) differently from the fitted ratings' key (``"KC"``).
   :func:`resolve_team` bridges that with an alias table plus a normalized
   fallback, and — critically — returns ``None`` rather than guess, so an
   unmatched game is *skipped and reported*, never silently mis-projected.
3. hands projections + canonical lines to :func:`build_slate` in **live mode**
   (``exclude_closing=False``), and returns the staked :class:`BetLog` plus the
   list of games it could not resolve.

The only real-world seam is (2); everything else is deterministic and offline
testable. CLV isn't measured here — that comes later, against the true closing
snapshot from the archive.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

import pandas as pd

from velocity.models.game_nfl import GameProjection
from velocity.wagering.bet_log import BetLog
from velocity.wagering.slate import SlateConfig, build_slate

# Full team name (normalized) → nflverse abbreviation, the key the NFL ratings
# use. NCAAF has no such fixed table (250+ teams); it leans on the normalized
# fallback and reports the misses.
NFL_TEAM_ALIASES: dict[str, str] = {
    "arizona cardinals": "ARI",
    "atlanta falcons": "ATL",
    "baltimore ravens": "BAL",
    "buffalo bills": "BUF",
    "carolina panthers": "CAR",
    "chicago bears": "CHI",
    "cincinnati bengals": "CIN",
    "cleveland browns": "CLE",
    "dallas cowboys": "DAL",
    "denver broncos": "DEN",
    "detroit lions": "DET",
    "green bay packers": "GB",
    "houston texans": "HOU",
    "indianapolis colts": "IND",
    "jacksonville jaguars": "JAX",
    "kansas city chiefs": "KC",
    "las vegas raiders": "LV",
    "los angeles chargers": "LAC",
    "los angeles rams": "LA",
    "miami dolphins": "MIA",
    "minnesota vikings": "MIN",
    "new england patriots": "NE",
    "new orleans saints": "NO",
    "new york giants": "NYG",
    "new york jets": "NYJ",
    "philadelphia eagles": "PHI",
    "pittsburgh steelers": "PIT",
    "san francisco 49ers": "SF",
    "seattle seahawks": "SEA",
    "tampa bay buccaneers": "TB",
    "tennessee titans": "TEN",
    "washington commanders": "WAS",
}

_TOTAL_SIDES = {"over": "over", "under": "under"}


def _normalize(name: str) -> str:
    """Lowercase and strip to alphanumerics for tolerant name comparison."""
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def resolve_team(
    name: str,
    known_teams: Iterable[str],
    aliases: dict[str, str] | None = None,
) -> str | None:
    """Map a provider team name to the model's rating key, or ``None`` if unsure.

    Resolution order: exact key → alias table (by normalized name) → unique
    normalized match against ``known_teams``. Returning ``None`` on ambiguity or
    a miss is deliberate: a wrong match silently mis-prices a game, so we skip and
    report instead.
    """
    known = list(known_teams)
    known_set = set(known)
    if name in known_set:
        return name

    aliases = NFL_TEAM_ALIASES if aliases is None else aliases
    norm = _normalize(name)
    # Normalize the alias keys too, so "Kansas City Chiefs" and "kansascitychiefs"
    # both resolve regardless of how the table is written.
    aliased = {_normalize(k): v for k, v in aliases.items()}.get(norm)
    if aliased is not None and aliased in known_set:
        return aliased

    # Unique normalized match (handles punctuation/casing drift).
    matches = [team for team in known if _normalize(team) == norm]
    if len(matches) == 1:
        return matches[0]
    return None


def canonicalize_sides(lines: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Remap provider side labels to ``home``/``away``/``over``/``under``.

    Spread/moneyline sides carry the team name; a total's sides are ``Over`` /
    ``Under``. Using each event's own home/away names (same snapshot), this is an
    exact per-game lookup. Rows whose side can't be mapped are dropped.
    """
    if lines.empty:
        return lines.copy()
    home_by_game = dict(zip(events["game_id"].astype(str), events["home_team"], strict=False))
    away_by_game = dict(zip(events["game_id"].astype(str), events["away_team"], strict=False))

    def _side(row: Mapping[Any, Any]) -> str | None:
        raw = str(row["side"])
        low = raw.strip().lower()
        if low in _TOTAL_SIDES:
            return _TOTAL_SIDES[low]
        gid = str(row["game_id"])
        if raw == home_by_game.get(gid):
            return "home"
        if raw == away_by_game.get(gid):
            return "away"
        return None

    out = lines.copy()
    out["side"] = [_side(row) for row in out.to_dict("records")]
    return out[out["side"].notna()].reset_index(drop=True)


def build_live_slate(
    events: pd.DataFrame,
    lines: pd.DataFrame,
    project: Callable[[str, str], GameProjection],
    known_teams: Iterable[str],
    config: SlateConfig | None = None,
    aliases: dict[str, str] | None = None,
) -> tuple[BetLog, list[dict[str, str]]]:
    """Run the wagering engine on one live snapshot; return the log and any skips.

    ``project(home_key, away_key)`` builds a :class:`GameProjection` from the
    fitted model. ``known_teams`` is the model's rating universe (e.g.
    ``ratings.teams``). Games whose teams don't resolve are skipped and returned
    in the second element so the caller can surface them.
    """
    config = config or SlateConfig(exclude_closing=False)
    known = list(known_teams)

    projections: dict[str, GameProjection] = {}
    unresolved: list[dict[str, str]] = []
    for event in events.to_dict("records"):
        gid = str(event["game_id"])
        home = resolve_team(str(event["home_team"]), known, aliases)
        away = resolve_team(str(event["away_team"]), known, aliases)
        if home is None or away is None:
            unresolved.append(
                {
                    "game_id": gid,
                    "home_team": str(event["home_team"]),
                    "away_team": str(event["away_team"]),
                    "reason": "unresolved home" if home is None else "unresolved away",
                }
            )
            continue
        projections[gid] = project(home, away)

    canonical = canonicalize_sides(lines, events)
    canonical = canonical[canonical["game_id"].astype(str).isin(projections)]
    games_min = events[["game_id", "kickoff"]].copy()
    games_min["game_id"] = games_min["game_id"].astype(str)

    log = build_slate(projections, canonical, games_min, config)
    return log, unresolved


def slate_to_frame(log: BetLog) -> pd.DataFrame:
    """Render a :class:`BetLog` as a readable slate table (one row per staked bet)."""
    rows = [
        {
            "game_id": bet.game_id,
            "market": bet.market,
            "side": bet.side,
            "point": bet.point,
            "book": bet.book,
            "price": bet.price,
            "p_model": round(bet.p_model, 4),
            "stake": round(bet.stake, 4),
        }
        for bet in log
    ]
    cols = ["game_id", "market", "side", "point", "book", "price", "p_model", "stake"]
    return pd.DataFrame(rows, columns=cols)
