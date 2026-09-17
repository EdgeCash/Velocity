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

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from velocity.models.game_nfl import GameProjection, NFLGameModel
from velocity.models.game_scores import ScoresGameModel
from velocity.models.simulate import SimConfig, simulate_game
from velocity.util.seed import make_rng


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
    return NFLGameModel(model.ratings, config, pace=model.pace)


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


# ---------------------------------------------------------------------------
# The scale — the level's other half.
# ---------------------------------------------------------------------------
#
# :func:`calibrate_level` fits the INTERCEPT of the projection through the
# model. Nothing fitted the SLOPE, and it was not one. Regressing actual on
# projected across the walk-forward residual banks (docs/PROJECTION_AUDIT.md
# §2.2): the NFL total came back at 0.48 ± 0.04 and the college total at
# 0.66 ± 0.03 — when the model's total sat six points above the league mean,
# the game scored about three above it. Margins were 0.87 (NFL, 0.72 in
# September) and 1.15 (college, before the SP+ prior reached the lab). A
# slope of 1 is a calibrated scale; the one staked strategy in the repo
# selects on exactly the quantity that was not.
#
# The slope cannot be fitted in-sample the way the level is: a ridge fit's
# own training games are shrunk toward it, so the in-sample slope reads near
# 1 whatever the out-of-sample truth. It is fitted on the residual BANK —
# every row a projection made before its week was trained on — restricted to
# the seasons before the one being projected, so the lab's use of it is as
# leak-free as the bank itself. The bank is the promoted model's; rebuild it
# after a promotion and the scale follows.

# Fewer out-of-sample games than this and the scale stays at 1: a slope on a
# season and a half of noise is worse than none.
SCALE_MIN_GAMES = 200


@dataclass(frozen=True)
class ScaleCalibration:
    """Multipliers on the projection's deviations, fitted out of sample.

    ``margin_slope`` scales the expected margin (home-field included: the
    fit's intercept is dropped, because a home-margin bias belongs to the
    HFA parameter, not to a scale that would also shift neutral games).
    ``total_slope`` scales the total's deviation from an anchor — the level —
    so the mean total the level fixed is untouched. ``n`` is the games it was
    fitted on; zero means the identity.
    """

    margin_slope: float = 1.0
    total_slope: float = 1.0
    n: int = 0

    @classmethod
    def from_residuals(
        cls,
        residuals: pd.DataFrame,
        *,
        before_season: int | None = None,
        seasons: int | None = None,
        min_games: int = SCALE_MIN_GAMES,
    ) -> ScaleCalibration:
        """Fit on a residual bank (``RESIDUAL_COLUMNS``).

        ``before_season`` keeps only seasons strictly before it — the leak
        gate for a walk-forward. ``seasons`` then keeps the trailing that
        many. Under ``min_games`` rows the identity is returned.
        """
        frame = residuals.dropna(subset=["mu_margin", "mu_total", "resid_margin", "resid_total"])
        if before_season is not None and "season" in frame.columns:
            frame = frame[frame["season"].astype(int) < int(before_season)]
        if seasons is not None and not frame.empty and "season" in frame.columns:
            cutoff = int(frame["season"].astype(int).max()) - int(seasons) + 1
            frame = frame[frame["season"].astype(int) >= cutoff]
        if len(frame) < max(int(min_games), 3):
            return cls()
        mu_m = frame["mu_margin"].to_numpy(dtype=float)
        act_m = mu_m + frame["resid_margin"].to_numpy(dtype=float)
        mu_t = frame["mu_total"].to_numpy(dtype=float)
        act_t = mu_t + frame["resid_total"].to_numpy(dtype=float)
        if mu_m.std() <= 0 or mu_t.std() <= 0:
            return cls()
        margin_slope = float(np.polyfit(mu_m, act_m, 1)[0])
        # Centred, so the slope is the deviation's and the mean is the level's.
        total_slope = float(np.polyfit(mu_t - mu_t.mean(), act_t - act_t.mean(), 1)[0])
        return cls(margin_slope=margin_slope, total_slope=total_slope, n=int(len(frame)))

    def apply(
        self, mu_home: float, mu_away: float, *, anchor_total: float
    ) -> tuple[float, float]:
        """The expected points with the margin and the total's deviation rescaled."""
        margin = (mu_home - mu_away) * self.margin_slope
        total = anchor_total + (mu_home + mu_away - anchor_total) * self.total_slope
        return (total + margin) / 2.0, (total - margin) / 2.0


def mean_projected_total(
    model: object, games: pd.DataFrame, *, seasons: int | None = None
) -> float | None:
    """The model's mean expected total over the played ``games`` — the anchor.

    Neutral flags honoured, no situational bonuses (the same pass
    :func:`level_shift` makes). ``None`` when there is nothing to project.
    """
    played = games.dropna(subset=["home_score", "away_score"])
    if seasons is not None and not played.empty and "season" in played.columns:
        cutoff = int(played["season"].max()) - (seasons - 1)
        played = played[played["season"] >= cutoff]
    if played.empty:
        return None
    neutral = (played["neutral_site"].astype(bool).to_numpy()
               if "neutral_site" in played.columns else [False] * len(played))
    total = 0.0
    for home, away, flag in zip(played["home_team"], played["away_team"], neutral, strict=True):
        mu_home, mu_away = model.expected_points(  # type: ignore[attr-defined]
            str(home), str(away), neutral_site=bool(flag))
        total += mu_home + mu_away
    return total / len(played)


class ScaledModel:
    """Any expected-points model with its deviations rescaled, simulated once.

    Sits directly over the game model, under the situational wrappers: rest
    and wind are point adjustments that arrive as ``home_bonus``/``away_bonus``
    and are added AFTER the scale, so a bye week is still worth its point
    whatever the slope. ``anchor_total`` is the level the total pivots on —
    :func:`mean_projected_total` over the training window.
    """

    def __init__(
        self, inner: object, calibration: ScaleCalibration, anchor_total: float, sim: SimConfig
    ) -> None:
        self.inner = inner
        self.calibration = calibration
        self.anchor_total = float(anchor_total)
        self.sim = sim

    def expected_points(
        self, home_team: str, away_team: str, *, neutral_site: bool = False,
        home_bonus: float = 0.0, away_bonus: float = 0.0,
    ) -> tuple[float, float]:
        mu_home, mu_away = self.inner.expected_points(  # type: ignore[attr-defined]
            home_team, away_team, neutral_site=neutral_site)
        home, away = self.calibration.apply(mu_home, mu_away, anchor_total=self.anchor_total)
        return home + home_bonus, away + away_bonus

    def project(
        self,
        home_team: str,
        away_team: str,
        *,
        neutral_site: bool = False,
        rng: np.random.Generator | None = None,
        home_bonus: float = 0.0,
        away_bonus: float = 0.0,
    ) -> GameProjection:
        rng = rng if rng is not None else make_rng()
        mu_home, mu_away = self.expected_points(
            home_team, away_team, neutral_site=neutral_site,
            home_bonus=home_bonus, away_bonus=away_bonus,
        )
        sim = simulate_game(
            mu_margin=mu_home - mu_away, mu_total=mu_home + mu_away, rng=rng, config=self.sim)
        return GameProjection(
            home_team=home_team, away_team=away_team,
            mu_home=mu_home, mu_away=mu_away, sim=sim,
        )


def scale_model(
    model: object, residuals: pd.DataFrame, games: pd.DataFrame, sim: SimConfig, *,
    before_season: int | None = None, seasons: int | None = None,
    anchor_seasons: int | None = 2,
) -> tuple[ScaledModel, ScaleCalibration]:
    """``model`` under a :class:`ScaleCalibration` fitted on ``residuals``.

    The anchor is the model's own mean projected total over the trailing
    ``anchor_seasons`` of ``games`` (the level's window); with nothing to
    anchor on the total is left unscaled.
    """
    calibration = ScaleCalibration.from_residuals(
        residuals, before_season=before_season, seasons=seasons)
    anchor = mean_projected_total(model, games, seasons=anchor_seasons)
    if anchor is None:
        calibration = replace(calibration, total_slope=1.0)
        anchor = 0.0
    return ScaledModel(model, calibration, anchor, sim), calibration
