"""Point-in-time stat window (velocity.ingest.mlb.asof_daterange) — pure, offline.

The walk-forward build may only see stats accrued *before* the game date. These
pin the anti-leakage window: it ends the day before the game (never includes the
day being projected) and starts at a safe pre-season floor.
"""

from __future__ import annotations

from velocity.ingest.mlb import asof_daterange


def test_end_is_the_day_before_the_game() -> None:
    start, end = asof_daterange(2026, "2026-07-20")
    assert end == "2026-07-19"  # strictly before the game date — no leakage
    assert start == "2026-03-01"


def test_end_rolls_across_month_boundary() -> None:
    _, end = asof_daterange(2026, "2026-07-01")
    assert end == "2026-06-30"


def test_start_tracks_the_season() -> None:
    start, _ = asof_daterange(2025, "2025-04-10")
    assert start == "2025-03-01"
