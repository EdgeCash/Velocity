"""PrizePicks ingest — JSON:API board payloads normalize tolerantly.

The board API is unofficial and unpinned, so the normalizer must join the
projection rows to their included player/league resources through alias
keys, drop rows that price nothing, and degrade gracefully when an include
is missing — while never crashing on a partial payload.
"""

from __future__ import annotations

import pandas as pd
from velocity.ingest.prizepicks import normalize_projections

_PAYLOAD = {
    "data": [
        {
            "type": "projection", "id": "101",
            "attributes": {
                "line_score": 249.5, "stat_type": "Pass Yards",
                "odds_type": "standard", "status": "pre_game",
                "start_time": "2026-09-07T17:00:00-04:00", "is_promo": False,
            },
            "relationships": {
                "new_player": {"data": {"type": "new_player", "id": "p1"}},
                "league": {"data": {"type": "league", "id": "9"}},
            },
        },
        {
            "type": "projection", "id": "102",
            "attributes": {
                # A shifted alternate: kept, but flagged by odds_type.
                "line_score": 279.5, "stat_type": "Pass Yards",
                "odds_type": "demon", "status": "pre_game",
            },
            "relationships": {
                "new_player": {"data": {"type": "new_player", "id": "p1"}},
                "league": {"data": {"type": "league", "id": "9"}},
            },
        },
        {
            "type": "projection", "id": "103",
            "attributes": {
                # Player include missing from the payload → null identity,
                # but the line still banks.
                "line_score": 5.5, "stat_type": "Receptions",
            },
            "relationships": {
                "new_player": {"data": {"type": "new_player", "id": "ghost"}},
            },
        },
        {
            "type": "projection", "id": "104",
            # No line at all → prices nothing → dropped.
            "attributes": {"stat_type": "Pass TDs", "line_score": None},
        },
    ],
    "included": [
        {
            "type": "new_player", "id": "p1",
            "attributes": {"display_name": "Justin Fields", "team": "NYJ",
                           "position": "QB", "league": "NFL"},
        },
        {"type": "league", "id": "9", "attributes": {"name": "NFL"}},
    ],
}


def test_normalize_joins_players_and_leagues() -> None:
    board = normalize_projections(_PAYLOAD)
    assert list(board["projection_id"]) == ["101", "102", "103"]  # 104 dropped
    top = board.set_index("projection_id").loc["101"]
    assert top["player_name"] == "Justin Fields"
    assert top["team"] == "NYJ"
    assert top["league"] == "NFL"
    assert top["line"] == 249.5
    assert top["odds_type"] == "standard"


def test_alternates_flagged_and_missing_includes_degrade() -> None:
    board = normalize_projections(_PAYLOAD).set_index("projection_id")
    assert board.loc["102", "odds_type"] == "demon"
    ghost = board.loc["103"]
    assert pd.isna(ghost["player_name"])  # include missing, row kept
    assert ghost["line"] == 5.5
    assert ghost["odds_type"] == "standard"  # absent → the default board line


def test_empty_and_malformed_payloads() -> None:
    assert normalize_projections({}).empty
    assert normalize_projections(None).empty
    assert normalize_projections({"data": ["not-a-mapping"]}).empty
