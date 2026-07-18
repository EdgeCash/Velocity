"""Odds conversions — American ↔ decimal ↔ implied probability.

American odds are how US books quote prices; decimal odds are the natural unit
for expected-value and Kelly math (decimal − 1 is the net payout per unit
staked); implied probability is what we de-vig and compare the model against.
These conversions are exact and mutually invertible, and every other wagering
module is built on them.

American odds convention: a positive price is the profit on a 100 stake
(``+150`` → risk 100 to win 150); a negative price is the stake needed to win
100 (``-200`` → risk 200 to win 100). There is no valid American price in
``(-100, 100)`` exclusive.
"""

from __future__ import annotations


def american_to_decimal(price: float) -> float:
    """Convert American odds to decimal odds (total return per unit staked)."""
    if -100 < price < 100:
        raise ValueError(f"invalid American odds: {price} (no price in (-100, 100))")
    if price > 0:
        return 1.0 + price / 100.0
    return 1.0 + 100.0 / -price


def decimal_to_american(decimal: float) -> float:
    """Convert decimal odds to American odds."""
    if decimal <= 1.0:
        raise ValueError(f"decimal odds must exceed 1.0, got {decimal}")
    if decimal >= 2.0:
        return (decimal - 1.0) * 100.0
    return -100.0 / (decimal - 1.0)


def american_to_prob(price: float) -> float:
    """Implied (vig-inclusive) probability of a single American price."""
    return 1.0 / american_to_decimal(price)


def prob_to_american(prob: float) -> float:
    """American odds implied by a probability (no vig added)."""
    if not 0.0 < prob < 1.0:
        raise ValueError(f"probability must be in (0, 1), got {prob}")
    return decimal_to_american(1.0 / prob)


def decimal_to_prob(decimal: float) -> float:
    """Implied (vig-inclusive) probability of decimal odds."""
    if decimal <= 1.0:
        raise ValueError(f"decimal odds must exceed 1.0, got {decimal}")
    return 1.0 / decimal


def net_payout(price: float) -> float:
    """Net profit per unit staked on a win (decimal odds − 1, aka *b*)."""
    return american_to_decimal(price) - 1.0
