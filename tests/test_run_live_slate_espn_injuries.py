"""ESPN injuries on a non-NFL card — end-to-end offline smoke.

Injuries reached this system from FantasyPros alone, which serves the NFL and
nothing else, so the intel layer's availability vetoes abstained on all five
other leagues. On a baseball card that meant pricing a game with no idea that a
listed starter was on the 60-day IL.

This runs the real CLI on an MLB board with a banked ESPN report and asserts
the outs reach the intel layer, keyed to the model's own team names.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "run_live_slate.py"
SNAPSHOT = REPO / "tests" / "fixtures" / "theoddsapi_mlb.json"


def _report(path: Path) -> Path:
    """A banked ESPN snapshot naming the two teams the MLB fixture plays."""
    events = json.loads(SNAPSHOT.read_text())
    home, away = events[0]["home_team"], events[0]["away_team"]
    rows = [
        # Genuinely out — baseball's vocabulary, which the football-shaped
        # status list would have read as "available".
        (home, "Ace Starter", "SP", "60-Day-IL", True),
        (away, "Everyday Bat", "1B", "10-Day-IL", True),
        # A game-time decision: present in the report, and not a veto.
        (home, "Probable Reliever", "RP", "Day-To-Day", False),
    ]
    frame = pd.DataFrame(
        [
            {
                "league": "mlb", "player_id": f"p{i}", "player_name": name,
                "position": pos, "team_abbreviation": None, "team_name": team,
                "status": status, "is_out": is_out, "injury_type": "Elbow",
                "injury_location": "Arm", "injury_detail": None,
                "return_date": None, "updated": None, "comment": None,
            }
            for i, (team, name, pos, status, is_out) in enumerate(rows)
        ]
    )
    frame.to_parquet(path, index=False)
    return path


def test_mlb_card_sees_outs_from_the_espn_report(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--league", "mlb", "--data", "datasets/mlb",
         "--offline", "--snapshot-file", str(SNAPSHOT),
         "--espn-injuries-file", str(_report(tmp_path / "espn.parquet")),
         "--n-sims", "1000", "--max-days", "0", "--out", str(tmp_path / "slate")],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    # Both injured-list players counted as genuine outs; the day-to-day one did
    # not. Before this wiring the line read "no injuries snapshot".
    assert "ESPN injuries loaded — 2 genuine outs across 2 team(s)" in result.stdout, (
        result.stdout
    )


def test_a_card_with_no_report_says_so_rather_than_silently_abstaining(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--league", "mlb", "--data", "datasets/mlb",
         "--offline", "--snapshot-file", str(SNAPSHOT),
         "--n-sims", "1000", "--max-days", "0", "--out", str(tmp_path / "slate")],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    assert "no injuries snapshot" in result.stdout, result.stdout


def test_rows_for_another_league_never_reach_this_card(tmp_path: Path) -> None:
    """A team abbreviation is only unique inside one league."""
    path = tmp_path / "espn.parquet"
    _report(path)
    frame = pd.read_parquet(path)
    frame["league"] = "nfl"  # same rows, wrong league
    frame.to_parquet(path, index=False)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--league", "mlb", "--data", "datasets/mlb",
         "--offline", "--snapshot-file", str(SNAPSHOT),
         "--espn-injuries-file", str(path),
         "--n-sims", "1000", "--max-days", "0", "--out", str(tmp_path / "slate")],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    assert "carries no MLB rows" in result.stdout, result.stdout
