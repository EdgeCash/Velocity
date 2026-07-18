"""Staking — fractional Kelly with hard guardrails.

Kelly sizing maximizes long-run growth, but full Kelly assumes we know the true
probability. We do not — our ``p`` is itself an estimate — so full Kelly on a
mis-estimated edge overbets and courts ruin. Two disciplines follow:

1. **Fractional Kelly.** Stake a fixed fraction (¼–½) of the Kelly bet. This
   sharply cuts variance and the cost of over-estimating an edge, for only a
   small give-up in growth.
2. **Hard caps.** Never stake more than a set fraction of bankroll on one bet,
   and never let a group of *correlated* bets (a side, its total, a same-game
   prop) exceed a group cap in aggregate — naive per-bet Kelly across correlated
   bets massively overstakes because it treats them as independent.

A non-positive edge always stakes zero: we never bet a price the model does not
beat.
"""

from __future__ import annotations

from dataclasses import dataclass

from velocity.wagering.edge import kelly_fraction

DEFAULT_KELLY_FRACTION = 0.25
DEFAULT_MAX_BET_FRACTION = 0.05


@dataclass(frozen=True)
class StakingConfig:
    """Fractional-Kelly multiplier and hard caps, as fractions of bankroll."""

    kelly_fraction: float = DEFAULT_KELLY_FRACTION
    max_bet_fraction: float = DEFAULT_MAX_BET_FRACTION

    def __post_init__(self) -> None:
        if not 0.0 < self.kelly_fraction <= 1.0:
            raise ValueError("kelly_fraction must be in (0, 1]")
        if not 0.0 < self.max_bet_fraction <= 1.0:
            raise ValueError("max_bet_fraction must be in (0, 1]")


def stake_fraction(
    p_model: float,
    price: float,
    config: StakingConfig | None = None,
) -> float:
    """Fraction of bankroll to stake: fractional Kelly, floored at 0 and capped.

    Returns 0 whenever the full-Kelly fraction is non-positive (no edge at this
    price), and never exceeds ``max_bet_fraction``.
    """
    config = config or StakingConfig()
    full = kelly_fraction(p_model, price)
    if full <= 0.0:
        return 0.0
    sized = config.kelly_fraction * full
    return min(sized, config.max_bet_fraction)


def stake_amount(
    bankroll: float,
    p_model: float,
    price: float,
    config: StakingConfig | None = None,
) -> float:
    """Absolute stake in bankroll units (``bankroll ×`` :func:`stake_fraction`)."""
    if bankroll < 0.0:
        raise ValueError("bankroll must be non-negative")
    return bankroll * stake_fraction(p_model, price, config)


def apply_group_cap(
    stakes: dict[str, float],
    group_cap_fraction: float,
    bankroll: float,
) -> dict[str, float]:
    """Scale a group of correlated stakes down so their sum ≤ the group cap.

    ``stakes`` maps a bet key to its stand-alone stake amount. If the group's
    total exposure exceeds ``group_cap_fraction × bankroll``, every stake is
    scaled by the same factor so relative sizing is preserved; otherwise the
    stakes are returned unchanged.
    """
    if not 0.0 < group_cap_fraction <= 1.0:
        raise ValueError("group_cap_fraction must be in (0, 1]")
    cap = group_cap_fraction * bankroll
    total = sum(stakes.values())
    if total <= cap or total == 0.0:
        return dict(stakes)
    scale = cap / total
    return {key: amount * scale for key, amount in stakes.items()}
