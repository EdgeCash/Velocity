"""MLB live-slate runner wiring (Phase M6).

The engine is already proven (M4); here we check the last mile — that the MLB
league-average model covers every club, and that the ``run_live_slate.py`` CLI
runs end-to-end for ``--league mlb`` on a saved snapshot and on an empty board,
writing a private slate parquet either way.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
from velocity.models.game_mlb import league_average_model
from velocity.wagering.live import MLB_TEAM_ALIASES

REPO = Path(__file__).parent.parent
RUNNER = REPO / "scripts" / "run_live_slate.py"
MLB_SNAPSHOT = REPO / "tests" / "fixtures" / "theoddsapi_mlb.json"


def test_league_average_model_covers_all_clubs() -> None:
    codes = sorted(set(MLB_TEAM_ALIASES.values()))
    model = league_average_model(codes, n_sims=200)
    assert set(model.known_teams) == set(codes)
    assert len(model.known_teams) == 30
    proj = model.project_full("LAD", "SF")  # any matchup resolves and prices
    assert 0.0 < proj.p_home_win() < 1.0


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), "--league", "mlb", "--n-sims", "600", *args],
        capture_output=True,
        text=True,
        cwd=REPO,
    )


def test_runner_writes_slate_from_saved_snapshot(tmp_path: Path) -> None:
    out = tmp_path / "slate"
    result = _run("--snapshot-file", str(MLB_SNAPSHOT), "--out", str(out))
    assert result.returncode == 0, result.stderr
    assert "games on the board" in result.stdout
    written = list(out.glob("slate_mlb_*.parquet"))
    assert len(written) == 1
    frame = pd.read_parquet(written[0])
    assert "league" in frame.columns  # persisted with league/generated_at tags


def test_runner_empty_board_succeeds(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text("[]")  # a valid Odds API payload with no events
    out = tmp_path / "slate"
    result = _run("--snapshot-file", str(empty), "--out", str(out))
    assert result.returncode == 0, result.stderr
    assert "no games on the board" in result.stdout
    # An off-day still writes an (empty) slate so the schedule keeps producing.
    assert len(list(out.glob("slate_mlb_*.parquet"))) == 1
