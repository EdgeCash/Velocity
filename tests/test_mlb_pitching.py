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
