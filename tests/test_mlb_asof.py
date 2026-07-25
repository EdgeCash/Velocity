"""Point-in-time stat window (velocity.ingest.mlb.asof_daterange) — pure, offline.

The walk-forward build may only see stats accrued *before* the game date. These
pin the anti-leakage window: it ends the day before the game (never includes the
day being projected) and starts at a safe pre-season floor.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from velocity.ingest.mlb import asof_daterange, load_player_stats_asof


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


def test_asof_cache_hit_skips_the_network(tmp_path: Path) -> None:
    # Pre-seed the cache for (role, end_date); the loader must return it without ever
    # touching the network (the fetch helper would fail offline). This is what lets a
    # calibration re-run replay the whole season without re-hitting StatsAPI.
    cached = pd.DataFrame({
        "player_id": ["1"], "player_name": ["Test"], "team": ["ATL"], "season": [2026],
        "role": ["bat"], "pa": [100.0], "k": [20.0], "bb": [10.0], "hbp": [1.0],
        "singles": [15.0], "doubles": [5.0], "triples": [1.0], "hr": [4.0],
    })
    cached.to_parquet(tmp_path / "asof_bat_2026-07-19.parquet", index=False)

    got = load_player_stats_asof(2026, "bat", "2026-07-19", "2026-03-01", cache_dir=str(tmp_path))
    assert got["player_id"].tolist() == ["1"]
    assert got["hr"].tolist() == [4.0]
