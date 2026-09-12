"""Resolve final scores onto a persisted slate's game ids, for grading.

The slate the runner archives is keyed by **The Odds API** event id; final
scores come from the league's schedule feed (nflverse/CFBD/statsapi), keyed by
its own game id. The two id spaces don't line up, so grading needs a bridge:
match each slate game to a final by the resolved home/away **team codes** and
the **kickoff instant** (both feeds carry team names and a time). That's the
same tolerant team resolution the slate itself uses, so a game that resolves
for pricing resolves for grading.

Two things the first version of this bridge got wrong, both of which showed up
as a record chain that would not fill:

* **It keyed on the calendar date.** The slate's kickoff is UTC (The Odds API's
  ``commence_time``); nflverse builds its kickoff by pasting a game date onto a
  local start time, so it is naive **Eastern**. Any game starting at or after
  8pm Eastern therefore lands on the next UTC day and could never match — every
  Thursday, Sunday and Monday night game, forever. NFL finished a whole season
  opener with zero settled rows.
* **The key held no clock, and later rows overwrote earlier ones.** Baseball
  plays the same pair twice on one date routinely, so a doubleheader collapsed
  onto a single entry and a slate row could be graded, recorded and settled
  against the other game's score.

So matching is now on the pair plus the nearest kickoff, one-to-one: each
schedule game is consumed by at most one slate game, closest first. Feeds whose
clock is local rather than UTC declare it with ``schedule_tz`` and are converted
before anything is compared.

Pure and offline-testable: :func:`finals_for_slate` takes the persisted games
map (``game_id`` + team names + kickoff) and a ``Games``-shaped schedule frame
and returns a finals frame keyed by the slate's ``game_id`` — exactly what
:func:`velocity.report.scorecard.grade_slate` wants. Unmatched or unplayed games
are dropped (they simply stay ungraded).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from velocity.wagering.live import NFL_TEAM_ALIASES, resolve_team

# How far apart two feeds' idea of one kickoff may be and still be one game.
# Wide enough to absorb a feed that reports a local clock we were not told
# about (a whole-day skew at the extreme), narrow enough that it never reaches
# the same pair's *next* meeting — and within the window, nearest-first
# assignment is what actually separates a doubleheader, not the bound.
DEFAULT_TOLERANCE = pd.Timedelta(hours=30)


def _pair(
    away: str, home: str, codes: list[str], aliases: Mapping[str, str]
) -> tuple[str, str] | None:
    """``(away_code, home_code)``, or ``None`` if either team doesn't resolve."""
    a = resolve_team(str(away), codes, dict(aliases))
    h = resolve_team(str(home), codes, dict(aliases))
    if a is None or h is None:
        return None
    return (a, h)


def _instant(when: Any, schedule_tz: str | None = None) -> Any:
    """One kickoff as a tz-naive UTC instant (``NaT`` when unusable).

    ``schedule_tz`` names the zone a feed's naive timestamps are written in.
    nflverse is the case that matters: its ``gametime`` is Eastern, so without
    this the same kickoff reads four or five hours earlier than the slate's.
    """
    stamp = pd.Timestamp(when) if when is not None and pd.notna(when) else pd.NaT
    if stamp is pd.NaT or pd.isna(stamp):
        return pd.NaT
    if stamp.tzinfo is not None:
        return stamp.tz_convert("UTC").tz_localize(None)
    if schedule_tz:
        return stamp.tz_localize(schedule_tz).tz_convert("UTC").tz_localize(None)
    return stamp


def finals_for_slate(
    games_map: pd.DataFrame,
    schedule: pd.DataFrame,
    *,
    aliases: Mapping[str, str] | None = None,
    schedule_tz: str | None = None,
    tolerance: pd.Timedelta = DEFAULT_TOLERANCE,
) -> pd.DataFrame:
    """Map schedule-feed finals onto the slate's ``game_id`` by team pair + kickoff.

    ``games_map`` has ``game_id``, ``home_team``, ``away_team``, ``kickoff`` (the
    slate side, UTC); ``schedule`` is a ``Games``-shaped frame (schedule-feed
    side) with team names, ``kickoff``, and ``home_score``/``away_score``.
    ``schedule_tz`` declares the zone of a schedule feed that writes a local
    clock. Returns ``[game_id, home_score, away_score]`` for every slate game
    matched to a played game.

    Each played game is claimed by at most one slate game: candidate pairings
    are ranked by how far apart the two kickoffs are and taken closest first,
    so a doubleheader's two games separate instead of collapsing.
    """
    alias_map = dict(NFL_TEAM_ALIASES if aliases is None else aliases)
    codes = list(alias_map.values())
    columns = ["game_id", "home_score", "away_score"]

    played: list[tuple[tuple[str, str], pd.Timestamp, float, float]] = []
    for row in schedule.to_dict("records"):
        pair = _pair(row["away_team"], row["home_team"], codes, alias_map)
        hs, as_ = row.get("home_score"), row.get("away_score")
        if pair is None or pd.isna(hs) or pd.isna(as_):
            continue
        played.append((pair, _instant(row.get("kickoff"), schedule_tz), float(hs), float(as_)))
    if not played:
        return pd.DataFrame(columns=columns)

    wanted: list[tuple[str, tuple[str, str], pd.Timestamp]] = []
    for game in games_map.to_dict("records"):
        pair = _pair(game["away_team"], game["home_team"], codes, alias_map)
        if pair is not None:
            wanted.append((str(game["game_id"]), pair, _instant(game.get("kickoff"))))
    if not wanted:
        return pd.DataFrame(columns=columns)

    # Rank every plausible (slate game, played game) pairing by kickoff gap.
    # A pairing where either side has no usable clock keeps an infinite gap:
    # it is allowed, but only after every timed pairing has had its pick, so a
    # missing time can never steal a game from a match that knows its own.
    candidates: list[tuple[float, int, int, int]] = []
    for want_idx, (_gid, pair, when) in enumerate(wanted):
        for play_idx, (other, played_when, _hs, _as) in enumerate(played):
            if pair != other:
                continue
            if pd.isna(when) or pd.isna(played_when):
                gap = float("inf")
            else:
                gap = abs((when - played_when).total_seconds())
                if gap > tolerance.total_seconds():
                    continue
            candidates.append((gap, want_idx, play_idx, 0))
    candidates.sort()

    taken_want: set[int] = set()
    taken_play: set[int] = set()
    rows: list[dict[str, object]] = []
    for _gap, want_idx, play_idx, _ in candidates:
        if want_idx in taken_want or play_idx in taken_play:
            continue
        taken_want.add(want_idx)
        taken_play.add(play_idx)
        _pair_unused, _when, hs, as_ = played[play_idx]
        rows.append(
            {"game_id": wanted[want_idx][0], "home_score": hs, "away_score": as_}
        )

    if not rows:
        return pd.DataFrame(columns=columns)
    # Emit in the slate's own order, so a graded frame reads like the card.
    order = {gid: i for i, (gid, _pair, _when) in enumerate(wanted)}
    rows.sort(key=lambda r: order[str(r["game_id"])])
    return pd.DataFrame(rows, columns=columns)
