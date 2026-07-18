"""NCAAF ingest — CFBD frames normalize to the canonical store, tolerantly.

College data is messy, so these tests assert the adapter drops unusable rows and
coerces malformed values to null instead of crashing — while still validating
against the canonical schema.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from velocity.ingest.ncaaf import normalize_games, normalize_plays
from velocity.store.io import read_table, write_table
from velocity.store.schema import Games, Plays

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def raw_games() -> pd.DataFrame:
    return pd.read_csv(FIXTURES / "raw_cfbd_games.csv")


@pytest.fixture
def raw_plays() -> pd.DataFrame:
    return pd.read_csv(FIXTURES / "raw_cfbd_plays.csv")


def test_games_validate_and_tag_league(raw_games: pd.DataFrame) -> None:
    games = normalize_games(raw_games)
    Games.validate(games)
    assert set(games["league"]) == {"ncaaf"}


def test_games_drop_rows_missing_essentials(raw_games: pd.DataFrame) -> None:
    games = normalize_games(raw_games)
    # The row with no id / no teams is dropped; four usable games remain.
    assert len(games) == 4
    assert "401888888" not in set(games["game_id"])


def test_postseason_maps_to_post(raw_games: pd.DataFrame) -> None:
    games = normalize_games(raw_games).set_index("game_id")
    assert games.loc["401525434", "season_type"] == "POST"
    assert games.loc["401550883", "season_type"] == "REG"


def test_neutral_site_and_null_scores(raw_games: pd.DataFrame) -> None:
    games = normalize_games(raw_games).set_index("game_id")
    assert bool(games.loc["401551786", "neutral_site"]) is True
    assert pd.isna(games.loc["401999999", "home_score"])  # unplayed


def test_plays_validate_and_are_tolerant(raw_plays: pd.DataFrame) -> None:
    plays = normalize_plays(raw_plays)
    Plays.validate(plays)
    # Row with no game_id dropped; five plays remain.
    assert len(plays) == 5


def test_plays_coerce_malformed_values_to_null(raw_plays: pd.DataFrame) -> None:
    plays = normalize_plays(raw_plays).set_index("play_id")
    # ppa "not_a_number" and an out-of-range down (7) both become null, not errors.
    assert pd.isna(plays.loc["1005", "epa"])
    assert pd.isna(plays.loc["1005", "down"])
    assert pd.isna(plays.loc["1005", "success"])


def test_ppa_becomes_epa_and_success(raw_plays: pd.DataFrame) -> None:
    plays = normalize_plays(raw_plays).set_index("play_id")
    assert plays.loc["1001", "epa"] == pytest.approx(0.32)
    assert bool(plays.loc["1001", "success"]) is True
    assert bool(plays.loc["1003", "success"]) is False  # negative ppa


def test_games_round_trip_through_store(raw_games: pd.DataFrame, tmp_path) -> None:
    games = normalize_games(raw_games)
    path = write_table(games, tmp_path / "ncaaf_games.parquet", schema=Games)
    back = read_table(path, schema=Games)
    assert list(back["game_id"]) == list(games["game_id"])
