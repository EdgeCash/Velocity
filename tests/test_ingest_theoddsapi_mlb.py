"""MLB plumbing (Phase M0) — the football odds path already speaks baseball.

The Odds API ingest is sport-agnostic for the three game markets, so adding MLB
is a sport-key entry plus a league in the schema — no new normalizer. These tests
pin that: a frozen ``baseball_mlb`` ``/odds`` sample flattens to canonical
``Lines`` exactly like football, the collector's ``league`` tag rides along, and
the schema now accepts ``mlb``. All offline, against a frozen fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from pandera.errors import SchemaError
from velocity.ingest.theoddsapi import SPORT_KEYS, TheOddsAPIClient, normalize_odds_events
from velocity.store.schema import Games, Lines

FIXTURES = Path(__file__).parent / "fixtures"


def _mlb_events() -> list[dict]:
    """The frozen live-array ``/odds`` sample for one MLB game (one book)."""
    return json.loads((FIXTURES / "theoddsapi_mlb.json").read_text())


def test_sport_key_maps_mlb() -> None:
    assert SPORT_KEYS["mlb"] == "baseball_mlb"
    assert TheOddsAPIClient.sport_key("mlb") == "baseball_mlb"
    assert TheOddsAPIClient.sport_key("MLB") == "baseball_mlb"


def test_mlb_odds_normalize_and_validate() -> None:
    lines = normalize_odds_events(_mlb_events())
    Lines.validate(lines)
    assert set(lines["market"]) == {"moneyline", "spread", "total"}
    # 2 h2h + 2 spreads (run line) + 2 totals = 6; the batter prop is dropped.
    assert len(lines) == 6


def test_run_line_and_total_points_moneyline_null() -> None:
    lines = normalize_odds_events(_mlb_events())
    ml = lines[lines["market"] == "moneyline"]
    assert ml["point"].isna().all()
    # Baseball's spread market is the ±1.5 run line.
    assert set(lines[lines["market"] == "spread"]["point"]) == {-1.5, 1.5}
    assert set(lines[lines["market"] == "total"]["point"]) == {8.5}


def test_mlb_prices_are_integer_american() -> None:
    lines = normalize_odds_events(_mlb_events())
    assert lines["price"].dtype.kind == "i"
    assert {-155, 132}.issubset(set(lines["price"]))


def test_collector_league_tag_rides_along() -> None:
    """The collector adds ``league`` post-normalization; it must not break Lines."""
    lines = normalize_odds_events(_mlb_events()).assign(league="mlb")
    Lines.validate(lines)  # extra column is tolerated by the non-strict schema
    assert (lines["league"] == "mlb").all()


def _mlb_games_frame() -> pd.DataFrame:
    """A minimal one-row MLB schedule frame. Baseball has no weeks — ``kickoff`` is
    the point-in-time anchor — so ``week`` is a placeholder 0 (see docs/BUILD_MLB.md)."""
    return pd.DataFrame(
        {
            "game_id": ["mlb-2026-lad-sfg"],
            "league": ["mlb"],
            "season": [2026],
            "week": [0],
            "season_type": ["REG"],
            "kickoff": [pd.Timestamp("2026-07-24T02:10:00")],
            "home_team": ["LAD"],
            "away_team": ["SFG"],
            "neutral_site": [False],
            "roof": [None],
            "surface": [None],
            "home_score": [None],
            "away_score": [None],
        }
    )


def test_schema_accepts_mlb_league() -> None:
    Games.validate(_mlb_games_frame())


def test_schema_still_rejects_unknown_league() -> None:
    bad = _mlb_games_frame()
    bad.loc[0, "league"] = "nba"
    with pytest.raises(SchemaError):
        Games.validate(bad)
