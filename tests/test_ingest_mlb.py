"""MLB ingest (Phase M1) — StatsAPI JSON flattens to the canonical store, offline.

Exercises the pure ``normalize_*`` layer against frozen StatsAPI-shaped fixtures:
schedule → ``Games`` and season splits → ``BaseballStats``. Covers the two things
that matter for an ingest adapter — schema validity + point-in-time safety (an
unplayed game must carry null scores) — plus the tolerance the college-data
adapter taught us to demand (bad/missing rows are dropped or coerced, never
crash). The network ``load_*`` functions are not touched here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from velocity.ingest.mlb import (
    normalize_boxscore,
    normalize_player_stats,
    normalize_schedule,
)
from velocity.store.schema import BaseballStats, Games

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# --- schedule → Games -------------------------------------------------------


def test_schedule_normalizes_and_validates() -> None:
    games = normalize_schedule(_load("mlb_schedule.json"))
    Games.validate(games)
    # The third game (null gamePk, empty teams) is dropped.
    assert list(games["game_id"]) == ["745804", "745820"]
    assert (games["league"] == "mlb").all()
    assert (games["season"] == 2026).all()


def test_unplayed_game_keeps_null_scores() -> None:
    """Point-in-time guard: a scheduled game has no result to leak."""
    games = normalize_schedule(_load("mlb_schedule.json")).set_index("game_id")
    final = games.loc["745804"]
    assert (final["home_score"], final["away_score"]) == (5.0, 3.0)
    preview = games.loc["745820"]
    assert pd.isna(preview["home_score"]) and pd.isna(preview["away_score"])


def test_kickoff_parsed_tz_naive() -> None:
    games = normalize_schedule(_load("mlb_schedule.json"))
    assert games["kickoff"].dtype.kind == "M"
    assert games["kickoff"].dt.tz is None
    assert str(games.set_index("game_id").loc["745804", "kickoff"]).startswith("2026-07-23 02:10")


def test_season_type_regular() -> None:
    games = normalize_schedule(_load("mlb_schedule.json"))
    assert set(games["season_type"]) == {"REG"}


def test_empty_schedule_yields_empty_valid_games() -> None:
    games = normalize_schedule({})
    Games.validate(games)
    assert games.empty


def test_suspended_game_listed_twice_is_deduped_to_the_final() -> None:
    """A resumed game shares its gamePk across two dates — keep the final listing.

    StatsAPI lists a suspended game on both its original and resumption date under
    the same ``gamePk``. Over a wide range that would break ``Games``' unique
    game_id rule; normalize keeps the last (resumption) row, which carries the final
    score.
    """
    def _team(name: str, s: int | None) -> dict:
        return {"team": {"name": name}, **({"score": s} if s is not None else {})}

    def _game(score: int | None) -> dict:
        return {
            "gamePk": 800001, "season": 2026, "gameType": "R", "gameDate": "2026-05-01T18:00:00Z",
            "teams": {"home": _team("Chicago Cubs", score), "away": _team("Miami Marlins", score)},
        }

    payload = {"dates": [
        {"games": [_game(None)]},   # suspended — no final yet
        {"games": [_game(4)]},      # resumed the next day — carries the final
    ]}
    games = normalize_schedule(payload)
    Games.validate(games)
    assert list(games["game_id"]) == ["800001"]  # one row, not two
    row = games.set_index("game_id").loc["800001"]
    assert (row["home_score"], row["away_score"]) == (4.0, 4.0)  # the resumption final


# --- season splits → BaseballStats ------------------------------------------


def test_hitting_normalizes_and_validates() -> None:
    bat = normalize_player_stats(_load("mlb_hitting.json"), "bat")
    BaseballStats.validate(bat)
    assert (bat["role"] == "bat").all()
    # The no-id split is dropped; two clean players + one malformed remain.
    assert set(bat["player_id"]) == {"660271", "605141", "999001"}


def test_singles_are_derived() -> None:
    bat = normalize_player_stats(_load("mlb_hitting.json"), "bat").set_index("player_id")
    # Ohtani: 150 H − 30 2B − 5 3B − 40 HR = 75 singles.
    assert bat.loc["660271", "singles"] == 75.0
    # Betts: 140 − 28 − 3 − 22 = 87.
    assert bat.loc["605141", "singles"] == 87.0


def test_malformed_stat_coerces_not_crashes() -> None:
    bat = normalize_player_stats(_load("mlb_hitting.json"), "bat").set_index("player_id")
    row = bat.loc["999001"]
    assert pd.isna(row["pa"])  # "NA" → null
    assert pd.isna(row["k"])  # null → null
    # singles still derive from the present fields: 5 H − 0 − 0 − 1 HR = 4.
    assert row["singles"] == 4.0


def test_pitching_normalizes_and_validates() -> None:
    pit = normalize_player_stats(_load("mlb_pitching.json"), "pit").set_index("player_id")
    BaseballStats.validate(pit.reset_index())
    row = pit.loc["477132"]
    assert row["role"] == "pit"
    assert (row["pa"], row["k"], row["bb"], row["hr"]) == (600.0, 180.0, 40.0, 18.0)
    # No ball-in-play breakdown in the pitching split.
    assert pd.isna(row["singles"]) and pd.isna(row["doubles"]) and pd.isna(row["triples"])


def test_bad_role_rejected() -> None:
    with pytest.raises(ValueError, match="role must be one of"):
        normalize_player_stats(_load("mlb_hitting.json"), "dh")


def test_empty_stats_yield_empty_valid_frame() -> None:
    bat = normalize_player_stats({}, "bat")
    BaseballStats.validate(bat)
    assert bat.empty


# --- boxscore → per-player actual prop stats --------------------------------


def test_boxscore_flattens_actual_prop_stats() -> None:
    box = normalize_boxscore(_load("mlb_boxscore.json"), game_id="g1").set_index("player_id")
    # Did-Not-Play (empty stats) is dropped; three players with stat lines remain.
    assert set(box.index) == {"660271", "477132", "592885"}
    assert (box["game_id"] == "g1").all()

    ohtani = box.loc["660271"]  # two-way: both batting and pitching lines
    assert (ohtani["total_bases"], ohtani["home_runs"], ohtani["strikeouts"]) == (7.0, 1.0, 1.0)
    assert (ohtani["pitcher_strikeouts"], ohtani["pitcher_outs"]) == (9.0, 18.0)

    kershaw = box.loc["477132"]  # pitching only → batting stats null
    assert kershaw["pitcher_strikeouts"] == 7.0
    assert pd.isna(kershaw["total_bases"])


def test_boxscore_empty_payload_is_empty_valid() -> None:
    box = normalize_boxscore({})
    assert box.empty
    assert "pitcher_strikeouts" in box.columns
