"""A right-skewed draw, for the one place football is not symmetric.

The gate-reference round (``docs/MODEL_LAB.md``) named a defect the old
ladder table had been blurring into "leptokurtosis": **total residuals are
right-skewed and the sim is symmetric.** A football game can run away upward
and cannot run away downward, and the data says so in both leagues — the
residual against the market close has a skew of +0.33 in the NFL and +0.34
in college, against +0.10 and +0.01 on the spreads. The consequence is
measurable at every rung near the line: at 4.5 out the sim overstates the
over tail by 0.028 and understates the under by 0.008.

**A fatter-tailed draw does not fix this.** That was the point of recording
it separately. Skew is not kurtosis; the empirical residual pool
(:mod:`velocity.models.residuals`) carries both at once and costs moneyline
calibration for it. This carries skew alone, with one parameter.

The draw is a sinh-arcsinh transform (Jones & Pewsey) at δ=1, where it
collapses to something with no special functions in it at all::

    Y = sinh(asinh(Z) + ε) = Z·cosh(ε) + √(1+Z²)·sinh(ε)

It is monotone in ``Z``, so it reorders nothing — a draw's rank is the rank
it came in with, which is what keeps a jointly-drawn margin and total
sensibly related. Its first three moments are closed form (see
:func:`standard_moments`), so the result is standardized exactly rather than
by the sample's own mean and sd; a sample-standardized draw would depend on
``n_sims`` and stop being reproducible at a different one.
"""

from __future__ import annotations

import numpy as np

# E[√(1+Z²)], E[Z²√(1+Z²)] and E[(1+Z²)^{3/2}] for a standard normal Z.
# Gauss–Hermite rather than Monte Carlo or a hardcoded literal: the constants
# then carry no sampling error and no transcription risk, and numpy computes
# them at import in under a millisecond.
_NODES, _WEIGHTS = np.polynomial.hermite_e.hermegauss(200)
_WEIGHTS = _WEIGHTS / _WEIGHTS.sum()
_C1 = float((np.sqrt(1.0 + _NODES**2) * _WEIGHTS).sum())
_C2 = float((_NODES**2 * np.sqrt(1.0 + _NODES**2) * _WEIGHTS).sum())
_C3 = float(((1.0 + _NODES**2) ** 1.5 * _WEIGHTS).sum())

# Past this the transform is stretching the tail far harder than any football
# residual asks for, and the bisection below has nowhere useful left to go.
MAX_EPSILON = 2.0


def standard_moments(epsilon: float) -> tuple[float, float, float]:
    """``(mean, sd, skew)`` of the raw transform, in closed form.

    With ``a = cosh(ε)`` and ``b = sinh(ε)``, ``Y = aZ + b√(1+Z²)``, and the
    odd moments of ``Z`` that would otherwise appear all vanish::

        E[Y]   = b·E[√(1+Z²)]
        E[Y²]  = a² + 2b²
        E[Y³]  = 3a²b·E[Z²√(1+Z²)] + b³·E[(1+Z²)^{3/2}]

    so the mean, variance and third central moment follow with no quadrature
    at call time.
    """
    a, b = float(np.cosh(epsilon)), float(np.sinh(epsilon))
    mean = b * _C1
    second = a * a + 2.0 * b * b
    third = 3.0 * a * a * b * _C2 + b**3 * _C3
    variance = second - mean * mean
    if variance <= 0.0:
        return mean, 0.0, 0.0
    central_third = third - 3.0 * mean * variance - mean**3
    return mean, float(np.sqrt(variance)), float(central_third / variance**1.5)


def skewness(epsilon: float) -> float:
    """The skew this ``epsilon`` produces. Monotone increasing through zero."""
    return standard_moments(epsilon)[2]


def solve_epsilon(target_skew: float, *, tol: float = 1e-9) -> float:
    """The ``epsilon`` whose draw has ``target_skew``.

    Monotone, so a bisection is enough and needs no derivative. A target
    beyond what the transform can reach is clamped to the bound rather than
    run away: a league that wants more skew than this can express is telling
    you to change the family, not the parameter.
    """
    target = float(target_skew)
    if not np.isfinite(target) or target == 0.0:
        return 0.0
    lo, hi = (0.0, MAX_EPSILON) if target > 0 else (-MAX_EPSILON, 0.0)
    if target > 0 and skewness(hi) < target:
        return hi
    if target < 0 and skewness(lo) > target:
        return lo
    for _step in range(200):
        mid = 0.5 * (lo + hi)
        if skewness(mid) < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def skew_draw(z: np.ndarray, epsilon: float) -> np.ndarray:
    """``z`` re-shaped to carry skew, still standardized to mean 0 / sd 1.

    Standardized by the closed-form moments above, not by the sample's, so
    the same ``epsilon`` means the same distribution at any ``n_sims`` and a
    config's ``sd_total`` keeps meaning what it says.
    """
    if not epsilon:
        return z
    mean, sd, _ = standard_moments(epsilon)
    if sd <= 0.0:
        return z
    y = z * float(np.cosh(epsilon)) + np.sqrt(1.0 + z * z) * float(np.sinh(epsilon))
    return (y - mean) / sd


def fit_epsilon(residuals: np.ndarray) -> float:
    """The ``epsilon`` matching a residual sample's own skew.

    Sample skew is noisy — its standard error is about √(6/n), so 4,000 games
    give ±0.04 — which is why this is fitted on the training seasons and
    scored out-of-sample like every other shape parameter in the lab, and why
    a league whose skew is inside that noise band is better served by zero.
    """
    x = np.asarray(residuals, dtype=float)
    x = x[np.isfinite(x)]
    if x.size < 3:
        return 0.0
    centred = x - x.mean()
    sd = float(np.sqrt((centred**2).mean()))
    if sd <= 0.0:
        return 0.0
    return solve_epsilon(float((centred**3).mean() / sd**3))
