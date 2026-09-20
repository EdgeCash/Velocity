"""Rule tiers: the curated list's ranking, from the wager lab's record.

A staked play earns a tier from the rule that admitted it — the market, the
side and the points of disagreement with the number — and the tier carries
the rule's walk-forward record (docs/OUTPUT_AUDIT.md §2.2): win rate, bets,
seasons cleared of the twelve or fifteen scored. That record is what the
card prints beside the play and what the publish gate ranks on, in place of
a conviction score the intel backtest measured as a null.

The table is the lab's output (``scripts/wager_lab.py``), pinned here and
checked against the committed projections by the tests, so it cannot drift
from the evidence without a test saying so.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class RuleTier:
    """A tier's rule and its record.

    ``sides`` names the sides the rule takes; ``min_points`` the disagreement
    floor in the side's direction. ``win_rate``/``n``/``seasons_cleared``/
    ``seasons`` are the lab's walk-forward numbers for the rule.
    """

    tier: str
    market: str
    sides: frozenset[str]
    min_points: float
    win_rate: float
    n: int
    seasons_cleared: int
    seasons: int

    @property
    def record(self) -> str:
        """The record as the card prints it."""
        return (f"{self.win_rate:.1%} over {self.n} bets, "
                f"{self.seasons_cleared} of {self.seasons} seasons")

    @property
    def short_record(self) -> str:
        """The record at chip width: ``"55.6% on 340"``."""
        return f"{self.win_rate:.1%} on {self.n}"

    @property
    def name(self) -> str:
        """The rule as a reader says it: ``"unders 4+"`` / ``"overs and unders 4+"``."""
        sides = " and ".join(f"{s}s" for s in sorted(self.sides))
        return f"{sides} {self.min_points:g}+"


# The wager lab's records on the promoted chains (NFL 2026-09-20, on the
# level round's ledger; college 2026-09-17). Tier A is the side of the rule
# with the stronger record; tier B the promoted rule's other side. A play
# that matches no row has no tier and is not posted. The NFL over side has
# no row: on the new level's ledger overs at 4+ read 49.8% over 291 bets
# (−3.3% at the juice; 49.4% on 2015+), where they read 52.8% before — a
# coin flip either way, and the slate now takes NFL totals on the under
# side only, as it has taken college's.
RULE_TIERS: Mapping[str, tuple[RuleTier, ...]] = {
    "nfl": (
        RuleTier("A", "total", frozenset({"under"}), 4.0, 0.562, 299, 11, 15),
    ),
    "ncaaf": (
        RuleTier("A", "total", frozenset({"under"}), 8.0, 0.572, 297, 8, 11),
        RuleTier("B", "total", frozenset({"under"}), 4.0, 0.536, 1263, 7, 12),
    ),
}

TIER_ORDER = {"A": 0, "B": 1, "C": 2}


def rules_for(
    league: str, market: str,
    tiers: Mapping[str, Iterable[RuleTier]] | None = None,
) -> tuple[RuleTier, ...]:
    """Every rule with a record on this league's market, in table order."""
    table = (tiers or RULE_TIERS).get(league, ())
    return tuple(t for t in table if t.market == market)


def tier_for(
    league: str, market: str, side: str, points: float | None,
    tiers: Mapping[str, Iterable[RuleTier]] | None = None,
) -> RuleTier | None:
    """The highest tier whose rule admits this play, or ``None``.

    ``points`` is the model's disagreement with the number in the side's
    direction (``velocity.wagering.slate.total_disagreement``); a play with
    no measured disagreement matches nothing.
    """
    if points is None:
        return None
    table = (tiers or RULE_TIERS).get(league, ())
    matches = [t for t in table
               if t.market == market and side in t.sides and points >= t.min_points]
    if not matches:
        return None
    return min(matches, key=lambda t: TIER_ORDER.get(t.tier, 99))


def tier_rank(tier: RuleTier | None) -> int:
    return TIER_ORDER.get(tier.tier, 99) if tier is not None else 99
