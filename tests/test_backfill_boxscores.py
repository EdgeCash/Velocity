"""Backfilling a season of games from the banked boxscores (the 2025 gap).

Pins the contract: boxscore team names are bridged to the games file's CFBD
conventions, backfilled rows carry NaN lines (fit-only — the market backtest
must not see them), and a season already present is refused rather than
double-appended.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "backfill_games_from_boxscores.py"
spec = importlib.util.spec_from_file_location("backfill_games_from_boxscores", _SCRIPT)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _games() -> pd.DataFrame:
    return pd.DataFrame([{
        "game_id": "g2024", "league": "ncaaf", "season": 2024, "week": 1,
        "season_type": "REG", "kickoff": pd.Timestamp("2024-08-31 19:00"),
        "home_team": "Hawai'i", "away_team": "South Florida", "neutral_site": False,
        "roof": None, "surface": None, "home_score": 20, "away_score": 24,
        "spread_line": -2.5, "total_line": 49.5,
    }])


def _boxscores() -> pd.DataFrame:
    return pd.DataFrame([{
        "game_id": "2025_x", "league": "ncaaf", "season": 2025, "week": 1,
        "season_type": "REG", "kickoff": pd.Timestamp("2025-08-30 19:00"),
        # Boxscore-CSV conventions on purpose — both must be bridged.
        "home_team": "Hawaii", "away_team": "USF", "neutral_site": False,
        "roof": None, "surface": None, "home_score": 17, "away_score": 31,
    }])


def test_backfill_bridges_names_and_leaves_lines_nan() -> None:
    merged = mod.backfill(_games(), _boxscores(), 2025)
    assert len(merged) == 2
    row = merged[merged["season"] == 2025].iloc[0]
    assert row["home_team"] == "Hawai'i"  # boxscore "Hawaii" bridged
    assert row["away_team"] == "South Florida"  # boxscore "USF" bridged
    assert np.isnan(row["spread_line"]) and np.isnan(row["total_line"])  # fit-only
    assert list(merged.columns) == mod.GAMES_COLUMNS
    # Existing rows are untouched.
    assert merged[merged["season"] == 2024].iloc[0]["spread_line"] == -2.5


def test_backfill_refuses_a_season_already_present() -> None:
    with pytest.raises(SystemExit, match="refusing to double-append"):
        mod.backfill(_games(), _boxscores(), 2024)


def test_backfill_refuses_an_absent_season() -> None:
    with pytest.raises(SystemExit, match="no season"):
        mod.backfill(_games(), _boxscores(), 2031)


def test_alias_targets_exist_in_the_committed_games_file() -> None:
    """Every alias target must be a name the games file actually uses —
    a stale target would silently split one school across two keys."""
    games_path = Path(__file__).parent.parent / "datasets" / "ncaaf" / "games.parquet"
    if not games_path.exists():
        pytest.skip("committed NCAAF dataset not present")
    games = pd.read_parquet(games_path, columns=["home_team", "away_team"])
    known = set(games["home_team"]) | set(games["away_team"])
    missing = [t for t in mod.BOX_TO_CFBD.values() if t not in known]
    assert not missing, f"alias targets unknown to games.parquet: {missing}"
