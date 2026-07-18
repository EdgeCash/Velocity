"""Bet log — CLV measures, grading, and a reproducible bankroll curve."""

from __future__ import annotations

import math

import pandas as pd
import pytest
from velocity.wagering.bet_log import Bet, BetLog


def _bet(**kw: object) -> Bet:
    base: dict = {
        "game_id": "G1",
        "market": "moneyline",
        "side": "home",
        "book": "bookA",
        "price": -110,
        "stake": 10.0,
        "p_model": 0.55,
    }
    base.update(kw)
    return Bet(**base)  # type: ignore[arg-type]


def test_price_clv_beats_the_close() -> None:
    # Took -110, closed -120 → we locked a better price.
    bet = _bet(price=-110, closing_price=-120)
    assert bet.price_clv() == pytest.approx(1.909090909 / 1.833333333 - 1.0, abs=1e-6)
    assert bet.price_clv() > 0


def test_price_clv_none_without_close() -> None:
    assert _bet().price_clv() is None


def test_line_clv_spread_home() -> None:
    bet = _bet(market="spread", side="home", point=3.0, closing_point=2.5)
    assert bet.line_clv() == pytest.approx(0.5)  # got more points than the close


def test_line_clv_total_over_and_under() -> None:
    over = _bet(market="total", side="over", point=48.5, closing_point=50.5)
    under = _bet(market="total", side="under", point=48.5, closing_point=46.5)
    assert over.line_clv() == pytest.approx(2.0)  # over wants a lower number
    assert under.line_clv() == pytest.approx(2.0)  # under wants a higher number


def test_line_clv_none_for_moneyline() -> None:
    assert _bet(market="moneyline", closing_point=None).line_clv() is None


def test_grade_moneyline() -> None:
    win = _bet(market="moneyline", side="home", price=-110, stake=10.0)
    assert win.grade(21, 20) == ("win", pytest.approx(10.0 * 0.909090909, abs=1e-6))
    assert win.grade(20, 21)[0] == "loss"


def test_grade_spread_win_loss_push() -> None:
    bet = _bet(market="spread", side="home", point=3.0)
    assert bet.grade(21, 20)[0] == "win"  # margin +1, +3 → +4
    assert _bet(market="spread", side="home", point=-3.0).grade(21, 20)[0] == "loss"
    assert _bet(market="spread", side="home", point=-1.0).grade(21, 20)[0] == "push"


def test_grade_total() -> None:
    over = _bet(market="total", side="over", point=40.0)
    assert over.grade(21, 20)[0] == "win"  # total 41
    assert _bet(market="total", side="over", point=41.0).grade(21, 20)[0] == "push"
    assert _bet(market="total", side="under", point=40.0).grade(21, 20)[0] == "loss"


def test_settle_rolls_bankroll_and_handles_pending() -> None:
    games = pd.DataFrame(
        {
            "game_id": ["G1", "G2"],
            "home_score": [21.0, float("nan")],
            "away_score": [20.0, float("nan")],
        }
    )
    log = BetLog()
    log.add(_bet(game_id="G1", market="moneyline", side="home", price=100, stake=10.0))
    log.add(_bet(game_id="G2", market="moneyline", side="home", price=100, stake=10.0))
    out = log.settle(games, starting_bankroll=100.0)
    assert out.loc[0, "result"] == "win"
    assert out.loc[0, "bankroll"] == pytest.approx(110.0)  # +10 profit at +100
    assert out.loc[1, "result"] == "pending"
    assert math.isnan(out.loc[1, "profit"])
    assert out.loc[1, "bankroll"] == pytest.approx(110.0)  # unchanged by pending


def test_settle_is_reproducible() -> None:
    games = pd.DataFrame(
        {"game_id": ["G1"], "home_score": [21.0], "away_score": [20.0]}
    )
    log = BetLog()
    log.add(_bet(game_id="G1", market="spread", side="home", point=3.0, price=-110, stake=5.0))
    a = log.settle(games)
    b = log.settle(games)
    pd.testing.assert_frame_equal(a, b)


def test_clv_summary_aggregates() -> None:
    log = BetLog()
    log.add(_bet(price=-110, closing_price=-120))  # +price CLV
    log.add(_bet(price=-110, closing_price=-105))  # -price CLV
    summary = log.clv_summary()
    assert summary["n_bets"] == 2.0
    assert summary["pct_positive_clv"] == pytest.approx(0.5)
