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
from collections.abc import Mapping
from dataclasses import dataclass, field

from velocity.wagering.staking import apply_group_cap


@dataclass(frozen=True)
class PortfolioConfig:
    """Slate-level caps, correlation assumption, and the kill-switch threshold."""

    max_portfolio_fraction: float = 0.25
    group_cap_fraction: float = 0.10
    group_correlation: float = 0.5
    max_drawdown_fraction: float = 0.30
    # The share of the slate cap any one *market class* may hold. Correlation
    # groups are games; model risk is not — sixty-six moneyline dogs from one
    # sim's tail are sixty-six "independent" bets to the game grouping and
    # took 60% of a card (docs/STRATEGY_REVIEW.md §2). A class is whatever
    # the caller labels (a market, "prop:receptions"); candidates without a
    # label are uncapped. 1.0 disables.
    max_class_fraction: float = 0.5
    # A second, independent cap dimension: ``venue label → share of the slate
    # cap``. A market class answers for model risk; a venue answers for the
    # risk of *where the bet lives* — an exchange's liquidity and fills, its
    # settlement, and in our case a ladder priced by a shape gate with no live
    # record yet. Those are not the same risk and a bet carries both, so this
    # is its own cap rather than a relabelling of the class.
    #
    # Only the venues named here are capped; everything absent is uncapped, so
    # this changes nothing for a caller that does not use it. A label may be a
    # venue or a family of them ("exchange" for Kalshi and Polymarket together,
    # which is the honest grouping while the shape risk they share is the
    # larger one).
    venue_caps: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("max_portfolio_fraction", "group_cap_fraction", "max_drawdown_fraction",
                     "max_class_fraction"):
            value = getattr(self, name)
            if not 0.0 < value <= 1.0:
                raise ValueError(f"{name} must be in (0, 1]")
        for venue, share in self.venue_caps.items():
            if not 0.0 <= share <= 1.0:
                raise ValueError(f"venue_caps[{venue!r}] must be in [0, 1]")
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
    market_class: str | None = None
    # The venue family this bet lives on, for :attr:`PortfolioConfig.venue_caps`.
    # ``None`` is uncapped, which is every caller that does not set it.
    venue: str | None = None


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
    group, capped per group, capped per market class and per named venue, and
    finally capped in aggregate.
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

    cap = config.max_portfolio_fraction * bankroll

    # Cap each market class at its share of the slate cap, so one model
    # assumption cannot be most of the card.
    if config.max_class_fraction < 1.0:
        by_class: dict[str, list[str]] = defaultdict(list)
        for cand in candidates:
            if cand.market_class is not None:
                by_class[cand.market_class].append(cand.key)
        class_cap = config.max_class_fraction * cap
        for keys in by_class.values():
            held = sum(stakes.get(key, 0.0) for key in keys)
            if held > class_cap and held > 0:
                scale = class_cap / held
                for key in keys:
                    if key in stakes:
                        stakes[key] *= scale

    # Cap each named venue at its share of the slate cap. Applied after the
    # class cap and before the aggregate one, so a venue cap can only ever
    # take exposure away — the slate cap still has the final word.
    if config.venue_caps:
        by_venue: dict[str, list[str]] = defaultdict(list)
        for cand in candidates:
            if cand.venue is not None and cand.venue in config.venue_caps:
                by_venue[cand.venue].append(cand.key)
        for venue, keys in by_venue.items():
            venue_cap = config.venue_caps[venue] * cap
            held = sum(stakes.get(key, 0.0) for key in keys)
            if held > venue_cap and held > 0:
                scale = venue_cap / held
                for key in keys:
                    if key in stakes:
                        stakes[key] *= scale

    # Enforce the aggregate portfolio cap across every group.
    total = sum(stakes.values())
    if total > cap and total > 0:
        scale = cap / total
        stakes = {key: amount * scale for key, amount in stakes.items()}

    return stakes
