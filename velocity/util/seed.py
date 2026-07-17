"""Deterministic randomness.

Every stochastic component (the Monte Carlo sim, any resampling) draws from a
seeded generator created here. Determinism is non-negotiable: same seed + same
input must always produce the same output, so a failing test means a real
regression, never flakiness.
"""

from __future__ import annotations

import numpy as np

DEFAULT_SEED = 1_729


def make_rng(seed: int = DEFAULT_SEED) -> np.random.Generator:
    """Return a NumPy random generator seeded for reproducibility."""
    return np.random.default_rng(seed)
