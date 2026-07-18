"""Round-trip and SQL access over the canonical store."""

from __future__ import annotations

import pandas as pd
from velocity.store.io import query, read_table, write_table
from velocity.store.schema import Games


def test_parquet_round_trip_preserves_data(games: pd.DataFrame, tmp_path) -> None:
    path = write_table(games, tmp_path / "games.parquet", schema=Games)
    back = read_table(path, schema=Games)
    assert list(back["game_id"]) == list(games["game_id"])
    assert len(back) == len(games)


def test_write_rejects_invalid_frame(games: pd.DataFrame, tmp_path) -> None:
    bad = games.copy()
    bad.loc[0, "league"] = "xfl"
    import pytest
    from pandera.errors import SchemaError

    with pytest.raises(SchemaError):
        write_table(bad, tmp_path / "bad.parquet", schema=Games)
    assert not (tmp_path / "bad.parquet").exists()


def test_duckdb_query(games: pd.DataFrame) -> None:
    out = query(
        "select league, count(*) as n from games group by league",
        games=games,
    )
    assert int(out.loc[out["league"] == "nfl", "n"].iloc[0]) == len(games)
