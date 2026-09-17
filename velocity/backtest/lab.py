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

import functools
import math
from collections.abc import Callable, Mapping
from pathlib import Path

import numpy as np
import pandas as pd

from velocity.features.scores import fit_scores_ratings
from velocity.features.team import (
    DEFAULT_RIDGE_LAMBDA,
    TeamRatings,
    fit_ratings,
    recency_weights,
    scrimmage_plays,
)
from velocity.models.game_nfl import NFLGameModel, NFLModelConfig
from velocity.models.game_scores import ScoresGameModel, ScoresModelConfig
from velocity.models.simulate import NCAAF_SD_MARGIN, NCAAF_SD_TOTAL, SimConfig

__all__ = [
    "ats_ou_vs_close",
    "combine_ratings",
    "compress_plays",
    "disagreement_sweep",
    "fit_split_ratings",
    "inseason_variants",
    "market_blend_sweep",
    "ncaaf_walk_order",
    "nfl_variants",
    "recency_weights",
    "score_accuracy",
]

# The classic NFL margin standard deviation, used to convert a point spread
# into a win probability (probit link). A fixed historical constant, never fit
# on the evaluation data.
NFL_MARGIN_SIGMA = 13.45

# The phase boundary for a phase-specific scale lives with the scale
# (velocity.models.level.EARLY_WEEK_BY_LEAGUE); these are its two names here.
NFL_EARLY_WEEK = 6
NCAAF_EARLY_WEEK = 4

# A variant maps a training frame to a projection model. `train` names which
# frame the engine should slice for it: "plays" (EPA fits) or "games" (the
# schedule-only scores fit — what the live slate currently runs).
VariantFactory = Callable[[pd.DataFrame], object]


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


def nfl_variants(
    n_sims: int, schedule: pd.DataFrame | None = None
) -> dict[str, tuple[str, VariantFactory]]:
    """The benchmark slate of NFL variants: name → (train frame kind, factory).

    ``schedule`` (the full games frame) enables the rest-spot wrappers — rest
    days are preseason-public schedule knowledge, so the closure is not
    leakage. Without it the rest variants are simply absent.
    """
    sim = SimConfig(n_sims=n_sims)

    def _model(ratings: object) -> NFLGameModel:
        return NFLGameModel(ratings, NFLModelConfig(sim=sim))  # type: ignore[arg-type]

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

    def qb_recency(
        half_life: float, qb_lam: float | None = None, *,
        garbage: tuple[str, float, float] | None = None,
        turnover: float | None = None, winsor: float | None = None,
        epa_col: str = "epa", home: bool = False, offseason_weeks: float = 0.0,
    ) -> VariantFactory:
        """The QB-decomposed recency fit, optionally conditioned on the play
        context the rebuilt plays carry (velocity.features.team):
        ``garbage`` = (wp column, factor, band) down-weights decided-game
        plays; ``turnover`` scales turnover-play EPA; ``winsor`` clips EPA;
        ``epa_col`` picks the EPA column (``qb_epa`` credits the passer as
        passing yards would); ``home`` fits the home-field edge in the ridge
        and prices it in place of the constant."""
        from velocity.features.team import (
            DEFAULT_QB_LAMBDA,
            attach_home_flag,
            fit_qb_ratings,
            garbage_time_weights,
            shrink_turnover_epa,
            winsorize_epa,
        )

        def factory(train: pd.DataFrame) -> NFLGameModel:
            frame = train
            if turnover is not None:
                frame = shrink_turnover_epa(frame, turnover, epa_col=epa_col)
            if winsor is not None:
                frame = winsorize_epa(frame, winsor, epa_col=epa_col)
            weights = recency_weights(frame, half_life, offseason_weeks=offseason_weeks)
            if garbage is not None:
                col, factor, band = garbage
                weights = weights * garbage_time_weights(
                    frame, factor=factor, band=band, wp_col=col)
            home_col: str | None = None
            if home and schedule is not None:
                frame = attach_home_flag(frame, schedule)
                home_col = "home"
            ratings = fit_qb_ratings(
                frame,
                qb_lambda=qb_lam if qb_lam is not None else DEFAULT_QB_LAMBDA,
                weights=weights, epa_col=epa_col, home_col=home_col,
            )
            config = NFLModelConfig(sim=sim)
            if home_col is not None:
                # ±0.5 in the column, so the coefficient is the offense's
                # home-minus-away EPA/play; over a game's plays that is the
                # margin edge the model splits half to each side.
                config = NFLModelConfig(
                    sim=sim, hfa_points=config.plays_per_game * ratings.home_epa)
            return NFLGameModel(ratings, config)

        return factory

    def scores(train_games: pd.DataFrame) -> ScoresGameModel:
        # The schedule-only fit the live slate currently runs — the promotion bar.
        return ScoresGameModel(fit_scores_ratings(train_games), ScoresModelConfig(sim=sim))

    # Every wrapper below forwards ``**kwargs`` and carries ``functools.wraps``:
    # the engine decides whether to pass ``predicting`` by inspecting the
    # OUTERMOST factory's signature, and ``wraps`` lets that inspection see
    # through to a scaled factory further in. Without it a chain such as
    # rest-over-scale silently ran unscaled (docs/MODEL_LAB.md, the NFL
    # composites round).
    def levelled(inner: VariantFactory, seasons: int | None = None) -> VariantFactory:
        """``inner`` with its scoring level fitted through the model on the
        training window's own games (velocity.models.level) — the totals
        bias the residual bank found, corrected where it arises."""
        @functools.wraps(inner)
        def factory(train: pd.DataFrame, **kwargs: object) -> NFLGameModel:
            from velocity.models.level import calibrate_level

            model = inner(train, **kwargs)
            if schedule is None:
                return model  # type: ignore[return-value]
            window = schedule[schedule["game_id"].isin(set(train["game_id"]))]
            return calibrate_level(model, window, seasons=seasons)  # type: ignore[arg-type]

        return factory

    variants: dict[str, tuple[str, VariantFactory]] = {
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
        # QB adjustment (docs/MODEL_LAB.md Round 3): the recency fit with
        # the passer decomposed out of the offense and the detected starter
        # priced back in at projection time. The bare name runs the promoted
        # config (DEFAULT_QB_LAMBDA).
        "qb-recency-17": ("plays", qb_recency(17.0)),
        "qb-recency-17-q75": ("plays", qb_recency(17.0, 75.0)),
        "qb-recency-17-q300": ("plays", qb_recency(17.0, 300.0)),
    }

    def scaled(
        inner: VariantFactory, league: str = "nfl", *, by_phase: bool = False,
        shift: bool = False,
    ) -> VariantFactory:
        """``inner`` under a ScaleCalibration fitted on the league's residual
        bank, seasons strictly before the one being projected
        (velocity.models.level). ``by_phase`` fits it on the bank rows of
        the same phase of the season as the projected week (early: through
        week 6). Absent bank → the identity."""
        from velocity.models.level import scale_model
        from velocity.models.residuals import load_residual_frame

        bank = load_residual_frame(league)

        def factory(train: pd.DataFrame, *, predicting: tuple[int, int] | None = None) -> object:
            model = inner(train)
            if bank is None or schedule is None or predicting is None:
                return model
            window = schedule[schedule["game_id"].isin(set(train["game_id"]))]
            weeks: tuple[int, int] | None = None
            if by_phase:
                early = predicting[1] <= NFL_EARLY_WEEK
                weeks = (1, NFL_EARLY_WEEK) if early else (NFL_EARLY_WEEK + 1, 30)
            # The scale sits directly over the game model; a starter wrapper
            # outside it keeps its kickoff keying, so wrap in that order.
            core = model.inner if isinstance(model, ScheduleStarterModel) else model
            scaled_model, _cal = scale_model(
                core, bank, window, sim, before_season=predicting[0], weeks=weeks, shift=shift)
            if isinstance(model, ScheduleStarterModel):
                return ScheduleStarterModel(scaled_model, schedule)  # type: ignore[arg-type]
            return scaled_model

        return factory

    def rested(
        inner: VariantFactory, bye_pts: float = 1.0, short_pts: float = 1.0
    ) -> VariantFactory:
        """``inner`` under the promoted rest wrapper — the live chain's outer
        layer, so a candidate can be scored exactly as it would run."""
        @functools.wraps(inner)
        def factory(train: pd.DataFrame, **kwargs: object) -> object:
            model = inner(train, **kwargs)
            if schedule is None:
                return model
            return RestAdjustedModel(
                model, schedule,  # type: ignore[arg-type]
                bye_points=bye_pts, short_points=short_pts)

        return factory

    def divisional(inner: VariantFactory, discount: float) -> VariantFactory:
        """``inner`` under the divisional home-field discount."""
        @functools.wraps(inner)
        def factory(train: pd.DataFrame, **kwargs: object) -> object:
            model = inner(train, **kwargs)
            if schedule is None:
                return model
            return DivisionalModel(model, schedule, discount)

        return factory

    def paced(inner: VariantFactory) -> VariantFactory:
        """``inner`` with each team's own plays per game from the training
        slice in place of the 63-play constant (velocity.features.team
        .team_pace) — the college model's pace treatment, in the NFL."""
        from velocity.features.team import team_pace

        @functools.wraps(inner)
        def factory(train: pd.DataFrame, **kwargs: object) -> object:
            model = inner(train, **kwargs)
            if not isinstance(model, NFLGameModel):
                return model
            # Pace is the fit's own definition of a play: scrimmage snaps per
            # game when the fit saw scrimmage snaps, every labelled play
            # otherwise — the same frame the ratings were fitted on.
            return NFLGameModel(model.ratings, model.config, pace=team_pace(train))

        return factory

    def starters(inner: VariantFactory) -> VariantFactory:
        """``inner`` priced with the schedule's announced starters per game."""
        @functools.wraps(inner)
        def factory(train: pd.DataFrame, **kwargs: object) -> object:
            model = inner(train, **kwargs)
            if schedule is None or "home_qb_id" not in schedule.columns:
                return model
            return ScheduleStarterModel(model, schedule)  # type: ignore[arg-type]

        return factory

    def scrimmage(inner: VariantFactory) -> VariantFactory:
        """``inner`` fitted on the offense's own snaps only (kicks, returns,
        kneels, spikes and no-plays dropped before the ridge —
        docs/PROJECTION_AUDIT.md §2.1)."""
        @functools.wraps(inner)
        def factory(train: pd.DataFrame, **kwargs: object) -> object:
            return inner(scrimmage_plays(train, "nfl"), **kwargs)

        return factory

    def live_plays(inner: VariantFactory) -> VariantFactory:
        """``inner`` fitted on every live play — kicks and returns kept, only
        kneels, spikes, no-plays and unlabelled rows dropped."""
        @functools.wraps(inner)
        def factory(train: pd.DataFrame, **kwargs: object) -> object:
            return inner(scrimmage_plays(train, "nfl", keep_kicks=True), **kwargs)

        return factory

    if schedule is not None:
        variants.update({
            # The promoted fit with its level calibrated on the training
            # window (all of it, and the trailing two seasons).
            "qb-recency-17-q300-level": ("plays", levelled(qb_recency(17.0, 300.0))),
            "qb-recency-17-q300-level2": ("plays", levelled(qb_recency(17.0, 300.0), 2)),
            "qb-recency-17-q300-level2-scrim": (
                "plays", scrimmage(levelled(qb_recency(17.0, 300.0), 2))),
            "qb-recency-17-q300-level2-scale": (
                "plays", scaled(levelled(qb_recency(17.0, 300.0), 2))),
            "qb-recency-17-q300-level2-scrim-scale": (
                "plays", scaled(scrimmage(levelled(qb_recency(17.0, 300.0), 2)))),
            "qb-recency-17-q300-level2-starters": (
                "plays", starters(levelled(qb_recency(17.0, 300.0), 2))),
            # The residual-bank core with the turnover shrink (the play-
            # context round): the bank the scale is fitted on is rebuilt
            # from this variant's projections when the shrink is promoted.
            "qb-recency-17-q300-level2-starters-to0.5": (
                "plays", starters(levelled(qb_recency(17.0, 300.0, turnover=0.5), 2))),
            "qb-recency-17-q300-level2-starters-to0.5-gap8": (
                "plays", starters(levelled(
                    qb_recency(17.0, 300.0, turnover=0.5, offseason_weeks=8.0), 2))),
            "qb-recency-17-q300-level2-scrim-starters": (
                "plays", starters(scrimmage(levelled(qb_recency(17.0, 300.0), 2)))),
            # Pace over the scrimmage fit: the level is re-fitted after the
            # pace map is attached, so the two calibrate together.
            "qb-recency-17-q300-level2-scrim-pace": (
                "plays", scrimmage(levelled(paced(qb_recency(17.0, 300.0)), 2))),
            "qb-recency-17-q300-level2-pace": (
                "plays", levelled(paced(qb_recency(17.0, 300.0)), 2)),
            "qb-recency-17-q300-level2-live": (
                "plays", live_plays(levelled(qb_recency(17.0, 300.0), 2))),
            "qb-recency-17-q300-level2-starters-scale": (
                "plays", scaled(starters(levelled(qb_recency(17.0, 300.0), 2)))),
            "qb-recency-17-q300-level2-starters-pace-scale": (
                "plays", scaled(starters(levelled(paced(qb_recency(17.0, 300.0)), 2)))),
            "qb-recency-17-q300-level2-starters-scale-phase": (
                "plays", scaled(starters(levelled(qb_recency(17.0, 300.0), 2)), by_phase=True)),
            # The live chain (rest over the fit) for the incumbent and the
            # candidate composites, so the promotion is scored as it runs.
            "live-nfl-incumbent": ("plays", rested(levelled(qb_recency(17.0, 300.0), 2))),
            "live-nfl-scrim": ("plays", rested(scrimmage(levelled(qb_recency(17.0, 300.0), 2)))),
            "live-nfl-scrim-scale": (
                "plays", rested(scaled(scrimmage(levelled(qb_recency(17.0, 300.0), 2))))),
            "live-nfl-scrim-starters-scale": (
                "plays", rested(scaled(starters(scrimmage(levelled(qb_recency(17.0, 300.0), 2)))))),
            "live-nfl-scrim-pace-scale": (
                "plays", rested(scaled(scrimmage(levelled(paced(qb_recency(17.0, 300.0)), 2))))),
            "live-nfl-scrim-pace-starters-scale": (
                "plays", rested(scaled(starters(scrimmage(
                    levelled(paced(qb_recency(17.0, 300.0)), 2)))))),
            "live-nfl-starters-scale": (
                "plays", rested(scaled(starters(levelled(qb_recency(17.0, 300.0), 2))))),
            "live-nfl-starters-pace-scale": (
                "plays", rested(scaled(starters(levelled(paced(qb_recency(17.0, 300.0)), 2))))),
            "live-nfl-starters-scale-phase": (
                "plays", rested(scaled(starters(levelled(qb_recency(17.0, 300.0), 2)),
                                       by_phase=True))),
        })
        def rest(bye_pts: float, short_pts: float) -> VariantFactory:
            base = qb_recency(17.0)

            def factory(train: pd.DataFrame) -> RestAdjustedModel:
                return RestAdjustedModel(
                    base(train), schedule,  # type: ignore[arg-type]
                    bye_points=bye_pts, short_points=short_pts,
                )

            return factory

        variants.update({
            "rest-1.0-1.0": ("plays", rest(1.0, 1.0)),
            "rest-0.5-1.5": ("plays", rest(0.5, 1.5)),
            "rest-2.0-1.0": ("plays", rest(2.0, 1.0)),
            # The post-2011-CBA literature (docs/EDGE_RESEARCH.md §2.1): the
            # true bye effect is ≈ +0.3 points while the market prices ≈ +1;
            # the promoted +1.0 constant matches the market's overpricing, not
            # the measured effect. These variants test the small-bye end.
            "rest-0.3-1.0": ("plays", rest(0.3, 1.0)),
            "rest-0.3-0.5": ("plays", rest(0.3, 0.5)),
            "rest-0.0-0.5": ("plays", rest(0.0, 0.5)),
        })

        # Wind on totals: needs the fetched weather archive joined onto the
        # schedule (scripts/fetch_weather.py → datasets/nfl/weather.parquet).
        weather_path = Path("datasets/nfl/weather.parquet")
        if weather_path.exists():
            from velocity.features.weather import join_weather

            joined = join_weather(schedule, pd.read_parquet(weather_path))

            def wind(threshold: float, per_mph: float) -> VariantFactory:
                base = qb_recency(17.0)

                def factory(train: pd.DataFrame) -> WeatherAdjustedModel:
                    return WeatherAdjustedModel(
                        base(train), joined,  # type: ignore[arg-type]
                        threshold_mph=threshold, points_per_mph=per_mph,
                    )

                return factory

            def windy(
                inner: VariantFactory, *, precip_points: float = 0.0,
                precip_threshold_in: float = 0.25, cold_points: float = 0.0,
                cold_threshold_f: float = 32.0,
            ) -> VariantFactory:
                """``inner`` under the promoted wind wrapper (15 mph, 0.30
                a mph) — the live chain's outermost layer — with optional
                precipitation and cold steps."""
                @functools.wraps(inner)
                def factory(train: pd.DataFrame, **kwargs: object) -> object:
                    return WeatherAdjustedModel(
                        inner(train, **kwargs), joined,  # type: ignore[arg-type]
                        threshold_mph=15.0, points_per_mph=0.30,
                        precip_points=precip_points, precip_threshold_in=precip_threshold_in,
                        cold_points=cold_points, cold_threshold_f=cold_threshold_f,
                    )

                return factory

            # The live chain as it runs today — wind over rest over the
            # scaled starters fit — and the situational candidates over it.
            live_core = rested(scaled(starters(levelled(qb_recency(17.0, 300.0), 2))))

            def injured(inner: VariantFactory, points_per_unit: float) -> VariantFactory:
                """``inner`` under the injury burden, from the committed
                designations and usage banks (both absent → ``inner``)."""
                injuries_path = Path("datasets/nfl/injuries.parquet")
                weeks_path = Path("datasets/nfl/player_weeks.parquet")
                if not injuries_path.exists() or not weeks_path.exists():
                    return inner
                from velocity.features.injuries import burden_by_team_week

                burden = burden_by_team_week(
                    pd.read_parquet(injuries_path), pd.read_parquet(weeks_path))

                @functools.wraps(inner)
                def factory(train: pd.DataFrame, **kwargs: object) -> object:
                    return InjuryBurdenModel(
                        inner(train, **kwargs), schedule, burden, points_per_unit)

                return factory

            variants.update({
                "wind-15-0.15": ("plays", wind(15.0, 0.15)),
                "wind-15-0.30": ("plays", wind(15.0, 0.30)),
                "wind-12-0.15": ("plays", wind(12.0, 0.15)),
                "live-nfl-full": ("plays", windy(live_core)),
                "live-nfl-full-precip0.25-0.5": (
                    "plays", windy(live_core, precip_points=0.5)),
                "live-nfl-full-precip0.25-1.0": (
                    "plays", windy(live_core, precip_points=1.0)),
                "live-nfl-full-precip0.1-0.5": (
                    "plays", windy(live_core, precip_points=0.5, precip_threshold_in=0.1)),
                "live-nfl-full-div0.5": ("plays", windy(divisional(live_core, 0.5))),
                "live-nfl-full-div1.0": ("plays", windy(divisional(live_core, 1.0))),
                # Points per whole team's worth of touches ruled out: a 15%
                # burden (the 90th percentile) costs 0.6 / 1.2 / 2.4 points.
                "live-nfl-full-injury4": ("plays", windy(injured(live_core, 4.0))),
                "live-nfl-full-injury8": ("plays", windy(injured(live_core, 8.0))),
                "live-nfl-full-injury16": ("plays", windy(injured(live_core, 16.0))),
                # Cold over the promoted chain (rain at 1.0, burden at 4).
                "live-nfl-promoted": (
                    "plays", windy(injured(live_core, 4.0), precip_points=1.0)),
                "live-nfl-promoted-cold32-0.5": (
                    "plays", windy(injured(live_core, 4.0), precip_points=1.0,
                                   cold_points=0.5)),
                "live-nfl-promoted-cold32-1.0": (
                    "plays", windy(injured(live_core, 4.0), precip_points=1.0,
                                   cold_points=1.0)),
                "live-nfl-promoted-cold40-0.5": (
                    "plays", windy(injured(live_core, 4.0), precip_points=1.0,
                                   cold_points=0.5, cold_threshold_f=40.0)),
            })

            def promoted(core: VariantFactory, *, shift: bool = False) -> VariantFactory:
                """``core`` under the whole promoted chain — level, scale,
                starters, rest, the injury burden, wind and rain — so a
                change to the fit itself is scored exactly as it would run.
                ``shift`` keeps the scale's home-margin intercept."""
                return windy(injured(rested(scaled(starters(levelled(core, 2)), shift=shift)),
                                     4.0), precip_points=1.0)

            variants.update({
                # The play-context round (docs/PROJECTION_AUDIT.md §2.1, the
                # plays rebuild): the chain as promoted before the round —
                # the fit on the plays as recorded — conditioned on the win
                # probability, the turnover flags and the home flag. Each
                # candidate was scored against "live-nfl-promoted-to1.0".
                "live-nfl-promoted-to1.0": (
                    "plays", windy(injured(live_core, 4.0), precip_points=1.0)),
                "live-nfl-promoted-gt0.5": (
                    "plays", promoted(qb_recency(17.0, 300.0, garbage=("wp", 0.5, 0.05)))),
                "live-nfl-promoted-gt0.25": (
                    "plays", promoted(qb_recency(17.0, 300.0, garbage=("wp", 0.25, 0.05)))),
                "live-nfl-promoted-gt0.5-b0.10": (
                    "plays", promoted(qb_recency(17.0, 300.0, garbage=("wp", 0.5, 0.10)))),
                "live-nfl-promoted-gtv0.5": (
                    "plays", promoted(qb_recency(17.0, 300.0,
                                                 garbage=("vegas_wp", 0.5, 0.05)))),
                "live-nfl-promoted-to0.5": (
                    "plays", promoted(qb_recency(17.0, 300.0, turnover=0.5))),
                "live-nfl-promoted-to0.25": (
                    "plays", promoted(qb_recency(17.0, 300.0, turnover=0.25))),
                "live-nfl-promoted-win4": (
                    "plays", promoted(qb_recency(17.0, 300.0, winsor=4.0))),
                "live-nfl-promoted-win3": (
                    "plays", promoted(qb_recency(17.0, 300.0, winsor=3.0))),
                "live-nfl-promoted-qbepa": (
                    "plays", promoted(qb_recency(17.0, 300.0, epa_col="qb_epa"))),
                "live-nfl-promoted-home": (
                    "plays", promoted(qb_recency(17.0, 300.0, home=True))),
                # The promoted chain after the play-context round: the
                # turnover shrink at 0.5 in the fit, the residual bank rebuilt
                # on its core (qb-recency-17-q300-level2-starters-to0.5), the
                # scale fitted on that bank. The recency round's candidates
                # below were scored against it.
                "live-nfl-promoted-gap0": (
                    "plays", promoted(qb_recency(17.0, 300.0, turnover=0.5))),
                # The promoted chain after the recency round: the eight-week
                # offseason gap in the recency key, the bank rebuilt on that
                # core (qb-recency-17-q300-level2-starters-to0.5-gap8) — what
                # the live runner prices.
                "live-nfl-promoted": (
                    "plays", promoted(qb_recency(17.0, 300.0, turnover=0.5, offseason_weeks=8.0))),
                # The scale's home-margin intercept, over the promoted chain.
                "live-nfl-promoted-shift": (
                    "plays", promoted(qb_recency(17.0, 300.0, turnover=0.5, offseason_weeks=8.0),
                                      shift=True)),
                # The recency round (after the college finding): the
                # half-life re-swept over the promoted chain, and an
                # offseason gap — the key otherwise steps a few empty week
                # slots between seasons.
                "live-nfl-promoted-hl8": (
                    "plays", promoted(qb_recency(8.0, 300.0, turnover=0.5))),
                "live-nfl-promoted-hl12": (
                    "plays", promoted(qb_recency(12.0, 300.0, turnover=0.5))),
                "live-nfl-promoted-hl25": (
                    "plays", promoted(qb_recency(25.0, 300.0, turnover=0.5))),
                "live-nfl-promoted-hl17-gap8": (
                    "plays", promoted(qb_recency(17.0, 300.0, turnover=0.5, offseason_weeks=8.0))),
                "live-nfl-promoted-hl17-gap16": (
                    "plays", promoted(qb_recency(17.0, 300.0, turnover=0.5, offseason_weeks=16.0))),
                "live-nfl-promoted-hl12-gap8": (
                    "plays", promoted(qb_recency(12.0, 300.0, turnover=0.5, offseason_weeks=8.0))),
                "live-nfl-promoted-hl25-gap16": (
                    "plays", promoted(qb_recency(25.0, 300.0, turnover=0.5, offseason_weeks=16.0))),
            })
    return variants


def compress_plays(
    plays: pd.DataFrame, games: pd.DataFrame | None = None, *, by_passer: bool = False,
) -> pd.DataFrame:
    """Aggregate plays to ``(posteam, defteam, season, week)`` cells for the ridge fit.

    The one-hot design matrix in :func:`fit_ratings` is identical for every
    play in the same cell, so the play-level ridge loss decomposes cell by
    cell: fitting the cell **means** with the cell **counts** as weights
    reproduces the play-level fit exactly (the within-cell variance is a
    constant the minimizer never sees). Recency weights compose too — every
    play in a cell shares its (season, week), hence its decay factor, so
    ``count × recency(cell)`` equals the summed play weights.

    This matters for college: ~1.4M plays × ~500 one-hot columns is a dense
    matrix in the gigabytes, refit every walk-forward week; the cells are a
    few thousand rows. The returned frame carries ``epa`` (the cell mean) and
    ``n`` (the cell count).

    ``by_passer`` splits each cell by ``passer_player_id`` (the plays with no
    passer form their own cell), so the QB dummies of
    :func:`~velocity.features.team.fit_qb_ratings` fire per cell the way
    they fire per play; pass ``n`` as its ``count_col`` and ``weights``.
    """
    keys = ["posteam", "defteam", "season", "week"]
    if by_passer and "passer_player_id" in plays.columns:
        keys.append("passer_player_id")
    cells = (
        plays.dropna(subset=["posteam", "defteam", "epa"])
        .groupby(keys, observed=True, dropna=False)["epa"]
        .agg(epa="mean", n="count")
        .reset_index()
    )
    if games is not None and not games.empty:
        # The home flag rides the cell (a posteam/defteam pair in one week is
        # one game): +0.5 at home, −0.5 away, 0 on a neutral field — the
        # column :func:`fit_ratings` fits a home-field edge on.
        neutral = (games["neutral_site"].astype(bool) if "neutral_site" in games.columns
                   else pd.Series(False, index=games.index))
        home = pd.DataFrame({
            "posteam": games["home_team"].astype(str), "defteam": games["away_team"].astype(str),
            "season": games["season"], "week": games["week"],
            "home": np.where(neutral, 0.0, 0.5),
        })
        away = pd.DataFrame({
            "posteam": games["away_team"].astype(str), "defteam": games["home_team"].astype(str),
            "season": games["season"], "week": games["week"],
            "home": np.where(neutral, 0.0, -0.5),
        })
        flags = pd.concat([home, away], ignore_index=True).drop_duplicates(
            subset=["posteam", "defteam", "season", "week"])
        cells = cells.merge(flags, on=["posteam", "defteam", "season", "week"], how="left")
        cells["home"] = cells["home"].fillna(0.0)
    return cells


def fbs_games(games: pd.DataFrame, sp: pd.DataFrame) -> pd.DataFrame:
    """The games between two FBS programs, as the SP+ ratings define FBS.

    A season's FBS list is the teams SP+ rated that season; a season the
    ratings do not reach yet (the current one) borrows the latest list. The
    frame keeps its columns and index order.
    """
    seasons_rated = sp["season"].astype(int)
    by_season = {
        season: set(sp.loc[seasons_rated == season, "team"].astype(str))
        for season in sorted(set(seasons_rated))
    }
    if not by_season:
        return games
    latest = by_season[max(by_season)]
    keep = []
    for season, home, away in zip(games["season"].astype(int), games["home_team"].astype(str),
                                  games["away_team"].astype(str), strict=True):
        teams = by_season.get(season, latest)
        keep.append(home in teams and away in teams)
    return games[np.asarray(keep, dtype=bool)]


def ncaaf_walk_order(games: pd.DataFrame) -> pd.DataFrame:
    """Renumber NCAAF postseason weeks so they sort **after** the regular season.

    CFBD labels most bowls week 1 of the postseason, and the committed games
    file keeps that label. The walk-forward engine slices training data by
    ``week <`` alone, so a week-1 bowl slots *before* the same season's
    regular weeks: bowls trained on nothing from their own season, and — the
    real leak — regular-season predictions from week 2 on trained on that
    season's bowls. This maps POST week → 17 + its dense rank within the
    season (REG tops out at 16), preserving postseason order. Capped at 24:
    the schema allows 25, but ``recency_weights``' season×25+week key would
    fold a week-25 label into the next season.
    """
    out = games.copy()
    post = out["season_type"] == "POST"
    if post.any():
        rank = (
            out.loc[post].groupby("season")["week"].rank(method="dense").astype(int)
        )
        out.loc[post, "week"] = (17 + rank).clip(upper=24)
    return out


class BlendedGameModel:
    """Average the expected points of two projection models, simulate once.

    Both inner models expose ``expected_points(home, away, neutral_site=...)``;
    the blend is ``w·primary + (1−w)·secondary`` on each team's expected
    points, priced through one shared simulation. Linear in the μs, so the
    blended margin/total are the same weighted blend of the components'.
    """

    def __init__(
        self, primary: object, secondary: object, weight: float, sim: SimConfig
    ) -> None:
        if not 0.0 <= weight <= 1.0:
            raise ValueError("weight must be in [0, 1]")
        self.primary = primary
        self.secondary = secondary
        self.weight = weight
        self.sim = sim

    def expected_points(
        self, home_team: str, away_team: str, *, neutral_site: bool = False
    ) -> tuple[float, float]:
        w = self.weight
        p_home, p_away = self.primary.expected_points(  # type: ignore[attr-defined]
            home_team, away_team, neutral_site=neutral_site
        )
        s_home, s_away = self.secondary.expected_points(  # type: ignore[attr-defined]
            home_team, away_team, neutral_site=neutral_site
        )
        return w * p_home + (1 - w) * s_home, w * p_away + (1 - w) * s_away

    def project(
        self,
        home_team: str,
        away_team: str,
        *,
        neutral_site: bool = False,
        rng: np.random.Generator | None = None,
    ) -> object:
        from velocity.models.game_nfl import GameProjection
        from velocity.models.simulate import simulate_game
        from velocity.util.seed import make_rng

        rng = rng if rng is not None else make_rng()
        mu_home, mu_away = self.expected_points(
            home_team, away_team, neutral_site=neutral_site
        )
        sim = simulate_game(
            mu_margin=mu_home - mu_away, mu_total=mu_home + mu_away,
            rng=rng, config=self.sim,
        )
        return GameProjection(
            home_team=home_team, away_team=away_team,
            mu_home=mu_home, mu_away=mu_away, sim=sim,
        )


def ncaaf_variants(
    n_sims: int, plays: pd.DataFrame | None = None, sp: pd.DataFrame | None = None
) -> dict[str, tuple[str, VariantFactory]]:
    """The NCAAF benchmark slate: scores-fit variants, plus EPA fits and
    EPA×scores blends when the committed college plays frame is passed.

    College schedules are thin and non-connective, so shrinkage and recency
    matter differently than the NFL; the sim SDs are the live slate's wider
    college calibration. ``ridge-10`` is the shipped configuration and the
    promotion bar (docs/MODEL_LAB.md NCAAF Round 1).

    The EPA variants fit :func:`fit_ratings` on :func:`compress_plays` cells
    (exactly the play-level fit, at college scale) and price through the same
    pace/base scoring shape as the NFL model with college constants. College
    ridge sweeps start heavier than the NFL's λ=200 — 130+ FBS programs plus
    FCS opponents on a barely-connected schedule graph.

    The blend variants average the μs of the two best single fits (EPA λ=50,
    scores λ=10). They train from the **games** frame — the engine's own
    point-in-time slice — and cut the closed-over ``plays`` to exactly those
    game_ids, so the plays side sees precisely the games the engine would
    have trained on (no leakage, no drift between the two fits).

    ``sp`` (the committed SP+ season ratings) adds the ``blend-level2-sp<K>``
    variants: the promoted blend with last season's final SP+ as ``K``
    pseudo-games in the scores half — the configuration the live runner has
    priced since the 2026-08-31 addendum, which until this round had never
    been through the harness. The leak gate takes the *predicted* season from
    the engine (``predicting``), so a season's final ratings never inform its
    own games, bowl weeks included.
    """
    from velocity.features.scores import scores_recency_weights

    sim = SimConfig(sd_margin=NCAAF_SD_MARGIN, sd_total=NCAAF_SD_TOTAL, n_sims=n_sims)

    def _model(ratings: object) -> ScoresGameModel:
        return ScoresGameModel(ratings, ScoresModelConfig(sim=sim))  # type: ignore[arg-type]

    def scores(train: pd.DataFrame) -> ScoresGameModel:
        return _model(fit_scores_ratings(train))

    def ridge(lam: float) -> VariantFactory:
        def factory(train: pd.DataFrame) -> ScoresGameModel:
            return _model(fit_scores_ratings(train, ridge_lambda=lam))

        return factory

    def recency(half_life: float, lam: float = 25.0) -> VariantFactory:
        def factory(train: pd.DataFrame) -> ScoresGameModel:
            return _model(fit_scores_ratings(
                train, ridge_lambda=lam,
                weights=scores_recency_weights(train, half_life),
            ))

        return factory

    variants: dict[str, tuple[str, VariantFactory]] = {
        "scores": ("games", scores),
        "ridge-10": ("games", ridge(10.0)),
        "ridge-50": ("games", ridge(50.0)),
        "recency-17": ("games", recency(17.0)),
        "recency-34": ("games", recency(34.0)),
        "recency-17-r50": ("games", recency(17.0, 50.0)),
    }

    if plays is not None:
        # College scoring/pace constants (calibration priors, not fits):
        # ~28.5 points per team, ~65 offensive scrimmage plays, HFA ~2.5.
        cfg = NFLModelConfig(
            base_points=28.5, plays_per_game=65.0, hfa_points=2.5, sim=sim
        )

        def epa(lam: float, half_life: float | None = None) -> VariantFactory:
            def factory(train: pd.DataFrame) -> NFLGameModel:
                cells = compress_plays(train)
                weights = cells["n"].astype(float)
                if half_life is not None:
                    weights = weights * recency_weights(cells, half_life)
                ratings = fit_ratings(cells, ridge_lambda=lam, weights=weights)
                return NFLGameModel(ratings, cfg)

            return factory

        variants.update({
            "epa-r50": ("plays", epa(50.0)),
            "epa-r100": ("plays", epa(100.0)),
            "epa-r200": ("plays", epa(200.0)),
            "epa-r400": ("plays", epa(400.0)),
            "epa-r800": ("plays", epa(800.0)),
            "epa-recency-17": ("plays", epa(400.0, 17.0)),
        })

        all_plays = plays

        def blend(epa_weight: float) -> VariantFactory:
            def factory(train_games: pd.DataFrame) -> BlendedGameModel:
                from dataclasses import replace as _replace

                from velocity.models.level import mean_points_per_team

                sub = all_plays[all_plays["game_id"].isin(set(train_games["game_id"]))]
                cells = compress_plays(sub)
                # The scoring level fitted per training window exactly as
                # the live runner fits it (velocity.models.level): a fixed
                # 28.5 kept the lab projecting the pre-2023 era while the
                # live blend had moved, so the lab's residuals were not the
                # live model's.
                epa_model = NFLGameModel(
                    fit_ratings(cells, ridge_lambda=50.0,
                                weights=cells["n"].astype(float)),
                    _replace(cfg, base_points=mean_points_per_team(train_games)),
                )
                scores_model = _model(fit_scores_ratings(train_games, ridge_lambda=10.0))
                return BlendedGameModel(epa_model, scores_model, epa_weight, sim)

            return factory

        variants.update({
            "blend-epa30": ("games", blend(0.30)),
            "blend-epa50": ("games", blend(0.50)),
            "blend-epa70": ("games", blend(0.70)),
        })

        # The college HFA and pace round (docs/SYSTEM_REVIEW.md §3.3–3.4).
        # The EPA half assumed 2.5 points of home field while the scores
        # half learned ~5 and the residual bank found the blend a point low
        # on home margin; and it ran every team at 65 plays while real pace
        # spans 55–71. Each variant is the promoted blend with one change.
        def blend_hfa(mode: str, *, pace: bool = False) -> VariantFactory:
            def factory(train_games: pd.DataFrame) -> BlendedGameModel:
                from dataclasses import replace as _replace

                from velocity.features.team import team_pace
                from velocity.models.game_ncaaf import NCAAFGameModel, NCAAFModelConfig
                from velocity.models.level import mean_points_per_team

                sub = all_plays[all_plays["game_id"].isin(set(train_games["game_id"]))]
                cells = compress_plays(sub, train_games)
                ratings = fit_ratings(cells, ridge_lambda=50.0,
                                      weights=cells["n"].astype(float), home_col="home")
                scores_ratings = fit_scores_ratings(train_games, ridge_lambda=10.0)
                paces = team_pace(sub) if pace else {}
                league_pace = (float(np.mean(list(paces.values()))) if paces else 65.0)
                # One HFA across the blend, in points: the EPA fit's edge at
                # the league's pace ("epa"), or the scores fit's learned edge
                # ("scores"); "own" keeps each half's own number.
                epa_hfa_points = ratings.home_epa * league_pace
                if mode == "epa":
                    hfa_epa, hfa_scores = epa_hfa_points, epa_hfa_points
                elif mode == "scores":
                    hfa_epa, hfa_scores = scores_ratings.home_edge, scores_ratings.home_edge
                else:
                    hfa_epa, hfa_scores = epa_hfa_points, scores_ratings.home_edge
                level = mean_points_per_team(train_games)
                if pace:
                    epa_model: object = NCAAFGameModel(
                        ratings, paces,
                        NCAAFModelConfig(base_points=level, hfa_points=hfa_epa,
                                         league_pace=league_pace, sim=sim),
                    )
                else:
                    epa_model = NFLGameModel(
                        ratings, _replace(cfg, base_points=level, hfa_points=hfa_epa))
                scores_model = _model(_replace(scores_ratings, home_edge=hfa_scores))
                return BlendedGameModel(epa_model, scores_model, 0.5, sim)

            return factory

        def blend_levelled(epa_weight: float, *, scrimmage: bool = False) -> VariantFactory:
            """The promoted blend with the scores half levelled on the
            trailing two seasons (velocity.models.level) — its unweighted
            intercept lagged the post-2021 scoring drop by 1–2 points a game.
            ``scrimmage`` fits the EPA half on the offense's own snaps only."""
            def factory(train_games: pd.DataFrame) -> BlendedGameModel:
                from dataclasses import replace as _replace

                from velocity.models.level import calibrate_scores_level, mean_points_per_team

                sub = all_plays[all_plays["game_id"].isin(set(train_games["game_id"]))]
                if scrimmage:
                    sub = scrimmage_plays(sub, "ncaaf")
                cells = compress_plays(sub)
                epa_model = NFLGameModel(
                    fit_ratings(cells, ridge_lambda=50.0,
                                weights=cells["n"].astype(float)),
                    _replace(cfg, base_points=mean_points_per_team(train_games)),
                )
                scores_model = calibrate_scores_level(
                    _model(fit_scores_ratings(train_games, ridge_lambda=10.0)), train_games)
                return BlendedGameModel(epa_model, scores_model, epa_weight, sim)

            return factory

        variants.update({
            "blend-level2": ("games", blend_levelled(0.50)),
            "blend-level2-scrim": ("games", blend_levelled(0.50, scrimmage=True)),
            "blend-hfa-own": ("games", blend_hfa("own")),
            "blend-hfa-epa": ("games", blend_hfa("epa")),
            "blend-hfa-scores": ("games", blend_hfa("scores")),
            "blend-hfa-own-pace": ("games", blend_hfa("own", pace=True)),
        })

        def college_scaled(
            inner: VariantFactory, *, by_phase: bool = False,
            sds: tuple[float, float] | None = None, shift: bool = False,
        ) -> VariantFactory:
            """``inner`` under the college residual bank's ScaleCalibration
            (seasons before the projected one); the anchor is the model's
            mean total over the trailing two training seasons. ``by_phase``
            fits on the bank rows of the projected week's phase (early:
            through week 4). ``sds`` = (sd_margin, sd_total) prices the
            scaled model through a sim of that dispersion instead of the
            league constants (the dispersion round)."""
            from dataclasses import replace as _replace_sim

            from velocity.models.level import scale_model
            from velocity.models.residuals import load_residual_frame

            bank = load_residual_frame("ncaaf")
            chain_sim = (_replace_sim(sim, sd_margin=sds[0], sd_total=sds[1])
                         if sds is not None else sim)

            def factory(
                train_games: pd.DataFrame, *, predicting: tuple[int, int] | None = None
            ) -> object:
                import inspect

                inner_kwargs = (
                    {"predicting": predicting}
                    if "predicting" in inspect.signature(inner).parameters else {}
                )
                model = inner(train_games, **inner_kwargs)
                if bank is None or predicting is None:
                    return model
                weeks = (
                    ((1, NCAAF_EARLY_WEEK) if predicting[1] <= NCAAF_EARLY_WEEK
                     else (NCAAF_EARLY_WEEK + 1, 30))
                    if by_phase else None
                )
                scaled_model, _cal = scale_model(
                    model, bank, train_games, chain_sim, before_season=predicting[0],
                    weeks=weeks, shift=shift)
                return scaled_model

            return factory

        def college_rested(
            inner: VariantFactory, bye_pts: float = 1.0, *, bye_days: int = 12,
            max_rest_days: int = 30,
        ) -> VariantFactory:
            """``inner`` with a bye-week bonus, from the training slice's own
            kickoffs (public schedule knowledge, as the NFL wrapper argues).
            College has no rest treatment at all today; a fortnight off is
            the common case. An offseason gap is not a bye."""
            def factory(
                train_games: pd.DataFrame, *, predicting: tuple[int, int] | None = None
            ) -> object:
                import inspect

                inner_kwargs = (
                    {"predicting": predicting}
                    if "predicting" in inspect.signature(inner).parameters else {}
                )
                model = inner(train_games, **inner_kwargs)
                sched = train_games.dropna(subset=["kickoff"])
                long = pd.concat([
                    sched[["home_team", "kickoff"]].rename(columns={"home_team": "team"}),
                    sched[["away_team", "kickoff"]].rename(columns={"away_team": "team"}),
                ], ignore_index=True)
                by_team = {
                    str(team): pd.to_datetime(group["kickoff"]).sort_values().to_numpy()
                    for team, group in long.groupby("team")
                }

                def rested(team: str, when: object) -> bool:
                    played = by_team.get(team)
                    if played is None or when is None or pd.isna(when):  # type: ignore[call-overload]
                        return False
                    ts = pd.Timestamp(when).to_datetime64()  # type: ignore[arg-type]
                    prior = played[played < ts - np.timedelta64(1, "D")]
                    if len(prior) == 0:
                        return False
                    rest_days = (ts - prior[-1]) / np.timedelta64(1, "D")
                    return bool(bye_days <= rest_days <= max_rest_days)

                def bonus(home: str, away: str, kickoff: object) -> tuple[float, float]:
                    return (bye_pts if rested(home, kickoff) else 0.0,
                            bye_pts if rested(away, kickoff) else 0.0)

                return BonusAdjustedModel(model, bonus, sim)

            return factory

        variants.update({
            "blend-level2-scale": ("games", college_scaled(blend_levelled(0.50))),
        })

        if sp is not None and not sp.empty:
            from velocity.ingest.ncaaf import sp_pseudo_games

            sp_frame = sp

            def blend_sp(
                k: int, *, scrimmage: bool = False, pace: bool = False,
                early_weight: float | None = None, qb_lambda: float | None = None,
                epa_prior_k: int | None = None, epa_half_life: float | None = None,
                epa_offseason_weeks: float = 0.0, st_prior: bool = False,
                scores_half_life: float | None = None,
            ) -> VariantFactory:
                """``blend-level2`` with the SP+ previous-season prior in the
                scores half, at ``k`` pseudo-games per team — what the live
                runner prices. ``scrimmage`` fits the EPA half on the
                offense's own snaps only; ``pace`` prices it at each team's
                own plays per game instead of the 65-play constant;
                ``early_weight`` is the EPA half's weight through week
                ``NCAAF_EARLY_WEEK`` (the half with no prior), 0.5 after;
                ``qb_lambda`` decomposes the passer out of the EPA half's
                offense (fit_qb_ratings on passer cells, the detected
                starter priced back in) at that QB ridge; ``epa_prior_k``
                puts the same SP+ prior into the EPA half as week-0
                pseudo-cells worth that many games (sp_pseudo_cells);
                ``epa_half_life`` recency-weights the EPA half's cells
                (on-field weeks); ``epa_offseason_weeks`` widens the gap
                between seasons in that key; ``st_prior`` folds SP+'s
                special-teams rating into the scores half's pseudo-games;
                ``scores_half_life`` recency-weights the scores half's games
                (scores_recency_weights; the pseudo-games sit at week 0 of
                the projected season, so the prior counts as current)."""
                def factory(
                    train_games: pd.DataFrame, *, predicting: tuple[int, int] | None = None
                ) -> BlendedGameModel:
                    from dataclasses import replace as _replace

                    from velocity.features.team import fit_qb_ratings, team_pace
                    from velocity.ingest.ncaaf import sp_pseudo_cells
                    from velocity.models.level import calibrate_scores_level, mean_points_per_team

                    # The knowledge point: Feb 1 of the season being projected
                    # admits exactly the seasons before it. Without the engine's
                    # hint, fall back to the live runner's rule (the latest
                    # kickoff on file).
                    cutoff = (
                        pd.Timestamp(year=int(predicting[0]), month=2, day=1)
                        if predicting is not None
                        else pd.to_datetime(train_games["kickoff"]).max()
                    )
                    teams = (set(train_games["home_team"].astype(str))
                             | set(train_games["away_team"].astype(str)))

                    sub = all_plays[all_plays["game_id"].isin(set(train_games["game_id"]))]
                    if scrimmage:
                        sub = scrimmage_plays(sub, "ncaaf")
                    cells = compress_plays(sub, by_passer=qb_lambda is not None)
                    if epa_prior_k:
                        prior_cells = sp_pseudo_cells(
                            sp_frame, teams, cutoff=cutoff, k=epa_prior_k,
                            league_epa=float(sub["epa"].mean()),
                            plays_per_game=cfg.plays_per_game)
                        if not prior_cells.empty:
                            cells = pd.concat([cells, prior_cells], ignore_index=True)
                    weights = cells["n"].astype(float)
                    if epa_half_life is not None:
                        weights = weights * recency_weights(
                            cells, epa_half_life, offseason_weeks=epa_offseason_weeks)
                    if qb_lambda is not None:
                        ratings: object = fit_qb_ratings(
                            cells, ridge_lambda=50.0, qb_lambda=qb_lambda,
                            weights=weights, count_col="n")
                    else:
                        ratings = fit_ratings(cells, ridge_lambda=50.0, weights=weights)
                    epa_model = NFLGameModel(
                        ratings,  # type: ignore[arg-type]
                        _replace(cfg, base_points=mean_points_per_team(train_games)),
                        pace=team_pace(sub) if pace else None,
                    )
                    pseudo = sp_pseudo_games(
                        sp_frame, teams, cutoff=cutoff, k=k, special_teams=st_prior)
                    fit_games = (pd.concat([train_games, pseudo], ignore_index=True)
                                 if not pseudo.empty else train_games)
                    # The level is fitted on real games only: pseudo-games sit
                    # at SP+'s own scale, not the season's scoring level.
                    scores_weights = (scores_recency_weights(fit_games, scores_half_life)
                                      if scores_half_life is not None else None)
                    scores_model = calibrate_scores_level(
                        _model(fit_scores_ratings(
                            fit_games, ridge_lambda=10.0, weights=scores_weights)),
                        train_games)
                    weight = 0.5
                    if (early_weight is not None and predicting is not None
                            and predicting[1] <= NCAAF_EARLY_WEEK):
                        weight = early_weight
                    return BlendedGameModel(epa_model, scores_model, weight, sim)

                return factory

            variants.update({
                "blend-level2-sp6": ("games", blend_sp(6)),
                "blend-level2-sp12": ("games", blend_sp(12)),
                "blend-level2-sp24": ("games", blend_sp(24)),
                "blend-level2-sp12-scrim": ("games", blend_sp(12, scrimmage=True)),
                "blend-level2-sp12-scale": ("games", college_scaled(blend_sp(12))),
                "blend-level2-sp12-scrim-scale": (
                    "games", college_scaled(blend_sp(12, scrimmage=True))),
                "blend-level2-sp12-scrim-pace": ("games", blend_sp(12, scrimmage=True, pace=True)),
                # Over the promoted scale: the K=24 prior, pace, the phase-
                # specific scale, and a college bye bonus.
                "blend-level2-sp24-scale": ("games", college_scaled(blend_sp(24))),
                "blend-level2-sp12-pace-scale": ("games", college_scaled(blend_sp(12, pace=True))),
                "blend-level2-sp12-scale-phase": (
                    "games", college_scaled(blend_sp(12), by_phase=True)),
                "blend-level2-sp12-scale-rest": (
                    "games", college_rested(college_scaled(blend_sp(12)), 1.0)),
                # The early-season blend weight over the promoted phase scale:
                # through week 4 the EPA half has no prior and the scores half
                # has SP+; lean on the one that knows the roster.
                "blend-level2-sp12-scale-phase-early30": (
                    "games", college_scaled(blend_sp(12, early_weight=0.3), by_phase=True)),
                "blend-level2-sp12-scale-phase-early40": (
                    "games", college_scaled(blend_sp(12, early_weight=0.4), by_phase=True)),
                # The college QB term over the promoted phase scale: the
                # passer decomposed out of the EPA half (the cfbfastR passer
                # ids scripts/attach_ncaaf_passers.py joins on), at three QB
                # ridges — the NFL's 300 and either side of it.
                "blend-level2-sp12-scale-phase-qb75": (
                    "games", college_scaled(blend_sp(12, qb_lambda=75.0), by_phase=True)),
                "blend-level2-sp12-scale-phase-qb100": (
                    "games", college_scaled(blend_sp(12, qb_lambda=100.0), by_phase=True)),
                "blend-level2-sp12-scale-phase-qb150": (
                    "games", college_scaled(blend_sp(12, qb_lambda=150.0), by_phase=True)),
                "blend-level2-sp12-scale-phase-qb300": (
                    "games", college_scaled(blend_sp(12, qb_lambda=300.0), by_phase=True)),
                "blend-level2-sp12-scale-phase-qb600": (
                    "games", college_scaled(blend_sp(12, qb_lambda=600.0), by_phase=True)),
                # The EPA half's own prior and recency over the promoted
                # phase scale: the SP+ prior as week-0 pseudo-cells (the
                # scores half has had it since the sp12 round; the EPA half
                # opens every season blind), and a recency decay on the
                # cells (the promoted EPA half weighs a four-season window
                # flat).
                "blend-level2-sp12-scale-phase-epaprior6": (
                    "games", college_scaled(blend_sp(12, epa_prior_k=6), by_phase=True)),
                "blend-level2-sp12-scale-phase-epaprior12": (
                    "games", college_scaled(blend_sp(12, epa_prior_k=12), by_phase=True)),
                "blend-level2-sp12-scale-phase-epahl17": (
                    "games", college_scaled(blend_sp(12, epa_half_life=17.0), by_phase=True)),
                "blend-level2-sp12-scale-phase-epahl34": (
                    "games", college_scaled(blend_sp(12, epa_half_life=34.0), by_phase=True)),
                "blend-level2-sp12-scale-phase-epahl51": (
                    "games", college_scaled(blend_sp(12, epa_half_life=51.0), by_phase=True)),
                "blend-level2-sp12-scale-phase-epaprior12-epahl34": (
                    "games", college_scaled(blend_sp(12, epa_prior_k=12, epa_half_life=34.0),
                                            by_phase=True)),
                # The recency sweep's short end, and recency with the QB term.
                "blend-level2-sp12-scale-phase-epahl8": (
                    "games", college_scaled(blend_sp(12, epa_half_life=8.0), by_phase=True)),
                "blend-level2-sp12-scale-phase-epahl12": (
                    "games", college_scaled(blend_sp(12, epa_half_life=12.0), by_phase=True)),
                "blend-level2-sp12-scale-phase-qb75-epahl17": (
                    "games", college_scaled(blend_sp(12, qb_lambda=75.0, epa_half_life=17.0),
                                            by_phase=True)),
                "blend-level2-sp12-scale-phase-qb150-epahl17": (
                    "games", college_scaled(blend_sp(12, qb_lambda=150.0, epa_half_life=17.0),
                                            by_phase=True)),
                "blend-level2-sp12-scale-phase-qb150-epahl12": (
                    "games", college_scaled(blend_sp(12, qb_lambda=150.0, epa_half_life=12.0),
                                            by_phase=True)),
                "blend-level2-sp12-scale-phase-qb300-epahl17": (
                    "games", college_scaled(blend_sp(12, qb_lambda=300.0, epa_half_life=17.0),
                                            by_phase=True)),
                "blend-level2-sp12-scale-phase-epahl4": (
                    "games", college_scaled(blend_sp(12, epa_half_life=4.0), by_phase=True)),
                "blend-level2-sp12-scale-phase-epahl6": (
                    "games", college_scaled(blend_sp(12, epa_half_life=6.0), by_phase=True)),
                "blend-level2-sp12-scale-phase-qb75-epahl8": (
                    "games", college_scaled(blend_sp(12, qb_lambda=75.0, epa_half_life=8.0),
                                            by_phase=True)),
                "blend-level2-sp12-scale-phase-qb100-epahl8": (
                    "games", college_scaled(blend_sp(12, qb_lambda=100.0, epa_half_life=8.0),
                                            by_phase=True)),
                "blend-level2-sp12-scale-phase-qb150-epahl8": (
                    "games", college_scaled(blend_sp(12, qb_lambda=150.0, epa_half_life=8.0),
                                            by_phase=True)),
                # The unscaled cores the residual bank is rebuilt from when
                # one of these is promoted.
                "blend-level2-sp12-epahl17": ("games", blend_sp(12, epa_half_life=17.0)),
                "blend-level2-sp12-epahl6": ("games", blend_sp(12, epa_half_life=6.0)),
                "blend-level2-sp12-epahl8": ("games", blend_sp(12, epa_half_life=8.0)),
                "blend-level2-sp12-qb150-epahl17": (
                    "games", blend_sp(12, qb_lambda=150.0, epa_half_life=17.0)),
                "blend-level2-sp12-qb100-epahl8": (
                    "games", blend_sp(12, qb_lambda=100.0, epa_half_life=8.0)),
                "blend-level2-sp12-qb150-epahl8": (
                    "games", blend_sp(12, qb_lambda=150.0, epa_half_life=8.0)),
                # The promoted college chain after the college round: the
                # six-week half-life on the EPA half, the bank rebuilt on its
                # core (blend-level2-sp12-epahl6), the phase scale on that
                # bank. The recency round's candidates below were scored
                # against it.
                "live-ncaaf-promoted-hl6": (
                    "games", college_scaled(blend_sp(12, epa_half_life=6.0), by_phase=True)),
                # The promoted college chain after the recency round: the
                # six-week offseason gap in the EPA half's key, a 34-week
                # half-life on the scores half and SP+ special teams in the
                # prior, the bank rebuilt on the combined core
                # (blend-level2-sp12-hl6-gap6-st-shl34). The home-margin
                # round's candidates were scored against it.
                "live-ncaaf-promoted-noshift": (
                    "games", college_scaled(
                        blend_sp(12, epa_half_life=6.0, epa_offseason_weeks=6.0, st_prior=True,
                                 scores_half_life=34.0),
                        by_phase=True)),
                # The promoted college chain after the home-margin round:
                # the same, with the scale's home-margin intercept kept and
                # the sim at the dispersion measured on the shifted chain
                # (NCAAF_SD_MARGIN / NCAAF_SD_TOTAL) — what the live runner
                # prices.
                "live-ncaaf-promoted": (
                    "games", college_scaled(
                        blend_sp(12, epa_half_life=6.0, epa_offseason_weeks=6.0, st_prior=True,
                                 scores_half_life=34.0),
                        by_phase=True, shift=True)),
                # Over the promoted chain: the offseason gap in the EPA
                # half's recency key, and SP+ special teams in the prior.
                "live-ncaaf-promoted-gap6": (
                    "games", college_scaled(
                        blend_sp(12, epa_half_life=6.0, epa_offseason_weeks=6.0), by_phase=True)),
                "live-ncaaf-promoted-gap12": (
                    "games", college_scaled(
                        blend_sp(12, epa_half_life=6.0, epa_offseason_weeks=12.0), by_phase=True)),
                "live-ncaaf-promoted-st": (
                    "games", college_scaled(blend_sp(12, epa_half_life=6.0, st_prior=True),
                                            by_phase=True)),
                "live-ncaaf-promoted-gap6-st": (
                    "games", college_scaled(
                        blend_sp(12, epa_half_life=6.0, epa_offseason_weeks=6.0, st_prior=True),
                        by_phase=True)),
                "live-ncaaf-promoted-gap6-st-shl17": (
                    "games", college_scaled(
                        blend_sp(12, epa_half_life=6.0, epa_offseason_weeks=6.0, st_prior=True,
                                 scores_half_life=17.0),
                        by_phase=True)),
                "live-ncaaf-promoted-gap6-st-shl34": (
                    "games", college_scaled(
                        blend_sp(12, epa_half_life=6.0, epa_offseason_weeks=6.0, st_prior=True,
                                 scores_half_life=34.0),
                        by_phase=True)),
                # The unscaled core of the combination, for the bank rebuild.
                "blend-level2-sp12-hl6-gap6-st-shl34": (
                    "games", blend_sp(12, epa_half_life=6.0, epa_offseason_weeks=6.0,
                                      st_prior=True, scores_half_life=34.0)),
                # The home-margin shift: the scale keeps its intercept on
                # home-and-away games (the college slope of 1.35 fitted
                # without it inflates the home edge by two points).
                "live-ncaaf-promoted-shift": (
                    "games", college_scaled(
                        blend_sp(12, epa_half_life=6.0, epa_offseason_weeks=6.0, st_prior=True,
                                 scores_half_life=34.0),
                        by_phase=True, shift=True)),
                "live-ncaaf-promoted-shift-sd16.2-16.2": (
                    "games", college_scaled(
                        blend_sp(12, epa_half_life=6.0, epa_offseason_weeks=6.0, st_prior=True,
                                 scores_half_life=34.0),
                        by_phase=True, shift=True, sds=(16.2, 16.2))),
                "live-ncaaf-promoted-shift-sd16.7-16.5": (
                    "games", college_scaled(
                        blend_sp(12, epa_half_life=6.0, epa_offseason_weeks=6.0, st_prior=True,
                                 scores_half_life=34.0),
                        by_phase=True, shift=True, sds=(16.7, 16.5))),
                # The dispersion round: the promoted chain priced through a
                # sim whose sds are the chain's own walk-forward residual
                # sds (2022–25: 16.2 / 16.2) instead of Round 3's, measured
                # on the flat ridge-10 fit (18.2 / 16.7).
                "live-ncaaf-promoted-sd16.2-16.2": (
                    "games", college_scaled(
                        blend_sp(12, epa_half_life=6.0, epa_offseason_weeks=6.0, st_prior=True,
                                 scores_half_life=34.0),
                        by_phase=True, sds=(16.2, 16.2))),
                "live-ncaaf-promoted-sd16.7-16.5": (
                    "games", college_scaled(
                        blend_sp(12, epa_half_life=6.0, epa_offseason_weeks=6.0, st_prior=True,
                                 scores_half_life=34.0),
                        by_phase=True, sds=(16.7, 16.5))),
                "live-ncaaf-promoted-sd17.2-16.7": (
                    "games", college_scaled(
                        blend_sp(12, epa_half_life=6.0, epa_offseason_weeks=6.0, st_prior=True,
                                 scores_half_life=34.0),
                        by_phase=True, sds=(17.2, 16.7))),
                # Recency on the scores half too (it has been flat since
                # Round 1; the prior's pseudo-games count as current).
                "live-ncaaf-promoted-shl8": (
                    "games", college_scaled(
                        blend_sp(12, epa_half_life=6.0, scores_half_life=8.0), by_phase=True)),
                "live-ncaaf-promoted-shl17": (
                    "games", college_scaled(
                        blend_sp(12, epa_half_life=6.0, scores_half_life=17.0), by_phase=True)),
                "live-ncaaf-promoted-shl34": (
                    "games", college_scaled(
                        blend_sp(12, epa_half_life=6.0, scores_half_life=34.0), by_phase=True)),
                "blend-level2-sp12-scale-phase-early60": (
                    "games", college_scaled(blend_sp(12, early_weight=0.6), by_phase=True)),
            })

    return variants


# Summer-league calibration: (sd_margin, sd_total) for the sim, the margin
# sigma for the market probit link, and the live ridge default (the promotion
# bar each league's lab measures against).
class BonusAdjustedModel:
    """Situational point bonuses over any expected-points model, resimulated.

    The generic sibling of the NFL rest/weather wrappers for models whose
    ``project`` takes no bonus kwargs (the scores model): ask the inner model
    for its expected points, apply ``bonus_fn(home, away, kickoff) →
    (home_bonus, away_bonus)``, and simulate once with the shared config.
    """

    def __init__(
        self,
        inner: object,  # duck-typed: expected_points(home, away, neutral_site=...)
        bonus_fn: Callable[[str, str, object], tuple[float, float]],
        sim: SimConfig,
    ) -> None:
        self.inner = inner
        self.bonus_fn = bonus_fn
        self.sim = sim

    def project(
        self,
        home_team: str,
        away_team: str,
        *,
        neutral_site: bool = False,
        rng: np.random.Generator | None = None,
        kickoff: object = None,
    ) -> object:
        from velocity.models.game_nfl import GameProjection
        from velocity.models.simulate import simulate_game
        from velocity.util.seed import make_rng

        mu_home, mu_away = self.inner.expected_points(  # type: ignore[attr-defined]
            home_team, away_team, neutral_site=neutral_site
        )
        home_bonus, away_bonus = self.bonus_fn(home_team, away_team, kickoff)
        mu_home += home_bonus
        mu_away += away_bonus
        rng = rng if rng is not None else make_rng()
        sim = simulate_game(mu_margin=mu_home - mu_away, mu_total=mu_home + mu_away,
                            rng=rng, config=self.sim)
        return GameProjection(home_team=home_team, away_team=away_team,
                              mu_home=mu_home, mu_away=mu_away, sim=sim)


INSEASON_CALIBRATION: dict[str, dict[str, float]] = {
    "mlb": {"sd_margin": 3.2, "sd_total": 4.6, "sigma": 3.2, "ridge": 100.0},
    # WNBA: walk-forward residuals of the promoted pace×efficiency fit over
    # 718 games — margin 12.93 (the shipped 12.5 was nearly right), total
    # 18.15 (the shipped 15.0 was a fifth too narrow). sd_total here is the
    # pre-overtime number that finishes on 18.15 (docs/BUILD_WNBA_SIM.md).
    "wnba": {"sd_margin": 12.93, "sd_total": 17.6, "sigma": 12.93, "ridge": 10.0},
    # 2026 walk-forward, pace×efficiency (docs/BUILD_NCAAB.md N2): residual
    # sds from the calibration diagnostic. Ridge is *near-zero* — 360
    # conference-clustered teams leave the fit compressed at football-scale
    # λ (calibration slope act≈2.6·pred at λ=50, monotone down to 1.03 at
    # λ=0.1) because shrinkage flattens cross-cluster strength differences;
    # what predictions use is each team's offense+defense sum, which stays
    # identified as λ→0. 0.5 balances that against early-season stability.
    "ncaab": {"sd_margin": 13.0, "sd_total": 18.5, "sigma": 13.0, "ridge": 0.5},
    # NHL: empirical outcome sds from the banked 2023–25 games (goals are
    # MLB-like low-scoring counts); ridge like MLB's heavy shrinkage — the
    # true team spread in goals/game is small. Walk-forward gated below
    # before anything ships (docs/BUILD_NHL.md).
    "nhl": {"sd_margin": 2.6, "sd_total": 2.3, "sigma": 2.6, "ridge": 25.0},
}


def inseason_variants(
    league: str, n_sims: int
) -> dict[str, tuple[str, VariantFactory]]:
    """The MLB/WNBA benchmark slates — scores-fit families, games-only.

    The live summer defaults were picked by hand when the leagues shipped as
    content surfaces; this is where they earn (or lose) their numbers. The
    ``scores`` entry runs the live ridge and is the promotion bar. Recency
    half-lives are in the frames' *week-bucket* steps (each bucket ≈ 15 days),
    so ``recency-2`` halves in about a month — these leagues play daily, and
    form questions live on that clock. Deeper decompositions (starting
    pitcher, pace×efficiency) join as variants in their own rounds.
    """
    from velocity.features.scores import scores_recency_weights

    cal = INSEASON_CALIBRATION[league]
    sim = SimConfig(sd_margin=cal["sd_margin"], sd_total=cal["sd_total"],
                    n_sims=n_sims)
    live_ridge = cal["ridge"]

    def _model(ratings: object) -> ScoresGameModel:
        return ScoresGameModel(ratings, ScoresModelConfig(sim=sim))  # type: ignore[arg-type]

    def ridge(lam: float) -> VariantFactory:
        def factory(train: pd.DataFrame) -> ScoresGameModel:
            return _model(fit_scores_ratings(train, ridge_lambda=lam))

        return factory

    def recency(half_life: float, lam: float) -> VariantFactory:
        def factory(train: pd.DataFrame) -> ScoresGameModel:
            return _model(fit_scores_ratings(
                train, ridge_lambda=lam,
                weights=scores_recency_weights(train, half_life),
            ))

        return factory

    sweep = {"mlb": (2.0, 10.0, 25.0, 50.0, 100.0, 200.0, 400.0),
             "wnba": (3.0, 25.0, 50.0),
             # NHL round 1: bracket the MLB-like heavy-shrinkage hypothesis.
             "nhl": (10.0, 25.0, 50.0, 100.0, 200.0, 400.0)}[league]
    variants: dict[str, tuple[str, VariantFactory]] = {
        "scores": ("games", ridge(live_ridge)),  # the live default — the bar
    }
    variants.update({
        f"ridge-{lam:g}": ("games", ridge(lam)) for lam in sweep
    })
    # WNBA's ladder extends past 8: round 1 came back monotone through 8, so
    # the sweep brackets the bottom (as half-life → ∞ this is the flat fit).
    rec_sweep = {"mlb": (2.0, 4.0, 8.0),
                 "wnba": (2.0, 4.0, 8.0, 12.0, 16.0, 24.0),
                 "nhl": (2.0, 4.0, 8.0)}[league]
    variants.update({
        f"recency-{hl:g}": ("games", recency(hl, live_ridge)) for hl in rec_sweep
    })

    def park(shrink_games: float = 40.0) -> VariantFactory:
        # Park factors, self-calibrated from the training slice only: each
        # home venue's mean total residual vs the league, shrunk toward zero,
        # split half per side. No hand-curated table to be wrong about.
        def factory(train: pd.DataFrame) -> BonusAdjustedModel:
            inner = _model(fit_scores_ratings(train, ridge_lambda=live_ridge))
            played = train.dropna(subset=["home_score", "away_score"])
            totals = played["home_score"] + played["away_score"]
            league = float(totals.mean())
            bonus: dict[str, float] = {}
            for team, group in totals.groupby(played["home_team"]):
                n = len(group)
                bonus[str(team)] = (
                    (float(group.mean()) - league) * n / (n + shrink_games) / 2.0
                )

            def park_bonus(home: str, away: str, kickoff: object) -> tuple[float, float]:
                half = bonus.get(home, 0.0)
                return half, half  # symmetric: parks move totals, not margins

            return BonusAdjustedModel(inner, park_bonus, sim)

        return factory

    def b2b(penalty: float) -> VariantFactory:
        # WNBA schedule congestion: a team playing the second night of a
        # back-to-back scores fewer points. Kickoffs are public schedule
        # knowledge (the rest-spot argument), read from the training slice
        # plus the predicted game's own kickoff via the engine's pass-through.
        def factory(train: pd.DataFrame) -> BonusAdjustedModel:
            inner = _model(fit_scores_ratings(train, ridge_lambda=live_ridge))
            sched = train.dropna(subset=["kickoff"])
            long = pd.concat([
                sched[["home_team", "kickoff"]].rename(columns={"home_team": "team"}),
                sched[["away_team", "kickoff"]].rename(columns={"away_team": "team"}),
            ], ignore_index=True)
            by_team = {
                str(team): pd.to_datetime(group["kickoff"]).sort_values().to_numpy()
                for team, group in long.groupby("team")
            }

            def tired(team: str, when: object) -> bool:
                played = by_team.get(team)
                if played is None or when is None or pd.isna(when):  # type: ignore[call-overload]
                    return False
                ts = pd.Timestamp(when).to_datetime64()  # type: ignore[arg-type]
                prior = played[played < ts]
                if len(prior) == 0:
                    return False
                rest_days = (ts - prior[-1]) / np.timedelta64(1, "D")
                return bool(rest_days <= 1.3)  # second night of a back-to-back

            def rest_bonus(home: str, away: str, kickoff: object) -> tuple[float, float]:
                return (
                    -penalty if tired(home, kickoff) else 0.0,
                    -penalty if tired(away, kickoff) else 0.0,
                )

            return BonusAdjustedModel(inner, rest_bonus, sim)

        return factory

    if league == "mlb":
        variants["park-fit"] = ("games", park())
    else:
        variants.update({
            "b2b-2": ("games", b2b(2.0)),
            "b2b-3.5": ("games", b2b(3.5)),
        })
    return variants


def mlb_starter_frame(games: pd.DataFrame, starters: pd.DataFrame) -> pd.DataFrame:
    """Games + banked starters → the frame the QB machinery fits as an SP model.

    Two rows per game — (batting team, pitching team, runs scored) — with
    ``passer_player_id`` set to the **opposing starter**, so
    :func:`~velocity.features.team.fit_qb_ratings` decomposes::

        runs = intercept + offense[bat] + bullpen_defense[field] + starter[SP]

    The starter dummy's coefficient is his runs-allowed impact (negative =
    good). Games missing a banked starter still contribute their team terms;
    the SP dummy simply doesn't fire.
    """
    st = starters.drop_duplicates(subset=["game_id", "side"]).pivot(
        index="game_id", columns="side", values="starter_id"
    )
    merged = games.merge(
        st.rename(columns={"home": "_home_sp", "away": "_away_sp"}),
        left_on="game_id", right_index=True, how="left",
    )
    rows = []
    for g in merged.to_dict("records"):
        for bat, field, runs, sp in (
            (g["home_team"], g["away_team"], g["home_score"], g.get("_away_sp")),
            (g["away_team"], g["home_team"], g["away_score"], g.get("_home_sp")),
        ):
            rows.append({
                "game_id": g["game_id"], "season": g["season"], "week": g["week"],
                "posteam": bat, "defteam": field, "epa": runs,
                # Every row "is a pass": pass_rate = 1.0, so the starter
                # effect applies at full weight in matchup_delta.
                "play_type": "pass",
                "passer_player_id": None if pd.isna(sp) else str(sp),
            })
    return pd.DataFrame(rows)


class StarterAwareModel:
    """Price MLB matchups with the game's actual starters (probables) plugged in.

    ``lookup`` maps ``(home, away, kickoff)`` to the game's (home starter,
    away starter) ids — probables are public pregame knowledge, so reading the
    evaluated game's starters is the rest-spot argument, not leakage. A game
    outside the lookup prices starter-neutral (league-average SP).
    """

    def __init__(
        self,
        ratings: object,  # QBTeamRatings duck-typed via matchup_delta(off, def, qb_id)
        lookup: Mapping[tuple[str, str, object], tuple[str | None, str | None]],
        sim: SimConfig,
        *,
        hfa_points: float = 0.15,
    ) -> None:
        self.ratings = ratings
        self.lookup = lookup
        self.sim = sim
        self.hfa_points = hfa_points

    def project(
        self,
        home_team: str,
        away_team: str,
        *,
        neutral_site: bool = False,
        rng: np.random.Generator | None = None,
        kickoff: object = None,
    ) -> object:
        from velocity.models.game_nfl import GameProjection
        from velocity.models.simulate import simulate_game
        from velocity.util.seed import make_rng

        key = (home_team, away_team, None if kickoff is None else pd.Timestamp(kickoff))  # type: ignore[arg-type]
        home_sp, away_sp = self.lookup.get(key, (None, None))
        base = float(getattr(self.ratings, "league_epa", 4.4))
        # An unknown starter must price league-average. qb_id=None would fall
        # back to QBTeamRatings.starters — the starter this team most recently
        # FACED in training, a stale wrong guess — so pass the falsy sentinel
        # "" instead, which matchup_delta prices as exactly neutral.
        mu_home = base + self.ratings.matchup_delta(  # type: ignore[attr-defined]
            home_team, away_team, qb_id=away_sp or ""
        )
        mu_away = base + self.ratings.matchup_delta(  # type: ignore[attr-defined]
            away_team, home_team, qb_id=home_sp or ""
        )
        if not neutral_site:
            mu_home += self.hfa_points / 2.0
            mu_away -= self.hfa_points / 2.0
        rng = rng if rng is not None else make_rng()
        sim = simulate_game(mu_margin=mu_home - mu_away, mu_total=mu_home + mu_away,
                            rng=rng, config=self.sim)
        return GameProjection(home_team=home_team, away_team=away_team,
                              mu_home=mu_home, mu_away=mu_away, sim=sim)


def wnba_pace_frame(box: pd.DataFrame) -> pd.DataFrame:
    """wehoop team boxes → one possession estimate per game.

    ``poss ≈ FGA − ORB + TOV + 0.44·FTA`` (the standard estimator), averaged
    across the two team rows. Points come from the games frame itself, so
    this only carries ``game_id, poss``. Games without exactly one home and
    one away row (data quirks) contribute nothing rather than a guess.
    """
    b = box.assign(_poss=(
        pd.to_numeric(box["field_goals_attempted"], errors="coerce")
        - pd.to_numeric(box["offensive_rebounds"], errors="coerce")
        + pd.to_numeric(box["total_turnovers"], errors="coerce")
        + 0.44 * pd.to_numeric(box["free_throws_attempted"], errors="coerce")
    ))
    rows = []
    for game_id, g in b.groupby("game_id"):
        if sorted(g["team_home_away"].astype(str)) != ["away", "home"]:
            continue
        poss = float(g["_poss"].mean())
        if not poss > 0:
            continue
        rows.append({"game_id": str(game_id), "poss": poss})
    return pd.DataFrame(rows)


class PaceEfficiencyModel:
    """Total = pace × efficiency, fit separately and priced together.

    ``eff_model`` is a scores fit on points-per-100-possessions (opponent
    adjustment and HFA land in efficiency units); pace is the additive
    ``league + dev[home] + dev[away]`` possessions estimate. The
    reconstruction prices a fast-pace matchup's total above what either
    team's raw points average shows — the decomposition's whole point.
    """

    def __init__(
        self,
        eff_model: ScoresGameModel,
        pace_league: float,
        pace_dev: Mapping[str, float],
        sim: SimConfig,
    ) -> None:
        self.eff_model = eff_model
        self.pace_league = pace_league
        self.pace_dev = pace_dev
        self.sim = sim

    def project(
        self,
        home_team: str,
        away_team: str,
        *,
        neutral_site: bool = False,
        rng: np.random.Generator | None = None,
        kickoff: object = None,
    ) -> object:
        from velocity.models.game_nfl import GameProjection
        from velocity.models.simulate import simulate_game
        from velocity.util.seed import make_rng

        eff_home, eff_away = self.eff_model.expected_points(
            home_team, away_team, neutral_site=neutral_site
        )
        poss = (self.pace_league + self.pace_dev.get(home_team, 0.0)
                + self.pace_dev.get(away_team, 0.0))
        mu_home = poss * eff_home / 100.0
        mu_away = poss * eff_away / 100.0
        rng = rng if rng is not None else make_rng()
        sim = simulate_game(mu_margin=mu_home - mu_away, mu_total=mu_home + mu_away,
                            rng=rng, config=self.sim)
        return GameProjection(home_team=home_team, away_team=away_team,
                              mu_home=mu_home, mu_away=mu_away, sim=sim)


def fit_pace_efficiency(
    train: pd.DataFrame,
    pace: pd.DataFrame,
    sim: SimConfig,
    *,
    ridge_lambda: float,
    half_life: float | None,
    shrink_games: float = 10.0,
) -> PaceEfficiencyModel:
    """Fit the pace×efficiency model (shared by the lab and the live slate).

    Efficiency is the scores fit on points-per-100-possessions; pace
    deviations are shrunk per-team means, half a game's deviation per side.
    """
    from velocity.features.scores import fit_scores_ratings, scores_recency_weights

    merged = train.dropna(subset=["home_score", "away_score"]).merge(
        pace, on="game_id", how="inner"
    )
    eff = merged.assign(
        home_score=merged["home_score"] / merged["poss"] * 100.0,
        away_score=merged["away_score"] / merged["poss"] * 100.0,
    )
    weights = (scores_recency_weights(eff, half_life)
               if half_life is not None else None)
    eff_model = ScoresGameModel(
        fit_scores_ratings(eff, ridge_lambda=ridge_lambda, weights=weights),
        ScoresModelConfig(sim=sim),
    )
    league = float(merged["poss"].mean())
    long = pd.concat([
        merged[["home_team", "poss"]].rename(columns={"home_team": "team"}),
        merged[["away_team", "poss"]].rename(columns={"away_team": "team"}),
    ], ignore_index=True)
    dev: dict[str, float] = {}
    for team, grp in long.groupby("team"):
        n = len(grp)
        dev[str(team)] = float(
            (grp["poss"].mean() - league) / 2.0 * n / (n + shrink_games)
        )
    return PaceEfficiencyModel(eff_model, league, dev, sim)


def wnba_pace_variants(
    n_sims: int, box: pd.DataFrame
) -> dict[str, tuple[str, VariantFactory]]:
    """The pace×efficiency slate — the research list's WNBA headline."""
    cal = INSEASON_CALIBRATION["wnba"]
    sim = SimConfig(sd_margin=cal["sd_margin"], sd_total=cal["sd_total"],
                    n_sims=n_sims)
    pace = wnba_pace_frame(box)

    def variant(half_life: float | None) -> VariantFactory:
        def factory(train: pd.DataFrame) -> PaceEfficiencyModel:
            return fit_pace_efficiency(
                train, pace, sim, ridge_lambda=cal["ridge"], half_life=half_life
            )

        return factory

    return {
        "pace-eff-flat": ("games", variant(None)),
        "pace-eff-r8": ("games", variant(8.0)),
    }


def ncaab_variants(
    n_sims: int, box: pd.DataFrame, torvik: pd.DataFrame | None = None
) -> dict[str, tuple[str, VariantFactory]]:
    """The NCAAB pace×efficiency slate — ridge ladder + the Torvik prior.

    The league's structural problem is connectivity: 360+ teams in
    conference clusters compress the ridge fit's cross-cluster strength
    differences (walk-forward calibration slope ≈ 2.6·pred at football-scale
    λ=50), so the ladder sweeps *light* penalties. The prior variants append
    :func:`velocity.ingest.ncaab.torvik_pseudo_games` — last season's final
    Torvik ratings as K week-0 anchor games, decaying under the same recency
    weights as real games. The leak gate inside the helper keys on the
    training slice's own latest kickoff.
    """
    from velocity.ingest.ncaab import torvik_pseudo_games

    cal = INSEASON_CALIBRATION["ncaab"]
    sim = SimConfig(sd_margin=cal["sd_margin"], sd_total=cal["sd_total"],
                    n_sims=n_sims)
    pace = wnba_pace_frame(box)
    half_life = 6.0  # ≈90 days — the bias-free total from the calibration run

    def plain(lam: float, hl: float | None = half_life) -> VariantFactory:
        def factory(train: pd.DataFrame) -> PaceEfficiencyModel:
            return fit_pace_efficiency(
                train, pace, sim, ridge_lambda=lam, half_life=hl
            )

        return factory

    def prior(k: int, lam: float) -> VariantFactory:
        assert torvik is not None

        def factory(train: pd.DataFrame) -> PaceEfficiencyModel:
            teams = set(train["home_team"]) | set(train["away_team"])
            pseudo_games, pseudo_pace = torvik_pseudo_games(
                torvik, teams, cutoff=pd.to_datetime(train["kickoff"]).max(), k=k
            )
            return fit_pace_efficiency(
                pd.concat([train, pseudo_games], ignore_index=True),
                pd.concat([pace, pseudo_pace], ignore_index=True),
                sim, ridge_lambda=lam, half_life=half_life,
            )

        return factory

    variants: dict[str, tuple[str, VariantFactory]] = {
        "pace-eff": ("games", plain(cal["ridge"])),  # the promotion bar
    }
    variants.update({
        f"ridge-{lam:g}": ("games", plain(lam)) for lam in (0.1, 0.25, 1.0, 2.0)
    })
    variants["pace-eff-flat"] = ("games", plain(cal["ridge"], None))
    variants["recency-12"] = ("games", plain(cal["ridge"], 12.0))
    if torvik is not None:
        variants.update({
            f"prior-k{k:g}": ("games", prior(k, cal["ridge"]))
            for k in (3, 6, 12, 24)
        })
    return variants


def mlb_fip_priors(
    starters: pd.DataFrame, shrink_innings: float
) -> dict[str, float]:
    """Per-starter runs-per-start delta vs league from defense-independent stats.

    FIP's numerator (13·HR + 3·(BB+HBP) − 2·K per inning) isolates what the
    pitcher controls; K/BB rates stabilize in weeks where runs allowed takes a
    season. The per-9 delta vs the slice's league rate is scaled by the
    starter's own innings share of a game (he only affects the frame while he
    pitches) and shrunk toward zero by ``outs/(outs + 3·shrink_innings)``.
    Units are runs per start — the same as the fitted starter dummy.
    """
    s = starters.dropna(subset=["outs"])
    s = s[s["outs"] > 0]
    if s.empty:
        return {}
    agg = s.groupby("starter_id").agg(
        outs=("outs", "sum"), k=("k", "sum"), bb=("bb", "sum"),
        hbp=("hbp", "sum"), hr=("hr", "sum"), starts=("game_id", "count"),
    )
    num = 13.0 * agg["hr"] + 3.0 * (agg["bb"] + agg["hbp"]) - 2.0 * agg["k"]
    # num/IP is FIP minus its constant — already on the runs-per-9 (ERA)
    # scale by construction, so the constant cancels in the league delta.
    fip9 = num / (agg["outs"] / 3.0)
    league = float(num.sum() / (agg["outs"].sum() / 3.0))
    share = (agg["outs"] / agg["starts"]) / 27.0  # avg innings per start / 9
    shrink = agg["outs"] / (agg["outs"] + 3.0 * shrink_innings)
    delta = (fip9 - league) * share * shrink
    return {str(idx): float(v) for idx, v in delta.items() if pd.notna(v)}


def mlb_sp_variants(
    n_sims: int, games: pd.DataFrame, starters: pd.DataFrame,
    league: str = "mlb", hfa_points: float = 0.15,
) -> dict[str, tuple[str, VariantFactory]]:
    """The starter-decomposition slate — the market's dominant MLB factor.

    Each variant fits :func:`~velocity.features.team.fit_qb_ratings` on the
    games-long frame (:func:`mlb_starter_frame` of the training slice) and
    prices through :class:`StarterAwareModel` with the evaluated game's
    starters. ``min_dropbacks`` is starts here: a 6-start sample prices as
    his shrunk self, fewer prices league-average.

    ``league`` generalizes the machinery to any starter-shaped sport: the
    NHL's starting goalie is the same decomposition (goals = offense +
    team defense + goalie), keyed by its own calibration and home edge.
    """
    from velocity.features.team import fit_qb_ratings

    cal = INSEASON_CALIBRATION[league]
    sim = SimConfig(sd_margin=cal["sd_margin"], sd_total=cal["sd_total"],
                    n_sims=n_sims)
    kick = pd.to_datetime(games["kickoff"])
    lookup_frame = games.merge(
        starters.drop_duplicates(subset=["game_id", "side"]).pivot(
            index="game_id", columns="side", values="starter_id"
        ).rename(columns={"home": "_hsp", "away": "_asp"}),
        left_on="game_id", right_index=True, how="left",
    )
    lookup: dict[tuple[str, str, object], tuple[str | None, str | None]] = {
        (str(g["home_team"]), str(g["away_team"]), pd.Timestamp(k)):
            (None if pd.isna(g.get("_hsp")) else str(g["_hsp"]),
             None if pd.isna(g.get("_asp")) else str(g["_asp"]))
        for g, k in zip(lookup_frame.to_dict("records"), kick, strict=True)
    }

    def sp(qb_lambda: float) -> VariantFactory:
        def factory(train: pd.DataFrame) -> StarterAwareModel:
            frame = mlb_starter_frame(train, starters)
            ratings = fit_qb_ratings(
                frame, ridge_lambda=cal["ridge"], qb_lambda=qb_lambda,
                min_dropbacks=6,
            )
            return StarterAwareModel(ratings, lookup, sim, hfa_points=hfa_points)

        return factory

    def sp_fip(shrink_innings: float, qb_lambda: float = 160.0) -> VariantFactory:
        # Round 3: shrink the starter dummy toward a FIP-quality prior
        # instead of zero. K/BB/HBP/HR stabilize far faster than runs
        # allowed, so the prior carries skill signal the dummy can't see in
        # a small sample: subtract each row's (train-slice) prior from the
        # runs before the fit, so the dummy learns the *residual*, then add
        # the prior back into the ratings — pricing = prior + deviation, and
        # a below-floor starter prices at his prior instead of league-average.
        def factory(train: pd.DataFrame) -> StarterAwareModel:
            train_ids = set(train["game_id"].astype(str))
            slice_ = starters[starters["game_id"].astype(str).isin(train_ids)]
            priors = mlb_fip_priors(slice_, shrink_innings)
            frame = mlb_starter_frame(train, starters)
            frame = frame.assign(
                epa=frame["epa"]
                - frame["passer_player_id"].map(priors).fillna(0.0)
            )
            ratings = fit_qb_ratings(
                frame, ridge_lambda=cal["ridge"], qb_lambda=qb_lambda,
                min_dropbacks=6,
            )
            for sp_id, prior in priors.items():
                ratings.qb[sp_id] = ratings.qb.get(sp_id, 0.0) + prior
            return StarterAwareModel(ratings, lookup, sim, hfa_points=hfa_points)

        return factory

    # q80/q160 extend the sweep after q5→q40 came back monotone (as
    # qb_lambda → ∞ this collapses to the frame's team-only fit).
    variants: dict[str, tuple[str, VariantFactory]] = {
        "sp-q5": ("games", sp(5.0)),
        "sp-q15": ("games", sp(15.0)),
        "sp-q40": ("games", sp(40.0)),
        "sp-q80": ("games", sp(80.0)),
        "sp-q160": ("games", sp(160.0)),
        "sp-q320": ("games", sp(320.0)),
        "sp-q640": ("games", sp(640.0)),
    }
    # The FIP prior needs the pitching outcome columns; a goalie starters
    # frame (shots against/saves) has no analog, so those variants only
    # join when the columns exist.
    if {"k", "bb", "hbp", "hr", "outs"}.issubset(starters.columns):
        variants.update({
            "sp-fip30": ("games", sp_fip(30.0)),
            "sp-fip60": ("games", sp_fip(60.0)),
            "sp-fip120": ("games", sp_fip(120.0)),
        })
    return variants


class ScheduleStarterModel:
    """The NFL model priced with each game's announced starters.

    The QB decomposition was promoted with the starter detected as "the
    primary passer in the team's latest training game" — wrong in Week 1,
    wrong the week of every injury, and the live runner corrects it from the
    depth chart. nflverse's schedule carries ``home_qb_id``/``away_qb_id`` for
    every game back to 1999 (docs/PROJECTION_AUDIT.md §2.4), which is the
    announced starter the depth chart would have named. Keyed by the two
    teams and the kickoff date, like the rest wrapper; a game the schedule
    does not name falls back to the ratings' own detection.
    """

    def __init__(self, inner: NFLGameModel, schedule: pd.DataFrame) -> None:
        self.inner = inner
        self._starters: dict[tuple[str, str, pd.Timestamp], tuple[str | None, str | None]] = {}
        if {"home_qb_id", "away_qb_id"} <= set(schedule.columns):
            keyed = schedule.dropna(subset=["kickoff"])
            dates = pd.to_datetime(keyed["kickoff"]).dt.normalize()
            for home, away, date, hq, aq in zip(
                keyed["home_team"].astype(str), keyed["away_team"].astype(str), dates,
                keyed["home_qb_id"], keyed["away_qb_id"], strict=True,
            ):
                self._starters[(home, away, date)] = (
                    None if pd.isna(hq) else str(hq), None if pd.isna(aq) else str(aq))

    def expected_points(
        self, home_team: str, away_team: str, *, neutral_site: bool = False,
        home_bonus: float = 0.0, away_bonus: float = 0.0,
    ) -> tuple[float, float]:
        return self.inner.expected_points(
            home_team, away_team, neutral_site=neutral_site,
            home_bonus=home_bonus, away_bonus=away_bonus)

    def project(
        self,
        home_team: str,
        away_team: str,
        *,
        neutral_site: bool = False,
        rng: object = None,
        kickoff: object = None,
        home_bonus: float = 0.0,
        away_bonus: float = 0.0,
    ) -> object:
        home_qb = away_qb = None
        if kickoff is not None and not pd.isna(kickoff):  # type: ignore[call-overload]
            date = pd.Timestamp(kickoff).normalize()  # type: ignore[arg-type]
            home_qb, away_qb = self._starters.get((home_team, away_team, date), (None, None))
        return self.inner.project(
            home_team, away_team, neutral_site=neutral_site,
            rng=rng,  # type: ignore[arg-type]
            home_bonus=home_bonus, away_bonus=away_bonus,
            home_qb=home_qb, away_qb=away_qb,
        )


class DivisionalModel:
    """A situational wrapper: less home field in a divisional game.

    The schedule's ``div_game`` flag (docs/PROJECTION_AUDIT.md §2.4) marks 36%
    of games, and the committed frame's home margin is 1.9 in them against
    2.3 elsewhere — familiarity and short travel take a fraction of a point
    off the edge, as the literature has long said. ``discount`` points come
    off the home side and go onto the away side in halves, so the total is
    untouched. Keyed by the two teams and the kickoff date, like the rest
    wrapper; the kickoff is forwarded to an inner that takes one.
    """

    def __init__(self, inner: object, schedule: pd.DataFrame, discount: float) -> None:
        import inspect

        self.inner = inner
        self.discount = float(discount)
        self._div: set[tuple[str, str, pd.Timestamp]] = set()
        if "div_game" in schedule.columns:
            keyed = schedule.dropna(subset=["kickoff"])
            flags = pd.to_numeric(keyed["div_game"], errors="coerce").fillna(0.0)
            dates = pd.to_datetime(keyed["kickoff"]).dt.normalize()
            for home, away, date, flag in zip(
                keyed["home_team"].astype(str), keyed["away_team"].astype(str), dates, flags,
                strict=True,
            ):
                if flag > 0:
                    self._div.add((home, away, date))
        try:
            self._inner_takes_kickoff = "kickoff" in inspect.signature(
                inner.project).parameters  # type: ignore[attr-defined]
        except (TypeError, ValueError):  # pragma: no cover - exotic callables
            self._inner_takes_kickoff = False

    def project(
        self,
        home_team: str,
        away_team: str,
        *,
        neutral_site: bool = False,
        rng: object = None,
        kickoff: object = None,
        home_bonus: float = 0.0,
        away_bonus: float = 0.0,
    ) -> object:
        shift = 0.0
        if kickoff is not None and not pd.isna(kickoff) and not neutral_site:  # type: ignore[call-overload]
            date = pd.Timestamp(kickoff).normalize()  # type: ignore[arg-type]
            if (home_team, away_team, date) in self._div:
                shift = self.discount / 2.0
        kwargs: dict[str, object] = {"kickoff": kickoff} if self._inner_takes_kickoff else {}
        return self.inner.project(  # type: ignore[attr-defined]
            home_team, away_team, neutral_site=neutral_site, rng=rng,
            home_bonus=home_bonus - shift, away_bonus=away_bonus + shift, **kwargs,
        )


class InjuryBurdenModel:
    """A situational wrapper: points off a team for the production it has out.

    ``burden`` is :func:`velocity.features.injuries.burden_by_team_week`'s
    frame; the game's (season, week) comes from the schedule by the two
    teams and the kickoff date, like the other wrappers. A team-week with
    no row costs nothing. The week's official designations are public
    before kickoff, so this is not leakage; the usage shares behind them
    are the season to date or the previous season (never the week itself).
    """

    def __init__(
        self, inner: object, schedule: pd.DataFrame, burden: pd.DataFrame,
        points_per_unit: float, *, fixed_season_week: tuple[int, int] | None = None,
    ) -> None:
        import inspect

        self.inner = inner
        self.points_per_unit = float(points_per_unit)
        # A live slate is one week: the runner names it outright rather than
        # keying every board game through a schedule it may not be on.
        self.fixed_season_week = fixed_season_week
        keyed = schedule.dropna(subset=["kickoff"])
        dates = pd.to_datetime(keyed["kickoff"]).dt.normalize()
        self._game_week: dict[tuple[str, str, pd.Timestamp], tuple[int, int]] = {
            (str(h), str(a), d): (int(s), int(w))
            for h, a, d, s, w in zip(keyed["home_team"], keyed["away_team"], dates,
                                     keyed["season"], keyed["week"], strict=True)
        }
        self._burden: dict[tuple[int, int, str], float] = {
            (int(s), int(w), str(t)): float(b)
            for s, w, t, b in zip(burden["season"], burden["week"], burden["team"],
                                  burden["burden"], strict=True)
        } if not burden.empty else {}
        try:
            self._inner_takes_kickoff = "kickoff" in inspect.signature(
                inner.project).parameters  # type: ignore[attr-defined]
        except (TypeError, ValueError):  # pragma: no cover - exotic callables
            self._inner_takes_kickoff = False

    def _cost(self, team: str, season_week: tuple[int, int] | None) -> float:
        if season_week is None:
            return 0.0
        return -self.points_per_unit * self._burden.get((*season_week, team), 0.0)

    def project(
        self,
        home_team: str,
        away_team: str,
        *,
        neutral_site: bool = False,
        rng: object = None,
        kickoff: object = None,
        home_bonus: float = 0.0,
        away_bonus: float = 0.0,
    ) -> object:
        season_week = self.fixed_season_week
        if season_week is None and kickoff is not None and not pd.isna(kickoff):  # type: ignore[call-overload]
            date = pd.Timestamp(kickoff).normalize()  # type: ignore[arg-type]
            season_week = self._game_week.get((home_team, away_team, date))
        kwargs: dict[str, object] = {"kickoff": kickoff} if self._inner_takes_kickoff else {}
        return self.inner.project(  # type: ignore[attr-defined]
            home_team, away_team, neutral_site=neutral_site, rng=rng,
            home_bonus=home_bonus + self._cost(home_team, season_week),
            away_bonus=away_bonus + self._cost(away_team, season_week), **kwargs,
        )


class RestAdjustedModel:
    """A situational wrapper: rest-spot point bonuses on top of any NFL model.

    Rest days are pure schedule knowledge (known before the season), so
    computing them from the full schedule is not leakage. A team coming off a
    bye (rest ≥ ``bye_days``) gets ``bye_points``; a team on a short week
    (rest ≤ ``short_days``) gets ``-short_points``. Teams with no prior game
    (week 1) get no adjustment.

    Two guards keep "prior game" honest. A gap longer than ``max_rest_days``
    is an offseason, not a bye — without the cap every Week-1 team collected
    the bye bonus off last season's finale, +2 points on every opening-week
    total. And a schedule row inside ``same_game_hours`` of the kickoff is the
    game itself, not a prior one: the league schedule lists kickoffs in local
    time while the board lists them in UTC, so the same game can sit a few
    hours "earlier" in the frame and read as a short week.
    """

    def __init__(
        self,
        inner: NFLGameModel,
        schedule: pd.DataFrame,
        *,
        bye_points: float = 1.0,
        short_points: float = 1.0,
        bye_days: int = 12,
        short_days: int = 5,
        max_rest_days: int = 30,
        same_game_hours: float = 24.0,
    ) -> None:
        import inspect

        self.inner = inner
        self.bye_points = bye_points
        self.short_points = short_points
        self.bye_days = bye_days
        self.short_days = short_days
        self.max_rest_days = max_rest_days
        self.same_game_hours = same_game_hours
        # An inner wrapper keyed on the kickoff (the schedule-starter model)
        # gets it forwarded; a bare game model would raise on it.
        try:
            self._inner_takes_kickoff = "kickoff" in inspect.signature(
                inner.project).parameters
        except (TypeError, ValueError):  # pragma: no cover - exotic callables
            self._inner_takes_kickoff = False
        sched = schedule.dropna(subset=["kickoff"]).copy()
        sched["kickoff"] = pd.to_datetime(sched["kickoff"])
        long = pd.concat([
            sched[["home_team", "kickoff"]].rename(columns={"home_team": "team"}),
            sched[["away_team", "kickoff"]].rename(columns={"away_team": "team"}),
        ], ignore_index=True)
        self._games_by_team = {
            str(team): group["kickoff"].sort_values().to_numpy()
            for team, group in long.groupby("team")
        }

    def _bonus(self, team: str, kickoff: object) -> float:
        if kickoff is None or pd.isna(kickoff):  # type: ignore[call-overload]
            return 0.0
        played = self._games_by_team.get(team)
        if played is None:
            return 0.0
        when = pd.Timestamp(kickoff).to_datetime64()  # type: ignore[arg-type]
        cutoff = when - np.timedelta64(int(self.same_game_hours * 3600), "s")
        prior = played[played < cutoff]
        if len(prior) == 0:
            return 0.0
        rest = (when - prior[-1]) / np.timedelta64(1, "D")
        if rest > self.max_rest_days:  # an offseason is not a bye
            return 0.0
        if rest >= self.bye_days:
            return self.bye_points
        if rest <= self.short_days:
            return -self.short_points
        return 0.0

    def project(
        self,
        home_team: str,
        away_team: str,
        *,
        neutral_site: bool = False,
        rng: object = None,
        kickoff: object = None,
        home_bonus: float = 0.0,
        away_bonus: float = 0.0,
    ) -> object:
        """Rest bonuses ON TOP of whatever an outer wrapper already applied.

        These wrappers stack — the live runner wraps the rest model in
        :class:`WeatherAdjustedModel` whenever a forecast is available, and
        that one passes its wind bonus down. Swallowing the argument instead
        of adding to it raised ``TypeError: unexpected keyword argument
        'home_bonus'`` on every NFL projection, killing the whole slate the
        moment a forecast came back. Each wrapper adds its own term and
        forwards the sum, so any stacking order composes.
        """
        if self._inner_takes_kickoff:
            return self.inner.project(
                home_team, away_team, neutral_site=neutral_site,
                rng=rng,  # type: ignore[arg-type]
                home_bonus=home_bonus + self._bonus(home_team, kickoff),
                away_bonus=away_bonus + self._bonus(away_team, kickoff),
                kickoff=kickoff,  # type: ignore[call-arg]
            )
        return self.inner.project(
            home_team, away_team, neutral_site=neutral_site,
            rng=rng,  # type: ignore[arg-type]
            home_bonus=home_bonus + self._bonus(home_team, kickoff),
            away_bonus=away_bonus + self._bonus(away_team, kickoff),
        )


class WeatherAdjustedModel:
    """A situational wrapper: wind suppression on totals over any NFL model.

    Wind is forecastable before kickoff, so pricing it from the historical
    archive in walk-forward mirrors what a live model would do with a
    forecast (an optimistic-but-small information edge: actuals vs forecast).
    The bonus is symmetric and negative — wind takes points off both teams,
    moving the total while leaving the margin untouched.
    """

    def __init__(
        self,
        inner: NFLGameModel,
        weather: pd.DataFrame,  # join_weather() output: home_team/kickoff keyed
        *,
        threshold_mph: float = 15.0,
        points_per_mph: float = 0.15,
        precip_points: float = 0.0,
        precip_threshold_in: float = 0.25,
        cold_points: float = 0.0,
        cold_threshold_f: float = 32.0,
    ) -> None:
        import inspect

        self.inner = inner
        self.threshold_mph = threshold_mph
        self.points_per_mph = points_per_mph
        # Precipitation (velocity.features.weather.precip_total_bonus): a
        # point a side at a quarter-inch, promoted by the situational round.
        self.precip_points = precip_points
        self.precip_threshold_in = precip_threshold_in
        # Cold (velocity.features.weather.cold_total_bonus): off at zero.
        self.cold_points = cold_points
        self.cold_threshold_f = cold_threshold_f
        # Decided once, at construction: a bare game model has no ``kickoff``
        # parameter and raises if handed one.
        try:
            self._inner_takes_kickoff = "kickoff" in inspect.signature(
                inner.project).parameters
        except (TypeError, ValueError):  # pragma: no cover - exotic callables
            self._inner_takes_kickoff = False
        keyed = weather.dropna(subset=["kickoff"]).copy()
        keyed["_date"] = pd.to_datetime(keyed["kickoff"]).dt.normalize()
        self._wind = {
            (str(r["home_team"]), r["_date"]): r["wind_max"]
            for r in keyed.to_dict("records")
        }
        self._precip = {
            (str(r["home_team"]), r["_date"]): r.get("precip")
            for r in keyed.to_dict("records")
        } if "precip" in keyed.columns else {}
        self._temp = {
            (str(r["home_team"]), r["_date"]): r.get("temp_mean")
            for r in keyed.to_dict("records")
        } if "temp_mean" in keyed.columns else {}

    def project(
        self,
        home_team: str,
        away_team: str,
        *,
        neutral_site: bool = False,
        rng: object = None,
        kickoff: object = None,
        home_bonus: float = 0.0,
        away_bonus: float = 0.0,
    ) -> object:
        """Wind on top of whatever the caller already applied.

        The inner model is either a bare game model (the lab's wind variants)
        or another situational wrapper (the live runner stacks this over
        :class:`RestAdjustedModel`). Only the wrappers take a ``kickoff``, so
        it is forwarded only where it is accepted — passing it to a bare
        model would raise, and *not* passing it to a rest wrapper would
        silently zero every rest bonus.
        """
        from velocity.features.weather import (
            cold_total_bonus,
            precip_total_bonus,
            wind_total_bonus,
        )

        bonus = 0.0
        if kickoff is not None and not pd.isna(kickoff):  # type: ignore[call-overload]
            date = pd.Timestamp(kickoff).normalize()  # type: ignore[arg-type]
            bonus = wind_total_bonus(
                self._wind.get((home_team, date)),
                threshold_mph=self.threshold_mph,
                points_per_mph=self.points_per_mph,
            )
            if self.precip_points > 0:
                bonus += precip_total_bonus(
                    self._precip.get((home_team, date)),
                    threshold_in=self.precip_threshold_in, points=self.precip_points)
            if self.cold_points > 0:
                bonus += cold_total_bonus(
                    self._temp.get((home_team, date)),
                    threshold_f=self.cold_threshold_f, points=self.cold_points)
        if self._inner_takes_kickoff:
            return self.inner.project(
                home_team, away_team, neutral_site=neutral_site,
                rng=rng,  # type: ignore[arg-type]
                home_bonus=home_bonus + bonus, away_bonus=away_bonus + bonus,
                kickoff=kickoff,  # type: ignore[call-arg]
            )
        return self.inner.project(
            home_team, away_team, neutral_site=neutral_site,
            rng=rng,  # type: ignore[arg-type]
            home_bonus=home_bonus + bonus, away_bonus=away_bonus + bonus,
        )


def market_blend_sweep(
    projections: pd.DataFrame,
    games: pd.DataFrame,
    *,
    sigma: float = NFL_MARGIN_SIGMA,
    weights: tuple[float, ...] = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
    select_through: int = 2019,
) -> pd.DataFrame:
    """Brier of market-blended win probabilities across model weights.

    The nfelo finding: regressing a model *toward* the market maximizes
    forecasting accuracy, because the close is the best single predictor in
    existence. This sweeps ``p_blend = w·p_model + (1−w)·p_market`` where
    ``p_market`` is the closing spread through a fixed probit link
    (``Φ(spread_margin/σ)`` — σ is a historical constant, nothing fit here).

    Honesty split: ``w`` must not be chosen on the games it is judged on, so
    every weight is scored separately on the **select** window (seasons ≤
    ``select_through``) and the **holdout** (later seasons). The promotion
    read is: pick argmin Brier on select, report that weight's holdout Brier.
    Returns one row per weight: ``weight, brier_select, brier_holdout,
    n_select, n_holdout``.
    """
    if "spread_line" not in games.columns:
        # No joined closes (a games-only league, or the committed public
        # frame) — there is no market to blend toward.
        return pd.DataFrame(columns=["weight", "brier_select", "brier_holdout",
                                     "n_select", "n_holdout"])
    line_cols = games[["game_id", "spread_line"]].copy()
    line_cols["game_id"] = line_cols["game_id"].astype(str)
    df = projections.copy()
    df["game_id"] = df["game_id"].astype(str)
    df = df.merge(line_cols, on="game_id", how="inner").dropna(
        subset=["spread_line", "p_home_win", "home_win"]
    )
    if df.empty:
        return pd.DataFrame(columns=["weight", "brier_select", "brier_holdout",
                                     "n_select", "n_holdout"])
    # spread_line is positive when home is favored → it *is* the market's
    # expected home margin in this dataset's convention. Φ via erf — no scipy.
    z = df["spread_line"].to_numpy(dtype=float) / (sigma * math.sqrt(2.0))
    p_market = 0.5 * (1.0 + np.vectorize(math.erf)(z))
    p_model = df["p_home_win"].to_numpy(dtype=float)
    outcome = df["home_win"].to_numpy(dtype=float)
    in_select = (df["season"].astype(int) <= select_through).to_numpy()

    rows = []
    for w in weights:
        blend = w * p_model + (1.0 - w) * p_market
        sq = (blend - outcome) ** 2
        rows.append({
            "weight": float(w),
            "brier_select": float(sq[in_select].mean()) if in_select.any() else float("nan"),
            "brier_holdout": float(sq[~in_select].mean()) if (~in_select).any() else float("nan"),
            "n_select": int(in_select.sum()),
            "n_holdout": int((~in_select).sum()),
        })
    return pd.DataFrame(rows)


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


def _rmse(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x))))


def score_accuracy(projections: pd.DataFrame, games: pd.DataFrame) -> dict[str, float]:
    """How close the projected scores land, in points — and whether they carry
    anything the closing line does not.

    Brier scores the win probability, which is blind to the total and to the
    *scale* of the margin; the goal is the most accurate score projection, so
    this is the gate's other half. Per ledger:

    * ``rmse_margin`` / ``rmse_total`` — actual against the model's μ
      (``mu_home``/``mu_away`` when the ledger carries them, else the sim's
      medians).
    * ``close_rmse_margin`` / ``close_rmse_total`` — the same against the
      closing line, where the games frame has one. The close is the sharpest
      forecast available, so this is the yardstick the model is measured
      against, not a baseline it is expected to beat.
    * ``info_w_margin`` / ``info_w_total`` — the least-squares weight the
      actual result puts on (model − close), fitted beside the close itself:
      0 means the close already contains everything the model knows, 1 that
      the model is the better forecast. A variant that lowers RMSE by moving
      *toward* the close leaves this at 0; one that adds information the
      market prices moves both.
    """
    keys = ("rmse_margin", "rmse_total", "close_rmse_margin", "close_rmse_total",
            "info_w_margin", "info_w_total", "close_brier")
    cols = ["game_id", "home_score", "away_score"]
    cols += [c for c in ("spread_line", "total_line", "home_moneyline", "away_moneyline")
             if c in games.columns]
    merged = projections.merge(games[cols], on="game_id", how="inner")
    merged = merged.dropna(subset=["home_score", "away_score"])
    out: dict[str, float] = {"n_scored": float(len(merged))}
    if merged.empty:
        out.update({k: float("nan") for k in keys})
        return out
    # The market's own Brier, from real moneyline closes de-vigged
    # multiplicatively — the ceiling the model's Brier is read against,
    # rather than the spread-probit approximation the blend sweep uses.
    out["close_brier"] = float("nan")
    if {"home_moneyline", "away_moneyline"} <= set(merged.columns):
        from velocity.wagering.odds import american_to_prob

        ml = merged[["home_moneyline", "away_moneyline"]].apply(pd.to_numeric, errors="coerce")
        priced = ml.notna().all(axis=1).to_numpy()
        if int(priced.sum()) >= 3:
            q_home = ml.loc[priced, "home_moneyline"].map(american_to_prob).to_numpy(dtype=float)
            q_away = ml.loc[priced, "away_moneyline"].map(american_to_prob).to_numpy(dtype=float)
            p_market = q_home / (q_home + q_away)
            won = (merged.loc[priced, "home_score"] > merged.loc[priced, "away_score"]).to_numpy(
                dtype=float)
            tied = (merged.loc[priced, "home_score"] == merged.loc[priced, "away_score"]).to_numpy()
            won = np.where(tied, 0.5, won)
            out["close_brier"] = float(np.mean(np.square(p_market - won)))
    if {"mu_home", "mu_away"} <= set(merged.columns):
        mu_margin = (merged["mu_home"] - merged["mu_away"]).to_numpy(dtype=float)
        mu_total = (merged["mu_home"] + merged["mu_away"]).to_numpy(dtype=float)
    else:  # an older ledger: the sim medians (fair_spread is the HOME spread)
        mu_margin = -merged["fair_spread"].to_numpy(dtype=float)
        mu_total = merged["fair_total"].to_numpy(dtype=float)
    actual_margin = (merged["home_score"] - merged["away_score"]).to_numpy(dtype=float)
    actual_total = (merged["home_score"] + merged["away_score"]).to_numpy(dtype=float)
    out["rmse_margin"] = _rmse(actual_margin - mu_margin)
    out["rmse_total"] = _rmse(actual_total - mu_total)
    for name, mu, actual, col in (
        ("margin", mu_margin, actual_margin, "spread_line"),
        ("total", mu_total, actual_total, "total_line"),
    ):
        close = (pd.to_numeric(merged[col], errors="coerce").to_numpy(dtype=float)
                 if col in merged.columns else np.full(len(merged), np.nan))
        keep = np.isfinite(close)
        if int(keep.sum()) < 3:
            out[f"close_rmse_{name}"] = float("nan")
            out[f"info_w_{name}"] = float("nan")
            continue
        out[f"close_rmse_{name}"] = _rmse(actual[keep] - close[keep])
        gap = mu[keep] - close[keep]
        if np.allclose(gap, 0.0):  # the model IS the close: nothing to weigh
            out[f"info_w_{name}"] = float("nan")
            continue
        x = np.column_stack([np.ones(int(keep.sum())), close[keep], gap])
        beta = np.linalg.lstsq(x, actual[keep], rcond=None)[0]
        out[f"info_w_{name}"] = float(beta[2])
    return out


def ncaab_segment_study(
    projections: pd.DataFrame,
    games: pd.DataFrame,
    torvik: pd.DataFrame | None = None,
    *,
    thresholds: tuple[float, ...] = (0.0, 2.0, 4.0),
    november_weeks: int = 2,
    major_rank: int = 100,
) -> pd.DataFrame:
    """ATS/O-U win rates by the documented NCAAB inefficiency segments.

    The N3 promotion read (docs/BUILD_NCAAB.md): inefficiency concentrates
    where pregame information is scarce — November (lines anchored to
    priors), low-majors (thin markets) — while the tournament tests
    efficient. Each row is one (market, segment, disagreement-threshold)
    cell: bets, win rate vs the close at −110, and a one-sided normal
    p-value against the 52.4% break-even. The caller runs the whole frame
    through :func:`velocity.eval.metrics.benjamini_hochberg` — the family IS
    the sweep, so no cell may be read alone.

    Majors/low-majors split on **last season's** Torvik rank (the market's
    own attention proxy, leak-free by construction): a game is ``major``
    when both teams ranked inside ``major_rank``, ``low-major`` when both
    ranked outside it or unranked.
    """
    import math

    cols = ["game_id", "home_score", "away_score", "season", "week",
            "home_team", "away_team", "neutral_site"]
    for optional in ("spread_line", "total_line"):
        if optional in games.columns:
            cols.append(optional)
    df = projections.merge(games[cols], on="game_id", how="inner",
                           suffixes=("", "_g"))
    rank: dict[tuple[int, str], float] = {}
    if torvik is not None:
        from velocity.ingest.ncaab import torvik_team_candidates

        teams = set(df["home_team"]) | set(df["away_team"])
        for r in torvik.to_dict("records"):
            team = next(
                (c for c in torvik_team_candidates(str(r["team"])) if c in teams),
                None,
            )
            if team is not None:
                # torvik season s ranks key the market's view of season s+1.
                rank[(int(str(r["season"])) + 1, team)] = float(str(r["rank"]))

    def team_rank(row: Mapping[object, object], side: str) -> float:
        return rank.get((int(str(row["season"])), str(row[side])), float("inf"))

    if rank:
        ranks = pd.DataFrame(
            [(team_rank(r, "home_team"), team_rank(r, "away_team"))
             for r in df.to_dict("records")],
            columns=["_home_rank", "_away_rank"], index=df.index,
        )
        df = pd.concat([df, ranks], axis=1)

    segments: dict[str, pd.Series] = {
        "all": pd.Series(True, index=df.index),
        "november": df["week"] <= november_weeks,
        "conference-play": df["week"] > november_weeks,
    }
    if rank:
        segments["major"] = (df["_home_rank"] <= major_rank) & (
            df["_away_rank"] <= major_rank)
        segments["low-major"] = (df["_home_rank"] > major_rank) & (
            df["_away_rank"] > major_rank)

    rows = []
    for market in ("spread", "total"):
        line_col = "total_line" if market == "total" else "spread_line"
        if line_col not in df.columns:
            continue
        if market == "total":
            realized = df["home_score"] + df["away_score"]
            gap = df["fair_total"] - df[line_col]
            diff = realized - df[line_col]
        else:
            margin = df["home_score"] - df["away_score"]
            gap = -df["fair_spread"] - df[line_col]
            diff = margin - df[line_col]
        win = ((gap > 0) & (diff > 0)) | ((gap < 0) & (diff < 0))
        decided = df[line_col].notna() & (gap != 0) & (diff != 0)
        for name, in_segment in segments.items():
            for threshold in thresholds:
                mask = decided & in_segment & (gap.abs() >= threshold)
                n = int(mask.sum())
                if n == 0:
                    continue
                rate = float(win[mask].mean())
                z = (rate - 0.524) / math.sqrt(0.524 * 0.476 / n)
                rows.append({
                    "market": market, "segment": name, "threshold": threshold,
                    "bets": n, "win_rate": rate,
                    "p_value": 0.5 * math.erfc(z / math.sqrt(2.0)),
                })
    return pd.DataFrame(rows)


def posted_team_total_study(
    lines: pd.DataFrame,
    games: pd.DataFrame,
    *,
    edges: tuple[float, ...] = (0.0, 14.0, 17.0, 21.0, 24.0, 28.0, 100.0),
) -> pd.DataFrame:
    """Posted team-total closes vs realized scores and the linear derivation.

    The derived-numbers study (:func:`team_total_censoring_study`) found the
    censoring bias in the *mean* but no >52.4% over-rate — the open question
    it left was whether books' **posted** lines deviate from the linear
    ``(total ± spread) / 2`` split, and whether that deviation carries a
    playable bias. This is that measurement, runnable once the collector's
    banked team-total lines accumulate.

    ``lines`` is the banked archive (canonical ``Lines`` rows with markets
    ``team_total_home``/``team_total_away``, any number of snapshots);
    ``games`` needs ``game_id`` (the lines' id space), ``kickoff``, finals,
    and — for the derivation-gap column — ``spread_line``/``total_line``
    closes. The close per (game, market, book) is the honest last pre-kickoff
    observation (:func:`velocity.store.pit.closing_line`); books are then
    collapsed to the median posted number so each team-game counts once.

    Per posted-number bucket: n, mean posted, realized bias, the over rate at
    the posted number (pushes excluded), and the mean posted-minus-derived
    gap (NaN until derivation inputs exist). An over rate clearing 52.4% on
    accumulated closes is what would finally set the
    ``min_team_total_disagreement`` default the live gate ships disabled.
    """
    from velocity.store.pit import closing_line

    columns = ["bucket", "n", "mean_posted", "mean_realized", "bias",
               "over_rate", "posted_minus_derived"]
    needed = ("game_id", "kickoff", "home_score", "away_score")
    if (
        lines is None or lines.empty or games.empty
        or any(c not in games.columns for c in needed)
    ):
        return pd.DataFrame(columns=columns)
    team_totals = lines[lines["market"].isin(("team_total_home", "team_total_away"))]
    if team_totals.empty:
        return pd.DataFrame(columns=columns)

    closes = closing_line(team_totals, games)
    if closes.empty:
        return pd.DataFrame(columns=columns)
    posted = (
        closes.groupby(["game_id", "market"], as_index=False)["point"].median()
    )
    keep = ["game_id", "home_score", "away_score"]
    for optional in ("spread_line", "total_line"):
        if optional in games.columns:
            keep.append(optional)
    merged = posted.merge(games[keep], on="game_id", how="inner")
    merged = merged.dropna(subset=["point", "home_score", "away_score"])
    if merged.empty:
        return pd.DataFrame(columns=columns)

    is_home = merged["market"] == "team_total_home"
    merged["realized"] = merged["away_score"].astype(float).where(
        ~is_home, merged["home_score"].astype(float)
    )
    if {"spread_line", "total_line"} <= set(merged.columns):
        derived_home = (merged["total_line"] + merged["spread_line"]) / 2.0
        derived_away = (merged["total_line"] - merged["spread_line"]) / 2.0
        merged["derived"] = derived_away.where(~is_home, derived_home)
    else:
        merged["derived"] = float("nan")
    merged["bucket"] = pd.cut(merged["point"], list(edges), include_lowest=True)

    rows = []
    for bucket, part in merged.groupby("bucket", observed=True):
        decided = part[part["realized"] != part["point"]]
        over = (decided["realized"] > decided["point"]).sum()
        gap = part["point"] - part["derived"]
        rows.append({
            "bucket": str(bucket),
            "n": len(part),
            "mean_posted": float(part["point"].mean()),
            "mean_realized": float(part["realized"].mean()),
            "bias": float((part["realized"] - part["point"]).mean()),
            "over_rate": float(over / len(decided)) if len(decided) else float("nan"),
            "posted_minus_derived": (
                float(gap.mean()) if gap.notna().any() else float("nan")
            ),
        })
    return pd.DataFrame(rows, columns=columns)


def team_total_censoring_study(
    games: pd.DataFrame,
    *,
    edges: tuple[float, ...] = (0.0, 14.0, 17.0, 21.0, 24.0, 28.0, 100.0),
) -> pd.DataFrame:
    """Measure the censored-score bias in linearly derived team totals.

    Books derive team-total lines from the game total and spread —
    ``home ≈ (total + spread) / 2`` — a linear split that ignores the zero
    floor on scores (Arscott 2023, J. Sports Economics: a lines-only strategy
    exploiting this won >55% over two decades). This study reproduces the
    measurement on the committed closes: for every completed game with both
    lines, compute each side's linearly implied team total, then per implied-
    total bucket report the realized bias (mean realized score − implied) and
    the over rate at the implied number (pushes excluded).

    An over rate above 52.4% (−110 break-even) in a bucket is the actionable
    signal: the linear derivation understates that bucket's scoring. The
    caveat is honest and structural — these are *derived* numbers, not banked
    team-total closes; what is measured is the bias of the derivation books
    are documented to use, on our own data.
    """
    cols = ("home_score", "away_score", "spread_line", "total_line")
    if games.empty or any(c not in games.columns for c in cols):
        return pd.DataFrame(
            columns=["bucket", "n", "mean_implied", "mean_realized", "bias", "over_rate"]
        )
    span = games.dropna(subset=list(cols))
    home = pd.DataFrame({
        "implied": (span["total_line"] + span["spread_line"]) / 2.0,
        "realized": span["home_score"].astype(float),
    })
    away = pd.DataFrame({
        "implied": (span["total_line"] - span["spread_line"]) / 2.0,
        "realized": span["away_score"].astype(float),
    })
    long = pd.concat([home, away], ignore_index=True)
    long["bucket"] = pd.cut(long["implied"], list(edges), include_lowest=True)

    rows = []
    for bucket, part in long.groupby("bucket", observed=True):
        decided = part[part["realized"] != part["implied"]]
        over = (decided["realized"] > decided["implied"]).sum()
        rows.append({
            "bucket": str(bucket),
            "n": len(part),
            "mean_implied": float(part["implied"].mean()),
            "mean_realized": float(part["realized"].mean()),
            "bias": float((part["realized"] - part["implied"]).mean()),
            "over_rate": float(over / len(decided)) if len(decided) else float("nan"),
        })
    return pd.DataFrame(rows)
