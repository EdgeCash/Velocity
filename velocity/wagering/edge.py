"""Edge & expected value — is the price wrong enough to bet?

A projection is only worth money relative to a price. Given our model
probability ``p`` for an outcome and the price on offer, this module answers:

* **Expected value** per unit staked: ``EV = p·b − (1 − p)`` where ``b`` is the
  net decimal payout. Positive EV is necessary but not sufficient.
* **Edge** — how far our probability sits above the market's *de-vigged* fair
  probability. We bet only when that edge clears a threshold sized to our own
  estimation error (wider for noisier markets like props and NCAAF).
* **Kelly fraction** — the growth-optimal fraction of bankroll for this price,
  ``(p·b − (1 − p)) / b``. Staking scales this down (see
  :mod:`~velocity.wagering.staking`); it is defined here because it is a pure
  function of ``p`` and the price.

**Line shopping** is a real, free edge: the same bet at a better number pays
more, so we always evaluate against the best price available across books.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from velocity.wagering.odds import american_to_decimal, net_payout


def _payout(price: float, venue: str | None) -> float:
    """Profit per unit staked, net of an exchange's per-contract fee.

    ``venue`` names an exchange whose fee is charged on top of the price
    (docs/BUILD_EXCHANGES.md D3). ``None`` — every sportsbook — is the plain
    payout, so existing callers are unaffected.
    """
    if venue is None:
        return net_payout(price)
    from velocity.wagering.fees import net_payout_after_fees

    return net_payout_after_fees(price, venue)


def expected_value(p_model: float, price: float, venue: str | None = None) -> float:
    """EV per unit staked at American ``price`` given model probability ``p_model``.

    ``+0.05`` means a nickel of expected profit per unit risked. On an exchange,
    pass ``venue`` so the taker fee is charged against the payout.
    """
    b = _payout(price, venue)
    return p_model * b - (1.0 - p_model)


def kelly_fraction(p_model: float, price: float, venue: str | None = None) -> float:
    """Growth-optimal bankroll fraction for this edge (may be ≤ 0 → no bet)."""
    b = _payout(price, venue)
    if b <= 0.0:  # fees swallow the payout — no size is growth-optimal
        return 0.0
    return (p_model * b - (1.0 - p_model)) / b


def probability_edge(p_model: float, p_fair: float) -> float:
    """Model probability minus the market's de-vigged fair probability."""
    return p_model - p_fair


def best_price(prices: Sequence[float]) -> float:
    """The most favorable American price for the bettor (highest decimal payout)."""
    if not prices:
        raise ValueError("no prices to shop")
    return max(prices, key=american_to_decimal)


@dataclass(frozen=True)
class BetSignal:
    """The verdict for one (outcome, price) opportunity."""

    p_model: float
    p_fair: float
    price: float
    edge: float
    ev: float
    kelly: float
    qualifies: bool


def evaluate(
    p_model: float,
    price: float,
    p_fair: float,
    *,
    min_edge: float = 0.02,
    venue: str | None = None,
) -> BetSignal:
    """Score one opportunity and decide whether it clears the edge threshold.

    ``price`` should already be the best shopped number. A bet ``qualifies``
    only when the probability edge meets ``min_edge`` *and* the expected value
    is strictly positive — both, so we never chase a thin edge on a bad price.
    On an exchange, ``venue`` charges its taker fee against the payout, so a
    thin edge that the fee eats no longer qualifies.
    """
    edge = probability_edge(p_model, p_fair)
    ev = expected_value(p_model, price, venue)
    kelly = kelly_fraction(p_model, price, venue)
    qualifies = edge >= min_edge and ev > 0.0
    return BetSignal(
        p_model=p_model,
        p_fair=p_fair,
        price=price,
        edge=edge,
        ev=ev,
        kelly=kelly,
        qualifies=qualifies,
    )
