"""MLB/WNBA ingest — the summer feeds normalize to canonical games, tolerantly.

Frozen payload samples in the providers' real shapes: statsapi's
dates→games nesting and ESPN's events→competitions→competitors. Completed
games only; anything missing an id, a team, or a final score is skipped.
"""

from __future__ import annotations

import pytest
from velocity.ingest.inseason import normalize_mlb_schedule, normalize_wnba_scoreboard
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

_WNBA_PAYLOAD = {
    "events": [
        {"id": "401736001", "date": "2026-08-14T23:00Z",
         "season": {"year": 2026, "type": 2},
         "competitions": [{
             "neutralSite": False,
             "status": {"type": {"completed": True}},
             "competitors": [
                 {"homeAway": "home", "score": "88",
                  "team": {"displayName": "Las Vegas Aces"}},
                 {"homeAway": "away", "score": "79",
                  "team": {"displayName": "Seattle Storm"}},
             ],
         }]},
        {"id": "401736002", "date": "2026-08-15T00:00Z",
         "season": {"year": 2026, "type": 2},
         "competitions": [{
             "status": {"type": {"completed": False}},  # live → skipped
             "competitors": [
                 {"homeAway": "home", "score": "40",
                  "team": {"displayName": "New York Liberty"}},
                 {"homeAway": "away", "score": "39",
                  "team": {"displayName": "Indiana Fever"}},
             ],
         }]},
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


def test_wnba_normalizes_completed_events() -> None:
    games = normalize_wnba_scoreboard(_WNBA_PAYLOAD, season=2026)
    Games.validate(games)
    assert list(games["game_id"]) == ["401736001"]
    row = games.iloc[0]
    assert row["home_team"] == "Las Vegas Aces"
    assert row["away_team"] == "Seattle Storm"
    assert (row["home_score"], row["away_score"]) == (88.0, 79.0)
    assert row["league"] == "wnba"


def test_empty_payloads() -> None:
    assert normalize_mlb_schedule({}, 2026).empty
    assert normalize_mlb_schedule(None, 2026).empty
    assert normalize_wnba_scoreboard({"events": []}, 2026).empty


def test_scores_fit_runs_on_the_inseason_frame() -> None:
    # The whole point: the league-agnostic scores fit consumes these frames.
    from velocity.features.scores import fit_scores_ratings

    games = normalize_mlb_schedule(_MLB_PAYLOAD, season=2026)
    ratings = fit_scores_ratings(games, ridge_lambda=5.0)
    assert ratings.expected_points("Baltimore Orioles", "New York Yankees",
                                   at_home=True) == pytest.approx(
        5.0, abs=3.0  # two games of data — just prove the plumbing prices
    )
