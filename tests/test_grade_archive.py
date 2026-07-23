"""grade_archive CLI — end-to-end grading smoke (offline).

Builds a persisted slate + games map in a tmp dir and grades them against the
frozen StatsAPI schedule fixture (finals), asserting the script joins finals by
team+date and prints a scorecard. The scoring math is unit-tested in
test_scorecard / test_results; this pins the wiring.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "grade_archive.py"
SCHEDULE = REPO / "tests" / "fixtures" / "mlb_schedule.json"


def test_grades_archived_slate(tmp_path: Path) -> None:
    # A slate on one Odds-API game id, mapped to SF @ LAD on the fixture's date.
    slate = pd.DataFrame({
        "game_id": ["odds1", "odds1"],
        "market": ["moneyline", "total"],
        "side": ["home", "over"],
        "point": [None, 7.5],
        "book": ["dk", "dk"],
        "price": [-120, -110],
        "stake": [3.0, 2.0],
        "p_model": [0.6, 0.58],
        "p_fair": [0.55, 0.54],
    })
    games = pd.DataFrame({
        "game_id": ["odds1"],
        "home_team": ["Los Angeles Dodgers"],
        "away_team": ["San Francisco Giants"],
        "kickoff": pd.to_datetime(["2026-07-23T02:10:00"]),
    })
    slate_path = tmp_path / "slate.parquet"
    games_path = tmp_path / "games.parquet"
    slate.to_parquet(slate_path, index=False)
    games.to_parquet(games_path, index=False)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--slate", str(slate_path),
         "--games", str(games_path), "--schedule-file", str(SCHEDULE)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    # LAD won 5-3: home ML wins, over 7.5 wins (total 8) → 2-0, and the scorecard prints.
    assert "Scorecard" in result.stdout
    assert "CLV by market" in result.stdout
    assert "wins: 2" in result.stdout
