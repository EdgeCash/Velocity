"""Pick'em slip EV engine — fixed-payout parlays priced honestly.

A pick'em board (PrizePicks-style) is not a market: one line per (player,
stat), fixed multipliers per slip shape, no vig to devig. That makes the EV
question exact once you know two things — the true probability of each leg,
and the joint distribution of hits. This module owns both halves:

* **Payout tables** — the official PrizePicks structures (help-center,
  fetched 2026-08). Multipliers are *total return* per $1 staked, so EV is a
  return multiple: 1.0 = breakeven, above it is +EV.
* **Slip EV** — two paths that agree by construction:

  - :func:`slip_ev` — independent legs via the exact Poisson-binomial hit
    distribution (a small DP, no simulation error).
  - :func:`slip_ev_from_hits` — **correlated** legs via a boolean hit matrix
    read off the correlated prop simulation
    (:class:`velocity.models.props.PropSim` samples every player conditioned
    on the same simulated game). This is the sharp half: fixed payout tables
    implicitly price legs as independent, so positively correlated legs (a
    QB's passing yards and his WR1's receiving yards) push the all-hit
    probability — and power-play EV — above what the multiplier assumes.

* **Breakevens** — the uniform per-leg probability at which a slip shape
  returns exactly 1.0; the content numbers (2-pick power ≈ 57.7%) and the
  sanity rail for the engine.
* **Slip search** — :func:`best_slips` enumerates candidate combinations
  across shapes and ranks by EV, correlation-aware when a samples source is
  supplied.

Deliberately *not* modeled: demon/goblin alternates (their payout deltas are
unpublished — the board rows arrive flagged via ``odds_type`` and are simply
not standard legs) and in-slip promos. Voided/DNP legs revert a slip to the
one-size-smaller structure per the published rules — :func:`reverted_table`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from itertools import combinations
from types import MappingProxyType

import numpy as np
import pandas as pd

from velocity.wagering.devig import devig

__all__ = [
    "Leg",
    "PAYOUTS",
    "PayoutTable",
    "best_slips",
    "breakeven_leg_prob",
    "conservative_leg",
    "fair_leg_prob",
    "hit_distribution",
    "hit_matrix",
    "reverted_table",
    "slip_ev",
    "slip_ev_from_hits",
]


@dataclass(frozen=True)
class PayoutTable:
    """One slip shape: how many picks, and the return multiple per hit count.

    ``multipliers`` maps *exact* correct-pick counts to the total return per
    $1 staked; absent counts pay 0. ``power-4`` is ``{4: 10.0}``; ``flex-5``
    is ``{5: 10.0, 4: 2.0, 3: 0.4}``.
    """

    name: str
    n_picks: int
    multipliers: Mapping[int, float]

    def __post_init__(self) -> None:
        if self.n_picks < 1:
            raise ValueError("a slip needs at least one pick")
        if not self.multipliers:
            raise ValueError("a payout table needs at least one paying count")
        if any(not 0 <= k <= self.n_picks for k in self.multipliers):
            raise ValueError("paying counts must be within 0..n_picks")
        object.__setattr__(self, "multipliers", MappingProxyType(dict(self.multipliers)))

    def payout(self, hits: int) -> float:
        """Return multiple for exactly ``hits`` correct picks."""
        return float(self.multipliers.get(int(hits), 0.0))


def _table(name: str, n: int, mult: dict[int, float]) -> PayoutTable:
    return PayoutTable(name=name, n_picks=n, multipliers=mult)


# The official structures (PrizePicks help-center, fetched 2026-08).
# Total-return multiples per $1. Keys are slip names used across the engine.
PAYOUTS: Mapping[str, PayoutTable] = MappingProxyType({
    "power-2": _table("power-2", 2, {2: 3.0}),
    "power-3": _table("power-3", 3, {3: 6.0}),
    "power-4": _table("power-4", 4, {4: 10.0}),
    "power-5": _table("power-5", 5, {5: 20.0}),
    "power-6": _table("power-6", 6, {6: 37.5}),
    "flex-2": _table("flex-2", 2, {2: 2.0, 1: 0.5}),
    "flex-3": _table("flex-3", 3, {3: 3.0, 2: 1.0}),
    "flex-4": _table("flex-4", 4, {4: 6.0, 3: 1.5}),
    "flex-5": _table("flex-5", 5, {5: 10.0, 4: 2.0, 3: 0.4}),
    "flex-6": _table("flex-6", 6, {6: 25.0, 5: 2.0, 4: 0.4}),
})


def reverted_table(table: PayoutTable, voids: int = 1) -> PayoutTable | None:
    """The structure a slip reverts to when ``voids`` legs DNP/void.

    Published rule: the lineup "reverts to a one-level-lower contest
    structure" per void; a 2-pick reverting becomes a refund. Returns the
    smaller table of the same kind, or ``None`` for the refund case (the
    caller treats ``None`` as multiplier 1.0 on the stake).
    """
    if voids < 1:
        return table
    kind = table.name.split("-")[0]
    target = table.n_picks - voids
    smaller = PAYOUTS.get(f"{kind}-{target}")
    return smaller


def hit_distribution(probs: Iterable[float]) -> np.ndarray:
    """P(exactly k hits) for independent legs — the Poisson-binomial DP.

    Exact (no simulation), O(n²). Index k of the result is the probability
    of exactly k correct picks.
    """
    dist = np.array([1.0])
    for p in probs:
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"leg probability {p} outside [0, 1]")
        dist = np.convolve(dist, [1.0 - p, p])
    return dist


def slip_ev(probs: Iterable[float], table: PayoutTable) -> float:
    """Expected return multiple of a slip, treating legs as independent."""
    probs = list(probs)
    if len(probs) != table.n_picks:
        raise ValueError(f"{table.name} needs {table.n_picks} legs, got {len(probs)}")
    dist = hit_distribution(probs)
    return float(sum(table.payout(k) * dist[k] for k in range(len(dist))))


def slip_ev_from_hits(hits: np.ndarray, table: PayoutTable) -> float:
    """Expected return multiple from a correlated boolean hit matrix.

    ``hits`` is (n_sims, n_legs): one row per simulated world, one column per
    leg, read off the correlated prop sim. Correlation flows through for
    free — the payout is applied to each world's actual hit count.
    """
    if hits.ndim != 2 or hits.shape[1] != table.n_picks:
        raise ValueError(f"{table.name} needs a (n_sims, {table.n_picks}) hit matrix")
    counts = hits.sum(axis=1)
    payouts = np.zeros(table.n_picks + 1)
    for k, mult in table.multipliers.items():
        payouts[k] = mult
    return float(payouts[counts].mean())


def breakeven_leg_prob(table: PayoutTable, tol: float = 1e-9) -> float:
    """The uniform per-leg probability at which the slip returns exactly 1.0.

    Bisection on a monotone function — exact enough for content and sanity
    checks (power-2 → 1/√3 ≈ 0.5774).
    """
    lo, hi = 0.0, 1.0
    while hi - lo > tol:
        mid = (lo + hi) / 2.0
        if slip_ev([mid] * table.n_picks, table) < 1.0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def fair_leg_prob(
    over_price: float, under_price: float, method: str = "multiplicative"
) -> float:
    """Devigged P(over) from a sportsbook's two-sided prop prices.

    The standard leg-probability source: the book's over/under prices at the
    *same* line as the board carry vig; devigging them yields the fair
    probability the payout-table breakevens are compared against.

    ``method="worst_case"`` returns the **smallest** P(over) any standard
    method (multiplicative/Shin/power) assigns — the conservative bound when
    the leg being priced is the over. A leg on the *under* wants the
    complement of the largest P(over); use :func:`conservative_leg` for the
    side-aware version the board builder runs.
    """
    if method == "worst_case":
        from velocity.wagering.devig import devig_worst_case

        return float(devig_worst_case([over_price, under_price])[0])
    fair = devig([over_price, under_price], method=method)
    return float(fair[0])


def conservative_leg(over_price: float, under_price: float) -> tuple[str, float]:
    """Side-aware worst-case leg: ``(side, p)`` with ``p`` every method endorses.

    Direction comes from the multiplicative baseline (which side is the
    favorite); magnitude is the minimum probability any standard devig method
    assigns *that side* — so a leg only clears a payout-table breakeven when
    multiplicative, Shin, and power all agree it does. This is the published
    pick'em discipline for suppressing false positives near the 54–58%
    thresholds (docs/EDGE_RESEARCH.md §4).
    """
    from velocity.wagering.devig import devig_worst_case

    baseline = devig([over_price, under_price], method="multiplicative")
    side = "over" if baseline[0] >= 0.5 else "under"
    conservative = devig_worst_case([over_price, under_price])
    p = conservative[0] if side == "over" else conservative[1]
    return side, float(p)


@dataclass(frozen=True)
class Leg:
    """One candidate pick: a (player, stat, line, side) with its probability.

    ``p`` is the marginal probability the leg hits — devigged from book
    prices (:func:`fair_leg_prob`), read from our prop model, or a blend;
    the engine does not care which, but the lab should before anything is
    published.
    """

    player: str
    stat: str
    line: float
    side: str  # "over" | "under"
    p: float
    # Which game the leg belongs to (None: unknown). Fixed payout tables
    # assume independence, and the operators know it: same-game combinations
    # now carry reduced payouts or outright blocks, so the slip builder needs
    # the identity to cap and flag them.
    game_id: str | None = None

    def __post_init__(self) -> None:
        if self.side not in ("over", "under"):
            raise ValueError(f"side must be over/under, got {self.side!r}")
        if not 0.0 < self.p < 1.0:
            raise ValueError(f"leg probability {self.p} must be inside (0, 1)")

    @property
    def label(self) -> str:
        return f"{self.player} {self.side} {self.line:g} {self.stat}"


SamplesFn = Callable[[str, str], np.ndarray]


def hit_matrix(legs: list[Leg], samples_fn: SamplesFn) -> np.ndarray:
    """Boolean (n_sims, n_legs) hit matrix from a correlated samples source.

    ``samples_fn(player, stat)`` returns the per-simulation stat samples —
    :class:`~velocity.models.props.PropSim` draws every player conditioned on
    the same simulated game, so columns built from one sim carry the real
    correlation structure. Pushes at the line count as misses, matching the
    over/under semantics of a numeric board line.
    """
    columns = []
    for leg in legs:
        samples = np.asarray(samples_fn(leg.player, leg.stat), dtype=float)
        hit = samples > leg.line if leg.side == "over" else samples < leg.line
        columns.append(hit)
    return np.column_stack(columns)


def best_slips(
    legs: list[Leg],
    *,
    samples_fn: SamplesFn | None = None,
    tables: Iterable[str] = tuple(PAYOUTS),
    max_legs: int = 12,
    min_ev: float = 0.0,
    top: int = 10,
    max_per_game: int | None = None,
) -> pd.DataFrame:
    """Enumerate and rank candidate slips across shapes by expected return.

    With ``samples_fn`` the EV is correlation-aware (the joint hit matrix is
    built once and sliced per combination); without it legs are treated as
    independent — fine for screening, wrong for same-game stacks. Candidates
    beyond ``max_legs`` are trimmed by marginal probability (fixed payouts
    reward hit rate, not price). Returns the ``top`` rows over ``min_ev``,
    sorted by EV: one row per (shape, combination) with the leg labels,
    the all-hit probability and the EV multiple.

    ``max_per_game`` caps how many legs of one slip may share a game (legs
    with a ``game_id``). Operators now tax the correlation the fixed tables
    ignore — reduced same-game payouts, blocked combos — so the published EV
    of a heavy same-game stack is an upper bound; every emitted row carries a
    ``same_game`` flag so downstream surfaces can say so.
    """
    ranked = sorted(legs, key=lambda leg: leg.p, reverse=True)[:max_legs]
    joint = hit_matrix(ranked, samples_fn) if samples_fn is not None else None

    rows: list[dict[str, object]] = []
    for name in tables:
        table = PAYOUTS[name]
        if table.n_picks > len(ranked):
            continue
        for combo in combinations(range(len(ranked)), table.n_picks):
            chosen = [ranked[i] for i in combo]
            games = [leg.game_id for leg in chosen if leg.game_id is not None]
            per_game = Counter(games)
            if max_per_game is not None and per_game and max(per_game.values()) > max_per_game:
                continue
            same_game = bool(per_game) and max(per_game.values()) > 1
            if joint is not None:
                sub = joint[:, combo]
                ev = slip_ev_from_hits(sub, table)
                p_all = float(sub.all(axis=1).mean())
            else:
                ev = slip_ev([leg.p for leg in chosen], table)
                p_all = float(np.prod([leg.p for leg in chosen]))
            if ev < min_ev:
                continue
            rows.append({
                "slip": name,
                "ev": ev,
                "p_all": p_all,
                "max_multiplier": max(table.multipliers.values()),
                "legs": " + ".join(leg.label for leg in chosen),
                "n_picks": table.n_picks,
                "same_game": same_game,
            })
    out = pd.DataFrame(
        rows,
        columns=["slip", "ev", "p_all", "max_multiplier", "legs", "n_picks", "same_game"],
    )
    if out.empty:
        return out
    return out.sort_values("ev", ascending=False).head(top).reset_index(drop=True)
