"""Portfolio staking — correlation-aware sizing across a whole slate.

Per-bet Kelly assumes each bet is independent. A slate is not: a spread, its
total, and a same-game prop move together, so naive per-bet Kelly across
correlated bets massively overstakes (DESIGN §6.3). Sizing the *portfolio* adds
three disciplines on top of the per-bet fractional Kelly:

1. **Correlation de-scaling.** Bets in the same correlation group (e.g. one game)
   are scaled down by ``1 / (1 + (m-1)·ρ)`` for a group of ``m`` bets with average
   pairwise correlation ``ρ`` — the factor by which correlated exposure inflates
   effective variance. Independent bets (``ρ = 0``) are untouched.
2. **Caps at two levels.** A per-group cap bounds exposure to any one game; an
   aggregate cap bounds total exposure across the slate. Both are enforced, so
   the book can never exceed either.
3. **A kill-switch.** If the bankroll's drawdown from its peak exceeds a
   threshold, every stake goes to zero — the circuit breaker that stops a cold
   streak from compounding into ruin.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from velocity.wagering.staking import apply_group_cap


@dataclass(frozen=True)
class PortfolioConfig:
    """Slate-level caps, correlation assumption, and the kill-switch threshold."""

    max_portfolio_fraction: float = 0.25
    group_cap_fraction: float = 0.10
    group_correlation: float = 0.5
    max_drawdown_fraction: float = 0.30

    def __post_init__(self) -> None:
        for name in ("max_portfolio_fraction", "group_cap_fraction", "max_drawdown_fraction"):
            value = getattr(self, name)
            if not 0.0 < value <= 1.0:
                raise ValueError(f"{name} must be in (0, 1]")
        if not 0.0 <= self.group_correlation <= 1.0:
            raise ValueError("group_correlation must be in [0, 1]")


@dataclass(frozen=True)
class BetCandidate:
    """One staked opportunity awaiting portfolio sizing.

    ``stake_fraction`` is the standalone fractional-Kelly fraction of bankroll;
    ``group`` labels its correlation group (typically the game id).
    """

    key: str
    stake_fraction: float
    group: str


def drawdown(current_bankroll: float, peak_bankroll: float) -> float:
    """Fractional drawdown from the peak (0 at a new high, →1 near ruin)."""
    if peak_bankroll <= 0:
        return 0.0
    return max(0.0, 1.0 - current_bankroll / peak_bankroll)


def should_halt(current_bankroll: float, peak_bankroll: float, max_drawdown: float) -> bool:
    """Whether the kill-switch trips at the current drawdown."""
    return drawdown(current_bankroll, peak_bankroll) >= max_drawdown


def correlation_scale(group_size: int, rho: float) -> float:
    """Exposure de-scaling factor for ``group_size`` bets correlated at ``rho``."""
    if group_size <= 1:
        return 1.0
    return 1.0 / (1.0 + (group_size - 1) * rho)


def size_portfolio(
    candidates: list[BetCandidate],
    bankroll: float,
    config: PortfolioConfig | None = None,
    *,
    current_bankroll: float | None = None,
    peak_bankroll: float | None = None,
) -> dict[str, float]:
    """Size a slate of candidates into stake amounts under all portfolio rules.

    Returns a ``key → stake amount`` map. If the kill-switch trips (drawdown from
    ``peak_bankroll`` to ``current_bankroll`` exceeds the configured threshold),
    every stake is zero. Otherwise stakes are correlation-de-scaled within their
    group, capped per group, and finally capped in aggregate.
    """
    config = config or PortfolioConfig()
    if bankroll <= 0:
        return {c.key: 0.0 for c in candidates}

    if current_bankroll is not None and peak_bankroll is not None and should_halt(
        current_bankroll, peak_bankroll, config.max_drawdown_fraction
    ):
        return {c.key: 0.0 for c in candidates}

    # Group candidates by correlation group.
    groups: dict[str, list[BetCandidate]] = defaultdict(list)
    for cand in candidates:
        groups[cand.group].append(cand)

    stakes: dict[str, float] = {}
    for members in groups.values():
        scale = correlation_scale(len(members), config.group_correlation)
        group_stakes = {c.key: bankroll * c.stake_fraction * scale for c in members}
        # Enforce the per-group exposure cap on the de-scaled stakes.
        group_stakes = apply_group_cap(group_stakes, config.group_cap_fraction, bankroll)
        stakes.update(group_stakes)

    # Enforce the aggregate portfolio cap across every group.
    total = sum(stakes.values())
    cap = config.max_portfolio_fraction * bankroll
    if total > cap and total > 0:
        scale = cap / total
        stakes = {key: amount * scale for key, amount in stakes.items()}

    return stakes
