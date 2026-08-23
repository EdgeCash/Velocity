"""Site data prep — latest-stamp selection, joins, and typed empty frames."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "build_site_data.py"


def _slate_frames(folder: Path) -> None:
    old, new = "20260101T120000Z", "20260102T120000Z"
    for stamp, price in ((old, -200), (new, -110)):
        pd.DataFrame([{
            "game_id": "g1", "market": "total", "side": "under", "point": 8.5,
            "book": "dk", "price": price, "p_model": 0.568, "p_fair": 0.512,
            "edge": 0.056, "stake": 1.2,
        }]).to_parquet(folder / f"slate_mlb_{stamp}.parquet", index=False)
    pd.DataFrame([{
        "game_id": "g1", "home_team": "Brewers", "away_team": "Cubs",
        "kickoff": pd.Timestamp("2026-01-02 19:10"),
    }]).to_parquet(folder / f"games_mlb_{new}.parquet", index=False)
    pd.DataFrame([{
        "game_id": "g1", "away": "Cubs", "home": "Brewers", "n_sims": 100,
        "mu_away": 3.7, "mu_home": 4.2, "p_home_win": 0.589,
        "fair_spread": -0.5, "fair_total": 7.9,
    }]).to_parquet(folder / f"projections_mlb_{new}.parquet", index=False)
    pd.DataFrame([
        {"section": "games", "play": "CHC@MIL U8.5", "market": "total",
         "side": "under", "point": 8.5, "price": -110.0, "stake": 1.0,
         "result": "win", "profit": 0.91, "slate_date": pd.Timestamp("2026-01-01")},
        {"section": "games", "play": "X@Y", "market": "spread", "side": "home",
         "point": -3.0, "price": -110.0, "stake": 1.0, "result": "loss",
         "profit": -1.0, "slate_date": pd.Timestamp("2026-01-02")},
    ]).to_parquet(folder / f"cumulative_record_mlb_{new}.parquet", index=False)


def test_build_site_data_end_to_end(tmp_path: Path) -> None:
    slate_dir = tmp_path / "slate"
    slate_dir.mkdir()
    _slate_frames(slate_dir)
    out = tmp_path / "data"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--slate-dir", str(slate_dir),
         "--out", str(out)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr

    board = pd.read_parquet(out / "board.parquet")
    assert len(board) == 1
    row = board.iloc[0]
    # The newest stamp won, and the joins landed.
    assert row["price"] == -110
    assert row["home_team"] == "Brewers"
    assert row["fair_total"] == 7.9
    assert row["league"] == "mlb"

    units = pd.read_parquet(out / "units.parquet")
    assert units["units"].tolist() == pytest.approx([0.91, -0.09])

    # Absent families still produce typed frames every page can query.
    dfs = pd.read_parquet(out / "dfs_lineup.parquet")
    assert dfs.empty
    assert "salary" in dfs.columns and "league" in dfs.columns
    record = pd.read_parquet(out / "record.parquet")
    assert "result" in record.columns


def test_build_units_coerces_object_profit(tmp_path: Path) -> None:
    # Real graded frames arrive with profit as object dtype (pending rows mix
    # None upstream) — the crash that failed the first live site build.
    slate_dir = tmp_path / "slate"
    slate_dir.mkdir()
    frame = pd.DataFrame([
        {"section": "games", "play": "A@B", "market": "total", "side": "under",
         "point": 8.5, "price": -110.0, "stake": 1.0, "result": "win",
         "profit": 0.91, "slate_date": pd.Timestamp("2026-01-01")},
        {"section": "games", "play": "C@D", "market": "spread", "side": "home",
         "point": -3.0, "price": -110.0, "stake": 1.0, "result": "pending",
         "profit": None, "slate_date": pd.Timestamp("2026-01-01")},
    ])
    frame["profit"] = frame["profit"].astype(object)
    frame.to_parquet(slate_dir / "cumulative_record_mlb_20260101T120000Z.parquet",
                     index=False)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--slate-dir", str(slate_dir),
         "--out", str(tmp_path / "data")],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    units = pd.read_parquet(tmp_path / "data" / "units.parquet")
    assert units["units"].tolist() == pytest.approx([0.91])
