"""Intelligence layer — end-to-end offline smoke through the live runner.

Runs the real CLI against the committed NFL dataset, the frozen odds snapshot,
a synthetic FantasyPros frame, a beatable prop board, and an injuries snapshot
that rules the propped QB out. Pins the whole wiring: datasets → context
library → signals → conviction tiers → the persisted ``intel_*.parquet`` —
including the availability veto landing on the injured player's own prop.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "run_live_slate.py"
SNAPSHOT = REPO / "tests" / "fixtures" / "theoddsapi_nfl.json"

_UPDATE = pd.Timestamp("2026-09-10 12:00:00")


def _fp_frame() -> pd.DataFrame:
    rows = [
        ("kc_qb", "Patrick Mahomes", "KC", "QB", "pass_yds", 280.0),
        ("kc_te", "Travis Kelce", "KC", "TE", "rec", 6.5),
        ("kc_te", "Travis Kelce", "KC", "TE", "rec_yds", 72.0),
        ("buf_qb", "Josh Allen", "BUF", "QB", "pass_yds", 285.0),
        ("buf_wr", "Khalil Shakir", "BUF", "WR", "rec", 5.0),
        ("buf_wr", "Khalil Shakir", "BUF", "WR", "rec_yds", 55.0),
    ]
    frame = pd.DataFrame(
        rows, columns=["player_id", "player_name", "team", "position", "stat", "value"]
    )
    return frame.assign(season=2026, week=1, source="fantasypros", league="nfl")


def _prop_lines() -> pd.DataFrame:
    rows = []
    for side, price in (("over", 100), ("under", -120)):
        rows.append(
            {
                "line_id": f"evt-nfl-001|pass_yards|joshallen|{side}|dk|249.5",
                "game_id": "evt-nfl-001",
                "book": "dk",
                "market": "pass_yards",
                "player": "Josh Allen",
                "side": side,
                "price": price,
                "point": 249.5,
                "timestamp": _UPDATE,
                "is_closing": False,
            }
        )
    return pd.DataFrame(rows)


def _injuries() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"player_id": "buf_qb", "player_name": "Josh Allen", "team": "BUF",
             "position": "QB", "status": "Out", "is_out": True},
            {"player_id": "kc_wr", "player_name": "Some Receiver", "team": "KC",
             "position": "WR", "status": "Questionable", "is_out": False},
        ]
    )


def test_intel_layer_end_to_end(tmp_path: Path) -> None:
    fp_path = tmp_path / "fp.parquet"
    lines_path = tmp_path / "prop_lines.parquet"
    injuries_path = tmp_path / "injuries.parquet"
    _fp_frame().to_parquet(fp_path, index=False)
    _prop_lines().to_parquet(lines_path, index=False)
    _injuries().to_parquet(injuries_path, index=False)
    out = tmp_path / "slate"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--league", "nfl", "--data", "datasets/nfl",
         "--snapshot-file", str(SNAPSHOT), "--fp-projections", str(fp_path),
         "--prop-lines-file", str(lines_path), "--injuries-file", str(injuries_path),
         "--n-sims", "2000", "--max-days", "0", "--min-edge", "0.0",
         "--out", str(out)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    assert "intelligence card" in result.stdout
    assert "injuries snapshot loaded (1 genuine outs)" in result.stdout

    intel_files = list(out.glob("intel_nfl_*.parquet"))
    assert intel_files, result.stdout
    intel = pd.read_parquet(intel_files[0])
    assert not intel.empty, result.stdout
    assert {"game_id", "market", "side", "conviction", "tier", "recommended",
            "rationale", "context_score", "edge_score"} <= set(intel.columns)
    assert set(intel["tier"]) <= {"A", "B", "C", "X"}

    # The injured QB's own prop is vetoed — flagged, never recommended.
    allen = intel[intel["player"] == "Josh Allen"]
    assert not allen.empty, result.stdout
    assert (allen["tier"] == "X").all()
    assert (~allen["recommended"]).all()
    assert allen["rationale"].str.contains("VETO").all()

    # Every assessed game bet corresponds to a slate row (the layer only
    # judges what the EV gate passed — it never invents picks).
    slate_files = list(out.glob("slate_nfl_2*.parquet"))
    assert slate_files
    slate = pd.read_parquet(slate_files[0])
    game_rows = intel[intel["player"].isna()]
    slate_keys = set(zip(slate["game_id"], slate["market"], slate["side"], strict=True))
    intel_keys = set(zip(game_rows["game_id"], game_rows["market"], game_rows["side"],
                         strict=True))
    assert intel_keys <= slate_keys


def test_intel_layer_can_be_disabled(tmp_path: Path) -> None:
    out = tmp_path / "slate"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--league", "nfl", "--data", "datasets/nfl",
         "--snapshot-file", str(SNAPSHOT), "--n-sims", "1000", "--max-days", "0",
         "--min-edge", "0.0", "--no-intel", "--out", str(out)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    assert "intelligence card" not in result.stdout
    assert not list(out.glob("intel_nfl_*.parquet"))
