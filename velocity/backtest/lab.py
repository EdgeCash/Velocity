"""Model lab — candidate rating variants, benchmarked identically.

Every idea for "fine-tuning the model" goes through the same gate: build it as
a variant factory here, run it through the walk-forward engine against the same
games/lines as the baseline, and read the scorecard. Nothing gets promoted into
the live slate on intuition — the lab table is the evidence.

Current variant families (motivated by the public state of the art — nfelo's
market-regression finding, DVOA-style phase splits, Elo-style recency):

* ``baseline`` — the shipped model: one opponent-adjusted EPA/play rating over
  all offensive plays.
* ``recency-<H>`` — the same fit with exponential play weights (half-life ``H``
  in on-field weeks): recent form counts more than last season.
* ``split-<W>`` — pass and rush plays fitted separately (each phase gets its
  own opponent adjustment), recombined at pass weight ``W``. ``W`` above the
  actual pass share overweights the passing game, which the public literature
  finds more predictive than rushing.
* ``ridge-<L>`` — the baseline at a different shrinkage, to check the default
  isn't a local habit.

Market regression is evaluated separately: it doesn't change the model, it
changes *when to bet* — the spread/total disagreement sweeps report the win
rate as a function of model-vs-close disagreement, per variant.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from velocity.features.scores import fit_scores_ratings
from velocity.features.team import DEFAULT_RIDGE_LAMBDA, TeamRatings, fit_ratings
from velocity.models.game_nfl import NFLGameModel, NFLModelConfig
from velocity.models.game_scores import ScoresGameModel, ScoresModelConfig
from velocity.models.simulate import SimConfig

# A variant maps a training frame to a projection model. `train` names which
# frame the engine should slice for it: "plays" (EPA fits) or "games" (the
# schedule-only scores fit — what the live slate currently runs).
VariantFactory = Callable[[pd.DataFrame], object]


def recency_weights(plays: pd.DataFrame, half_life_weeks: float) -> pd.Series:
    """Exponential play weights by age in on-field weeks (newest = 1.0).

    Age counts (season, week) steps on a contiguous key — the offseason gap is
    deliberately not inflated, so a half-life of ~17 weighs last season's plays
    at roughly half of this week's.
    """
    key = plays["season"].astype(int) * 25 + plays["week"].astype(int)
    age = key.max() - key
    return pd.Series(np.power(0.5, age / half_life_weeks), index=plays.index)


def combine_ratings(
    pass_ratings: TeamRatings, rush_ratings: TeamRatings, pass_weight: float
) -> TeamRatings:
    """Blend per-phase ratings into one TeamRatings at ``pass_weight``.

    ``matchup_delta`` is linear in the offense/defense dicts, so a weighted
    blend of the dicts prices exactly as the weighted blend of the phase
    deltas. Teams missing from one phase's fit contribute 0 there (league
    average), matching ``matchup_delta``'s own fallback.
    """
    if not 0.0 <= pass_weight <= 1.0:
        raise ValueError("pass_weight must be in [0, 1]")
    w, v = pass_weight, 1.0 - pass_weight
    teams = sorted(set(pass_ratings.teams) | set(rush_ratings.teams))
    offense = {
        t: w * pass_ratings.offense.get(t, 0.0) + v * rush_ratings.offense.get(t, 0.0)
        for t in teams
    }
    defense = {
        t: w * pass_ratings.defense.get(t, 0.0) + v * rush_ratings.defense.get(t, 0.0)
        for t in teams
    }
    return TeamRatings(
        offense=offense,
        defense=defense,
        league_epa=w * pass_ratings.league_epa + v * rush_ratings.league_epa,
        ridge_lambda=pass_ratings.ridge_lambda,
        n_plays=pass_ratings.n_plays + rush_ratings.n_plays,
        teams=tuple(teams),
    )


def fit_split_ratings(
    plays: pd.DataFrame,
    *,
    pass_weight: float,
    ridge_lambda: float = DEFAULT_RIDGE_LAMBDA,
    weights: pd.Series | None = None,
) -> TeamRatings:
    """Fit pass and rush plays separately, blend at ``pass_weight``.

    Each phase gets its own opponent adjustment (a great pass defense that
    can't stop the run stops muddying both numbers). Plays that are neither
    pass nor run (already rare in the canonical frame) are dropped.
    """
    kind = plays["play_type"].astype(str)
    pass_plays = plays[kind == "pass"]
    rush_plays = plays[kind == "run"]
    return combine_ratings(
        fit_ratings(pass_plays, ridge_lambda=ridge_lambda, weights=weights),
        fit_ratings(rush_plays, ridge_lambda=ridge_lambda, weights=weights),
        pass_weight,
    )


def nfl_variants(n_sims: int) -> dict[str, tuple[str, VariantFactory]]:
    """The benchmark slate of NFL variants: name → (train frame kind, factory)."""
    sim = SimConfig(n_sims=n_sims)

    def _model(ratings: TeamRatings) -> NFLGameModel:
        return NFLGameModel(ratings, NFLModelConfig(sim=sim))

    def baseline(train: pd.DataFrame) -> NFLGameModel:
        return _model(fit_ratings(train))

    def recency(half_life: float, lam: float = DEFAULT_RIDGE_LAMBDA) -> VariantFactory:
        def factory(train: pd.DataFrame) -> NFLGameModel:
            return _model(fit_ratings(
                train, ridge_lambda=lam, weights=recency_weights(train, half_life)
            ))

        return factory

    def split(pass_weight: float) -> VariantFactory:
        def factory(train: pd.DataFrame) -> NFLGameModel:
            return _model(fit_split_ratings(train, pass_weight=pass_weight))

        return factory

    def ridge(lam: float) -> VariantFactory:
        def factory(train: pd.DataFrame) -> NFLGameModel:
            return _model(fit_ratings(train, ridge_lambda=lam))

        return factory

    def scores(train_games: pd.DataFrame) -> ScoresGameModel:
        # The schedule-only fit the live slate currently runs — the promotion bar.
        return ScoresGameModel(fit_scores_ratings(train_games), ScoresModelConfig(sim=sim))

    return {
        "baseline": ("plays", baseline),
        "scores": ("games", scores),
        "recency-17": ("plays", recency(17.0)),
        "recency-34": ("plays", recency(34.0)),
        "recency-17-r400": ("plays", recency(17.0, 400.0)),
        "recency-34-r400": ("plays", recency(34.0, 400.0)),
        "split-0.60": ("plays", split(0.60)),
        "split-0.75": ("plays", split(0.75)),
        "ridge-100": ("plays", ridge(100.0)),
        "ridge-400": ("plays", ridge(400.0)),
    }


def disagreement_sweep(
    projections: pd.DataFrame,
    games: pd.DataFrame,
    *,
    market: str,
    thresholds: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0, 4.0, 6.0),
) -> pd.DataFrame:
    """Win rate betting only where the model disagrees with the close by ≥ N points.

    The market-regression evaluation: how much *residual* model-vs-close
    disagreement it takes before the model's side wins above break-even
    (52.4% at −110). ``market`` is ``"total"`` (pick over when the fair total
    is above the closing number) or ``"spread"`` (pick the home side when the
    fair home spread is more favorable than the closing ``spread_line``,
    positive = home favored). Pushes are excluded, as the market excludes them.
    """
    line_col = "total_line" if market == "total" else "spread_line"
    if projections.empty or line_col not in games.columns:
        return pd.DataFrame(columns=["threshold", "win_rate", "bets"])
    df = projections.merge(
        games[["game_id", "home_score", "away_score", line_col]], on="game_id", how="inner"
    )
    if market == "total":
        realized = df["home_score"] + df["away_score"]
        gap = df["fair_total"] - df[line_col]
        diff = realized - df[line_col]
        pick_high = gap > 0  # over
        win = (pick_high & (diff > 0)) | (~pick_high & (diff < 0))
    else:
        # fair_spread is the fair home spread (negative = home favored);
        # spread_line is positive when home is favored — align signs.
        margin = df["home_score"] - df["away_score"]
        fair_home_margin = -df["fair_spread"]
        gap = fair_home_margin - df[line_col]
        diff = margin - df[line_col]
        pick_high = gap > 0  # home covers
        win = (pick_high & (diff > 0)) | (~pick_high & (diff < 0))
    decided = df[line_col].notna() & (gap != 0) & (diff != 0)
    rows = []
    for threshold in thresholds:
        mask = decided & (gap.abs() >= threshold)
        rows.append({
            "threshold": threshold,
            "win_rate": float(win[mask].mean()) if mask.any() else float("nan"),
            "bets": int(mask.sum()),
        })
    return pd.DataFrame(rows)


def ats_ou_vs_close(projections: pd.DataFrame, games: pd.DataFrame) -> dict[str, float]:
    """Flat ATS / O/U win rates vs the closing lines (threshold 0 of the sweeps)."""
    out: dict[str, float] = {}
    for market, key in (("spread", "ats"), ("total", "ou")):
        sweep = disagreement_sweep(projections, games, market=market, thresholds=(0.0,))
        if sweep.empty or not sweep["bets"].iloc[0]:
            out[f"{key}_win_rate"] = float("nan")
            out[f"{key}_bets"] = 0.0
        else:
            out[f"{key}_win_rate"] = float(sweep["win_rate"].iloc[0])
            out[f"{key}_bets"] = float(sweep["bets"].iloc[0])
    return out
