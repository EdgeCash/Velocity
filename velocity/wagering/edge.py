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


def expected_value(p_model: float, price: float) -> float:
    """EV per unit staked at American ``price`` given model probability ``p_model``.

    ``+0.05`` means a nickel of expected profit per unit risked.
    """
    b = net_payout(price)
    return p_model * b - (1.0 - p_model)


def kelly_fraction(p_model: float, price: float) -> float:
    """Growth-optimal bankroll fraction for this edge (may be ≤ 0 → no bet)."""
    b = net_payout(price)
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
) -> BetSignal:
    """Score one opportunity and decide whether it clears the edge threshold.

    ``price`` should already be the best shopped number. A bet ``qualifies``
    only when the probability edge meets ``min_edge`` *and* the expected value
    is strictly positive — both, so we never chase a thin edge on a bad price.
    """
    edge = probability_edge(p_model, p_fair)
    ev = expected_value(p_model, price)
    kelly = kelly_fraction(p_model, price)
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
