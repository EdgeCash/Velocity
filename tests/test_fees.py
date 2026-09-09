"""Exchange fees — hand-checked values, and sportsbook math left bit-identical.

An exchange quotes vig-free but charges a per-contract fee at match, which the
EV and Kelly math had no term for (docs/BUILD_EXCHANGES.md D3). These pin the
published fee curves, the fee-adjusted payout algebra, and — most importantly —
that a fee-free venue reproduces the existing numbers exactly.
"""

from __future__ import annotations

import pytest
from velocity.wagering.edge import evaluate, expected_value, kelly_fraction
from velocity.wagering.fees import (
    fee_for_price,
    net_payout_after_fees,
    taker_fee,
    venue_for_book,
)
from velocity.wagering.odds import net_payout, prob_to_american
from velocity.wagering.staking import stake_amount, stake_fraction


def test_published_fee_curves() -> None:
    # Kalshi: multiplier · 0.07 · a · (1 − a), rounded up to $0.000001.
    assert taker_fee(0.50, "kalshi") == pytest.approx(0.0175)  # 1.75c peak at 50c
    assert taker_fee(0.19, "kalshi") == pytest.approx(0.010773)  # the doc's example
    # Polymarket sports: 0.05 · a · (1 − a).
    assert taker_fee(0.50, "polymarket") == pytest.approx(0.0125)  # 1.25c peak
    # Both shrink toward the extremes, and are symmetric about 50c.
    assert taker_fee(0.99, "kalshi") == taker_fee(0.01, "kalshi")
    assert taker_fee(0.99, "kalshi") < taker_fee(0.50, "kalshi")
    # A per-series multiplier scales the whole curve.
    assert taker_fee(0.50, "kalshi", 0.5) == pytest.approx(0.00875)


def test_fee_rounds_up_but_not_past_an_exact_increment() -> None:
    # $0.0175 is exactly 17,500 micro-dollars; rounding up must not bill 17,501.
    assert taker_fee(0.50, "kalshi") == 0.0175
    # A genuinely fractional fee does round up: 0.07 · 0.331 · 0.669 is
    # $0.01550073, which bills as $0.015501.
    assert taker_fee(0.331, "kalshi") == pytest.approx(0.015501)


def test_sportsbooks_and_unknown_venues_are_free() -> None:
    assert taker_fee(0.5, "draftkings") == 0.0
    assert fee_for_price(-110, "draftkings") == 0.0
    assert venue_for_book("draftkings") is None
    assert venue_for_book("bookA") is None
    # Exchange rows carry the venue as their book name.
    assert venue_for_book("kalshi") == "kalshi"
    assert venue_for_book("Polymarket") == "polymarket"


def test_fee_adjusted_payout_matches_the_cost_algebra() -> None:
    # Buying at $0.19 costs 0.19 + 0.010773 and pays $1, so
    # b' = (1 − cost) / cost.
    price = round(prob_to_american(0.19))
    cost = 0.19 + 0.010773
    expected = (1.0 - cost) / cost
    # The price round-trips through integer American odds, so allow the
    # documented sub-0.1% rounding of D1 rather than demanding exactness.
    assert net_payout_after_fees(price, "kalshi") == pytest.approx(expected, rel=2e-3)
    # The fee is a real haircut: ~4.26 becomes ~3.98 per unit staked.
    assert net_payout_after_fees(price, "kalshi") < net_payout(price)


def test_fee_free_venues_reproduce_existing_numbers_exactly() -> None:
    # The guarantee that keeps every sportsbook caller untouched: no venue and
    # a fee-free venue must both return precisely what the old code returned.
    for price in (-250, -110, -101, 100, 137, 900):
        assert net_payout_after_fees(price, "draftkings") == net_payout(price)
        assert expected_value(0.55, price) == expected_value(0.55, price, "draftkings")
        assert kelly_fraction(0.55, price) == kelly_fraction(0.55, price, "draftkings")
        assert stake_fraction(0.55, price) == stake_fraction(0.55, price, None, "draftkings")


def test_fees_shrink_ev_kelly_and_stake_on_an_exchange() -> None:
    price = round(prob_to_american(0.19))  # a $0.19 ask
    p_model = 0.24  # the model likes it: a real edge before fees
    assert expected_value(p_model, price, "kalshi") < expected_value(p_model, price)
    assert kelly_fraction(p_model, price, "kalshi") < kelly_fraction(p_model, price)
    assert stake_amount(100.0, p_model, price, None, "kalshi") < stake_amount(
        100.0, p_model, price
    )
    # Still a real bet — the fee trims the edge here, it doesn't erase it.
    assert expected_value(p_model, price, "kalshi") > 0.0


def test_a_fee_thin_edge_stops_qualifying() -> None:
    # An edge worth less than the fee must fail the EV gate on an exchange
    # while passing on a sportsbook at the same price.
    price = round(prob_to_american(0.50))  # $0.50 ask: the fee peaks here
    p_model, p_fair = 0.515, 0.49
    book_signal = evaluate(p_model, price, p_fair, min_edge=0.02)
    exchange_signal = evaluate(p_model, price, p_fair, min_edge=0.02, venue="kalshi")
    assert book_signal.qualifies
    assert not exchange_signal.qualifies
    assert exchange_signal.ev < 0.0


def test_near_certain_contracts_stop_qualifying_once_the_fee_is_paid() -> None:
    # A $0.999 ask pays $0.001 and the fee takes $0.00007 of it, so a model
    # that agrees with the market exactly is now betting at a loss.
    assert expected_value(0.999, -99900, "kalshi") < 0.0
    assert kelly_fraction(0.999, -99900, "kalshi") <= 0.0
    assert stake_fraction(0.999, -99900, None, "kalshi") == 0.0
    # Defensive: a fee big enough to swallow the payout leaves no profit at
    # all rather than a negative or divide-by-zero one.
    assert net_payout_after_fees(-99900, "kalshi", multiplier=100.0) == 0.0
    assert kelly_fraction(0.999, -99900, "kalshi") > -1.0  # finite, not blown up


def test_exchange_dead_heats_settle_at_fifty_cents() -> None:
    """A tie is a push at a book and a settlement on an exchange (D5).

    Kalshi's winner rules pay $0.50 a contract on a tied game, so a ticket
    bought cheap profits and one bought rich loses. Grading it as a push would
    book the wrong P&L on exactly the games where the distinction exists.
    """
    from velocity.wagering.bet_log import Bet

    def moneyline(book: str, ask: float) -> Bet:
        return Bet(
            game_id="g", market="moneyline", side="home", book=book,
            price=round(prob_to_american(ask)), stake=1.0, p_model=0.5,
        )

    # A sportsbook pushes: stake back, nothing won or lost.
    assert moneyline("bookA", 0.5).grade(21, 21) == ("push", 0.0)

    # An exchange settles at 50c. Bought at 19c, that is a 163% return.
    result, profit = moneyline("kalshi", 0.19).grade(21, 21)
    assert result == "tie"
    assert profit == pytest.approx(1.63, abs=0.01)

    # Bought at 82c, the same dead heat is a 39% loss.
    result, profit = moneyline("kalshi", 0.82).grade(21, 21)
    assert result == "tie"
    assert profit == pytest.approx(-0.39, abs=0.01)

    # Pricing never needed the branch: the sim splits ties 0.5, which is
    # exactly the contract's expected payout.
