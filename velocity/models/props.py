"""Player prop models — decomposition, distributions, and correlated simulation.

Props are a ``team volume × player share × efficiency × game script`` problem
(DESIGN §5), and because books price them with less attention than sides/totals,
they are where a free-data model finds the softest lines. Two things must be
right:

* **Distributions, not points.** A prop is an over/under, so we need the whole
  distribution to price the line and its alternates. Counts (receptions,
  carries) are modeled as an overdispersed :class:`NegativeBinomial`; the mean
  comes from the decomposition and the dispersion from historical spread.
* **Correlation.** A QB's passing yards, his WR1's receiving yards, and the team
  total move together. :func:`simulate_props` draws a team pass volume *once per
  simulated game* and samples every player's outcome conditioned on it, so
  teammates and the passing game move together — which is what makes correlated
  props and same-game parlays honest.

Injuries are handled upstream in :mod:`velocity.features.player` by repricing the
share room; feeding the repriced shares here reprices every teammate's prop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


def expected_stat(team_volume: float, player_share: float, efficiency: float) -> float:
    """The decomposition product: ``volume × share × efficiency``.

    e.g. team pass attempts × target share × yards-per-target → expected
    receiving yards.
    """
    return team_volume * player_share * efficiency


@dataclass(frozen=True)
class NegativeBinomial:
    """Overdispersed count distribution parameterized by mean and dispersion.

    ``dispersion`` is the negative-binomial size ``r``: variance is
    ``mean + mean²/r``, so smaller ``r`` means more overdispersion and ``r → ∞``
    recovers the Poisson. Used to price count props (receptions, carries).
    """

    mean: float
    dispersion: float = 8.0

    def __post_init__(self) -> None:
        if self.mean < 0:
            raise ValueError("mean must be non-negative")
        if self.dispersion <= 0:
            raise ValueError("dispersion must be positive")

    @property
    def variance(self) -> float:
        return self.mean + self.mean**2 / self.dispersion

    def pmf(self, k: int) -> float:
        """P(X = k) via the log-gamma form (numerically stable)."""
        if k < 0:
            return 0.0
        if self.mean == 0:
            return float(k == 0)  # degenerate mass at zero
        r = self.dispersion
        p = r / (r + self.mean)
        log_coeff = math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1)
        return math.exp(log_coeff + r * math.log(p) + k * math.log1p(-p))

    def cdf(self, k: int) -> float:
        """P(X ≤ k)."""
        if k < 0:
            return 0.0
        return sum(self.pmf(i) for i in range(k + 1))

    def prob_over(self, line: float) -> float:
        """P(X > line). For a half-point line this is P(X ≥ ⌈line⌉)."""
        return 1.0 - self.cdf(math.floor(line))

    def prob_under(self, line: float) -> float:
        """P(X < line). For a half-point line this is P(X ≤ ⌊line⌋)."""
        return self.cdf(math.ceil(line) - 1)


@dataclass(frozen=True)
class PropSim:
    """Correlated per-simulation player outcomes, with pricing helpers."""

    volume: np.ndarray
    receptions: dict[str, np.ndarray]
    receiving_yards: dict[str, np.ndarray]

    @property
    def n_sims(self) -> int:
        return int(self.volume.shape[0])

    def team_pass_yards(self) -> np.ndarray:
        """Total receiving yards across players — a proxy for QB passing yards."""
        return np.sum(list(self.receiving_yards.values()), axis=0)

    def prob_over(self, player: str, stat: str, line: float) -> float:
        """Empirical P(stat > line) for ``player`` from the simulation."""
        samples = self._samples(player, stat)
        return float(np.mean(samples > line))

    def mean(self, player: str, stat: str) -> float:
        return float(np.mean(self._samples(player, stat)))

    def _samples(self, player: str, stat: str) -> np.ndarray:
        if stat == "receptions":
            return self.receptions[player]
        if stat in ("receiving_yards", "rec_yards"):
            return self.receiving_yards[player]
        raise ValueError(f"unknown stat {stat!r}")


def simulate_props(
    volume_mean: float,
    shares: dict[str, float],
    catch_rates: dict[str, float],
    yards_per_reception: dict[str, float],
    rng: np.random.Generator,
    *,
    n_sims: int = 50_000,
    volume_dispersion: float = 20.0,
    yards_sd_per_reception: float = 6.0,
) -> PropSim:
    """Simulate correlated player receiving outcomes for one game.

    A team pass volume is drawn once per simulated game (overdispersed), then
    each player's targets are Poisson-thinned from that shared volume by their
    share, receptions are binomial on targets given the catch rate, and yards are
    receptions × yards-per-reception plus reception-scaled noise. Because every
    player shares the same per-sim volume draw, their outcomes are correlated.
    """
    r = volume_dispersion
    p = r / (r + volume_mean) if volume_mean > 0 else 1.0
    volume = rng.negative_binomial(r, p, size=n_sims).astype(float)

    receptions: dict[str, np.ndarray] = {}
    receiving_yards: dict[str, np.ndarray] = {}
    for player, share in shares.items():
        targets = rng.poisson(volume * share)
        catch = catch_rates.get(player, 0.65)
        recs = rng.binomial(targets, catch).astype(float)
        ypr = yards_per_reception.get(player, 10.0)
        noise = rng.normal(0.0, yards_sd_per_reception * np.sqrt(recs))
        receptions[player] = recs
        receiving_yards[player] = np.clip(recs * ypr + noise, 0.0, None)

    return PropSim(volume=volume, receptions=receptions, receiving_yards=receiving_yards)
