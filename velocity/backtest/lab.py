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
)
from velocity.models.game_nfl import NFLGameModel, NFLModelConfig
from velocity.models.game_scores import ScoresGameModel, ScoresModelConfig
from velocity.models.simulate import SimConfig

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
]

# The classic NFL margin standard deviation, used to convert a point spread
# into a win probability (probit link). A fixed historical constant, never fit
# on the evaluation data.
NFL_MARGIN_SIGMA = 13.45

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

    def qb_recency(half_life: float, qb_lam: float | None = None) -> VariantFactory:
        from velocity.features.team import DEFAULT_QB_LAMBDA, fit_qb_ratings

        def factory(train: pd.DataFrame) -> NFLGameModel:
            return _model(fit_qb_ratings(
                train,
                qb_lambda=qb_lam if qb_lam is not None else DEFAULT_QB_LAMBDA,
                weights=recency_weights(train, half_life),
            ))

        return factory

    def scores(train_games: pd.DataFrame) -> ScoresGameModel:
        # The schedule-only fit the live slate currently runs — the promotion bar.
        return ScoresGameModel(fit_scores_ratings(train_games), ScoresModelConfig(sim=sim))

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

    if schedule is not None:
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

            variants.update({
                "wind-15-0.15": ("plays", wind(15.0, 0.15)),
                "wind-15-0.30": ("plays", wind(15.0, 0.30)),
                "wind-12-0.15": ("plays", wind(12.0, 0.15)),
            })
    return variants


def compress_plays(plays: pd.DataFrame) -> pd.DataFrame:
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
    """
    cells = (
        plays.dropna(subset=["posteam", "defteam", "epa"])
        .groupby(["posteam", "defteam", "season", "week"], observed=True)["epa"]
        .agg(epa="mean", n="count")
        .reset_index()
    )
    return cells


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
    n_sims: int, plays: pd.DataFrame | None = None
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
    """
    from velocity.features.scores import scores_recency_weights

    sim = SimConfig(sd_margin=17.0, sd_total=16.0, n_sims=n_sims)

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
                sub = all_plays[all_plays["game_id"].isin(set(train_games["game_id"]))]
                cells = compress_plays(sub)
                epa_model = NFLGameModel(
                    fit_ratings(cells, ridge_lambda=50.0,
                                weights=cells["n"].astype(float)),
                    cfg,
                )
                scores_model = _model(fit_scores_ratings(train_games, ridge_lambda=10.0))
                return BlendedGameModel(epa_model, scores_model, epa_weight, sim)

            return factory

        variants.update({
            "blend-epa30": ("games", blend(0.30)),
            "blend-epa50": ("games", blend(0.50)),
            "blend-epa70": ("games", blend(0.70)),
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
    "wnba": {"sd_margin": 12.5, "sd_total": 15.0, "sigma": 12.5, "ridge": 10.0},
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
             "wnba": (3.0, 25.0, 50.0)}[league]
    variants: dict[str, tuple[str, VariantFactory]] = {
        "scores": ("games", ridge(live_ridge)),  # the live default — the bar
    }
    variants.update({
        f"ridge-{lam:g}": ("games", ridge(lam)) for lam in sweep
    })
    variants.update({
        "recency-2": ("games", recency(2.0, live_ridge)),
        "recency-4": ("games", recency(4.0, live_ridge)),
        "recency-8": ("games", recency(8.0, live_ridge)),
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
        mu_home = base + self.ratings.matchup_delta(  # type: ignore[attr-defined]
            home_team, away_team, qb_id=away_sp
        )
        mu_away = base + self.ratings.matchup_delta(  # type: ignore[attr-defined]
            away_team, home_team, qb_id=home_sp
        )
        if not neutral_site:
            mu_home += self.hfa_points / 2.0
            mu_away -= self.hfa_points / 2.0
        rng = rng if rng is not None else make_rng()
        sim = simulate_game(mu_margin=mu_home - mu_away, mu_total=mu_home + mu_away,
                            rng=rng, config=self.sim)
        return GameProjection(home_team=home_team, away_team=away_team,
                              mu_home=mu_home, mu_away=mu_away, sim=sim)


def mlb_sp_variants(
    n_sims: int, games: pd.DataFrame, starters: pd.DataFrame
) -> dict[str, tuple[str, VariantFactory]]:
    """The starter-decomposition slate — the market's dominant MLB factor.

    Each variant fits :func:`~velocity.features.team.fit_qb_ratings` on the
    games-long frame (:func:`mlb_starter_frame` of the training slice) and
    prices through :class:`StarterAwareModel` with the evaluated game's
    starters. ``min_dropbacks`` is starts here: a 6-start sample prices as
    his shrunk self, fewer prices league-average.
    """
    from velocity.features.team import fit_qb_ratings

    cal = INSEASON_CALIBRATION["mlb"]
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
            return StarterAwareModel(ratings, lookup, sim)

        return factory

    return {
        "sp-q5": ("games", sp(5.0)),
        "sp-q15": ("games", sp(15.0)),
        "sp-q40": ("games", sp(40.0)),
    }


class RestAdjustedModel:
    """A situational wrapper: rest-spot point bonuses on top of any NFL model.

    Rest days are pure schedule knowledge (known before the season), so
    computing them from the full schedule is not leakage. A team coming off a
    bye (rest ≥ ``bye_days``) gets ``bye_points``; a team on a short week
    (rest ≤ ``short_days``) gets ``-short_points``. Teams with no prior game
    (week 1) get no adjustment.
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
    ) -> None:
        self.inner = inner
        self.bye_points = bye_points
        self.short_points = short_points
        self.bye_days = bye_days
        self.short_days = short_days
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
        prior = played[played < when]
        if len(prior) == 0:
            return 0.0
        rest = (when - prior[-1]) / np.timedelta64(1, "D")
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
    ) -> object:
        return self.inner.project(
            home_team, away_team, neutral_site=neutral_site,
            rng=rng,  # type: ignore[arg-type]
            home_bonus=self._bonus(home_team, kickoff),
            away_bonus=self._bonus(away_team, kickoff),
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
    ) -> None:
        self.inner = inner
        self.threshold_mph = threshold_mph
        self.points_per_mph = points_per_mph
        keyed = weather.dropna(subset=["kickoff"]).copy()
        keyed["_date"] = pd.to_datetime(keyed["kickoff"]).dt.normalize()
        self._wind = {
            (str(r["home_team"]), r["_date"]): r["wind_max"]
            for r in keyed.to_dict("records")
        }

    def project(
        self,
        home_team: str,
        away_team: str,
        *,
        neutral_site: bool = False,
        rng: object = None,
        kickoff: object = None,
    ) -> object:
        from velocity.features.weather import wind_total_bonus

        bonus = 0.0
        if kickoff is not None and not pd.isna(kickoff):  # type: ignore[call-overload]
            date = pd.Timestamp(kickoff).normalize()  # type: ignore[arg-type]
            bonus = wind_total_bonus(
                self._wind.get((home_team, date)),
                threshold_mph=self.threshold_mph,
                points_per_mph=self.points_per_mph,
            )
        return self.inner.project(
            home_team, away_team, neutral_site=neutral_site,
            rng=rng,  # type: ignore[arg-type]
            home_bonus=bonus, away_bonus=bonus,
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
