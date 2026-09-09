"""Exchange trading fees — the cost a sportsbook hides in the vig.

A sportsbook's margin is inside its price, so ``expected_value`` on an American
price already accounts for it. An exchange quotes vig-free: its margin arrives
as the bid/ask spread (which de-vig strips, docs/BUILD_EXCHANGES.md D2) *plus*
an explicit per-contract fee charged at match, which nothing in the EV path
knew about until now.

The correction is a fee-adjusted net payout. Buying one contract at ask ``a``
with fee ``f(a)`` costs ``a + f(a)`` and pays $1, so the profit per unit staked
becomes ``b' = (1 − a − f) / (a + f)`` — the same ``b`` the Kelly and EV math
already take, with the fee folded into the cost. At ``f = 0`` this is exactly
:func:`velocity.wagering.odds.net_payout`, which is why every sportsbook caller
is untouched.

Fee schedules (verified 2026-09-09, docs/BUILD_EXCHANGES.md D3):

* **Kalshi** — ``multiplier · 0.07 · a · (1 − a)`` per contract, rounded up to
  $0.000001. Peaks at 1.75¢ at 50¢ and shrinks toward the extremes. The
  ``multiplier`` is per-series (``fee_multiplier`` on ``GET /series/{ticker}``);
  football series also charge **makers** (``quadratic_with_maker_fees``), so no
  maker-free assumption applies here.
* **Polymarket** — sports taker fee ``0.05 · a · (1 − a)`` per share, peaking at
  1.25¢ at 50¢. Makers pay zero, which this does not model: a resting order is
  an execution refinement, and pricing every fill as a taker is the
  conservative side of that choice.
* **Sportsbooks** — no separate fee; the margin is in the price already.
"""

from __future__ import annotations

import math

from velocity.wagering.odds import american_to_prob, net_payout

# Venue → the quadratic fee coefficient on ``a · (1 − a)``. A book absent from
# this table charges nothing beyond its price.
FEE_RATE_BY_VENUE = {
    "kalshi": 0.07,
    "polymarket": 0.05,
}
# Fees are rounded up to this increment (Kalshi's documented fee rounding).
_FEE_INCREMENT = 1e-6


def venue_for_book(book: str) -> str | None:
    """The fee schedule a ``book`` trades under, or ``None`` for a sportsbook.

    Exchange rows carry the venue as their book name (``kalshi``,
    ``polymarket``), so no separate mapping has to be configured or kept in
    sync as venues are added.
    """
    key = str(book).strip().lower()
    return key if key in FEE_RATE_BY_VENUE else None


def taker_fee(ask: float, venue: str, multiplier: float = 1.0) -> float:
    """The per-contract fee for buying at probability-price ``ask`` on ``venue``.

    ``ask`` is the executable price in dollars (= implied probability).
    An unknown venue — any sportsbook — charges nothing.
    """
    rate = FEE_RATE_BY_VENUE.get(venue)
    if rate is None:
        return 0.0
    if not 0.0 < ask < 1.0:
        return 0.0
    raw = multiplier * rate * ask * (1.0 - ask)
    # Round the increment count before ceiling: an exact fee like $0.0175 lands
    # a few float ulps above 17,500 increments, and a naive ceil would bill an
    # extra micro-dollar for it.
    units = math.ceil(round(raw / _FEE_INCREMENT, 6))
    return round(units * _FEE_INCREMENT, 6)


def fee_for_price(price: float, venue: str, multiplier: float = 1.0) -> float:
    """The per-contract fee for an American ``price`` on ``venue``.

    Exchange prices reach the store as American odds (D1), so the ask is
    recovered as the price's implied probability before the fee is computed.
    """
    if venue not in FEE_RATE_BY_VENUE:
        return 0.0
    return taker_fee(american_to_prob(price), venue, multiplier)


def net_payout_after_fees(price: float, venue: str, multiplier: float = 1.0) -> float:
    """Profit per unit staked at ``price`` once ``venue``'s fee is paid.

    Returns ``net_payout(price)`` untouched for a venue that charges no fee, so
    sportsbook math is bit-for-bit unchanged. A fee that swallows the whole
    payout returns ``0.0`` — no profit is possible, and callers gate on it.
    """
    fee = fee_for_price(price, venue, multiplier)
    if fee <= 0.0:
        return net_payout(price)
    cost = american_to_prob(price) + fee
    if cost >= 1.0:
        return 0.0
    return (1.0 - cost) / cost
