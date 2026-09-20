"""Football's margin lattice, measured and reapplied.

The drive round (``docs/MODEL_LAB.md``) left the two sims failing in opposite
directions. The shipped normal has the right dispersion and no key numbers:
it puts 5.4% of NFL margins on 3 where football puts 14.8%. The possession
sampler has the key numbers and, in the NFL, more dispersion than football
has. This is the third option — take the lattice from the data and leave the
dispersion alone.

**What it is, and what it is not.** This is a CORRECTION, not a model. The
drive sim explains where the mass at 7 comes from; this one only measures
that football lands on 3 more often than a normal would and reapplies that
measurement. It earns its place by being right, not by being an
explanation — but the distinction matters when reading a win here, and it
matters more if the lattice ever moves: a correction fitted to a rule set
goes stale silently when the rule set changes, where a structural model
adapts. The extra point moving to the 15 in 2015 is exactly the sort of
change that would do it, which is why the weights are fitted walk-forward
rather than written down.

**Why the correction has to be global.** The obvious design — pull margins
that land near 3 onto 3 — cannot work, and the data says so plainly. Against
the shipped sim over 3,044 NFL games, a margin of 3 is short by 9.4
percentage points while 2 and 4 together are long by only 1.6. The
three-point spike is not borrowed from its neighbours; it is drawn from the
whole distribution, including margins of 9, 11, 12 and 15, which football
produces at half the rate a normal does. So the correction is a weight on
every absolute margin, not a local snap.

The weight at each absolute margin is how much more often football lands
there than the sim being corrected does. Applied by resampling the sim's own
draws, so the output is an ordinary :class:`GameSim` that every pricing
helper reads unchanged, and a margin the sim never produced is never
invented.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import erf, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

from velocity.models.simulate import GameSim

# The banked table (scripts/build_lattice.py): one row per absolute margin,
# fitted on the league's own residual bank against the shipped normal.
DATASETS = Path("datasets")
LATTICE_FILE = "lattice.parquet"
LATTICE_COLUMNS = ["abs_margin", "weight"]

# How far out the lattice is corrected. Beyond this the weight is 1: the
# structure has washed out (football's 25-point margins are not special), the
# bins hold a handful of games each, and a ratio fitted on a handful is noise
# with a decimal point.
DEFAULT_MAX_ABS = 21
# Pseudo-games added to both sides of every ratio, which shrinks a bin toward
# "no correction" in proportion to how little evidence it has. A bin holding
# 120 games barely moves; one holding 8 is pulled most of the way back to 1.
DEFAULT_PRIOR = 30.0


def _normal_cdf(x: np.ndarray) -> np.ndarray:
    """Φ, without pulling in scipy for one function."""
    return 0.5 * (1.0 + np.vectorize(erf)(np.asarray(x, dtype=float) / sqrt(2.0)))


def rounded_normal_mass(
    mu_margin: np.ndarray, sd_margin: float | np.ndarray,
    max_abs: int = DEFAULT_MAX_ABS,
) -> np.ndarray:
    """``P(|margin| = k)`` for ``k`` in ``0..max_abs``, averaged over games.

    Closed form for exactly what :func:`velocity.models.simulate.simulate_game`
    produces on its normal path — a normal rounded to the nearest integer —
    so the reference the weights are measured against carries no Monte Carlo
    noise of its own. The last bin is the tail: everything at or beyond
    ``max_abs``, so the masses sum to one and a weight of 1 out there means
    what it says.
    """
    mu = np.asarray(mu_margin, dtype=float)
    sd = np.asarray(sd_margin, dtype=float)
    out = np.zeros(max_abs + 1)
    for k in range(max_abs):
        p = _normal_cdf((k + 0.5 - mu) / sd) - _normal_cdf((k - 0.5 - mu) / sd)
        if k > 0:
            p = p + (_normal_cdf((-k + 0.5 - mu) / sd)
                     - _normal_cdf((-k - 0.5 - mu) / sd))
        out[k] = float(np.mean(p))
    out[max_abs] = max(1.0 - out[:max_abs].sum(), 0.0)
    return out


def simulated_margin_mass(
    mu_margin: np.ndarray, mu_total: np.ndarray,
    simulate: Callable[[float, float, np.random.Generator], GameSim],
    rng: np.random.Generator, *, max_abs: int = DEFAULT_MAX_ABS,
    sample: int | None = 1500,
) -> np.ndarray:
    """The same reference mass as :func:`rounded_normal_mass`, for any sim.

    A sim with no closed form — the possession sampler, an empirical draw —
    has to be asked rather than solved. ``sample`` caps how many of the
    training games are actually simulated, taken on a deterministic stride
    across the frame rather than at random so the answer does not move
    between runs. A couple of thousand games is far more than a twenty-two
    bin histogram needs, and simulating every one of eight thousand to fill
    it would be paying for precision the bins cannot hold.
    """
    mu_m = np.asarray(mu_margin, dtype=float)
    mu_t = np.asarray(mu_total, dtype=float)
    if mu_m.size == 0:
        raise ValueError("no games to measure the reference mass on")
    if sample is not None and mu_m.size > sample:
        stride = int(np.ceil(mu_m.size / sample))
        mu_m, mu_t = mu_m[::stride], mu_t[::stride]
    counts = np.zeros(max_abs + 1)
    for margin, total in zip(mu_m, mu_t, strict=True):
        sim = simulate(float(margin), float(total), rng)
        index = np.clip(np.rint(np.abs(sim.margin)).astype(np.int64), 0, max_abs)
        counts += np.bincount(index, minlength=max_abs + 1) / sim.n_sims
    return counts / float(mu_m.size)


@dataclass(frozen=True, eq=False)
class LatticeWeights:
    """How much more often football lands on each absolute margin than a sim does.

    ``weights[k]`` multiplies the sim's mass at an absolute margin of ``k``;
    the last entry covers everything at or beyond ``len(weights) - 1``, and
    anything past that is left alone.

    ``eq=False`` with an explicit ``__eq__``: the generated one compares the
    fields as a tuple, and a numpy array in a tuple comparison raises rather
    than answering. A table nobody can compare is a trap for whoever first
    puts one in a config and tries to check two configs are the same.
    """

    weights: np.ndarray

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LatticeWeights):
            return NotImplemented
        return bool(np.array_equal(self.weights, other.weights))

    def __post_init__(self) -> None:
        if self.weights.ndim != 1 or self.weights.size < 2:
            raise ValueError("weights must be a 1-D table over absolute margins")
        if not np.all(np.isfinite(self.weights)) or np.any(self.weights < 0):
            raise ValueError("weights must be finite and non-negative")

    @property
    def max_abs(self) -> int:
        return int(self.weights.size - 1)

    def of(self, abs_margin: np.ndarray) -> np.ndarray:
        """The weight for each sample's absolute margin."""
        index = np.clip(np.rint(np.abs(abs_margin)).astype(np.int64), 0, self.max_abs)
        return self.weights[index]

    def apply(self, sim: GameSim, rng: np.random.Generator) -> GameSim:
        """Resample ``sim``'s own draws in proportion to the lattice weights.

        Resampling rather than re-weighting on purpose: every pricing helper
        in the repo counts samples, so a weighted :class:`GameSim` would need
        each of them changed and would be wrong in any it missed. Whole
        ``(home, away)`` pairs are carried, so the scores stay coherent and
        the total is reshaped only through whatever the margin and the total
        actually share — which for football is nearly nothing.
        """
        weights = self.of(sim.margin)
        total = float(weights.sum())
        if not np.isfinite(total) or total <= 0.0:
            # Every draw landed where football never does. Refusing to
            # resample leaves the uncorrected sim, which is the honest
            # fallback: a correction with no mass to move is not a licence
            # to invent one.
            return sim
        picks = rng.choice(sim.n_sims, size=sim.n_sims, p=weights / total)
        return GameSim(home_score=sim.home_score[picks],
                       away_score=sim.away_score[picks])

    def to_frame(self) -> pd.DataFrame:
        """The table as it is banked (``LATTICE_COLUMNS``)."""
        return pd.DataFrame({
            "abs_margin": np.arange(self.weights.size, dtype=np.int64),
            "weight": self.weights.astype(float),
        })

    @classmethod
    def from_frame(cls, frame: pd.DataFrame) -> LatticeWeights:
        """A table from its banked frame; the rows may arrive in any order."""
        rows = frame[LATTICE_COLUMNS].sort_values("abs_margin")
        expected = np.arange(len(rows))
        if not np.array_equal(rows["abs_margin"].to_numpy(dtype=np.int64), expected):
            raise ValueError("a banked lattice covers every absolute margin from 0 once")
        return cls(rows["weight"].to_numpy(dtype=float))


def fit_lattice_weights(
    actual_margin: np.ndarray, reference_mass: np.ndarray,
    *, prior: float = DEFAULT_PRIOR, correct_tail: bool = False,
) -> LatticeWeights:
    """Measure the lattice: how often football lands on each margin vs the sim.

    ``actual_margin`` is the finished margin of every training game and
    ``reference_mass`` what the sim being corrected puts on each absolute
    margin — :func:`rounded_normal_mass` for the shipped normal, or
    :func:`simulated_margin_mass` for anything without a closed form. Both
    must describe the same games, or the ratio is measuring the schedule
    rather than the lattice.

    Each bin's weight is a ratio of counts with ``prior`` pseudo-games added
    to both sides. That shrinkage is the whole defence against overfitting a
    twenty-two-bin table: the 450 games that ended on a three-point margin
    move their bin almost all the way, and the eleven that ended on 20 barely
    move theirs.

    The last bin — everything at or beyond ``max_abs`` — is left at 1 unless
    ``correct_tail``. Its ratio is not a lattice measurement: football's
    residuals are leptokurtic, so the tail is heavier than the normal's and
    the ratio reads above 1 (×1.13 in the NFL over 17% of games, ×1.20 in
    college over 35%), and applying that is a dispersion correction in a
    tool built to correct key numbers. The tail round (docs/MODEL_LAB.md)
    measured it on the ladder gate: with the tail corrected, college closed
    eight sides and the NFL's spread shoulder sat at 0.019; with it left
    alone, both leagues open every spread side and the NFL shoulder reads
    0.014. A tail that needs correcting is a σ that needs re-fitting.
    """
    reference = np.asarray(reference_mass, dtype=float)
    max_abs = reference.size - 1
    games = int(np.asarray(actual_margin).size)
    if games == 0:
        raise ValueError("no games to fit the lattice on")
    index = np.clip(
        np.rint(np.abs(np.asarray(actual_margin, dtype=float))).astype(np.int64),
        0, max_abs)
    observed = np.bincount(index, minlength=max_abs + 1).astype(float)
    expected = reference * games
    weights = (observed + prior) / (expected + prior)
    if not correct_tail:
        weights[-1] = 1.0
    return LatticeWeights(weights)


def fit_lattice_from_residuals(
    residuals: pd.DataFrame, sd_margin: float,
    *, max_abs: int = DEFAULT_MAX_ABS, prior: float = DEFAULT_PRIOR,
    correct_tail: bool = False,
) -> LatticeWeights:
    """The lattice the shipped normal misses, measured off a residual bank.

    ``residuals`` is a banked walk-forward frame
    (:data:`velocity.models.residuals.RESIDUAL_COLUMNS`): each game's
    projected margin and what it missed by, so the finished margin is the
    sum and the reference is the rounded normal at ``sd_margin`` around
    each projection. This is the one fit every consumer shares — the bank
    (``scripts/build_lattice.py``), the sim-shape gate and the derivative
    re-check — so a weight seen in one is the weight the others mean.
    """
    mu = residuals["mu_margin"].to_numpy(dtype=float)
    actual = mu + residuals["resid_margin"].to_numpy(dtype=float)
    keep = np.isfinite(mu) & np.isfinite(actual)
    return fit_lattice_weights(
        actual[keep], rounded_normal_mass(mu[keep], sd_margin, max_abs=max_abs),
        prior=prior, correct_tail=correct_tail)


def load_lattice_weights(
    league: str, datasets: Path | None = None
) -> LatticeWeights | None:
    """The banked lattice for ``league``, or ``None`` when none is committed.

    Mirrors :func:`velocity.models.residuals.load_residual_pool`: the table
    is data the sim reads, not a knob, and a league without one simulates
    without a correction rather than inventing one.
    """
    path = (datasets or DATASETS) / league / LATTICE_FILE
    if not path.exists():
        return None
    frame = pd.read_parquet(path)
    return None if frame.empty else LatticeWeights.from_frame(frame)
