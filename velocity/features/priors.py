"""Preseason priors and Bayesian shrinkage — the heart of the NCAAF problem.

College football gives you ~12 games a season across 130+ teams with enormous
roster turnover, so early-season results are a thin, noisy signal. A team that
wins its first game 49–0 over a cupcake has told you almost nothing. The fix is
to start every team from an informed **preseason prior** and let in-season
results pull the rating off that prior only as fast as the evidence justifies —
regress hard early, trust the data later.

The preseason prior blends three public signals (DESIGN §4.3):

* **Recruiting** — the blue-chip ratio / talent composite; talent is the single
  best predictor of a new season before any games are played.
* **Returning production** — how much of last year's snaps/production is back.
* **Prior-year adjusted rating** — where the team actually finished.

Each is standardized and combined with configurable weights into a prior on the
team's rating scale. The shrinkage step is a conjugate-normal update: the
posterior is a precision-weighted average of prior and data, where the data's
weight grows with the number of games observed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PriorWeights:
    """Relative weights for the three preseason-prior signals (need not sum to 1)."""

    recruiting: float = 0.5
    returning_production: float = 0.2
    prior_rating: float = 0.3

    def normalized(self) -> tuple[float, float, float]:
        total = self.recruiting + self.returning_production + self.prior_rating
        if total <= 0:
            raise ValueError("prior weights must sum to a positive value")
        return (
            self.recruiting / total,
            self.returning_production / total,
            self.prior_rating / total,
        )


def _standardize(x: pd.Series) -> pd.Series:
    """Zero-mean, unit-variance; a degenerate (zero-variance) input maps to zeros."""
    x = pd.to_numeric(x, errors="coerce")
    std = x.std(ddof=0)
    if not np.isfinite(std) or std == 0:
        return pd.Series(np.zeros(len(x)), index=x.index)
    return (x - x.mean()) / std


def preseason_prior(
    team_inputs: pd.DataFrame,
    weights: PriorWeights | None = None,
    *,
    rating_scale: float = 0.10,
) -> pd.Series:
    """Blend recruiting, returning production and prior rating into a prior rating.

    ``team_inputs`` is indexed (or keyed) by team with columns ``recruiting``,
    ``returning_production`` and ``prior_rating``. Each is standardized across
    teams, combined with ``weights``, and scaled to the EPA/play rating scale by
    ``rating_scale`` (so a one-sigma team sits ``rating_scale`` above average).
    Returns a Series of prior ratings indexed by team.
    """
    weights = weights or PriorWeights()
    w_rec, w_ret, w_prior = weights.normalized()
    if "team" in team_inputs.columns:
        team_inputs = team_inputs.set_index("team")

    blended = (
        w_rec * _standardize(team_inputs["recruiting"])
        + w_ret * _standardize(team_inputs["returning_production"])
        + w_prior * _standardize(team_inputs["prior_rating"])
    )
    return (blended * rating_scale).rename("prior_rating")


def shrink_to_prior(
    prior: float,
    observed: float,
    n_games: float,
    *,
    prior_strength: float = 6.0,
) -> float:
    """Conjugate-normal shrinkage of an observed rating toward its prior.

    ``prior_strength`` is expressed in *pseudo-games*: the posterior weights the
    observed rating by ``n_games`` and the prior by ``prior_strength``, so with
    no games the posterior is the prior, and as games accumulate the estimate
    converges to the data. This is the "regress hard early, trust data later"
    behavior made precise.
    """
    if n_games < 0:
        raise ValueError("n_games must be non-negative")
    weight_data = n_games / (n_games + prior_strength)
    return (1.0 - weight_data) * prior + weight_data * observed


def shrink_series(
    prior: pd.Series,
    observed: pd.Series,
    n_games: pd.Series,
    *,
    prior_strength: float = 6.0,
) -> pd.Series:
    """Vectorized :func:`shrink_to_prior` over teams (aligned by index)."""
    prior, observed, n_games = prior.align(observed)[0], observed, n_games
    idx = prior.index
    observed = observed.reindex(idx).fillna(prior)
    n_games = n_games.reindex(idx).fillna(0.0)
    weight_data = n_games / (n_games + prior_strength)
    return (1.0 - weight_data) * prior + weight_data * observed
