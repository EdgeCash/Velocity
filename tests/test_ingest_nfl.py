"""NFL ingest — provider frames normalize to the canonical store, offline.

These tests exercise only the pure ``normalize_*`` mappings against frozen raw
samples that mimic nflverse's columns; the live ``load_*`` fetchers are not
touched (they hit the network and are out of the per-commit gate).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from velocity.features.team import fit_ratings
from velocity.ingest.nfl import normalize_pbp, normalize_rosters, normalize_schedules
from velocity.store.io import read_table, write_table
from velocity.store.schema import Games, Players, Plays

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def raw_schedules() -> pd.DataFrame:
    return pd.read_csv(FIXTURES / "raw_nfl_schedules.csv")


@pytest.fixture
def raw_pbp() -> pd.DataFrame:
    return pd.read_csv(FIXTURES / "raw_nfl_pbp.csv")


@pytest.fixture
def raw_rosters() -> pd.DataFrame:
    return pd.read_csv(FIXTURES / "raw_nfl_rosters.csv")


def test_schedules_validate_against_games(raw_schedules: pd.DataFrame) -> None:
    games = normalize_schedules(raw_schedules)
    Games.validate(games)
    assert list(games["league"].unique()) == ["nfl"]


def test_playoff_game_type_maps_to_post(raw_schedules: pd.DataFrame) -> None:
    games = normalize_schedules(raw_schedules)
    by_id = games.set_index("game_id")["season_type"]
    assert by_id["2023_22_SF_KC"] == "POST"  # Super Bowl (SB) collapses to POST
    assert by_id["2023_01_KC_DET"] == "REG"
    assert by_id["2023_00_LAR_LAC"] == "PRE"


def test_neutral_site_detected_from_location(raw_schedules: pd.DataFrame) -> None:
    games = normalize_schedules(raw_schedules).set_index("game_id")
    assert bool(games.loc["2023_22_SF_KC", "neutral_site"]) is True
    assert bool(games.loc["2023_01_KC_DET", "neutral_site"]) is False


def test_kickoff_is_parsed_datetime(raw_schedules: pd.DataFrame) -> None:
    games = normalize_schedules(raw_schedules).set_index("game_id")
    assert games["kickoff"].dtype.kind == "M"
    assert games.loc["2023_01_KC_DET", "kickoff"] == pd.Timestamp("2023-09-07 20:20:00")


def test_unplayed_game_scores_stay_null(raw_schedules: pd.DataFrame) -> None:
    games = normalize_schedules(raw_schedules).set_index("game_id")
    assert pd.isna(games.loc["2024_01_AAA_BBB", "home_score"])
    assert pd.isna(games.loc["2024_01_AAA_BBB", "away_score"])


def test_unknown_game_type_raises(raw_schedules: pd.DataFrame) -> None:
    bad = raw_schedules.copy()
    bad.loc[0, "game_type"] = "XFL"
    with pytest.raises(ValueError, match="unrecognized nflverse game_type"):
        normalize_schedules(bad)


def test_pbp_validates_against_plays(raw_pbp: pd.DataFrame) -> None:
    plays = normalize_pbp(raw_pbp)
    Plays.validate(plays)
    assert all(isinstance(v, str) for v in plays["play_id"])  # coerced to string ids
    assert set(plays["play_id"]) == {"1", "2", "3", "4", "5"}


def test_pbp_preserves_nullable_non_play_row(raw_pbp: pd.DataFrame) -> None:
    plays = normalize_pbp(raw_pbp)
    timeout = plays[plays["play_id"] == "5"].iloc[0]
    assert pd.isna(timeout["posteam"])
    # The crucial coercion check: a missing success must stay missing, not True.
    assert pd.isna(timeout["success"])
    assert bool(plays[plays["play_id"] == "1"].iloc[0]["success"]) is True


def test_pbp_fills_missing_optional_column(raw_pbp: pd.DataFrame) -> None:
    trimmed = raw_pbp.drop(columns=["epa"])
    plays = normalize_pbp(trimmed)
    assert plays["epa"].isna().all()
    Plays.validate(plays)


def test_rosters_validate_against_players(raw_rosters: pd.DataFrame) -> None:
    players = normalize_rosters(raw_rosters)
    Players.validate(players)
    assert set(players["player_id"]) >= {"00-0033873"}


def test_rosters_accept_display_name_fallback() -> None:
    raw = pd.DataFrame(
        {
            "player_id": ["00-0000001"],
            "player_display_name": ["Patrick Mahomes"],
            "position": ["QB"],
            "team": ["KC"],
            "season": [2023],
        }
    )
    players = normalize_rosters(raw)
    assert players.loc[0, "player_name"] == "Patrick Mahomes"


def test_rosters_without_name_column_raise() -> None:
    raw = pd.DataFrame({"player_id": ["x"], "position": ["QB"], "season": [2023]})
    with pytest.raises(ValueError, match="player_name or player_display_name"):
        normalize_rosters(raw)


def test_ingest_feeds_store_round_trip(raw_schedules: pd.DataFrame, tmp_path) -> None:
    games = normalize_schedules(raw_schedules)
    path = write_table(games, tmp_path / "games.parquet", schema=Games)
    back = read_table(path, schema=Games)
    assert list(back["game_id"]) == list(games["game_id"])


def test_ingest_feeds_ratings(raw_pbp: pd.DataFrame) -> None:
    # The whole point of ingest: normalized plays drop straight into the model.
    plays = normalize_pbp(raw_pbp)
    ratings = fit_ratings(plays, ridge_lambda=10.0)
    assert "KC" in ratings.offense
    assert "DET" in ratings.defense
