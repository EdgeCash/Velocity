"""Player ratings — process for passers, usage and efficiency for everyone.

The floors are the point. A rate computed on two carries describes the sample,
not the player, so it comes back null rather than spectacular
(docs/FOOTBALL_PAL.md).
"""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.features.players import (
    MIN_CARRIES,
    MIN_DROPBACKS,
    PLAYER_COLUMNS,
    player_ratings,
    quarterback_process,
)


def _weeks() -> pd.DataFrame:
    rows = []
    for week in range(1, 9):
        rows.append({
            "season": 2026, "week": week, "game_id": f"g{week}",
            "player_id": "rb1", "player_name": "Workhorse Back", "team": "GB",
            "position": "RB", "carries": 15, "rush_yards": 75, "targets": 3,
            "receptions": 2, "receiving_yards": 14, "dk_points": 12.0,
        })
        rows.append({
            "season": 2026, "week": week, "game_id": f"g{week}",
            "player_id": "rb2", "player_name": "Cameo Back", "team": "GB",
            "position": "RB", "carries": 1, "rush_yards": 30, "targets": 0,
            "receptions": 0, "receiving_yards": 0, "dk_points": 3.0,
        })
    return pd.DataFrame(rows)


def test_a_rate_below_its_volume_floor_is_null_not_spectacular() -> None:
    rated = player_ratings(_weeks(), "nfl").set_index("player_id")
    work = rated.loc["rb1"]
    assert work["carries"] == 120
    assert work["yards_per_carry"] == pytest.approx(5.0)

    # Eight carries for 240 yards is a 30-yard average and a meaningless one.
    cameo = rated.loc["rb2"]
    assert cameo["carries"] == 8
    assert cameo["carries"] < MIN_CARRIES
    assert pd.isna(cameo["yards_per_carry"])
    # The volume itself still shows, so the reader can see why it is blank.
    assert cameo["rush_yards"] == 240


def test_per_game_numbers_use_games_actually_played() -> None:
    rated = player_ratings(_weeks(), "nfl").set_index("player_id")
    assert rated.loc["rb1", "games"] == 8
    assert rated.loc["rb1", "dk_points_per_game"] == pytest.approx(12.0)


def test_identity_comes_from_the_players_most_recent_week() -> None:
    """A midseason trade shows the team he is on now."""
    weeks = _weeks()
    weeks.loc[weeks["week"] >= 7, "team"] = "PIT"
    rated = player_ratings(weeks, "nfl").set_index("player_id")
    assert rated.loc["rb1", "team"] == "PIT"


def _dropbacks(player: str, n: int, epa: float, cpoe: float) -> pd.DataFrame:
    return pd.DataFrame({
        "season": [2026] * n, "passer_player_id": [player] * n,
        "qb_epa": [epa] * n, "cpoe": [cpoe] * n,
    })


def test_passer_process_is_averaged_over_dropbacks_above_the_floor() -> None:
    plays = pd.concat([
        _dropbacks("qb1", MIN_DROPBACKS + 10, 0.2, 4.0),
        _dropbacks("qb2", 5, 2.0, 30.0),
    ], ignore_index=True)
    process = quarterback_process(plays, [2026]).set_index("player_id")
    assert process.loc["qb1", "epa_per_dropback"] == pytest.approx(0.2)
    assert process.loc["qb1", "cpoe"] == pytest.approx(4.0)
    # Five dropbacks is not a quarterback rating, however good they were.
    assert process.loc["qb2", "dropbacks"] == 5
    assert pd.isna(process.loc["qb2", "epa_per_dropback"])
    assert pd.isna(process.loc["qb2", "cpoe"])


def test_a_league_with_no_per_player_epa_gets_usage_only() -> None:
    """College banks box scores but no per-player EPA, and says so with nulls."""
    rated = player_ratings(_weeks(), "ncaaf", plays=None).set_index("player_id")
    assert pd.isna(rated.loc["rb1", "epa_per_dropback"])
    assert pd.isna(rated.loc["rb1", "dropbacks"])
    assert rated.loc["rb1", "yards_per_carry"] == pytest.approx(5.0)
    assert (rated["league"] == "ncaaf").all()


def test_the_passer_process_joins_onto_the_box_score_rows() -> None:
    weeks = _weeks()
    weeks = pd.concat([weeks, pd.DataFrame([{
        "season": 2026, "week": 1, "game_id": "g1", "player_id": "qb1",
        "player_name": "Starter", "team": "GB", "position": "QB",
        "carries": 2, "rush_yards": 5, "targets": 0, "receptions": 0,
        "receiving_yards": 0, "dk_points": 20.0,
    }])], ignore_index=True)
    plays = _dropbacks("qb1", MIN_DROPBACKS + 10, 0.25, 3.0)
    rated = player_ratings(weeks, "nfl", plays=plays).set_index("player_id")
    assert rated.loc["qb1", "epa_per_dropback"] == pytest.approx(0.25)
    assert rated.loc["qb1", "dropbacks"] == MIN_DROPBACKS + 10


def test_players_who_did_nothing_are_left_out() -> None:
    """The box score lists everyone who dressed; a table of zeroes buries the
    players who actually played."""
    weeks = pd.concat([_weeks(), pd.DataFrame([{
        "season": 2026, "week": 1, "game_id": "g1", "player_id": "dl1",
        "player_name": "Defensive End", "team": "GB", "position": "DE",
        "carries": 0, "rush_yards": 0, "targets": 0, "receptions": 0,
        "receiving_yards": 0, "dk_points": 0.0,
    }])], ignore_index=True)
    assert "dl1" not in set(player_ratings(weeks, "nfl")["player_id"])


def test_empty_and_shapeless_inputs_give_a_typed_empty_table() -> None:
    assert list(player_ratings(pd.DataFrame(), "nfl").columns) == PLAYER_COLUMNS
    assert player_ratings(pd.DataFrame({"x": [1]}), "nfl").empty
    assert quarterback_process(None, [2026]).empty
    assert quarterback_process(pd.DataFrame(), [2026]).empty
