"""De-vigging — recover the market's fair probabilities from quoted prices.

A quoted price bakes in the book's margin (the *vig* or *overround*): the
implied probabilities of a market's outcomes sum to more than 1. To compare the
market to our model — and to measure closing-line value — we strip that margin
back out so the fair probabilities sum to exactly 1.

*How* the margin is removed matters, because books do not spread it uniformly.
Four methods are offered:

* ``multiplicative`` — scale every implied probability by ``1/overround``. The
  standard baseline; fast and unbiased when the vig is applied proportionally.
* ``additive`` — subtract an equal share of the overround from each outcome.
* ``shin`` — Shin's model, which attributes part of the margin to informed
  money and therefore shrinks favorites less than longshots; it reduces the
  favorite-longshot bias that plagues naive normalization on lopsided markets.
* ``power`` — raise each implied probability to a common exponent chosen so they
  sum to 1; another favorite-longshot correction.

``shin`` and ``power`` have no general closed form, so they are solved with a
deterministic bisection (no randomness — the same prices always give the same
fair probabilities).
"""

from __future__ import annotations

from collections.abc import Sequence

from velocity.wagering.odds import american_to_prob

Method = str
_METHODS = ("multiplicative", "additive", "shin", "power")
_TOL = 1e-12
_MAX_ITER = 200


def implied_probabilities(prices: Sequence[float]) -> list[float]:
    """Vig-inclusive implied probabilities for a set of American prices."""
    return [american_to_prob(p) for p in prices]


def overround(prices: Sequence[float]) -> float:
    """The book's margin: implied probabilities summed, minus 1 (a.k.a. vig)."""
    return sum(implied_probabilities(prices)) - 1.0


def devig(prices: Sequence[float], method: Method = "multiplicative") -> list[float]:
    """Fair (no-vig) probabilities for a market's American ``prices``.

    The result is non-negative and sums to 1. ``method`` selects the margin-
    removal model (see the module docstring).
    """
    if method not in _METHODS:
        raise ValueError(f"unknown devig method {method!r}; choose from {_METHODS}")
    q = implied_probabilities(prices)
    if len(q) < 2:
        raise ValueError("need at least two outcomes to de-vig a market")

    if method == "multiplicative":
        return _multiplicative(q)
    if method == "additive":
        return _additive(q)
    if method == "shin":
        return _shin(q)
    return _power(q)


def _multiplicative(q: list[float]) -> list[float]:
    total = sum(q)
    return [qi / total for qi in q]


def _additive(q: list[float]) -> list[float]:
    n = len(q)
    excess = (sum(q) - 1.0) / n
    fair = [qi - excess for qi in q]
    # Extreme lopsided markets can push a longshot below zero; floor and
    # renormalize so the result stays a valid probability vector.
    if any(f < 0 for f in fair):
        fair = [max(f, 0.0) for f in fair]
        total = sum(fair)
        return [f / total for f in fair]
    return fair


def _shin(q: list[float]) -> list[float]:
    """Shin (1993) — solve the insider-money fraction ``z`` so fair probs sum to 1."""
    s = sum(q)

    def fair_for_z(z: float) -> list[float]:
        denom = 2.0 * (1.0 - z)
        return [((z * z + 4.0 * (1.0 - z) * qi * qi / s) ** 0.5 - z) / denom for qi in q]

    # sum(fair) decreases monotonically in z; bisection on z ∈ [0, 1).
    lo, hi = 0.0, 0.999_999
    if sum(fair_for_z(lo)) <= 1.0:  # no overround → nothing to remove
        return _multiplicative(q)
    for _ in range(_MAX_ITER):
        mid = 0.5 * (lo + hi)
        if sum(fair_for_z(mid)) > 1.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < _TOL:
            break
    return fair_for_z(0.5 * (lo + hi))


def _power(q: list[float]) -> list[float]:
    """Raise each implied prob to a common exponent ``e`` so they sum to 1."""
    # sum(q_i^e) is decreasing in e for q_i < 1; bisection on e.
    lo, hi = 1.0, 1.0
    # Expand the upper bound until the sum drops below 1.
    while sum(qi**hi for qi in q) > 1.0 and hi < 1e6:
        hi *= 2.0
    for _ in range(_MAX_ITER):
        mid = 0.5 * (lo + hi)
        if sum(qi**mid for qi in q) > 1.0:
            lo = mid
        else:
            hi = mid
        if hi - lo < _TOL:
            break
    e = 0.5 * (lo + hi)
    fair = [qi**e for qi in q]
    total = sum(fair)
    return [f / total for f in fair]
