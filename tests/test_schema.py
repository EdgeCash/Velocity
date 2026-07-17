"""Schema validation is the store's contract with every ingest adapter."""

from __future__ import annotations

import pandas as pd
import pytest
from pandera.errors import SchemaError

from velocity.store.schema import Games, Lines


def test_games_fixture_validates(games: pd.DataFrame) -> None:
    validated = Games.validate(games)
    assert len(validated) == len(games)


def test_lines_fixture_validates(lines: pd.DataFrame) -> None:
    validated = Lines.validate(lines)
    assert len(validated) == len(lines)


def test_unplayed_game_allows_null_scores(games: pd.DataFrame) -> None:
    future = games[games["season"] == 2099]
    assert future["home_score"].isna().all()
    Games.validate(games)  # must not raise on null scores


def test_bad_league_is_rejected(games: pd.DataFrame) -> None:
    bad = games.copy()
    bad.loc[0, "league"] = "xfl"
    with pytest.raises(SchemaError):
        Games.validate(bad)


def test_duplicate_game_id_is_rejected(games: pd.DataFrame) -> None:
    dupe = pd.concat([games, games.iloc[[0]]], ignore_index=True)
    with pytest.raises(SchemaError):
        Games.validate(dupe)


def test_bad_market_is_rejected(lines: pd.DataFrame) -> None:
    bad = lines.copy()
    bad.loc[0, "market"] = "parlay"
    with pytest.raises(SchemaError):
        Lines.validate(bad)
