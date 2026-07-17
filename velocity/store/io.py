"""Parquet + DuckDB IO helpers for the canonical store.

Parquet is the on-disk format (columnar, typed, portable). DuckDB gives us
zero-ops SQL over those parquet files when we need it. Every write validates
against the canonical schema first, so nothing malformed ever lands on disk.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import pandera.pandas as pa


def write_table(
    df: pd.DataFrame,
    path: str | Path,
    schema: type[pa.DataFrameModel] | None = None,
) -> Path:
    """Validate ``df`` against ``schema`` (if given) and write it as parquet.

    Returns the path written. Parent directories are created as needed.
    """
    if schema is not None:
        df = schema.validate(df)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return path


def read_table(
    path: str | Path,
    schema: type[pa.DataFrameModel] | None = None,
) -> pd.DataFrame:
    """Read a parquet table and optionally re-validate it against ``schema``."""
    df = pd.read_parquet(path)
    if schema is not None:
        df = schema.validate(df)
    return df


def query(sql: str, **tables: pd.DataFrame) -> pd.DataFrame:
    """Run a DuckDB SQL query over in-memory DataFrames.

    Each keyword becomes a queryable relation, e.g.
    ``query("select * from games", games=df)``.
    """
    con = duckdb.connect(database=":memory:")
    try:
        for name, frame in tables.items():
            con.register(name, frame)
        return con.execute(sql).fetch_df()
    finally:
        con.close()
