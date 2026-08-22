"""NCAAB live slate, offline end-to-end — the N4 wiring (docs/BUILD_NCAAB.md).

Runs the real script against a canned Odds API snapshot and a tiny committed-
shape dataset folder: the promoted model (pace×efficiency + the Torvik
pseudo-games prior) must fit, the nickname bridge must resolve the provider's
"Duke Blue Devils" onto the hoopR-keyed "Duke", and the slate must persist.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "run_live_slate.py"
SNAPSHOT = REPO / "tests" / "fixtures" / "theoddsapi_ncaab.json"

TEAMS = ("Duke", "North Carolina", "Kansas", "Villanova")


def _tiny_datasets(folder: Path) -> None:
    """games/team_box/torvik parquets in the committed datasets' shapes."""
    rows, box_rows = [], []
    i = 0
    for home in TEAMS:
        for away in TEAMS:
            if home == away:
                continue
            gid = f"g{i}"
            rows.append({
                "game_id": gid, "league": "ncaab", "season": 2026,
                "week": i % 2, "season_type": "REG",
                "kickoff": pd.Timestamp("2025-11-05") + pd.Timedelta(days=i),
                "home_team": home, "away_team": away, "neutral_site": False,
                "roof": None, "surface": None,
                "home_score": 76.0 + (i % 5), "away_score": 70.0 + (i % 3),
            })
            for side in ("home", "away"):
                box_rows.append({
                    "game_id": gid, "team_home_away": side,
                    "field_goals_attempted": 58.0, "offensive_rebounds": 9.0,
                    "total_turnovers": 11.0, "free_throws_attempted": 20.0,
                    "season": 2026,
                })
            i += 1
    pd.DataFrame(rows).to_parquet(folder / "games.parquet", index=False)
    pd.DataFrame(box_rows).to_parquet(folder / "team_box.parquet", index=False)
    pd.DataFrame([
        {"team": "Duke", "conf": "ACC", "rank": 2, "adj_o": 122.0, "adj_d": 90.0,
         "adj_t": 68.0, "barthag": 0.96, "wab": 8.0, "season": 2025},
        {"team": "North Carolina", "conf": "ACC", "rank": 20, "adj_o": 115.0,
         "adj_d": 96.0, "adj_t": 70.0, "barthag": 0.9, "wab": 4.0, "season": 2025},
    ]).to_parquet(folder / "torvik.parquet", index=False)


def test_ncaab_slate_end_to_end(tmp_path: Path) -> None:
    data = tmp_path / "datasets"
    data.mkdir()
    _tiny_datasets(data)
    out = tmp_path / "slate"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--league", "ncaab", "--data", str(data),
         "--snapshot-file", str(SNAPSHOT), "--n-sims", "2000", "--max-days", "0",
         "--min-edge", "0.0", "--out", str(out)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    # The promoted N2 configuration fit, prior included.
    assert "pace×efficiency" in result.stdout
    assert "Torvik prior" in result.stdout
    # The nickname bridge resolved the provider names — nothing skipped.
    assert "teams not in the model's universe" not in result.stdout

    slate_files = list(out.glob("slate_ncaab_*.parquet"))
    assert slate_files, result.stdout
    games_files = list(out.glob("games_ncaab_*.parquet"))
    assert games_files, result.stdout
    games_map = pd.read_parquet(games_files[0])
    assert set(games_map["home_team"]) == {"Duke Blue Devils"}
