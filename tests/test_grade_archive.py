"""grade_archive CLI — end-to-end grading smoke (offline).

Builds a persisted slate + games map + schedule file in a tmp dir and grades
them, asserting the script joins finals by team+date and prints a scorecard.
The scoring math is unit-tested in test_scorecard / test_results; this pins the
wiring.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "grade_archive.py"


def test_grades_archived_slate(tmp_path: Path) -> None:
    # A slate on one Odds-API game id, mapped to BUF @ KC on the schedule's date.
    slate = pd.DataFrame({
        "game_id": ["odds1", "odds1"],
        "market": ["moneyline", "total"],
        "side": ["home", "over"],
        "point": [None, 44.5],
        "book": ["dk", "dk"],
        "price": [-120, -110],
        "stake": [3.0, 2.0],
        "p_model": [0.6, 0.58],
        "p_fair": [0.55, 0.54],
    })
    games = pd.DataFrame({
        "game_id": ["odds1"],
        "home_team": ["Kansas City Chiefs"],
        "away_team": ["Buffalo Bills"],
        "kickoff": pd.to_datetime(["2026-09-10T00:20:00"]),
    })
    # The schedule-feed side: nflverse codes + a played final (KC 27, BUF 20).
    schedule = pd.DataFrame({
        "game_id": ["2026_01_BUF_KC"],
        "home_team": ["KC"],
        "away_team": ["BUF"],
        "kickoff": pd.to_datetime(["2026-09-10T00:20:00"]),
        "home_score": [27.0],
        "away_score": [20.0],
    })
    slate_path = tmp_path / "slate.parquet"
    games_path = tmp_path / "games.parquet"
    schedule_path = tmp_path / "schedule.parquet"
    slate.to_parquet(slate_path, index=False)
    games.to_parquet(games_path, index=False)
    schedule.to_parquet(schedule_path, index=False)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--slate", str(slate_path),
         "--games", str(games_path), "--schedule-file", str(schedule_path)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    # KC won 27-20: home ML wins, over 44.5 wins (total 47) → 2-0, and the
    # scorecard prints.
    assert "Scorecard" in result.stdout
    assert "CLV by market" in result.stdout
    assert "wins: 2" in result.stdout
