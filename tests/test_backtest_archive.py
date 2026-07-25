"""Entry/closing snapshot selection (velocity.backtest.archive) — pure, offline.

The banked archive holds several snapshots per game; these pin that per game the
selector picks the right two boards: closing = nearest snapshot at/before first
pitch, entry = latest snapshot at/before (first_pitch − lead) with an
earliest-available fallback, and games with no pre-game snapshot are dropped.
"""

from __future__ import annotations

import pandas as pd
from velocity.backtest.archive import ArchiveBoards, select_boards

# One game, first pitch 23:10 UTC, banked at 18:00 / 21:00 / 23:30.
# 23:30 is AFTER first pitch → not a valid closing; 21:00 is the closing snap;
# entry (lead 3h → 20:10 cutoff) → 18:00.
EVENTS = pd.DataFrame({
    "game_id": ["g1"],
    "home_team": ["Los Angeles Dodgers"],
    "away_team": ["San Francisco Giants"],
    "kickoff": pd.to_datetime(["2026-07-20T23:10:00"]),
})


def _line(game_id: str, snapshot: str, price: int, point=None, side="home", market="moneyline"):
    return {
        "game_id": game_id, "book": "dk", "market": market, "side": side,
        "price": price, "point": point, "snapshot": snapshot, "is_closing": True,
    }


LINES = pd.DataFrame([
    _line("g1", "2026-07-20T18:00:00Z", -110),   # entry
    _line("g1", "2026-07-20T21:00:00Z", -125),   # closing (nearest before 23:10)
    _line("g1", "2026-07-20T23:30:00Z", -140),   # after first pitch — ignored
])


def test_entry_and_closing_snapshots() -> None:
    boards = select_boards(LINES, EVENTS)
    assert isinstance(boards, ArchiveBoards)
    assert boards.entry["price"].tolist() == [-110]     # 18:00 snapshot
    assert boards.closing["price"].tolist() == [-125]   # 21:00 snapshot
    assert set(boards.events["game_id"]) == {"g1"}


def test_post_first_pitch_snapshot_never_closes() -> None:
    # Only the 23:30 (post-game) snapshot exists → no valid pre-game board → dropped.
    late = pd.DataFrame([_line("g1", "2026-07-20T23:30:00Z", -140)])
    boards = select_boards(late, EVENTS)
    assert boards.entry.empty and boards.closing.empty
    assert boards.events.empty


def test_entry_falls_back_to_earliest_when_no_early_snapshot() -> None:
    # First pitch 19:00; only 18:00 + 18:45 snapshots → no snap ≤ 16:00 (3h lead),
    # so entry falls back to the earliest (18:00); closing = 18:45 (nearest pre-game).
    events = EVENTS.assign(kickoff=pd.to_datetime(["2026-07-20T19:00:00"]))
    lines = pd.DataFrame([
        _line("g1", "2026-07-20T18:00:00Z", -110),
        _line("g1", "2026-07-20T18:45:00Z", -120),
    ])
    boards = select_boards(lines, events)
    assert boards.entry["price"].tolist() == [-110]
    assert boards.closing["price"].tolist() == [-120]


def test_multiple_games_selected_independently() -> None:
    events = pd.DataFrame({
        "game_id": ["g1", "g2"],
        "home_team": ["Los Angeles Dodgers", "New York Yankees"],
        "away_team": ["San Francisco Giants", "Boston Red Sox"],
        "kickoff": pd.to_datetime(["2026-07-20T23:10:00", "2026-07-20T17:05:00"]),
    })
    lines = pd.DataFrame([
        _line("g1", "2026-07-20T18:00:00Z", -110),
        _line("g1", "2026-07-20T21:00:00Z", -125),
        _line("g2", "2026-07-20T14:00:00Z", 130),   # entry (>=3h before 17:05)
        _line("g2", "2026-07-20T16:30:00Z", 118),   # closing (nearest before 17:05)
    ])
    boards = select_boards(lines, events)
    entry = boards.entry.set_index("game_id")["price"].to_dict()
    closing = boards.closing.set_index("game_id")["price"].to_dict()
    assert entry == {"g1": -110, "g2": 130}
    assert closing == {"g1": -125, "g2": 118}


def test_empty_inputs() -> None:
    boards = select_boards(LINES.iloc[:0], EVENTS)
    assert boards.entry.empty and boards.closing.empty
