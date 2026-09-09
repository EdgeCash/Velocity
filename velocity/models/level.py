"""The scoring level a football model hangs its ratings from.

A ratings fit produces *deviations* — this offense against that defense,
above or below an average matchup — and the model adds them to a league
scoring level (``base_points``) to get expected points. The deviations
track the data every week; the level did not. Two ways it drifted:

* College totals fell ~4 points a game after the 2023 clock rules and a
  fixed 28.5 kept projecting the old era (the live runner already fits
  half the trailing-two-season mean total — :func:`mean_points_per_team`
  is that function, moved here so the lab's college variants mirror it).
* The NFL QB-adjusted fit projects each team with its *starter's* passer
  effect priced back in, and starters throw better than the play-weighted
  intercept the deviations were centered on — so every projected total ran
  ~2.3 points high over fifteen seasons of walk-forward (the residual bank
  in ``datasets/nfl/sim_residuals.parquet`` measured it; the market's
  closes sat 2.6 below the model on average). A level constant cannot see
  a bias the decomposition introduces; :func:`calibrate_level` fits the
  level *through* the model: shift ``base_points`` so the model's mean
  projected total over the played training games equals what those games
  actually scored. Ratings untouched, margins untouched (the shift is the
  same for both teams), only the total's center moves — and it moves with
  the era, the rule changes, and whatever the fit does upstream.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from velocity.models.game_nfl import NFLGameModel
from velocity.models.game_scores import ScoresGameModel


def mean_points_per_team(
    games: pd.DataFrame, seasons: int = 2, *, fallback: float = 28.5
) -> float:
    """Half the trailing-``seasons`` mean total — the level a season scores at."""
    if games.empty or "season" not in games.columns:
        return fallback
    recent = games[games["season"] >= int(games["season"].max()) - (seasons - 1)]
    played = recent.dropna(subset=["home_score", "away_score"])
    if played.empty:
        return fallback
    return float((played["home_score"] + played["away_score"]).mean()) / 2.0


def level_shift(
    model: NFLGameModel | ScoresGameModel, games: pd.DataFrame, *, seasons: int | None = None
) -> float:
    """Points per team the model runs high (+) or low (−) on ``games``.

    Projects every played game in ``games`` (the trailing ``seasons`` of it
    when given) with the model as it stands — neutral flags honoured, no
    situational bonuses — and compares the mean projected total to the mean
    actual one. Half that gap is the per-team shift; zero when there is
    nothing to compare.
    """
    played = games.dropna(subset=["home_score", "away_score"])
    if seasons is not None and not played.empty and "season" in played.columns:
        cutoff = int(played["season"].max()) - (seasons - 1)
        played = played[played["season"] >= cutoff]
    if played.empty:
        return 0.0
    neutral = (played["neutral_site"].astype(bool).to_numpy()
               if "neutral_site" in played.columns else [False] * len(played))
    projected = 0.0
    for home, away, flag in zip(played["home_team"], played["away_team"], neutral, strict=True):
        mu_home, mu_away = model.expected_points(str(home), str(away), neutral_site=bool(flag))
        projected += mu_home + mu_away
    actual = float((played["home_score"] + played["away_score"]).sum())
    return (projected - actual) / (2.0 * len(played))


def calibrate_level(
    model: NFLGameModel, games: pd.DataFrame, *, seasons: int | None = None
) -> NFLGameModel:
    """The same model with ``base_points`` shifted so its totals center on the data."""
    shift = level_shift(model, games, seasons=seasons)
    if shift == 0.0:
        return model
    config = replace(model.config, base_points=model.config.base_points - shift)
    return NFLGameModel(model.ratings, config)


def calibrate_scores_level(
    model: ScoresGameModel, games: pd.DataFrame, *, seasons: int | None = 2
) -> ScoresGameModel:
    """The scores model with ``base_points`` shifted so its totals center on the data.

    The scores ridge fits one intercept over its whole training history with
    no recency, so in a league whose scoring moved (college, −4 points a
    game after the 2023 clock rules) its level lags the era: the college
    blend's totals ran 1.0–2.3 points high every season from 2021 on. Same
    remedy as the NFL's: fit the level through the model on the trailing
    ``seasons``; ratings and the home edge untouched.
    """
    shift = level_shift(model, games, seasons=seasons)
    if shift == 0.0:
        return model
    ratings = replace(model.ratings, base_points=model.ratings.base_points - shift)
    return ScoresGameModel(ratings, model.config)
