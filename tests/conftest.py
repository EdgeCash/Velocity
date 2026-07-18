"""Shared test fixtures — loaded from frozen CSVs so the suite runs offline."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def games() -> pd.DataFrame:
    df = pd.read_csv(FIXTURES / "nfl_games.csv")
    df["kickoff"] = pd.to_datetime(df["kickoff"])
    return df


@pytest.fixture
def lines() -> pd.DataFrame:
    df = pd.read_csv(FIXTURES / "lines.csv")
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


@pytest.fixture
def plays() -> pd.DataFrame:
    """Frozen synthetic play-by-play (see ``tests/fixtures/_generate.py``)."""
    return pd.read_csv(FIXTURES / "nfl_plays.csv")


@pytest.fixture
def market() -> pd.DataFrame:
    """Frozen two-sided line archive (open + close, two books) for one game."""
    df = pd.read_csv(FIXTURES / "nfl_market.csv")
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df
