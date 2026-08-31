"""Attaching pulled CFBD lines onto backfilled fit-only rows.

Pins the join: season + team pair + kickoff within two days, both
orientations (spread sign flips on a reversed pair, the total does not);
pulled games with no counterpart are appended; rows that already carry
lines are never rewritten.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT = Path(__file__).parent.parent / "scripts" / "attach_cfbd_lines.py"
spec = importlib.util.spec_from_file_location("attach_cfbd_lines", _SCRIPT)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _row(gid, season, home, away, kickoff, hs, as_, spread=np.nan, total=np.nan):
    return {"game_id": gid, "league": "ncaaf", "season": season, "week": 1,
            "season_type": "REG", "kickoff": pd.Timestamp(kickoff),
            "home_team": home, "away_team": away, "neutral_site": False,
            "roof": None, "surface": None, "home_score": hs, "away_score": as_,
            "spread_line": spread, "total_line": total}


def test_attach_matches_flips_and_appends() -> None:
    games = pd.DataFrame([
        # Backfilled, lineless, same orientation as the pull.
        _row("box1", 2025, "Georgia", "Clemson", "2025-08-30 19:00", 34, 3),
        # Backfilled, lineless, REVERSED orientation vs the pull.
        _row("box2", 2025, "Iowa", "Iowa State", "2025-09-06 16:00", 20, 13),
        # Already lined — must never be rewritten.
        _row("g2024", 2024, "Georgia", "Clemson", "2024-08-31 19:00", 34, 3,
             spread=-13.5, total=48.5),
    ])
    pulled = pd.DataFrame([
        _row("401001", 2025, "Georgia", "Clemson", "2025-08-30 23:30", 34, 3,
             spread=12.5, total=49.5),
        _row("401002", 2025, "Iowa State", "Iowa", "2025-09-06 20:00", 13, 20,
             spread=-3.0, total=41.5),
        # No counterpart in games → appended whole.
        _row("401003", 2025, "Texas", "Ohio State", "2025-08-30 20:00", 21, 24,
             spread=-1.5, total=52.5),
    ])

    merged, counts = mod.attach(games, pulled, 2025)
    assert counts == {"attached": 2, "appended": 1, "still_lineless": 0}
    by_id = {r["game_id"]: r for r in merged.to_dict("records")}
    assert by_id["box1"]["spread_line"] == 12.5  # same orientation: verbatim
    assert by_id["box1"]["total_line"] == 49.5
    # Reversed pair: pulled says Iowa State -(-3.0)=… favored — flipping to the
    # games row's Iowa-home orientation negates the spread, keeps the total.
    assert by_id["box2"]["spread_line"] == 3.0
    assert by_id["box2"]["total_line"] == 41.5
    assert "401003" in by_id  # appended CFBD-only game
    assert by_id["g2024"]["spread_line"] == -13.5  # untouched


def test_attach_respects_the_date_window() -> None:
    games = pd.DataFrame([
        _row("box1", 2025, "Georgia", "Clemson", "2025-08-30 19:00", 34, 3),
    ])
    pulled = pd.DataFrame([
        # Same pair, but a different meeting months away — must not match.
        _row("401001", 2025, "Georgia", "Clemson", "2025-12-06 20:00", 30, 27,
             spread=7.5, total=51.5),
    ])
    merged, counts = mod.attach(games, pulled, 2025)
    assert counts["attached"] == 0
    assert counts["appended"] == 1  # the December game joins as its own row
    assert counts["still_lineless"] == 1
    assert len(merged) == 2
