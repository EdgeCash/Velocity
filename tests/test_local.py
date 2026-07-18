"""Local dataset harness — reading real files onto the canonical schema."""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.ingest.local import describe, load_games, load_plays, read_data_file
from velocity.store.schema import Games, Plays


def _write(df: pd.DataFrame, path) -> str:
    df.to_csv(path, index=False)
    return str(path)


def _canonical_games() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game_id": ["G1", "G2"],
            "season": [2023, 2023],
            "week": [1, 1],
            "home_team": ["KC", "SF"],
            "away_team": ["DET", "PIT"],
            "home_score": [21, 30],
            "away_score": [20, 7],
            "kickoff": ["2023-09-07 20:20", "2023-09-10 13:00"],
        }
    )


def _canonical_plays() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "play_id": [1, 2, 3],
            "game_id": ["G1", "G1", "G1"],
            "season": [2023, 2023, 2023],
            "week": [1, 1, 1],
            "posteam": ["KC", "DET", "KC"],
            "defteam": ["DET", "KC", "DET"],
            "epa": [0.4, -0.2, 0.1],
        }
    )


def test_read_csv_and_parquet_round_trip(tmp_path) -> None:
    df = _canonical_games()
    csv = tmp_path / "g.csv"
    df.to_csv(csv, index=False)
    pq = tmp_path / "g.parquet"
    df.to_parquet(pq, index=False)
    assert len(read_data_file(csv)) == 2
    assert len(read_data_file(pq)) == 2


def test_unsupported_extension_rejected(tmp_path) -> None:
    p = tmp_path / "g.txt"
    p.write_text("nope")
    with pytest.raises(ValueError, match="unsupported data file"):
        read_data_file(p)


def test_load_games_injects_league_and_defaults(tmp_path) -> None:
    path = _write(_canonical_games(), tmp_path / "games.csv")
    games = load_games(path, league="nfl")
    Games.validate(games)
    assert set(games["league"]) == {"nfl"}
    assert set(games["season_type"]) == {"REG"}  # defaulted
    assert (~games["neutral_site"]).all()  # defaulted False
    assert games["kickoff"].dtype.kind == "M"  # parsed to datetime


def test_load_games_rename_bridge(tmp_path) -> None:
    raw = pd.DataFrame(
        {
            "gid": ["G1"],
            "yr": [2023],
            "wk": [1],
            "home": ["KC"],
            "visitor": ["DET"],
            "hs": [21],
            "vs": [20],
            "date": ["2023-09-07 20:20"],
        }
    )
    path = _write(raw, tmp_path / "games.csv")
    games = load_games(
        path,
        league="nfl",
        rename={
            "gid": "game_id", "yr": "season", "wk": "week", "home": "home_team",
            "visitor": "away_team", "hs": "home_score", "vs": "away_score", "date": "kickoff",
        },
    )
    Games.validate(games)
    assert games.loc[0, "game_id"] == "G1"
    assert games.loc[0, "away_team"] == "DET"


def test_load_games_requires_kickoff(tmp_path) -> None:
    path = _write(_canonical_games().drop(columns=["kickoff"]), tmp_path / "games.csv")
    with pytest.raises(ValueError, match="kickoff"):
        load_games(path, league="nfl")


def test_load_plays_derives_success_from_epa(tmp_path) -> None:
    path = _write(_canonical_plays(), tmp_path / "plays.csv")
    plays = load_plays(path)
    Plays.validate(plays)
    # success is derived: epa 0.4 → True, -0.2 → False.
    assert bool(plays.loc[0, "success"]) is True
    assert bool(plays.loc[1, "success"]) is False


def test_load_plays_rename_and_missing_optionals(tmp_path) -> None:
    raw = pd.DataFrame(
        {
            "pid": [1, 2],
            "gid": ["G1", "G1"],
            "season": [2023, 2023],
            "week": [1, 1],
            "off": ["KC", "DET"],
            "deff": ["DET", "KC"],
            "ppa": [0.5, -0.3],
        }
    )
    path = _write(raw, tmp_path / "plays.csv")
    plays = load_plays(
        path,
        rename={
            "pid": "play_id", "gid": "game_id", "off": "posteam",
            "deff": "defteam", "ppa": "epa",
        },
    )
    Plays.validate(plays)
    assert plays["play_type"].isna().all()  # optional, filled null
    assert bool(plays.loc[0, "success"]) is True


def test_load_plays_missing_required_raises(tmp_path) -> None:
    path = _write(_canonical_plays().drop(columns=["game_id"]), tmp_path / "plays.csv")
    with pytest.raises(ValueError, match="game_id"):
        load_plays(path)


def test_describe_lists_columns(tmp_path) -> None:
    path = _write(_canonical_games(), tmp_path / "games.csv")
    summary = describe(path)
    assert "game_id" in summary.index
    assert "dtype" in summary.columns
