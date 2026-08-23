"""MLB statsapi season stats → DFS projections frame (pure layer)."""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.dfs.optimizer import lineup_pool
from velocity.dfs.scoring import dk_expected_points_mlb
from velocity.ingest.mlbstats import normalize_season_stats, stats_long_frame

HITTING = {"stats": [{"totalSplits": 2, "splits": [
    {"player": {"id": 1, "fullName": "Juan Sóto",
                "primaryPosition": {"abbreviation": "RF"}},
     "team": {"name": "New York Mets"},
     "stat": {"gamesPlayed": 120, "hits": 130, "doubles": 24, "triples": 1,
              "homeRuns": 35, "rbi": 90, "runs": 100, "baseOnBalls": 110,
              "hitByPitch": 4, "stolenBases": 10, "avg": ".280"}},
    {"player": {"id": 2, "fullName": "Some Pitcher",
                "primaryPosition": {"abbreviation": "P"}},
     "team": {"name": "Atlanta Braves"},
     "stat": {"gamesPlayed": 17, "hits": 1, "doubles": 0}},  # pitcher batting
]}]}

PITCHING = {"stats": [{"totalSplits": 1, "splits": [
    {"player": {"id": 2, "fullName": "Some Pitcher",
                "primaryPosition": {"abbreviation": "P"}},
     "team": {"name": "Atlanta Braves"},
     "stat": {"gamesPlayed": 25, "gamesStarted": 25, "inningsPitched": "150.2",
              "strikeOuts": 160, "wins": 11, "earnedRuns": 55, "hits": 130,
              "baseOnBalls": 40}},
]}]}


def test_normalize_maps_keys_and_converts_innings() -> None:
    hit = normalize_season_stats(HITTING, "hitting", 2026)
    soto = hit[hit["player_name"] == "Juan Sóto"].set_index("stat")["value"]
    assert soto["h"] == 130.0 and soto["2b"] == 24.0 and soto["g"] == 120.0
    assert "avg" not in soto.index  # unmapped keys stay out
    pit = normalize_season_stats(PITCHING, "pitching", 2026)
    ip = pit[pit["stat"] == "ip"]["value"].iloc[0]
    assert ip == pytest.approx(150 + 2 / 3)  # "150.2" is the innings notation
    assert (pit["week"] == 0).all() and (pit["source"] == "statsapi").all()


def test_long_frame_keeps_one_group_per_player() -> None:
    frame = stats_long_frame(
        normalize_season_stats(HITTING, "hitting", 2026),
        normalize_season_stats(PITCHING, "pitching", 2026),
    )
    pitcher = frame[frame["player_name"] == "Some Pitcher"]
    # The pitcher's batting line is dropped — his "h" means hits ALLOWED.
    assert set(pitcher["stat"]) == {"g", "gs", "ip", "k", "w", "er", "h", "bb"}
    assert "Juan Sóto" in set(frame["player_name"])


def test_statsapi_frame_scores_and_joins_across_accents() -> None:
    frame = stats_long_frame(
        normalize_season_stats(HITTING, "hitting", 2026),
        normalize_season_stats(PITCHING, "pitching", 2026),
    )
    points = dk_expected_points_mlb(frame)
    soto = points[points["player_name"] == "Juan Sóto"].iloc[0]
    per_game = (130 * 3 + 24 * 2 + 1 * 5 + 35 * 7 + 90 * 2 + 100 * 2
                + 110 * 2 + 4 * 2 + 10 * 5) / 120
    assert soto["points"] == pytest.approx(round(per_game, 2), abs=0.01)
    # DK spells the name unaccented; the pool join folds the accent.
    board = pd.DataFrame([{"draft_group_id": "1", "player_id": "d1",
                           "player_name": "Juan Soto", "position": "OF",
                           "salary": 6000, "team": "NYM",
                           "competition": "NYM @ ATL"}])
    pool = lineup_pool(board, points)
    assert len(pool) == 1 and pool.iloc[0]["points"] == soto["points"]
