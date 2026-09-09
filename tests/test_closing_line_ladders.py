"""A ladder rung closes against its own rung, not a neighbour's.

Every closing-line path was point-blind: ``closing_line`` kept one arbitrary
survivor per (game, market, side, book) and the slate matched a close without
regard to the number. That is right for a sportsbook, whose close is the same
market wherever its main line moved to — the movement is what ``line_clv``
measures. It is badly wrong for an exchange, where each number is a separate
contract quoted at the same instant: a −20.5 bet would be closed out at the
−1.5 rung's price, reporting ~19 points of CLV that never existed
(docs/BUILD_EXCHANGES.md E7).
"""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.store.pit import closing_line
from velocity.wagering.bet_log import Bet
from velocity.wagering.slate import _closing_for

GAMES = pd.DataFrame(
    {"game_id": ["g1"], "kickoff": [pd.Timestamp("2026-09-14 17:00:00")]}
)


def _row(book: str, side: str, point: float | None, price: int, minute: int) -> dict:
    tag = "ml" if point is None else f"{point:g}"
    return {
        "line_id": f"g1|spread|{side}|{book}|{tag}",
        "game_id": "g1",
        "book": book,
        "market": "spread",
        "side": side,
        "price": price,
        "point": point,
        "timestamp": pd.Timestamp("2026-09-14 16:00:00") + pd.Timedelta(minutes=minute),
        "is_closing": False,
    }


LADDER = pd.DataFrame(
    [
        _row("kalshi", "home", -1.5, -120, 1),
        _row("kalshi", "home", -7.5, 150, 2),
        _row("kalshi", "home", -20.5, 900, 3),
        # The near rung is quoted last, so a point-blind close would hand its
        # price to every rung on the ladder — including the -20.5 bet below.
        _row("kalshi", "home", -20.5, 850, 30),
        _row("kalshi", "home", -7.5, 145, 31),
        _row("kalshi", "home", -1.5, -125, 32),
    ]
)


def test_every_ladder_rung_keeps_its_own_close() -> None:
    closes = closing_line(LADDER, GAMES)
    # One close per rung — not one arbitrary survivor for the whole ladder.
    assert len(closes) == 3
    by_point = dict(zip(closes["point"], closes["price"], strict=True))
    assert by_point == {-1.5: -125, -7.5: 145, -20.5: 850}


def test_a_rung_is_closed_out_against_itself_not_a_neighbour() -> None:
    closes = closing_line(LADDER, GAMES)
    for point, expected in ((-1.5, -125), (-7.5, 145), (-20.5, 850)):
        found = _closing_for(closes, "g1", "spread", "home", "kalshi", point)
        assert found == (expected, point)


def test_the_far_rung_reports_no_fictitious_line_clv() -> None:
    closes = closing_line(LADDER, GAMES)
    price, point = _closing_for(closes, "g1", "spread", "home", "kalshi", -20.5)
    bet = Bet(
        game_id="g1", market="spread", side="home", book="kalshi",
        price=900, stake=1.0, p_model=0.12, point=-20.5,
        timestamp=pd.Timestamp("2026-09-14 16:00:00"),
        closing_price=price, closing_point=point,
    )
    # Same contract, so the line did not move: zero line CLV. Matched against
    # the last-quoted -1.5 rung instead, this reads as -19 points.
    assert (price, point) == (850, -20.5)
    assert bet.line_clv() == pytest.approx(0.0)


def test_sportsbook_closes_still_track_a_moving_main_line() -> None:
    # The behaviour that must not regress: a book posts one number, it moves,
    # and the close is that later number — which is the CLV signal.
    book_lines = pd.DataFrame(
        [
            _row("bookA", "home", -3.5, -110, 1),
            _row("bookA", "home", -4.5, -108, 40),
        ]
    )
    closes = closing_line(book_lines, GAMES)
    assert len(closes) == 1
    assert (closes.iloc[0]["point"], closes.iloc[0]["price"]) == (-4.5, -108)
    # And a bet struck at -3.5 finds that close, scoring a point of CLV.
    found = _closing_for(closes, "g1", "spread", "home", "bookA", -3.5)
    assert found == (-108, -4.5)
    bet = Bet(
        game_id="g1", market="spread", side="home", book="bookA",
        price=-110, stake=1.0, p_model=0.55, point=-3.5,
        timestamp=pd.Timestamp("2026-09-14 16:00:00"),
        closing_price=-108, closing_point=-4.5,
    )
    assert bet.line_clv() == pytest.approx(1.0)


def test_moneyline_rungs_survive_the_null_point_grouping() -> None:
    # ``point`` is null for moneylines and pandas drops NaN group keys, so the
    # ladder path has to fill before grouping or lose every moneyline close.
    ml = pd.DataFrame(
        [
            _row("kalshi", "home", None, 120, 1) | {"market": "moneyline"},
            _row("kalshi", "home", None, 115, 40) | {"market": "moneyline"},
        ]
    )
    closes = closing_line(ml, GAMES)
    assert len(closes) == 1
    assert closes.iloc[0]["price"] == 115
    assert _closing_for(closes, "g1", "moneyline", "home", "kalshi", None) == (115.0, None)
