"""Dataset season-refresh — pure merge + per-source normalizers, offline.

The weekly refresh must be idempotent and schema-stable: re-running replaces
the target season's rows without touching history, and the fresh frames are
aligned onto the committed files' exact columns. Sources are exercised from
frozen/synthetic payloads; the network fetchers stay behind pragmas.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

REPO = Path(__file__).parent.parent

_spec = importlib.util.spec_from_file_location(
    "refresh_datasets", REPO / "scripts" / "refresh_datasets.py"
)
rd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rd)  # type: ignore[union-attr]


def test_current_season_rolls_over_in_august() -> None:
    assert rd.current_season(datetime(2026, 8, 9, tzinfo=UTC)) == 2026
    assert rd.current_season(datetime(2026, 2, 8, tzinfo=UTC)) == 2025  # Super Bowl week
    assert rd.current_season(datetime(2026, 7, 31, tzinfo=UTC)) == 2025


def test_merge_season_replaces_only_the_target_season() -> None:
    existing = pd.DataFrame({
        "game_id": ["a", "b", "c"],
        "season": [2025, 2025, 2026],
        "home_score": [20, 24, 10],
    })
    fresh = pd.DataFrame({
        "game_id": ["c", "d"],
        "season": [2026, 2026],
        "home_score": [10, 31],
        "extra_col": [1, 2],  # not in the committed file → dropped by alignment
    })
    merged = rd.merge_season(existing, fresh, 2026)
    assert sorted(merged["game_id"]) == ["a", "b", "c", "d"]
    assert list(merged.columns) == ["game_id", "season", "home_score"]
    assert (merged[merged["season"] == 2025]["game_id"].tolist()) == ["a", "b"]
    # Idempotent: running the same merge again changes nothing.
    again = rd.merge_season(merged, fresh, 2026)
    assert again.sort_values("game_id").reset_index(drop=True).equals(
        merged.sort_values("game_id").reset_index(drop=True)
    )


def test_merge_season_aligns_missing_columns_to_null() -> None:
    existing = pd.DataFrame({
        "game_id": ["a"], "season": [2025], "spread_line": [3.5],
    })
    fresh = pd.DataFrame({"game_id": ["b"], "season": [2026]})  # no spread_line
    merged = rd.merge_season(existing, fresh, 2026)
    assert merged.loc[merged["game_id"] == "b", "spread_line"].isna().all()


def test_nfl_games_from_schedules_keeps_played_and_lines() -> None:
    raw = pd.read_csv(REPO / "tests" / "fixtures" / "raw_nfl_schedules.csv")
    games = rd.nfl_games_from_schedules(raw, 2023)
    assert not games.empty
    assert (games["season"] == 2023).all()
    assert games["home_score"].notna().all()
    # The fixture carries no lines columns → aligned to null, not crashed.
    assert games["spread_line"].isna().all()
    # A season with no rows returns an empty frame (nothing to refresh).
    assert rd.nfl_games_from_schedules(raw, 1999).empty


def test_ncaaf_games_from_cfbd_normalizes_and_flips_spread() -> None:
    games_json = [
        {
            "id": 4011, "season": 2026, "week": 1, "seasonType": "regular",
            "startDate": "2026-09-05T19:30:00.000Z",
            "homeTeam": "Georgia", "awayTeam": "Clemson",
            "neutralSite": True, "homePoints": 31, "awayPoints": 17,
        },
        {  # unplayed → dropped
            "id": 4012, "season": 2026, "week": 2, "seasonType": "regular",
            "startDate": "2026-09-12T19:30:00.000Z",
            "homeTeam": "Alabama", "awayTeam": "Auburn",
            "neutralSite": False, "homePoints": None, "awayPoints": None,
        },
    ]
    lines_json = [
        {"id": 4011, "lines": [
            {"spread": -13.5, "overUnder": 49.5},
            {"spread": -14.5, "overUnder": 50.5},
        ]},
    ]
    games = rd.ncaaf_games_from_cfbd(games_json, lines_json, 2026)
    assert len(games) == 1
    row = games.iloc[0]
    assert row["game_id"] == "4011"
    assert row["season_type"] == "REG"
    assert bool(row["neutral_site"]) is True
    # CFBD negative-home-favored → positive-home-favored, at the median line.
    assert row["spread_line"] == 14.0
    assert row["total_line"] == 50.0
    assert pd.Timestamp(row["kickoff"]) == pd.Timestamp("2026-09-05 19:30:00")


def test_ncaaf_games_without_lines_still_join() -> None:
    games_json = [{
        "id": 5, "season": 2026, "week": 0, "seasonType": "regular",
        "startDate": "2026-08-22T16:00:00.000Z",
        "homeTeam": "Hawai'i", "awayTeam": "Stanford",
        "neutralSite": False, "homePoints": 21, "awayPoints": 20,
    }]
    games = rd.ncaaf_games_from_cfbd(games_json, [], 2026)
    assert len(games) == 1
    assert games.iloc[0]["spread_line"] is None or pd.isna(games.iloc[0]["spread_line"])
