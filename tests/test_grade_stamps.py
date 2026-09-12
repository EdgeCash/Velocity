"""Which of the day's slates get graded, and how their cards merge.

The runner writes a card twice a day. Grading only the newest left the earlier
one permanently pending: its closing-line value was never measured, and the
games it uniquely covered — the ones already under way when the later run built
its board, so absent from that board — never reached the record at all.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

_SCRIPT = Path(__file__).parent.parent / "scripts" / "grade_yesterday.py"


def _grader():
    spec = importlib.util.spec_from_file_location("grade_yesterday", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Two runs on 11 Sep (16:00 and 22:00 UTC) and one already from today.
_YESTERDAY_EARLY = "20260911T160000Z"
_YESTERDAY_LATE = "20260911T220000Z"
_TODAY = "20260912T160000Z"


def test_both_of_yesterdays_runs_are_graded() -> None:
    grader = _grader()
    stamps = {s: {} for s in (_YESTERDAY_EARLY, _YESTERDAY_LATE, _TODAY)}
    now = datetime(2026, 9, 12, 19, 0, tzinfo=UTC)
    assert grader.prior_stamps(stamps, now) == [_YESTERDAY_EARLY, _YESTERDAY_LATE]
    # The single-stamp helper still answers with the newest, for the callers
    # that only want a label.
    assert grader._pick_prior_stamp(stamps, now) == _YESTERDAY_LATE


def test_todays_own_runs_are_never_graded_early() -> None:
    grader = _grader()
    stamps = {_TODAY: {}}
    now = datetime(2026, 9, 12, 19, 0, tzinfo=UTC)
    assert grader.prior_stamps(stamps, now) == []


def _slate(rows: list[tuple[str, str, str, float | None, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["game_id", "market", "side", "point", "book", "price"]
    )


def test_a_bet_on_both_cards_is_graded_once_at_the_price_it_was_placed() -> None:
    grader = _grader()
    early = _slate([("g1", "total", "over", 44.5, "kalshi", 120.0)])
    late = _slate([
        ("g1", "total", "over", 44.5, "kalshi", 135.0),   # same bet, moved price
        ("g2", "spread", "home", -3.5, "fanduel", -110.0),  # only on the late card
    ])
    merged = grader.merge_stamped([early, late], "slate")
    assert len(merged) == 2
    # The afternoon price is kept: that is the one the ledger booked.
    assert merged.query("game_id == 'g1'").iloc[0]["price"] == 120.0
    assert set(merged["game_id"]) == {"g1", "g2"}


def test_the_early_cards_own_games_survive_the_merge() -> None:
    """The whole point: an afternoon game is gone from the evening board."""
    grader = _grader()
    early = _slate([("day-game", "moneyline", "home", None, "kalshi", -140.0)])
    late = _slate([("night-game", "moneyline", "away", None, "kalshi", 150.0)])
    merged = grader.merge_stamped([early, late], "slate")
    assert set(merged["game_id"]) == {"day-game", "night-game"}


def test_a_null_point_does_not_split_a_moneyline_into_two_plays() -> None:
    # Moneylines carry no point. Comparing null to null naively leaves every
    # such row looking unique, so the same bet would grade twice.
    grader = _grader()
    one = _slate([("g1", "moneyline", "home", None, "kalshi", -140.0)])
    two = _slate([("g1", "moneyline", "home", None, "kalshi", -150.0)])
    assert len(grader.merge_stamped([one, two], "slate")) == 1


def test_games_maps_union_by_game() -> None:
    grader = _grader()
    early = pd.DataFrame({"game_id": ["g1"], "home_team": ["A"], "away_team": ["B"]})
    late = pd.DataFrame(
        {"game_id": ["g1", "g2"], "home_team": ["A", "C"], "away_team": ["B", "D"]}
    )
    merged = grader.merge_stamped([early, late], "games")
    assert list(merged["game_id"]) == ["g1", "g2"]


def test_an_empty_day_merges_to_nothing() -> None:
    grader = _grader()
    assert grader.merge_stamped([], "slate") is None
    assert grader.merge_stamped([_slate([]).iloc[0:0]], "slate") is None
