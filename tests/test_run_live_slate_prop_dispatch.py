"""Which prop model runs is a property of the league, not of the flags passed.

``--fp-projections`` carries every league the FantasyPros collector banks, so
supplying it for MLB used to take the *football* prop path on baseball players
— and because the dispatch is an if/elif, it silently skipped the pitcher-K
slate MLB actually has. The only thing preventing that was ``live-slate.yml``
gating the flag on ``league = nfl``: a load-bearing condition in a shell script
with nothing saying so.

The two paths announce themselves differently ("K prop slate skipped" vs "prop
slate skipped"), which is what lets these tests tell which one ran.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "run_live_slate.py"
NFL_SNAPSHOT = REPO / "tests" / "fixtures" / "theoddsapi_nfl.json"
MLB_SNAPSHOT = REPO / "tests" / "fixtures" / "theoddsapi_mlb.json"


def _mixed_league_fp(path: Path) -> Path:
    """A snapshot carrying both leagues, exactly as the collector banks it."""
    rows = [
        ("kc_qb", "Patrick Mahomes", "KC", "QB", "pass_yds", 280.0, "nfl"),
        ("buf_qb", "Josh Allen", "BUF", "QB", "pass_yds", 285.0, "nfl"),
        ("mlb_sp", "Some Pitcher", "SF", "SP", "k", 180.0, "mlb"),
    ]
    frame = pd.DataFrame(
        rows,
        columns=["player_id", "player_name", "team", "position", "stat", "value", "league"],
    )
    frame.assign(season=2026, week=1, source="fantasypros").to_parquet(path, index=False)
    return path


def _run(league: str, snapshot: Path, tmp_path: Path) -> str:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--league", league, "--data", f"datasets/{league}",
         "--offline", "--snapshot-file", str(snapshot),
         "--fp-projections", str(_mixed_league_fp(tmp_path / "fp.parquet")),
         "--n-sims", "500", "--max-days", "0", "--out", str(tmp_path / "slate")],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_mlb_keeps_its_own_prop_slate_when_fp_projections_are_passed(tmp_path: Path) -> None:
    out = _run("mlb", MLB_SNAPSHOT, tmp_path)
    # The MLB pitcher-K path ran...
    assert "K prop slate" in out, out
    # ...and the football one did not. Without the league guard this line is
    # "prop slate skipped" and MLB's real prop board never gets built.
    assert "\nprop slate" not in out, out


def test_football_prop_path_still_runs_for_football(tmp_path: Path) -> None:
    """The guard narrows by league only — it must not disable the NFL path."""
    out = _run("nfl", NFL_SNAPSHOT, tmp_path)
    assert "prop slate" in out, out
    assert "K prop slate" not in out, out
