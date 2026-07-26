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


def test_settle_marks_unmatched_game_pending_but_keeps_clv() -> None:
    # A bet on a game absent from the finals frame (postponed, or unmatched to the
    # results feed) must not crash settle — it's pending, bankroll unchanged, and
    # its CLV (which needs no final) still counts.
    games = pd.DataFrame({"game_id": ["G1"], "home_score": [21.0], "away_score": [20.0]})
    log = BetLog()
    log.add(_bet(game_id="ABSENT", market="moneyline", side="home",
                 price=-110, closing_price=-120, stake=10.0))
    out = log.settle(games, starting_bankroll=100.0)
    assert out.loc[0, "result"] == "pending"
    assert math.isnan(out.loc[0, "profit"])
    assert out.loc[0, "bankroll"] == pytest.approx(100.0)  # untouched by an ungraded bet
    assert out.loc[0, "price_clv"] > 0  # CLV needs only the prices, not the final


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


# --- segment markets (F5 / first inning) --------------------------------------


def test_segment_bets_grade_like_their_base_market() -> None:
    f5_ml = _bet(market="moneyline_f5", price=100, stake=10.0)
    # Grade is called with the segment's scores: a tied F5 pushes.
    assert f5_ml.grade(3.0, 1.0) == ("win", pytest.approx(10.0))
    assert f5_ml.grade(2.0, 2.0) == ("push", 0.0)
    nrfi = _bet(market="total_i1", side="under", point=0.5, price=-110, stake=11.0)
    assert nrfi.grade(0.0, 0.0)[0] == "win"  # scoreless first inning
    assert nrfi.grade(1.0, 0.0)[0] == "loss"
    f5_rl = _bet(market="spread_f5", side="home", point=-0.5, price=100, stake=5.0)
    assert f5_rl.grade(3.0, 2.0)[0] == "win"
    assert f5_rl.grade(2.0, 2.0)[0] == "loss"  # −0.5 loses a tied F5


def test_segment_line_clv_uses_base_semantics() -> None:
    # An F5 run line is a spread: a higher handicap always helps its holder.
    bet = _bet(market="spread_f5", side="home", point=0.5, closing_point=-0.5)
    assert bet.line_clv() == pytest.approx(1.0)
    # First-inning totals are totals: the under wants a higher number, so taking
    # 1.5 against a 0.5 close is a point in our favor — and the over's mirror.
    nrfi = _bet(market="total_i1", side="under", point=1.5, closing_point=0.5)
    assert nrfi.line_clv() == pytest.approx(1.0)
    yrfi = _bet(market="total_i1", side="over", point=1.5, closing_point=0.5)
    assert yrfi.line_clv() == pytest.approx(-1.0)  # over wants a *lower* number


def test_settle_grades_segments_against_segment_scores() -> None:
    games = pd.DataFrame(
        {
            "game_id": ["G1"],
            "home_score": [6.0], "away_score": [5.0],       # full game: over 8.5 wins
            "home_score_f5": [2.0], "away_score_f5": [2.0],  # F5 tied → ML pushes
            "home_score_i1": [0.0], "away_score_i1": [0.0],  # scoreless 1st → NRFI wins
        }
    )
    log = BetLog()
    log.add(_bet(market="total", side="over", point=8.5, price=100, stake=10.0))
    log.add(_bet(market="moneyline_f5", price=100, stake=10.0))
    log.add(_bet(market="total_i1", side="under", point=0.5, price=100, stake=10.0))
    out = log.settle(games, starting_bankroll=100.0)
    assert out["result"].tolist() == ["win", "push", "win"]
    assert out.loc[2, "bankroll"] == pytest.approx(120.0)  # +10, 0, +10


def test_settle_leaves_segments_pending_without_segment_scores() -> None:
    games = pd.DataFrame({"game_id": ["G1"], "home_score": [6.0], "away_score": [5.0]})
    log = BetLog()
    log.add(_bet(market="moneyline", price=100, stake=10.0))
    log.add(_bet(market="total_i1", side="under", point=0.5, price=100, stake=10.0))
    out = log.settle(games, starting_bankroll=100.0)
    # The full-game bet grades; the segment bet must never grade off the final
    # score, so without first-inning columns it stays pending.
    assert out.loc[0, "result"] == "win"
    assert out.loc[1, "result"] == "pending"
    assert out.loc[1, "bankroll"] == pytest.approx(110.0)  # only the graded bet moved it
