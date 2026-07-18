"""Shared Monte Carlo game simulation.

A projection is a *distribution*, not a point estimate. Given a matchup's
expected margin and expected total, this engine samples many game outcomes so
that every derivative market — spread, total, moneyline, team totals, alternate
lines — can be priced from one coherent joint distribution. Margin and total are
sampled jointly (a shootout is a high total that can break either way), so the
spread and total prices stay internally consistent.

**Determinism is non-negotiable.** Every draw comes from a caller-supplied,
seeded :class:`numpy.random.Generator`; the same seed and config always produce
the same samples, so a moved projection is always a real change, never noise.

Variance is calibrated to real NFL residuals (see ``DEFAULT_SD_MARGIN`` /
``DEFAULT_SD_TOTAL``): the margin and total each deviate from the model's
expectation with a standard deviation near 13 points, measured on a real
walk-forward. Scores are rounded to integers by default so simulated margins land
on the discrete values real games produce (this is a first-order treatment of the
well-known mass at key numbers 3 and 7; a drive-level scoring sim is a later
refinement).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Calibrated to real NFL residuals — the standard deviation of (actual − model)
# margin and total from a 2022–2023 walk-forward (n≈570): margin ≈ 12.8, total
# ≈ 13.6, with the two essentially uncorrelated (≈0.03). Totals are far less
# predictable than an earlier 10.5 guess implied. Overridable per league.
DEFAULT_SD_MARGIN = 13.0
DEFAULT_SD_TOTAL = 13.6


@dataclass(frozen=True)
class SimConfig:
    """Simulation variance and size knobs.

    ``margin_total_corr`` is the correlation between the game's margin and its
    total; it is close to zero for football (who wins says little about how many
    points get scored) and defaults accordingly.
    """

    n_sims: int = 50_000
    sd_margin: float = DEFAULT_SD_MARGIN
    sd_total: float = DEFAULT_SD_TOTAL
    margin_total_corr: float = 0.0
    round_scores: bool = True

    def __post_init__(self) -> None:
        if self.n_sims <= 0:
            raise ValueError("n_sims must be positive")
        if self.sd_margin <= 0 or self.sd_total <= 0:
            raise ValueError("standard deviations must be positive")
        if not -1.0 <= self.margin_total_corr <= 1.0:
            raise ValueError("margin_total_corr must be in [-1, 1]")


@dataclass(frozen=True)
class GameSim:
    """A sampled joint distribution of one game's home and away scores.

    All pricing helpers read from these samples, so spread, total and moneyline
    prices are guaranteed mutually consistent — they come from the same draws.
    """

    home_score: np.ndarray
    away_score: np.ndarray

    @property
    def margin(self) -> np.ndarray:
        """Home score minus away score (positive = home won the sample)."""
        return self.home_score - self.away_score

    @property
    def total(self) -> np.ndarray:
        """Combined points scored in the sample."""
        return self.home_score + self.away_score

    @property
    def n_sims(self) -> int:
        return int(self.home_score.shape[0])

    def p_home_win(self) -> float:
        """Probability the home team wins outright (ties split evenly)."""
        margin = self.margin
        wins = np.count_nonzero(margin > 0)
        ties = np.count_nonzero(margin == 0)
        return float((wins + 0.5 * ties) / self.n_sims)

    def prob_home_cover(self, home_spread: float) -> float:
        """P(home covers a spread of ``home_spread``), e.g. ``-3.5`` for a favorite.

        The home side covers when ``margin + home_spread > 0``. Pushes (exactly
        zero) are excluded from the numerator, matching how a push is graded.
        """
        adjusted = self.margin + home_spread
        return float(np.count_nonzero(adjusted > 0) / self.n_sims)

    def prob_over(self, total_point: float) -> float:
        """Probability the game's total goes over ``total_point`` (pushes excluded)."""
        return float(np.count_nonzero(self.total > total_point) / self.n_sims)

    def fair_spread(self) -> float:
        """The home spread with a ~50/50 cover — the median margin, negated.

        A home team projected to win by a median of 6 has a fair spread of -6.
        """
        return float(-np.median(self.margin))

    def fair_total(self) -> float:
        """The over/under with a ~50/50 over — the median simulated total."""
        return float(np.median(self.total))


def simulate_game(
    mu_margin: float,
    mu_total: float,
    rng: np.random.Generator,
    config: SimConfig | None = None,
) -> GameSim:
    """Sample a game's joint score distribution from its expected margin/total.

    ``mu_margin`` is expected home-minus-away points; ``mu_total`` is expected
    combined points. Draws are taken from a bivariate normal over (margin,
    total) with the config's standard deviations and correlation, then split
    into home/away scores, floored at zero, and (by default) rounded to
    integers.
    """
    config = config or SimConfig()

    cov_mt = config.margin_total_corr * config.sd_margin * config.sd_total
    cov = np.array(
        [
            [config.sd_margin**2, cov_mt],
            [cov_mt, config.sd_total**2],
        ]
    )
    draws = rng.multivariate_normal([mu_margin, mu_total], cov, size=config.n_sims)
    margin = draws[:, 0]
    total = draws[:, 1]

    home = (total + margin) / 2.0
    away = (total - margin) / 2.0
    home = np.clip(home, 0.0, None)
    away = np.clip(away, 0.0, None)
    if config.round_scores:
        home = np.rint(home)
        away = np.rint(away)

    return GameSim(home_score=home, away_score=away)
