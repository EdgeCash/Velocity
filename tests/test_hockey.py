"""NHL ingest — schedule normalization, goalie starts, day scores."""

from __future__ import annotations

import pandas as pd
from velocity.ingest.hockey import (
    NHL_CLUBS,
    NHL_TEAM_ALIASES,
    extract_goalie_starts,
    normalize_day_scores,
    normalize_nhl_games,
)


def _game(gid: int, when: str, gtype: int = 2, state: str = "OFF",
          home_score: int | None = 3, away_score: int | None = 2,
          last: str = "REG") -> dict:
    home: dict = {"abbrev": "TOR"}
    away: dict = {"abbrev": "MTL"}
    if home_score is not None:
        home["score"] = home_score
        away["score"] = away_score
    return {"id": gid, "gameType": gtype, "gameState": state,
            "startTimeUTC": when, "homeTeam": home, "awayTeam": away,
            "gameOutcome": {"lastPeriodType": last}}


def test_normalize_games_filters_and_flags() -> None:
    payload = {"games": [
        _game(1, "2024-10-10T23:00:00Z"),
        _game(2, "2024-09-25T23:00:00Z", gtype=1),           # preseason: out
        _game(3, "2025-04-30T23:00:00Z", gtype=3, last="OT"),
        _game(4, "2025-01-05T00:00:00Z", state="FUT",
              home_score=None, away_score=None),
    ]}
    frame = normalize_nhl_games([payload, payload], 2024)  # dupes collapse
    assert len(frame) == 3
    assert set(frame["season_type"]) == {"REG", "POST"}
    played = frame[frame["game_id"] == "3"].iloc[0]
    assert played["last_period_type"] == "OT"
    future = frame[frame["game_id"] == "4"].iloc[0]
    assert pd.isna(future["home_score"])


def test_week_buckets_stay_monotone_across_new_year() -> None:
    payload = {"games": [
        _game(1, "2024-10-10T23:00:00Z"),
        _game(2, "2024-12-30T23:00:00Z"),
        _game(3, "2025-01-03T00:00:00Z"),
        _game(4, "2025-04-10T23:00:00Z"),
    ]}
    frame = normalize_nhl_games([payload], 2024).sort_values("kickoff")
    weeks = frame["week"].tolist()
    assert weeks == sorted(weeks)
    assert weeks[0] >= 1 and weeks[-1] <= 25


def test_extract_goalie_starts_takes_the_flagged_starter() -> None:
    payload = {"playerByGameStats": {
        "homeTeam": {"goalies": [
            {"playerId": 1, "starter": False, "name": {"default": "Backup"}},
            {"playerId": 2, "starter": True, "name": {"default": "Starter H"},
             "shotsAgainst": 30, "saves": 28, "goalsAgainst": 2},
        ]},
        "awayTeam": {"goalies": [
            {"playerId": 3, "starter": True, "name": {"default": "Starter A"},
             "shotsAgainst": 25, "saves": 22, "goalsAgainst": 3},
        ]},
    }}
    rows = extract_goalie_starts(payload, "g1")
    assert len(rows) == 2
    home = next(r for r in rows if r["side"] == "home")
    assert home["starter_id"] == "2"
    assert home["shots_against"] == 30.0


def test_normalize_day_scores_finals_only() -> None:
    payload = {"games": [
        _game(1, "2026-01-15T00:00:00Z"),
        _game(2, "2026-01-15T03:00:00Z", state="LIVE"),
    ]}
    finals = normalize_day_scores(payload)
    assert len(finals) == 1
    assert finals.iloc[0]["home_score"] == 3.0


def test_alias_table_covers_every_club() -> None:
    assert set(NHL_TEAM_ALIASES.values()) >= set(NHL_CLUBS) - {"UTA"} | {"UTA", "ARI"}
    # Every alias maps to a plausible NHL code.
    assert all(2 <= len(code) <= 3 for code in NHL_TEAM_ALIASES.values())
