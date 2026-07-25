"""Pick each game's entry + closing board from the banked historical archive.

The CLV backfill (``scripts/collect_historical_odds.py``) banks several odds
snapshots per game-day, each tagged with the UTC time it was pulled (``snapshot``).
The walk-forward backtest needs two boards per game:

* **entry** — the market at our bet time (a snapshot a few hours before first
  pitch), which we price our slate against, and
* **closing** — the last snapshot before first pitch, the CLV anchor.

This is the pure selection layer over the banked lines: for each game it reads the
available snapshot times, and against the game's first-pitch time (``commence``,
from the banked events) picks the closing snapshot (nearest at-or-before first
pitch) and the entry snapshot (latest at-or-before ``first_pitch − entry_lead``,
falling back to the earliest available). A game with no pre-game snapshot is
dropped — it can't be priced or graded honestly.

Offline and deterministic; the driver canonicalizes sides and joins finals.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

DEFAULT_ENTRY_LEAD = pd.Timedelta(hours=3)


@dataclass(frozen=True)
class ArchiveBoards:
    """The two boards the backtest prices and grades against, plus their events."""

    events: pd.DataFrame  # game_id, home_team, away_team, kickoff (bet-time metadata)
    entry: pd.DataFrame  # lines at our bet time (priced by the model)
    closing: pd.DataFrame  # lines at the close (CLV anchor)


def _naive_utc(values: pd.Series) -> pd.Series:
    """Parse to tz-naive UTC, so tz-aware snapshots and naive kickoffs compare."""
    ts = pd.to_datetime(values, errors="coerce", utc=True)
    return ts.dt.tz_localize(None)


def _commence(events: pd.DataFrame) -> dict[str, pd.Timestamp]:
    kick = _naive_utc(events["kickoff"])
    return {
        str(g): t
        for g, t in zip(events["game_id"].astype(str), kick, strict=False)
        if pd.notna(t)
    }


def select_boards(
    lines: pd.DataFrame,
    events: pd.DataFrame,
    *,
    entry_lead: pd.Timedelta = DEFAULT_ENTRY_LEAD,
) -> ArchiveBoards:
    """Split banked multi-snapshot lines into per-game entry + closing boards.

    ``lines`` is the concatenated banked ``Lines`` (with a ``snapshot`` column, the
    pull time); ``events`` carries each game's ``kickoff`` (first pitch) + teams.
    Rows for a game with no snapshot at or before first pitch are dropped.
    """
    if lines.empty:
        return ArchiveBoards(events.iloc[:0].copy(), lines.copy(), lines.copy())

    commence = _commence(events)
    work = lines.copy()
    work["snapshot"] = _naive_utc(work["snapshot"])
    work = work.dropna(subset=["snapshot"])
    gid = work["game_id"].astype(str)

    entry_parts: list[pd.DataFrame] = []
    closing_parts: list[pd.DataFrame] = []
    kept_games: set[str] = set()
    for game_id, grp in work.groupby(gid):
        first_pitch = commence.get(game_id)
        if first_pitch is None:
            continue
        snaps = sorted(grp["snapshot"].unique())
        pre = [s for s in snaps if s <= first_pitch]
        if not pre:  # no pre-game board — can't price or grade honestly
            continue
        closing_snap = pre[-1]
        early = [s for s in snaps if s <= first_pitch - entry_lead]
        entry_snap = early[-1] if early else snaps[0]
        entry_parts.append(grp[grp["snapshot"] == entry_snap])
        closing_parts.append(grp[grp["snapshot"] == closing_snap])
        kept_games.add(game_id)

    empty = work.iloc[:0]
    entry = pd.concat(entry_parts, ignore_index=True) if entry_parts else empty
    closing = pd.concat(closing_parts, ignore_index=True) if closing_parts else empty
    kept_events = events[events["game_id"].astype(str).isin(kept_games)].reset_index(drop=True)
    return ArchiveBoards(events=kept_events, entry=entry, closing=closing)
