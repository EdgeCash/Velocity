"""NCAAF ingest — CFBD frames normalize to the canonical store, tolerantly.

College data is messy, so these tests assert the adapter drops unusable rows and
coerces malformed values to null instead of crashing — while still validating
against the canonical schema.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from velocity.ingest.ncaaf import distill_rest_plays, normalize_games, normalize_plays
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


# --- distill_rest_plays: the REST /plays payload → the committed plays rows ---

_REST_ROWS = [
    # camelCase, as the REST API answers; no season/week in the rows.
    {"id": "a1", "gameId": 401550883, "offense": "Georgia", "defense": "UT Martin",
     "playType": "Rush", "down": 1, "yardsGained": 6, "ppa": 0.32},
    {"id": "a2", "gameId": 401550883, "offense": "UT Martin", "defense": "Georgia",
     "playType": "Pass Incompletion", "down": 2, "yardsGained": 0, "ppa": -0.44},
    # kickoff: CFBD leaves ppa null — the distilled frame must drop it.
    {"id": "a3", "gameId": 401550883, "offense": "Georgia", "defense": "UT Martin",
     "playType": "Kickoff", "down": 0, "yardsGained": 0, "ppa": None},
]


def test_distill_rest_plays_renames_stamps_and_filters() -> None:
    plays = distill_rest_plays(_REST_ROWS, season=2023, week=5)
    Plays.validate(plays)
    assert list(plays["play_id"]) == ["a1", "a2"]  # unscored kickoff dropped
    assert set(plays["season"]) == {2023}
    assert set(plays["week"]) == {5}
    assert plays.set_index("play_id").loc["a2", "epa"] == pytest.approx(-0.44)
    assert set(plays["game_id"]) == {"401550883"}


def test_distill_rest_plays_empty_payload() -> None:
    assert distill_rest_plays([], season=2023, week=5).empty


def test_camel_case_cfbd_payloads_normalize() -> None:
    """The v5 client serializes through its API aliases, so the frame arrives
    camelCase. Indexing one spelling raised ``KeyError('home_team')`` and took
    NCAAF grading offline: the schedule fetch failed every night and the
    college record never accumulated."""
    from velocity.ingest.ncaaf import snake_columns

    camel = pd.DataFrame([{
        "id": 401628442, "season": 2026, "week": 2, "seasonType": "regular",
        "startDate": "2026-09-06T23:30:00.000Z", "homeTeam": "Georgia",
        "awayTeam": "Clemson", "homePoints": 34, "awayPoints": 17,
        "neutralSite": False,
    }])
    games = normalize_games(camel)
    assert len(games) == 1
    row = games.iloc[0]
    assert row["home_team"] == "Georgia" and row["away_team"] == "Clemson"
    assert row["home_score"] == 34 and row["away_score"] == 17
    assert row["season_type"] == "REG" and row["game_id"] == "401628442"

    # The old spelling still works, and the two agree exactly.
    snake = camel.rename(columns={
        "seasonType": "season_type", "startDate": "start_date",
        "homeTeam": "home_team", "awayTeam": "away_team",
        "homePoints": "home_points", "awayPoints": "away_points",
        "neutralSite": "neutral_site"})
    assert normalize_games(snake).equals(games)
    # A frame carrying both spellings does not end up with duplicate columns.
    assert not snake_columns(pd.concat([camel, snake], axis=1)).columns.duplicated().any()



def test_postseason_weeks_are_renumbered_past_the_regular_season() -> None:
    """CFBD's postseason week is not an ordinal, so kickoffs supply one.

    The committed frames carry 411 of 478 postseason games at week 1 -- the
    2025 FCS title game (6 January) at week 1 while that season's CFP
    semi-finals sit at week 20. Taken verbatim a January bowl becomes the
    season's OPENER on any (season, week) ordering, which put 55,007 plays of
    future football inside the walk-forward's own training window.
    """
    from velocity.ingest.ncaaf import POST_WEEK_BASE, normalize_games

    raw = pd.DataFrame([
        # A real week 1 opener, and the last week of the regular season.
        {"id": 1, "season": 2025, "week": 1, "season_type": "regular",
         "start_date": "2025-08-30T18:00:00Z", "home_team": "Georgia",
         "away_team": "Clemson", "home_points": 34, "away_points": 3},
        {"id": 2, "season": 2025, "week": 16, "season_type": "regular",
         "start_date": "2025-12-06T20:00:00Z", "home_team": "Georgia",
         "away_team": "Alabama", "home_points": 27, "away_points": 24},
        # The two postseason spellings that actually appear in the data.
        {"id": 3, "season": 2025, "week": 1, "season_type": "postseason",
         "start_date": "2026-01-06T00:30:00Z", "home_team": "Montana State",
         "away_team": "Illinois State", "home_points": 31, "away_points": 17},
        {"id": 4, "season": 2025, "week": 20, "season_type": "postseason",
         "start_date": "2026-01-19T19:30:00Z", "home_team": "Indiana",
         "away_team": "Miami", "home_points": 28, "away_points": 21},
    ])
    out = normalize_games(raw).set_index("game_id")

    # Regular season is untouched.
    assert int(out.loc["1", "week"]) == 1
    assert int(out.loc["2", "week"]) == 16
    # Every postseason game lands clear of every regular-season week...
    post = out[out["season_type"] == "POST"]["week"].astype(int)
    assert post.min() >= POST_WEEK_BASE
    assert post.min() > int(out.loc["2", "week"])
    # ...and the later game gets the later week, whatever CFBD called them:
    # the one CFBD numbered 1 is played FIRST, so it sorts first now.
    assert int(out.loc["3", "week"]) < int(out.loc["4", "week"])


def test_postseason_renumbering_does_not_depend_on_the_batch() -> None:
    """A single-game pull must number that game as a full-season pull would.

    The anchor is the 1st of December in the season's own year, not the
    earliest kickoff in whatever rows happen to be in hand -- otherwise two
    pulls of the same season disagree about what week a bowl was.
    """
    from velocity.ingest.ncaaf import normalize_games

    late = {"id": 4, "season": 2025, "week": 20, "season_type": "postseason",
            "start_date": "2026-01-19T19:30:00Z", "home_team": "Indiana",
            "away_team": "Miami", "home_points": 28, "away_points": 21}
    early = {"id": 3, "season": 2025, "week": 1, "season_type": "postseason",
             "start_date": "2026-01-06T00:30:00Z", "home_team": "Montana State",
             "away_team": "Illinois State", "home_points": 31, "away_points": 17}
    alone = normalize_games(pd.DataFrame([late])).set_index("game_id")
    together = normalize_games(pd.DataFrame([early, late])).set_index("game_id")
    assert int(alone.loc["4", "week"]) == int(together.loc["4", "week"])


def test_postseason_weeks_stay_inside_the_schema_ceiling() -> None:
    """The renumbering is bounded above as well as below.

    A mid-January title game is seven weeks past the anchor, and the Games
    schema caps week at 25 -- so the base has to leave room for the whole bowl
    calendar underneath it.
    """
    from velocity.ingest.ncaaf import MAX_WEEK, POST_WEEK_BASE, normalize_games

    out = normalize_games(pd.DataFrame([
        {"id": 5, "season": 2025, "week": 1, "season_type": "postseason",
         "start_date": "2026-01-19T19:30:00Z", "home_team": "A",
         "away_team": "B", "home_points": 1, "away_points": 0},
    ])).set_index("game_id")
    week = int(out.loc["5", "week"])
    assert POST_WEEK_BASE < week <= MAX_WEEK


def test_no_training_window_reaches_a_game_that_had_not_kicked_off() -> None:
    """The criterion the renumbering actually exists to satisfy.

    Checking that postseason games carry a postseason-looking week is checking
    the symptom. What matters is chronology: for every (season, week), nothing
    in "the plays before week N" may come from a game that kicked off after
    week N started. This audit run against the committed frames returns zero;
    before the renumbering it flagged 55,007 plays.
    """
    from velocity.ingest.ncaaf import normalize_games

    raw = pd.DataFrame([
        {"id": 1, "season": 2025, "week": 1, "season_type": "regular",
         "start_date": "2025-08-30T18:00:00Z", "home_team": "A",
         "away_team": "B", "home_points": 7, "away_points": 3},
        {"id": 2, "season": 2025, "week": 8, "season_type": "regular",
         "start_date": "2025-10-18T18:00:00Z", "home_team": "A",
         "away_team": "C", "home_points": 21, "away_points": 14},
        # The bowl CFBD calls week 1, played four months after the opener.
        {"id": 3, "season": 2025, "week": 1, "season_type": "postseason",
         "start_date": "2026-01-06T00:30:00Z", "home_team": "A",
         "away_team": "D", "home_points": 28, "away_points": 24},
    ])
    games = normalize_games(raw)
    starts = games.groupby(["season", "week"])["kickoff"].min()

    inversions = 0
    for (season, week), start in starts.items():
        train = games[(games["season"] < season)
                      | ((games["season"] == season) & (games["week"] < week))]
        inversions += int((train["kickoff"] > start).sum())
    assert inversions == 0, "a training window reached a game not yet played"


def _game(gid, season, week, home, away, kickoff):
    return {"game_id": gid, "season": season, "week": week, "home_team": home,
            "away_team": away, "kickoff": pd.Timestamp(kickoff)}


def test_rekey_games_to_cfbd_by_reference_then_plays_never_duplicating() -> None:
    from velocity.ingest.ncaaf import rekey_games_to_cfbd

    games = pd.DataFrame([
        _game("2025_20250830_Clemson_Georgia", 2025, 1, "Georgia", "Clemson", "2025-08-30 19:00"),
        # Reversed orientation in the reference, kickoff a day apart (UTC drift).
        _game("2025_20250906_IowaState_Iowa", 2025, 2, "Iowa", "Iowa State", "2025-09-06 16:00"),
        # No reference row; the plays frame knows it.
        _game("2025_20250913_Texas_OhioState", 2025, 3, "Ohio State", "Texas", "2025-09-13 12:00"),
        # Nothing knows it: stays synthetic.
        _game("2025_20250920_Rice_Army", 2025, 4, "Army", "Rice", "2025-09-20 12:00"),
        # Already CFBD-keyed: untouched, and a synthetic twin must not steal it.
        _game("401009", 2025, 5, "Navy", "Tulane", "2025-09-27 12:00"),
        _game("2025_20250927_Tulane_Navy", 2025, 5, "Navy", "Tulane", "2025-09-27 12:00"),
    ])
    reference = pd.DataFrame([
        _game("401001", 2025, 1, "Georgia", "Clemson", "2025-08-30 23:30"),
        _game("401002", 2025, 2, "Iowa State", "Iowa", "2025-09-07 01:00"),
        _game("401009", 2025, 5, "Navy", "Tulane", "2025-09-27 16:00"),
        # A different meeting of the same pair, months away: not a match.
        _game("401099", 2025, 14, "Georgia", "Clemson", "2025-12-06 20:00"),
    ])
    plays = pd.DataFrame({
        "game_id": ["401003", "401003"], "season": [2025, 2025], "week": [3, 3],
        "posteam": ["Texas", "Ohio State"], "defteam": ["Ohio State", "Texas"],
    })
    out, counts = rekey_games_to_cfbd(games, reference, plays)
    assert counts == {"synthetic": 5, "by_reference": 2, "by_plays": 1,
                      "collisions": 1, "unmatched": 1}
    assert list(out["game_id"]) == ["401001", "401002", "401003",
                                    "2025_20250920_Rice_Army", "401009",
                                    "2025_20250927_Tulane_Navy"]
    assert not out["game_id"].duplicated().any()
    # Everything but the id is exactly as it was.
    pd.testing.assert_frame_equal(out.drop(columns="game_id"), games.drop(columns="game_id"))


def test_rekey_is_a_no_op_on_a_cfbd_keyed_frame() -> None:
    from velocity.ingest.ncaaf import rekey_games_to_cfbd

    games = pd.DataFrame([_game("401001", 2025, 1, "Georgia", "Clemson", "2025-08-30")])
    out, counts = rekey_games_to_cfbd(games, games.iloc[0:0])
    assert counts["synthetic"] == 0
    pd.testing.assert_frame_equal(out, games)
