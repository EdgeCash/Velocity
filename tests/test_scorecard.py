"""Scorecard (velocity.report.scorecard) — grade a slate, score CLV + calibration.

All offline: a synthetic slate + closing lines + finals exercise the bet
reconstruction, the closing-line join for CLV, the reliability table + ECE, and
the per-market CLV rollup. The grading/CLV math itself lives in bet_log (tested
there); this pins the report layer on top.
"""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.report.scorecard import (
    bets_from_slate,
    calibration_table,
    clv_by_market,
    expected_calibration_error,
    grade_slate,
    summarize,
)

# Two games. g1: home wins 5-3 (total 8). g2: home wins 6-2 (total 8).
FINALS = pd.DataFrame({
    "game_id": ["g1", "g2"],
    "home_score": [5.0, 6.0],
    "away_score": [3.0, 2.0],
})

SLATE = pd.DataFrame({
    "game_id": ["g1", "g1", "g2"],
    "market": ["moneyline", "total", "moneyline"],
    "side": ["home", "over", "away"],
    "point": [None, 7.5, None],
    "book": ["dk", "dk", "fd"],
    "price": [-120, -110, 140],
    "stake": [3.0, 2.0, 1.5],
    "p_model": [0.60, 0.58, 0.42],
    "p_fair": [0.55, 0.54, 0.40],
})

# Closing: home ML drifted to -140 (we beat it at -120); total closed at 8.5
# (we took over 7.5 — a worse number, negative line CLV); g2 away closed +120.
CLOSING = pd.DataFrame({
    "game_id": ["g1", "g1", "g2"],
    "market": ["moneyline", "total", "moneyline"],
    "side": ["home", "over", "away"],
    "point": [None, 8.5, None],
    "price": [-140, -108, 120],
})


def _graded():
    return grade_slate(SLATE, FINALS, CLOSING)


def test_grading_settles_each_bet() -> None:
    graded = _graded().set_index(["game_id", "market"])
    # g1 home ML: home won → win. g1 over 7.5: total 8 > 7.5 → win.
    assert graded.loc[("g1", "moneyline"), "result"] == "win"
    assert graded.loc[("g1", "total"), "result"] == "win"
    # g2 away ML: home won → away loses.
    assert graded.loc[("g2", "moneyline"), "result"] == "loss"


def test_price_and_line_clv_from_closing() -> None:
    graded = _graded().set_index(["game_id", "market"])
    # Took home ML at -120, closed -140 → beat the price → positive price CLV.
    assert graded.loc[("g1", "moneyline"), "price_clv"] > 0
    # Took over 7.5, closed 8.5 → we hold the easier (lower) number → positive line CLV.
    assert graded.loc[("g1", "total"), "line_clv"] > 0


def test_pending_when_final_is_missing() -> None:
    finals = FINALS.copy()
    finals.loc[finals["game_id"] == "g2", ["home_score", "away_score"]] = float("nan")
    graded = grade_slate(SLATE, finals, CLOSING)
    assert (graded[graded["game_id"] == "g2"]["result"] == "pending").all()


def test_clv_by_market_rolls_up() -> None:
    table = clv_by_market(_graded()).set_index("market")
    assert set(table.index) == {"moneyline", "total"}
    assert table.loc["moneyline", "n"] == 2
    assert table.loc["total", "mean_line_clv"] > 0  # we held the favorable total number


def test_calibration_table_and_ece() -> None:
    # A calibrated book: p_model 0.65 wins ~65% of the time (mid-bin, no boundary).
    n = 200
    rows = []
    for i in range(n):
        won = i < int(0.65 * n)  # exactly 65% winners
        rows.append({
            "game_id": f"c{i}", "market": "moneyline", "side": "home",
            "book": "x", "price": -110, "point": None, "stake": 1.0, "p_model": 0.65,
            "result": "win" if won else "loss", "profit": 0.9 if won else -1.0,
            "bankroll": 100.0, "price_clv": 0.0, "line_clv": None,
        })
    graded = pd.DataFrame(rows)
    table = calibration_table(graded, n_bins=10)
    row = table[table["bin"] == "0.6-0.7"].iloc[0]
    assert row["mean_p"] == pytest.approx(0.65)
    assert row["realized"] == pytest.approx(0.65, abs=0.01)
    assert expected_calibration_error(graded) == pytest.approx(0.0, abs=0.01)


def test_summarize_reports_record_and_roi() -> None:
    s = summarize(_graded())
    assert s["n_bets"] == 3
    assert s["wins"] == 2 and s["losses"] == 1
    assert "roi" in s and "mean_price_clv" in s and "ece" in s


def test_an_exchange_rung_is_never_scored_against_the_main_line_close() -> None:
    """The fabricated-CLV defect, in one case.

    A Kalshi spread rung bought 17 points off the main number was looked up on
    ``(game, market, side)`` alone, matched the sportsbook's consensus close on
    the main number, and booked the whole distance as line value earned. A rung
    can only be compared with its own contract; with no exchange close banked
    it carries none.
    """
    slate = pd.DataFrame(
        {
            "game_id": ["g1", "g1"],
            "market": ["spread", "spread"],
            "side": ["home", "home"],
            "point": [-20.5, -3.5],
            "book": ["kalshi", "fanduel"],
            "price": [900.0, -110.0],
            "stake": [1.0, 1.0],
            "p_model": [0.2, 0.55],
        }
    )
    closing = pd.DataFrame(
        {
            "game_id": ["g1"],
            "market": ["spread"],
            "side": ["home"],
            "point": [-3.0],
            "price": [-110.0],
            "book": ["consensus"],
        }
    )
    bets = {b.book: b for b in bets_from_slate(slate, closing)}
    # The exchange rung gets no close, so no CLV of any kind.
    assert bets["kalshi"].closing_price is None
    assert bets["kalshi"].closing_point is None
    assert bets["kalshi"].line_clv() is None
    assert bets["kalshi"].price_clv() is None
    # The sportsbook bet still scores against the consensus close, and its
    # line CLV is the half point it actually beat.
    assert bets["fanduel"].closing_point == -3.0
    assert bets["fanduel"].line_clv() == -0.5


def test_an_exchange_rung_scores_against_its_own_contracts_close() -> None:
    # Same venue, same strike: that is the same contract, so it does carry CLV.
    slate = pd.DataFrame(
        {
            "game_id": ["g1"],
            "market": ["total"],
            "side": ["over"],
            "point": [50.5],
            "book": ["kalshi"],
            "price": [138.0],
            "stake": [1.0],
            "p_model": [0.45],
        }
    )
    closing = pd.DataFrame(
        {
            "game_id": ["g1"],
            "market": ["total"],
            "side": ["over"],
            "point": [50.5],
            "price": [110.0],
            "book": ["kalshi"],
        }
    )
    bet = bets_from_slate(slate, closing)[0]
    assert bet.closing_price == 110.0
    # Bought at +138 and it closed at +110: a real, earned price edge.
    assert bet.price_clv() is not None and bet.price_clv() > 0
