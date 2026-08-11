"""Live-slate prop path — end-to-end offline smoke.

Runs the real CLI against the committed NFL dataset, the frozen odds snapshot,
a synthetic FantasyPros projections parquet, and a banked prop board, and
asserts a priced prop slate lands next to the game slate. This pins the whole
wiring: FP frame → correlated sim → name resolution → de-vig → stake →
persisted parquet with the raw ``p_model``/``p_fair`` the backtest replays.
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
    """A beatable two-sided board for Allen pass yards on the snapshot's game id."""
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


def test_prop_slate_end_to_end(tmp_path: Path) -> None:
    fp_path = tmp_path / "fp.parquet"
    lines_path = tmp_path / "prop_lines.parquet"
    _fp_frame().to_parquet(fp_path, index=False)
    _prop_lines().to_parquet(lines_path, index=False)
    out = tmp_path / "slate"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--league", "nfl", "--data", "datasets/nfl",
         "--snapshot-file", str(SNAPSHOT), "--fp-projections", str(fp_path),
         "--prop-lines-file", str(lines_path), "--n-sims", "2000", "--max-days", "0",
         "--min-edge", "0.0", "--out", str(out)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    assert "props" in result.stdout

    props_files = list(out.glob("slate_nfl_props_*.parquet"))
    assert props_files, result.stdout
    props = pd.read_parquet(props_files[0])
    assert not props.empty, result.stdout
    assert set(props["player"]) == {"Josh Allen"}
    assert (props["market"] == "pass_yards").all()
    # The raw pricing rides along for the shrink-sweep backtest.
    assert props["p_model"].notna().all()
    assert props["p_fair"].notna().all()
