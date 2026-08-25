"""MLB starters bank — boxscore extraction is exact and guess-free."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "build_mlb_pitching",
    Path(__file__).resolve().parents[1] / "scripts" / "build_mlb_pitching.py",
)
bp = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(bp)


def _payload() -> dict:
    return {"teams": {
        "home": {
            "team": {"name": "Baltimore Orioles"},
            "players": {
                "ID1": {"person": {"id": 1, "fullName": "Reliever Guy"},
                        "stats": {"pitching": {"gamesStarted": 0, "inningsPitched": "2.0",
                                               "strikeOuts": 3, "baseOnBalls": 0,
                                               "homeRuns": 0, "hitBatsmen": 0,
                                               "battersFaced": 7}}},
                "ID2": {"person": {"id": 2, "fullName": "Ace Starter"},
                        "stats": {"pitching": {"gamesStarted": 1, "inningsPitched": "6.2",
                                               "strikeOuts": 8, "baseOnBalls": 2,
                                               "homeRuns": 1, "hitBatsmen": 0,
                                               "battersFaced": 26}}},
                "ID3": {"person": {"id": 3, "fullName": "Bench Bat"},
                        "stats": {}},  # position player — no pitching line
            },
        },
        "away": {
            "team": {"name": "New York Yankees"},
            "players": {
                "ID9": {"person": {"id": 9, "fullName": "Road Starter"},
                        "stats": {"pitching": {"gamesStarted": 1, "inningsPitched": "5.0",
                                               "strikeOuts": 4, "baseOnBalls": 3,
                                               "homeRuns": 2, "hitBatsmen": 1,
                                               "battersFaced": 23}}},
            },
        },
    }}


def test_extract_starters_finds_one_per_side_with_fip_line() -> None:
    rows = bp.extract_starters(_payload(), "745001")
    assert len(rows) == 2
    home = next(r for r in rows if r["side"] == "home")
    assert home["starter_id"] == "2"
    assert home["starter_name"] == "Ace Starter"
    assert home["outs"] == pytest.approx(20.0)  # 6.2 IP = 20 outs
    assert (home["k"], home["bb"], home["hr"]) == (8, 2, 1)
    away = next(r for r in rows if r["side"] == "away")
    assert away["team"] == "New York Yankees"
    assert away["starter_id"] == "9"


def test_extract_starters_skips_sides_without_a_starter() -> None:
    payload = _payload()
    del payload["teams"]["away"]["players"]["ID9"]
    rows = bp.extract_starters(payload, "745001")
    assert [r["side"] for r in rows] == ["home"]
    assert bp.extract_starters({}, "x") == []


def test_outs_parser() -> None:
    assert bp._outs("6.2") == 20.0
    assert bp._outs("0.1") == 1.0
    assert bp._outs(None) is None


def _schedule_payload() -> dict:
    return {"dates": [{"games": [
        {"teams": {
            "home": {"team": {"name": "Baltimore Orioles"},
                     "probablePitcher": {"id": 2, "fullName": "Ace Starter"}},
            "away": {"team": {"name": "New York Yankees"},
                     "probablePitcher": {"id": 9, "fullName": "Road Starter"}},
        }},
        {"teams": {  # no probables announced yet
            "home": {"team": {"name": "Athletics"}},
            "away": {"team": {"name": "Seattle Mariners"}},
        }},
        {"teams": {  # doubleheader game 2 — same pair, different probable
            "home": {"team": {"name": "Baltimore Orioles"},
                     "probablePitcher": {"id": 55, "fullName": "Game Two Guy"}},
            "away": {"team": {"name": "New York Yankees"},
                     "probablePitcher": {"id": 56, "fullName": "Other Guy"}},
        }},
    ]}]}


def test_extract_probables_keys_by_team_pair_first_game_wins() -> None:
    lookup = bp.extract_probables(_schedule_payload())
    assert lookup[("Baltimore Orioles", "New York Yankees", None)] == ("2", "9")
    assert lookup[("Athletics", "Seattle Mariners", None)] == (None, None)
    assert len(lookup) == 2  # the doubleheader repeat did not overwrite
    assert bp.extract_probables({}) == {}


def _lineup_payload() -> dict:
    return {"dates": [{"games": [
        {"teams": {"home": {"team": {"name": "Baltimore Orioles"}},
                   "away": {"team": {"name": "New York Yankees"}}},
         "lineups": {
             "homePlayers": [{"id": i, "fullName": f"O{i}"} for i in range(1, 10)],
             "awayPlayers": [{"id": i, "fullName": f"Y{i}"} for i in range(11, 20)],
         }},
        {"teams": {"home": {"team": {"name": "Athletics"}},
                   "away": {"team": {"name": "Seattle Mariners"}}}},  # not posted
        {"teams": {"home": {"team": {"name": "Baltimore Orioles"}},
                   "away": {"team": {"name": "New York Yankees"}}},
         "lineups": {"homePlayers": [{"id": 99, "fullName": "Game Two"}]}},
    ]}]}


def test_extract_lineups_keeps_the_batting_order_and_first_game_wins() -> None:
    cards = bp.extract_lineups(_lineup_payload())
    # Order is the batting order: index 0 hits leadoff.
    assert cards["Baltimore Orioles"] == [str(i) for i in range(1, 10)]
    assert cards["New York Yankees"][0] == "11"
    # A team whose card is not posted is simply absent — the caller falls
    # back to the batter's most recent slot rather than guessing a nine.
    assert "Athletics" not in cards
    # The doubleheader's second game must not overwrite the first.
    assert "99" not in cards["Baltimore Orioles"]
    assert bp.extract_lineups({}) == {}


def test_slim_team_box_keeps_possession_columns_only() -> None:
    import importlib.util as _ilu

    import pandas as pd

    spec = _ilu.spec_from_file_location(
        "build_wnba_box",
        Path(__file__).resolve().parents[1] / "scripts" / "build_wnba_box.py",
    )
    wb = _ilu.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(wb)
    raw = pd.DataFrame([{
        "game_id": 401, "team_home_away": "home", "field_goals_attempted": "68",
        "offensive_rebounds": 7, "total_turnovers": 12, "free_throws_attempted": 20,
        "team_logo": "https://x/y.png", "largest_lead": 15,
    }])
    slim = wb.slim_team_box(raw, 2026)
    assert list(slim.columns) == wb._KEEP + ["season"]
    assert slim.loc[0, "game_id"] == "401"
    assert slim.loc[0, "field_goals_attempted"] == 68.0
    assert slim.loc[0, "season"] == 2026


# --- batter lines (the home-run model's substrate) ------------------------------

_BOX_BATTERS = {
    "teams": {
        "home": {
            "team": {"name": "Seattle Mariners"},
            # The TEAM list is the FINAL lineup: the replaced starter is gone
            # from it, which is why the player-level code is authoritative.
            "battingOrder": [1, 2, 99],
            "players": {
                "ID1": {"person": {"id": 1, "fullName": "Lead Off"},
                        "battingOrder": "100",
                        "stats": {"batting": {"plateAppearances": 5,
                                              "atBats": 4, "homeRuns": 1}}},
                "ID2": {"person": {"id": 2, "fullName": "Clean Up"},
                        "battingOrder": "400",
                        "stats": {"batting": {"plateAppearances": 4,
                                              "atBats": 4, "homeRuns": 2}}},
                "ID99": {"person": {"id": 99, "fullName": "Pinch Hitter"},
                         "battingOrder": "401",
                         "stats": {"batting": {"plateAppearances": 1,
                                               "atBats": 1, "homeRuns": 0}}},
                "ID3": {"person": {"id": 3, "fullName": "Late Glove"},
                        "stats": {"batting": {"plateAppearances": 0}}},
                "ID4": {"person": {"id": 4, "fullName": "Reliever"},
                        "stats": {"pitching": {"inningsPitched": "1.0"}}},
            },
        },
        "away": {"team": {"name": "Chicago Cubs"}, "players": {}},
    }
}


def test_extract_batters_reads_slots_and_plate_appearances() -> None:
    rows = bp.extract_batters(_BOX_BATTERS, "823101")
    by_name = {r["batter_name"]: r for r in rows}
    # Only players who actually batted appear — no defensive replacements,
    # no pitchers.
    assert set(by_name) == {"Lead Off", "Clean Up", "Pinch Hitter"}
    assert by_name["Clean Up"]["hr"] == 2
    assert by_name["Lead Off"]["lineup_slot"] == 1
    assert by_name["Lead Off"]["pa"] == 5


def test_extract_batters_marks_the_real_starters() -> None:
    rows = bp.extract_batters(_BOX_BATTERS, "823101")
    by_name = {r["batter_name"]: r for r in rows}
    # "400" started the 4th slot even though the FINAL team list drops him;
    # "401" came off the bench into the same slot.
    assert by_name["Clean Up"]["started"] is True
    assert by_name["Pinch Hitter"]["started"] is False
    assert by_name["Pinch Hitter"]["lineup_slot"] == 4
    assert all(r["game_id"] == "823101" for r in rows)
    assert all(r["team"] == "Seattle Mariners" for r in rows)


def test_extract_batters_survives_an_empty_payload() -> None:
    assert bp.extract_batters({}, "g1") == []
    assert bp.extract_batters({"teams": {"home": {}}}, "g1") == []
