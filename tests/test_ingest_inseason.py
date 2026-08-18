"""MLB/WNBA ingest — the summer feeds normalize to canonical games, tolerantly.

Frozen payload samples in the providers' real shapes: statsapi's
dates→games nesting and ESPN's events→competitions→competitors. Completed
games only; anything missing an id, a team, or a final score is skipped.
"""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.ingest.inseason import (
    normalize_mlb_schedule,
    normalize_wehoop_schedule,
)
from velocity.store.schema import Games

_MLB_PAYLOAD = {
    "dates": [
        {"date": "2026-08-14", "games": [
            {"gamePk": 745001, "gameType": "R",
             "gameDate": "2026-08-15T00:10:00Z",
             "status": {"abstractGameState": "Final"},
             "teams": {
                 "away": {"score": 3, "team": {"id": 147, "name": "New York Yankees"}},
                 "home": {"score": 5, "team": {"id": 110, "name": "Baltimore Orioles"}},
             }},
            {"gamePk": 745002, "gameType": "R",
             "gameDate": "2026-08-15T00:40:00Z",
             "status": {"abstractGameState": "Live"},  # in progress → skipped
             "teams": {
                 "away": {"score": 1, "team": {"id": 119, "name": "Los Angeles Dodgers"}},
                 "home": {"score": 0, "team": {"id": 137, "name": "San Francisco Giants"}},
             }},
            {"gamePk": 745003, "gameType": "W",  # World Series → POST
             "gameDate": "2026-10-25T00:00:00Z",
             "status": {"abstractGameState": "Final"},
             "teams": {
                 "away": {"score": 2, "team": {"id": 121, "name": "New York Mets"}},
                 "home": {"score": 6, "team": {"id": 117, "name": "Houston Astros"}},
             }},
            {"gamePk": None, "gameType": "R",  # no id → skipped
             "status": {"abstractGameState": "Final"}, "teams": {}},
        ]},
    ],
}

def test_mlb_normalizes_finals_only_with_season_types() -> None:
    games = normalize_mlb_schedule(_MLB_PAYLOAD, season=2026)
    Games.validate(games)
    assert list(games["game_id"]) == ["745001", "745003"]
    row = games.set_index("game_id").loc["745001"]
    assert row["home_team"] == "Baltimore Orioles"
    assert (row["home_score"], row["away_score"]) == (5.0, 3.0)
    assert row["season_type"] == "REG"
    assert row["league"] == "mlb"
    assert games.set_index("game_id").loc["745003", "season_type"] == "POST"
    assert (games["week"] <= 25).all()


def _wehoop_frame() -> pd.DataFrame:
    # The wehoop release-parquet shape (sportsdataverse-data,
    # espn_wnba_schedules): tz-aware ET datetimes, ESPN season_type ints,
    # completed flags, full display names.
    ts = pd.Timestamp("2026-08-14 19:00:00-04:00")
    return pd.DataFrame([
        {"game_id": 401736001, "season": 2026, "season_type": 2,
         "game_date_time": ts, "status_type_completed": True,
         "neutral_site": False,
         "home_display_name": "Las Vegas Aces", "home_score": 88,
         "away_display_name": "Seattle Storm", "away_score": 79},
        {"game_id": 401736002, "season": 2026, "season_type": 2,
         "game_date_time": ts, "status_type_completed": False,  # future game
         "neutral_site": False,
         "home_display_name": "New York Liberty", "home_score": 0,
         "away_display_name": "Indiana Fever", "away_score": 0},
        {"game_id": 401736003, "season": 2026, "season_type": 3,  # playoffs
         "game_date_time": ts, "status_type_completed": True,
         "neutral_site": True,
         "home_display_name": "Minnesota Lynx", "home_score": 90,
         "away_display_name": "Phoenix Mercury", "away_score": 81},
    ])


def test_wehoop_schedule_normalizes_completed_games() -> None:
    games = normalize_wehoop_schedule(_wehoop_frame(), season=2026)
    Games.validate(games)
    assert list(games["game_id"]) == ["401736001", "401736003"]
    row = games.iloc[0]
    assert row["home_team"] == "Las Vegas Aces"
    assert (row["home_score"], row["away_score"]) == (88.0, 79.0)
    assert row["league"] == "wnba"
    assert games.iloc[1]["season_type"] == "POST"
    assert bool(games.iloc[1]["neutral_site"]) is True
    assert normalize_wehoop_schedule(pd.DataFrame(), 2026).empty
    assert normalize_wehoop_schedule(None, 2026).empty


def test_empty_payloads() -> None:
    assert normalize_mlb_schedule({}, 2026).empty
    assert normalize_mlb_schedule(None, 2026).empty


def test_scores_fit_runs_on_the_inseason_frame() -> None:
    # The whole point: the league-agnostic scores fit consumes these frames.
    from velocity.features.scores import fit_scores_ratings

    games = normalize_mlb_schedule(_MLB_PAYLOAD, season=2026)
    ratings = fit_scores_ratings(games, ridge_lambda=5.0)
    assert ratings.expected_points("Baltimore Orioles", "New York Yankees",
                                   at_home=True) == pytest.approx(
        5.0, abs=3.0  # two games of data — just prove the plumbing prices
    )


def test_mlb_suspended_game_dedupes_to_the_completion_row() -> None:
    # statsapi lists a suspended/resumed game under both dates, same gamePk;
    # the normalizer keeps the last (completion-date) row instead of failing
    # the schema's unique-id check. Proven live: gamePk 745180 (backfill run
    # 32181420568).
    payload = {"dates": [
        {"date": "2026-06-01", "games": [
            {"gamePk": 745180, "gameType": "R",
             "gameDate": "2026-06-01T23:00:00Z",
             "status": {"abstractGameState": "Final"},
             "teams": {
                 "away": {"score": 2, "team": {"id": 1, "name": "Chicago Cubs"}},
                 "home": {"score": 4, "team": {"id": 2, "name": "St. Louis Cardinals"}},
             }},
        ]},
        {"date": "2026-06-02", "games": [
            {"gamePk": 745180, "gameType": "R",
             "gameDate": "2026-06-02T23:00:00Z",
             "status": {"abstractGameState": "Final"},
             "teams": {
                 "away": {"score": 3, "team": {"id": 1, "name": "Chicago Cubs"}},
                 "home": {"score": 4, "team": {"id": 2, "name": "St. Louis Cardinals"}},
             }},
        ]},
    ]}
    games = normalize_mlb_schedule(payload, season=2026)
    assert list(games["game_id"]) == ["745180"]
    assert games.iloc[0]["away_score"] == 3.0  # the completion row won


def test_mlb_franchise_filter_and_athletics_rename() -> None:
    # Live backfill lesson: statsapi's schedule carries WBC friendlies,
    # All-Star sides, spring split-squads vs minor-league clubs — and 2024's
    # "Oakland Athletics" naming. Only the 30 clubs enter, renamed for
    # franchise continuity, and spring (PRE) results never inform ratings.
    def _game(pk, gtype, home, away):
        return {"gamePk": pk, "gameType": gtype,
                "gameDate": "2024-06-01T23:00:00Z",
                "status": {"abstractGameState": "Final"},
                "teams": {"away": {"score": 1, "team": {"id": 1, "name": away}},
                          "home": {"score": 2, "team": {"id": 2, "name": home}}}}

    payload = {"dates": [{"date": "2024-06-01", "games": [
        _game(1, "R", "Oakland Athletics", "Seattle Mariners"),
        _game(2, "R", "American League All-Stars", "National League All-Stars"),
        _game(3, "E", "Dominican Republic", "Boston Red Sox"),
        _game(4, "S", "Boston Red Sox", "Tampa Bay Rays"),  # spring → excluded
        _game(5, "R", "Sacramento River Cats", "Athletics"),  # minor-league opp
    ]}]}
    games = normalize_mlb_schedule(payload, season=2024)
    assert list(games["game_id"]) == ["1"]
    assert games.iloc[0]["home_team"] == "Athletics"  # renamed for continuity


def test_wehoop_franchise_filter_drops_allstar_sides() -> None:
    frame = _wehoop_frame()
    extra = frame.iloc[[0]].copy()
    extra["game_id"] = 999
    extra["home_display_name"] = "TEAM CLARK"
    extra["away_display_name"] = "TEAM COLLIER"
    games = normalize_wehoop_schedule(
        pd.concat([frame, extra], ignore_index=True), season=2026
    )
    assert "999" not in set(games["game_id"])
    assert set(games["game_id"]) == {"401736001", "401736003"}
