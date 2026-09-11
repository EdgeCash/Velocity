"""Live slate runner — today's board → staked bet recommendations.

Ties the whole system together: fit the projection model on committed history,
pull the current board from The Odds API (or a saved snapshot for offline runs),
and run the identical wagering engine the backtest used to produce a staked slate
of recommended bets, plus any games it couldn't resolve to the model's teams.

    # offline, from a saved Odds API /odds payload:
    python scripts/run_live_slate.py --league nfl --data datasets/nfl \
        --snapshot-file snap.json

    # live (needs THE_ODDS_API in the environment):
    THE_ODDS_API=... python scripts/run_live_slate.py --league nfl --data datasets/nfl

This does not place bets — it prints the slate for a human to act on. CLV is
measured later, against the closing snapshot from the archive.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from velocity.eval.ladders import default_relative_tolerance
from velocity.features.scores import fit_scores_ratings
from velocity.ingest.local import load_games
from velocity.ingest.theoddsapi import extract_events, normalize_odds_events
from velocity.intel.publish import (
    DEFAULT_MAX_PLAYS,
    DEFAULT_MIN_CONTEXT,
    DEFAULT_MIN_CONVICTION,
)
from velocity.models.game_nfl import GameProjection
from velocity.models.game_scores import ScoresGameModel, ScoresModelConfig
from velocity.models.simulate import (
    DEFAULT_SD_MARGIN,
    DEFAULT_SD_TOTAL,
    FOOTBALL_SD_SLOPES,
    NCAAF_SD_MARGIN,
    NCAAF_SD_TOTAL,
    SimConfig,
)
from velocity.report.slate_xlsx import (
    export_slate_workbook,
    plays_display,
    projections_display,
    props_display,
)
from velocity.util.seed import make_rng
from velocity.wagering.live import canonicalize_sides, project_board, slate_to_frame
from velocity.wagering.slate import SlateConfig, build_slate


def _find_games(folder: Path) -> Path:
    for ext in (".parquet", ".pq", ".csv"):
        candidate = folder / f"games{ext}"
        if candidate.exists():
            return candidate
    raise SystemExit(f"need a games file in {folder}/ to fit the model")


def _load_snapshot(args: argparse.Namespace) -> object:
    if args.snapshot_file:
        return json.loads(Path(args.snapshot_file).read_text())
    from velocity.ingest.theoddsapi import TheOddsAPIClient  # network path

    client = TheOddsAPIClient.from_env()
    return client.odds_payload(args.league)


def _find_plays(folder: Path) -> Path | None:
    for ext in (".parquet", ".pq", ".csv"):
        candidate = folder / f"plays{ext}"
        if candidate.exists():
            return candidate
    return None


def _league_schedule(args: argparse.Namespace, folder: Path, now: pd.Timestamp) -> pd.DataFrame:
    """The committed games frame plus, online, the league's *current* schedule.

    The committed frame holds played games only (``refresh_datasets.py`` keeps
    it that way on purpose), so on its own it cannot say where an upcoming
    game is played. The neutral-site flag (docs/SYSTEM_REVIEW.md §3.2) and the
    rest-spot wrapper both want the games that have not happened yet: nflverse
    publishes the full NFL schedule keyless, and CFBD serves the college one
    when a key is present. Best-effort — a failed fetch leaves the committed
    frame, and every upcoming game then prices with home field as before.
    """
    games = load_games(_find_games(folder), league=args.league)
    if args.offline:
        return games
    try:
        if args.league == "nfl":
            from velocity.ingest.nfl import NFLVERSE_SCHEDULE_URL, normalize_schedules

            fetched = normalize_schedules(pd.read_csv(NFLVERSE_SCHEDULE_URL, low_memory=False))
        elif args.league == "ncaaf":
            key = os.environ.get("CFBD_API_KEY", "")
            if not key:
                return games
            from velocity.ingest.ncaaf import load_games as cfbd_games

            season = now.year if now.month >= 7 else now.year - 1
            fetched = cfbd_games([season], key)
        else:
            return games
    except Exception as exc:  # noqa: BLE001 - the schedule is a nicety live
        print(f"schedule fetch skipped ({exc}); neutral sites price as home games")
        return games
    kick = pd.to_datetime(fetched["kickoff"], errors="coerce")
    window = fetched[(kick >= now - pd.Timedelta(days=60)) & (kick <= now + pd.Timedelta(days=60))]
    fresh = window[~window["game_id"].astype(str).isin(games["game_id"].astype(str))]
    neutral = int(window["neutral_site"].fillna(False).astype(bool).sum())
    print(f"schedule: {len(window)} current {args.league.upper()} games fetched "
          f"({neutral} neutral-site)")
    return pd.concat([games, fresh], ignore_index=True, sort=False)


def _epa_ratings_rows(ratings: object, plays_per_game: float) -> list[dict]:
    """Per-team rows from an EPA fit, converted to points per game."""
    rows = []
    for team in ratings.teams:  # type: ignore[attr-defined]
        off = ratings.offense.get(team, 0.0) * plays_per_game  # type: ignore[attr-defined]
        # Defense is "EPA allowed vs average" — negative is good.
        dfn = ratings.defense.get(team, 0.0) * plays_per_game  # type: ignore[attr-defined]
        rows.append({"team": team, "off": off, "def": dfn, "net": off - dfn})
    return rows


def _scores_ratings_rows(ratings: object) -> list[dict]:
    """Per-team rows from a scores fit (already in points/runs per game)."""
    rows = []
    for team in ratings.teams:  # type: ignore[attr-defined]
        off = ratings.offense.get(team, 0.0)  # type: ignore[attr-defined]
        dfn = ratings.defense.get(team, 0.0)  # type: ignore[attr-defined]
        rows.append({"team": team, "off": off, "def": dfn, "net": off - dfn})
    return rows


def _ratings_frame(league: str, model: object, scores_model: object) -> pd.DataFrame:
    """The power-ratings table behind the live fit: team · off · def · net.

    ``off``/``def`` are deviations from league average in the league's
    natural per-game scale (points; runs for MLB; points per 100
    possessions for the basketball leagues, which also carry ``pace``).
    ``def`` is points *allowed* vs average, so negative is good and
    ``net = off − def`` is the expected margin against an average
    opponent on a neutral floor.
    """
    from velocity.backtest.lab import (
        BlendedGameModel,
        PaceEfficiencyModel,
        StarterAwareModel,
    )

    pace: dict[str, float] = {}
    if isinstance(model, PaceEfficiencyModel):
        rows = _scores_ratings_rows(model.eff_model.ratings)
        pace = {t: model.pace_league + 2.0 * model.pace_dev.get(t, 0.0)
                for t in model.eff_model.ratings.teams}
        scale = "pts/100 poss"
    elif isinstance(model, StarterAwareModel):
        rows = _scores_ratings_rows(model.ratings)
        scale = ("goals/gm (ex-goalie)" if league == "nhl"
                 else "runs/gm (ex-starter)")
    elif isinstance(model, BlendedGameModel):
        # The 50/50 college blend: each half converted to points first.
        epa = {r["team"]: r for r in _epa_ratings_rows(
            model.primary.ratings, plays_per_game=65.0)}
        rows = []
        for row in _scores_ratings_rows(model.secondary.ratings):
            half = epa.get(row["team"], {"off": 0.0, "def": 0.0, "net": 0.0})
            rows.append({"team": row["team"],
                         "off": 0.5 * row["off"] + 0.5 * half["off"],
                         "def": 0.5 * row["def"] + 0.5 * half["def"],
                         "net": 0.5 * row["net"] + 0.5 * half["net"]})
        scale = "pts/gm (blend)"
    else:
        ratings = getattr(scores_model, "ratings", None) or getattr(
            model, "ratings", None)
        if ratings is None:
            return pd.DataFrame()
        rows = _scores_ratings_rows(ratings)
        scale = {"mlb": "runs/gm", "nhl": "goals/gm"}.get(league, "pts/gm")

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    # Prior anchors ("__PRIOR__", "__SP_PRIOR__") are fit scaffolding, not teams.
    frame = frame[~frame["team"].astype(str).str.fullmatch(r"__.*__")]
    frame["pace"] = frame["team"].map(pace).astype(float) if pace else float("nan")
    frame["scale"] = scale
    frame = frame.sort_values("net", ascending=False).reset_index(drop=True)
    frame["rank"] = frame.index + 1
    for col in ("off", "def", "net", "pace"):
        frame[col] = frame[col].astype(float).round(2)
    return frame


def _build_projection(
    args: argparse.Namespace,
    schedule: pd.DataFrame | None = None,
) -> tuple[Callable[[str, str], GameProjection], list[str], pd.DataFrame, str]:
    """Fit the league's promoted ratings from the committed data → ``(project, teams)``.

    NFL: the recency-weighted EPA fit (docs/MODEL_LAB.md — Brier 0.2234 vs
    0.2343 for the schedule-only fit over 2014–2025), trained on the trailing
    four seasons exactly as validated; falls back to the scores fit when the
    data folder carries no plays file. NCAAF: the scores fit.
    """
    if not args.data:
        raise SystemExit(f"--data is required for {args.league} (a folder with a games file)")
    folder = Path(args.data)
    plays_path = _find_plays(folder) if args.league == "nfl" else None

    if plays_path is not None:
        from velocity.features.team import (
            DEFAULT_RECENCY_HALF_LIFE,
            fit_qb_ratings,
            fit_ratings,
            recency_weights,
        )
        from velocity.ingest.local import load_plays
        from velocity.models.game_nfl import NFLGameModel, NFLModelConfig

        plays = load_plays(plays_path)
        cutoff = int(plays["season"].max()) - 3
        plays = plays[plays["season"] >= cutoff]
        weights = recency_weights(plays, DEFAULT_RECENCY_HALF_LIFE)
        if "passer_player_id" in plays.columns and plays["passer_player_id"].notna().any():
            # The promoted fit (docs/MODEL_LAB.md Round 3): QB decomposed out
            # of the offense, detected starter priced back in at projection.
            ratings: object = fit_qb_ratings(plays, weights=weights)
            kind = "QB-adjusted recency EPA"
        else:  # plays without passer identity (older datasets, fixtures)
            ratings = fit_ratings(plays, weights=weights)
            kind = "recency-weighted EPA"
        print(f"NFL ratings: {kind} fit on {len(plays)} plays "
              f"(seasons {cutoff}+, half-life {DEFAULT_RECENCY_HALF_LIFE:g} wks)")

        # The starter map (docs/SYSTEM_REVIEW.md §3.1). The fit detects each
        # team's passer from its latest training game — after a Week-18 rest
        # game that is the backup, and KC priced 5.6 pts/game low with
        # Oladokun at quarterback. FantasyPros' projected depth names the
        # real QB1 and the injuries snapshot demotes an Out; applied to the
        # ratings object itself, so every wrapper, prop and DFS projection on
        # the team follows. A name that resolves to nothing leaves the fit's
        # detection in place.
        weeks_path = folder / "player_weeks.parquet"
        if args.fp_projections and weeks_path.exists() and hasattr(ratings, "starters"):
            from dataclasses import replace

            from velocity.features.starters import describe_changes, starter_map

            fp_frame = pd.read_parquet(args.fp_projections)
            if "league" in fp_frame.columns:
                fp_frame = fp_frame[fp_frame["league"].astype(str) == "nfl"]
            weeks = pd.read_parquet(weeks_path)
            injuries = pd.read_parquet(args.injuries_file) if args.injuries_file else None
            overrides, notes = starter_map(fp_frame, weeks, injuries)
            # Only clubs the fit knows: FantasyPros lists free agents under "FA".
            overrides = {t: q for t, q in overrides.items() if t in ratings.teams}  # type: ignore[attr-defined]
            changes = describe_changes(overrides, ratings.starters, weeks)  # type: ignore[attr-defined]
            if overrides:
                ratings = replace(ratings, starters={**ratings.starters, **overrides})  # type: ignore[attr-defined,type-var]
            print(f"starter map: {len(overrides)} teams from the FantasyPros depth, "
                  f"{len(changes)} changed from the fit's own detection")
            for line in changes + notes:
                print(f"  {line}")
        nfl_model = NFLGameModel(ratings, NFLModelConfig(sim=football_sim_config("nfl", args)))  # type: ignore[arg-type]
        # The scoring level, fitted through the model (velocity.models.level):
        # the QB decomposition prices each team with its starter's passer
        # effect, and starters throw above the play-weighted intercept the
        # deviations were centered on — every projected total ran ~2.3 pts
        # high over fifteen walk-forward seasons. Shift base_points so the
        # training window's mean projected total matches what it scored.
        if resolve_nfl_level(args.nfl_level) == "fit":
            from velocity.models.level import calibrate_level, level_shift

            # The trailing two seasons (docs/MODEL_LAB.md, the sim-shape
            # round): the whole four-season window lagged the era by +0.7.
            window = load_games(_find_games(folder), league="nfl")
            window = window[window["season"] >= cutoff]
            shift = level_shift(nfl_model, window, seasons=NFL_LEVEL_SEASONS)
            nfl_model = calibrate_level(nfl_model, window, seasons=NFL_LEVEL_SEASONS)
            kind += f", level {nfl_model.config.base_points:.2f} ({shift:+.2f} vs 22.5)"
            print(f"NFL level: base {nfl_model.config.base_points:.2f} pts/team "
                  f"(the fit ran {shift:+.2f} vs the constant on {len(window)} games)")

        # Rest spots (docs/MODEL_LAB.md Round 4): bye +1.0 / short week −1.0 on
        # top of the fit — small, consistent across every tested grid.
        from velocity.backtest.lab import RestAdjustedModel

        if schedule is None:
            schedule = load_games(_find_games(folder), league="nfl")
        rest_model = RestAdjustedModel(nfl_model, schedule)

        # Wind on totals (Round 5 constants, live forecast): best-effort — a
        # failed forecast fetch just leaves totals unadjusted.
        model: object = rest_model
        try:
            if args.offline:  # a true no-network run (tests/CI)
                raise RuntimeError("offline run")
            from velocity.backtest.lab import WeatherAdjustedModel
            from velocity.features.weather import forecast_frame

            forecast = forecast_frame(days=max(args.max_days, 1) + 1)
            if not forecast.empty:
                model = WeatherAdjustedModel(rest_model, forecast,  # type: ignore[arg-type]
                                             points_per_mph=0.30)
                print(f"wind forecast: {len(forecast)} stadium-days fetched")
        except Exception as exc:  # noqa: BLE001 - weather is a nicety live
            print(f"wind forecast skipped ({exc})")

        def project_epa(
            home: str, away: str, kickoff: object = None, neutral_site: bool = False
        ) -> GameProjection:
            return model.project(  # type: ignore[attr-defined,return-value]
                home, away, rng=make_rng(), kickoff=kickoff, neutral_site=neutral_site
            )

        nfl_ratings = pd.DataFrame(_epa_ratings_rows(ratings, plays_per_game=63.0))
        nfl_ratings["pace"] = float("nan")
        nfl_ratings["scale"] = "pts/gm (EPA)"
        nfl_ratings = nfl_ratings.sort_values("net", ascending=False).reset_index(drop=True)
        nfl_ratings["rank"] = nfl_ratings.index + 1
        for col in ("off", "def", "net"):
            nfl_ratings[col] = nfl_ratings[col].round(2)
        return project_epa, list(ratings.teams), nfl_ratings, kind

    games = load_games(_find_games(folder), league=args.league)
    # Per-league outcome-noise calibration. Football's constants are the
    # lab-validated ones; MLB (runs) and WNBA (points) use the leagues'
    # historical margin/total sigmas — content-surface defaults, honest but
    # not yet lab-tuned (their datasets carry no closing lines to tune on).
    sims = {
        "ncaaf": football_sim_config("ncaaf", args),
        "mlb": SimConfig(sd_margin=3.2, sd_total=4.6, n_sims=args.n_sims),
        "wnba": SimConfig(sd_margin=12.5, sd_total=15.0, n_sims=args.n_sims),
        # NCAAB: walk-forward residual sds (docs/BUILD_NCAAB.md N2).
        "ncaab": SimConfig(sd_margin=13.0, sd_total=18.5, n_sims=args.n_sims),
        # NHL: empirical outcome sds from the banked 2023–25 seasons
        # (docs/BUILD_NHL.md) — goals are MLB-like low-scoring counts.
        "nhl": SimConfig(sd_margin=2.6, sd_total=2.3, n_sims=args.n_sims),
    }
    sim = sims.get(args.league, football_sim_config("nfl", args))
    # NCAAF: λ=10 promoted by the college lab; MLB: λ=100 promoted by the
    # summer lab (docs/MODEL_LAB.md MLB Round 1 — heavy shrinkage wins in a
    # league whose true team spread is small). WNBA: recency half-life 8
    # week-buckets promoted (WNBA Round 1 — an interior optimum, 12/16/24
    # all worse). NCAAB: λ=0.5 — 360 conference-clustered teams leave the
    # fit compressed at heavier penalties (BUILD_NCAAB.md N2's compression
    # finding). The NFL scores path is only a no-plays fallback and keeps
    # the default.
    ridge = {"ncaaf": 10.0, "mlb": 100.0, "wnba": 10.0, "ncaab": 0.5,
             "nhl": 25.0}.get(args.league, 25.0)
    recency_hl = {"wnba": 8.0, "ncaab": 6.0}.get(args.league)
    weights = None
    if recency_hl is not None:
        from velocity.features.scores import scores_recency_weights

        weights = scores_recency_weights(games, recency_hl)
    # SP+ previous-season prior (docs/BACKTEST_NCAAF.md 2026-08-31 addendum):
    # K pseudo-games per team from the latest FINISHED season's final SP+
    # ratings, the Torvik pattern ported to football. Walk-forward Brier
    # improves at every tested K (0.2038 stock → 0.2019 at K=6 → 0.2009 at
    # K=12) with the totals strategy unharmed; the prior carries the roster
    # knowledge a results-only fit lacks in the early weeks. Only the scores
    # fit sees the pseudo-games — the EPA half, ncaaf_base_points, and every
    # other reader keep the real games frame.
    fit_games = games
    sp_file = folder / "sp_ratings.parquet"
    if args.league == "ncaaf" and sp_file.exists():
        from velocity.ingest.ncaaf import sp_pseudo_games

        pseudo = sp_pseudo_games(
            pd.read_parquet(sp_file),
            set(games["home_team"]) | set(games["away_team"]),
            cutoff=pd.to_datetime(games["kickoff"]).max(),
        )
        if not pseudo.empty:
            fit_games = pd.concat([games, pseudo], ignore_index=True)
            print(f"SP+ prior: {len(pseudo)} pseudo-games from season "
                  f"{int(pseudo['season'].max()) - 1}'s final ratings")
    scores_model = ScoresGameModel(
        fit_scores_ratings(fit_games, ridge_lambda=ridge, weights=weights),
        ScoresModelConfig(sim=sim),
    )
    model: object = scores_model
    kind = f"scores fit (λ={ridge:g})" + (
        f", recency-{recency_hl:g}" if recency_hl is not None else "")

    box_file = folder / "team_box.parquet"
    if args.league in ("wnba", "ncaab") and box_file.exists():
        # The promoted basketball configurations: WNBA pace×efficiency with
        # recency-8 (docs/MODEL_LAB.md WNBA Round 2); NCAAB pace×efficiency
        # with recency-6 plus the Torvik pseudo-games prior at K=6
        # (docs/BUILD_NCAAB.md N2 — prior-k6, the Brier winner that
        # replicated at 80k games in N3). Best-effort: any failure keeps the
        # recency scores fit above.
        try:
            from velocity.backtest.lab import fit_pace_efficiency, wnba_pace_frame

            pace = wnba_pace_frame(pd.read_parquet(box_file))
            fit_games = games
            prior_note = ""
            torvik_file = folder / "torvik.parquet"
            if args.league == "ncaab" and torvik_file.exists():
                from velocity.ingest.ncaab import torvik_pseudo_games

                teams = set(games["home_team"]) | set(games["away_team"])
                pseudo_games, pseudo_pace = torvik_pseudo_games(
                    pd.read_parquet(torvik_file), teams,
                    cutoff=pd.to_datetime(games["kickoff"]).max(), k=6,
                )
                if not pseudo_games.empty:
                    fit_games = pd.concat([games, pseudo_games], ignore_index=True)
                    pace = pd.concat([pace, pseudo_pace], ignore_index=True)
                    prior_note = f" + Torvik prior ({len(pseudo_games)} pseudo-games)"
            model = fit_pace_efficiency(
                fit_games, pace, sim, ridge_lambda=ridge, half_life=recency_hl
            )
            kind = (f"pace×efficiency (λ={ridge:g}, recency-{recency_hl:g})"
                    f"{prior_note}, {len(pace)} games' possessions")
        except Exception as exc:  # noqa: BLE001 - never blocks the slate
            print(f"pace×efficiency skipped: {exc}")

    starters_file = folder / "starters.parquet"
    if args.league == "mlb" and starters_file.exists():
        # The promoted MLB configuration (docs/MODEL_LAB.md MLB Round 2): the
        # starter decomposition at q=160 on the λ=100 team fit — Brier 0.2449
        # vs 0.2463 team-only, calibration error halved. Today's probables
        # come from the keyless statsapi schedule (public pregame knowledge);
        # a game with no announced probable prices starter-neutral, which
        # collapses to the team fit. Best-effort: any failure keeps the
        # scores fit above.
        try:
            from datetime import date, timedelta

            from build_mlb_pitching import fetch_probables
            from velocity.backtest.lab import StarterAwareModel, mlb_starter_frame
            from velocity.features.team import fit_qb_ratings

            played = games.dropna(subset=["home_score", "away_score"])
            ratings = fit_qb_ratings(
                mlb_starter_frame(played, pd.read_parquet(starters_file)),
                ridge_lambda=ridge, qb_lambda=160.0, min_dropbacks=6,
            )
            today = date.today()
            lookup = fetch_probables(str(today), str(today + timedelta(days=1)))
            named = sum(1 for h, a in lookup.values() if h or a)
            model = StarterAwareModel(ratings, lookup, sim)
            kind = (f"starter decomposition (λ={ridge:g}, q=160), "
                    f"{named} games with probables")
        except Exception as exc:  # noqa: BLE001 - the SP layer never blocks the slate
            print(f"starter decomposition skipped: {exc}")

    nhl_starters = folder / "starters.parquet"
    if args.league == "nhl" and nhl_starters.exists():
        # The promoted NHL configuration (docs/BUILD_NHL.md H2): the goalie
        # decomposition at q=40 on the λ=25 team fit — walk-forward Brier
        # 0.24258 vs 0.24350 for the best plain fit, the MLB starter finding
        # repeating in goals. No free confirmed-goalie feed exists pregame,
        # so pricing is goalie-neutral (empty lookup — the decomposition
        # still cleans the team estimates by removing goalie noise).
        # Best-effort: any failure keeps the scores fit above.
        try:
            from velocity.backtest.lab import StarterAwareModel, mlb_starter_frame
            from velocity.features.team import fit_qb_ratings

            played = games.dropna(subset=["home_score", "away_score"])
            ratings = fit_qb_ratings(
                mlb_starter_frame(played, pd.read_parquet(nhl_starters)),
                ridge_lambda=ridge, qb_lambda=40.0, min_dropbacks=6,
            )
            model = StarterAwareModel(ratings, {}, sim, hfa_points=0.15)
            kind = f"goalie decomposition (λ={ridge:g}, q=40), goalie-neutral"
        except Exception as exc:  # noqa: BLE001 - never blocks the slate
            print(f"goalie decomposition skipped: {exc}")

    ncaaf_plays = folder / "plays.parquet"
    if args.league == "ncaaf" and ncaaf_plays.exists():
        # The promoted college configuration (docs/MODEL_LAB.md NCAAF Round
        # 2): a 50/50 blend of the EPA fit (CFBD ppa, λ=50 on compressed
        # cells) and the scores fit above — Brier 0.1949 vs 0.1976 for the
        # scores fit alone, the best calibration recorded for college.
        from velocity.backtest.lab import BlendedGameModel, compress_plays
        from velocity.features.team import fit_ratings
        from velocity.ingest.local import load_plays
        from velocity.models.game_nfl import NFLGameModel, NFLModelConfig

        plays = load_plays(ncaaf_plays)
        cells = compress_plays(plays)
        base = ncaaf_base_points(games)
        epa_model = NFLGameModel(
            fit_ratings(cells, ridge_lambda=50.0, weights=cells["n"].astype(float)),
            NFLModelConfig(base_points=base, plays_per_game=65.0,
                           hfa_points=2.5, sim=sim),
        )
        # The scores half's level (docs/MODEL_LAB.md, the college level
        # round): its unweighted intercept lags the post-2021 scoring drop
        # by 1–2 points a game; fitted through the model on the trailing
        # two seasons like the NFL's, ratings and home edge untouched.
        if resolve_ncaaf_level(args.ncaaf_level) == "fit":
            from velocity.models.level import calibrate_scores_level, level_shift

            drift = level_shift(scores_model, games, seasons=NFL_LEVEL_SEASONS)
            scores_model = calibrate_scores_level(scores_model, games, seasons=NFL_LEVEL_SEASONS)
            print(f"NCAAF scores level: base {scores_model.ratings.base_points:.2f} pts/team "
                  f"(the fit ran {drift:+.2f} on the trailing {NFL_LEVEL_SEASONS} seasons)")
        model = BlendedGameModel(epa_model, scores_model, 0.5, sim)
        kind = (f"EPA×scores blend (λ50/λ{ridge:g}, w=0.5, "
                f"base {base:.1f}) on {len(plays)} plays")

    print(f"{args.league.upper()} ratings: {kind}, {len(games)} games")

    def project(home: str, away: str, neutral_site: bool = False) -> GameProjection:
        return model.project(  # type: ignore[attr-defined,return-value]
            home, away, rng=make_rng(), neutral_site=neutral_site
        )

    return project, list(scores_model.ratings.teams), _ratings_frame(
        args.league, model, scores_model), kind


def build_parser() -> argparse.ArgumentParser:
    """The live-slate CLI — a function so tests can pin the policy defaults."""
    parser = argparse.ArgumentParser(description="Live slate of staked recommendations")
    parser.add_argument("--league", choices=["nfl", "ncaaf", "mlb", "wnba", "ncaab", "nhl"],
                        required=True)
    parser.add_argument("--data", help="folder with a games file to fit the model")
    parser.add_argument("--snapshot-file", help="saved Odds API /odds JSON (offline mode)")
    parser.add_argument("--n-sims", type=int, default=10_000)
    parser.add_argument("--ncaaf-level", choices=["fit", "constant"], default=None,
                        help="the college blend's scores-half level: fitted on the trailing "
                             "two seasons, or the ridge's own intercept")
    parser.add_argument("--nfl-level", choices=["fit", "constant"], default=None,
                        help="NFL scoring level: fitted through the model on the training "
                             "window, or the 22.5 constant (default: fit)")
    parser.add_argument("--sim-shape", choices=["normal", "empirical"], default=None,
                        help="football sim draw: bivariate normal, or the banked "
                             "walk-forward residual pool (default: the gate's pick per league)")
    parser.add_argument("--sim-dispersion", choices=["constant", "sloped"], default=None,
                        help="football sim sd: one per league, or moving with the "
                             "expected total (default: the gate's pick per league)")
    parser.add_argument("--min-edge", type=float, default=0.02)
    # Market anchoring (docs/MODEL_LAB.md Round 3): the NFL close's Brier beats
    # every pure model in this family, so the belief used for gating and Kelly
    # sizing is regressed toward each line's devigged probability:
    #   p_belief = p_market + w · (p_model − p_market)
    # At w the measured edge scales by w, so clearing --min-edge takes 1/w times
    # the raw disagreement — the selectivity lever the Round-3 finding argued
    # for. Unset, the weight resolves per league (resolve_model_weight): 0.2
    # for the NFL (the round's select-chosen blend weight), 1.0 (raw model)
    # everywhere else until a league's own lab argues otherwise. Leans and
    # matchup cards keep the pure model either way — wagering policy, not a
    # fit change.
    parser.add_argument("--model-weight", type=float, default=None,
                        help="market-anchoring weight for gating/staking "
                             "(1.0 = raw model; default: 0.2 for nfl, else 1.0)")
    # Per-market selectivity (docs/WAGERING.md Phase W4). Props clear a wider
    # bar than game markets: the same measured edge in a noisy prop market is
    # more likely our own estimation error (DESIGN §6.2). Unset, the prop bar
    # resolves to 2× --min-edge — a reasoned, provisional multiplier until the
    # football shrink sweep re-tunes per-market values on the banked archive.
    parser.add_argument("--prop-min-edge", type=float, default=None,
                        help="edge threshold for player props "
                             "(default: 2 × --min-edge)")
    parser.add_argument("--min-edge-market", action="append", default=[],
                        metavar="MARKET=EDGE",
                        help="per-market edge override, repeatable "
                             "(e.g. --min-edge-market pass_yards=0.05)")
    # NCAAF sides: 50.1% ATS flat and no edge at any disagreement threshold
    # (docs/BACKTEST_NCAAF.md) — the college edge is totals. Spreads sit out
    # of the NCAAF game slate unless explicitly re-enabled.
    parser.add_argument("--ncaaf-spreads", action=argparse.BooleanOptionalAction,
                        default=False,
                        help="bet NCAAF spreads (backtest found no edge; off by default)")
    # NCAAF moneylines have never been backtested (the committed closes carry
    # no moneyline column), and on the first live card they took 60% of the
    # solo-Kelly exposure — 66 bets, 28 at +1000 or longer, the model's median
    # win probability 2.3× the market's (docs/STRATEGY_REVIEW.md §1.2). Off
    # until the backtest says otherwise; --ncaaf-moneylines re-enables.
    parser.add_argument("--ncaaf-moneylines", action=argparse.BooleanOptionalAction,
                        default=False,
                        help="stake NCAAF moneylines (default off — walk-forward tested "
                             "2021–2025 and negative in every price bucket, "
                             "docs/BACKTEST_NCAAF.md S3)")
    # Paper posture — priced, logged and graded for CLV, never staked. Team
    # totals stay paper until banked posted closes calibrate their gate; the
    # content-posture leagues (NCAAB, NHL, WNBA — no promoted edge) run paper
    # end to end. --team-totals-paper/--paper flip either per run.
    parser.add_argument("--team-totals-paper", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="price team totals but stake them at zero (default on)")
    parser.add_argument("--paper", action=argparse.BooleanOptionalAction, default=None,
                        help="stake nothing — every market paper (default: on for "
                             "ncaab/nhl/wnba, off for nfl/ncaaf)")
    # The adverse-selection guard, applied where the money is
    # (docs/PUBLISH_GATE.md §2 measured our biggest edges as our worst CLV).
    # A row past either ceiling is logged as paper with the reason.
    # The fair-probability anchor (docs/SYSTEM_REVIEW.md §4.2): the cross-book
    # consensus of both sides, not the best-priced book's own pair — which is
    # by construction the pair most generous to our side.
    parser.add_argument("--devig-anchor", choices=["consensus", "book"], default="consensus",
                        help="de-vig against the cross-book consensus (default) or the "
                             "shopped book's own opposite side")
    parser.add_argument("--odds-dir", default="artifacts/odds",
                        help="hourly odds archive — the previous snapshot is the publish "
                             "gate's 'then' for the adverse-drift rule")
    parser.add_argument("--max-edge", type=float, default=0.12,
                        help="absolute edge ceiling; a bigger edge is paper (0 = off)")
    parser.add_argument("--max-relative-edge", type=float, default=0.50,
                        help="edge / fair probability ceiling; bites on longshots (0 = off)")
    parser.add_argument("--bankroll", type=float, default=100.0)
    # The August board carries the whole season's games at stale opening
    # numbers (the first live run priced 272 NFL events and "staked" 20x the
    # bankroll). A slate is this week's games: only events kicking off inside
    # the window are priced, staked, and carded.
    parser.add_argument("--max-days", type=float, default=6.0,
                        help="only price games kicking off within this many days (0 = all)")
    # NCAAF selectivity, in POINTS of total disagreement (docs/BACKTEST_NCAAF.md).
    # With the restored 2025 season on the record the ≥4-point cut no longer
    # clears the 52.4% break-even (52.3% on 5,657 walk-forward bets; 51.2% in
    # 2025 alone), while ≥6 still does (53.0%, 53.1% with the SP+ prior) — so
    # the default moved 4 → 6 on 2026-08-31. Applies to full-game totals only;
    # NCAAF spreads showed no edge at any threshold and sit out by default.
    parser.add_argument("--ncaaf-total-edge", type=float, default=6.0,
                        help="NCAAF: min points of total disagreement to bet (0 = off)")
    # Team totals — the censored-score derivative (docs/EDGE_RESEARCH.md §2.2).
    # Books derive them linearly from total+spread, ignoring the zero floor on
    # scores; the sim's floored scores price that mass correctly. Offline, rows
    # already in the snapshot are priced automatically; live, football boards
    # need a per-event fetch (The Odds API serves team_totals per event only).
    # The censoring study on our own closes (backtest/lab.py
    # team_total_censoring_study) found the mean bias (+0.7–1.0 pts at low
    # implied totals) but no >52.4% over-rate on *derived* numbers, so the
    # disagreement gate defaults to off — the EV gate still applies, and the
    # threshold gets calibrated once banked team-total closes accumulate.
    parser.add_argument("--offline", action="store_true",
                        help="make no network calls at all (test and CI runs). Distinct from "
                             "--snapshot-file, which only means the sportsbook board comes "
                             "from a banked payload rather than a fresh, credit-spending pull.")
    parser.add_argument("--exchanges", action=argparse.BooleanOptionalAction, default=False,
                        help="also price the Kalshi and Polymarket boards alongside the "
                             "sportsbooks (free, keyless; docs/BUILD_EXCHANGES.md E6). "
                             "Paper only — nothing is ever ordered.")
    # The exchange prices belong on the board — a Kalshi contract can be the
    # best number on a game — and as of E8b they are staked too. The condition
    # this flag was waiting on was a shape gate that could be trusted at a
    # rung's own distance and price; that landed, though not by the route the
    # old note here expected. It is not a tolerance fitted from banked candle
    # closes (there are two collection runs, nowhere near enough): it is a gate
    # that reads the sim's error per *tail* and charges it against the rung's
    # own price, which is what the deep cheap rungs the first board filled
    # itself with were never bounded by (docs/BUILD_EXCHANGES.md E8b).
    #
    # Staking is still a separate decision from showing, so the flag stays:
    # ``--exchange-paper`` puts the venues back to stake zero without giving up
    # their prices, their CLV or their place on the board.
    parser.add_argument("--exchange-paper", action=argparse.BooleanOptionalAction,
                        default=False,
                        help="price the exchange venues but never stake them "
                             "(default off — exchange rows are staked; "
                             "--exchange-paper returns them to stake zero)")
    # The first live exposure on a venue class whose record is entirely paper.
    # A share of the slate cap, so it moves with the slate rather than being a
    # second absolute number to keep in sync: 0.25 of a 25% slate cap is 6.25%
    # of bankroll across every exchange row on the card.
    parser.add_argument("--exchange-slate-share", type=float, default=0.25,
                        help="most of the slate cap every exchange row may hold "
                             "together (0 stakes none, 1 removes the cap)")
    parser.add_argument("--ladder-tolerance", type=float, default=0.02,
                        help="max probability error the sim's distribution shape may have "
                             "at a rung's distance from the fair line before that exchange "
                             "rung is refused (docs/BUILD_EXCHANGES.md E8). Only exchange "
                             "rows are gated; 0 disables the gate.")
    parser.add_argument("--team-totals", action=argparse.BooleanOptionalAction, default=True,
                        help="fetch + price team totals on live football boards")
    parser.add_argument("--team-total-edge", type=float, default=0.0,
                        help="min points of team-total disagreement to bet (0 = EV gate only)")
    # Player props: priced only when a FantasyPros projections snapshot is
    # supplied (the collect-fantasypros artifact) — the prop model simulates
    # correlated player outcomes from those consensus means. The board comes
    # from --prop-lines-file (a banked collect-football-props parquet) or, live,
    # from The Odds API's per-event endpoint. NFL-first: FP team codes resolve
    # through the NFL alias table; unresolved teams are skipped, never guessed.
    parser.add_argument("--fp-projections",
                        help="FantasyPros projections parquet (enables the prop slate)")
    parser.add_argument("--prop-lines-file",
                        help="banked PropLines parquet (offline prop board)")
    # Confidence calibration for props. 1.0 = the raw model: deliberately
    # untuned until the football prop backtest's shrink sweep picks the values
    # (docs/FOOTBALL_CUTOVER.md Phase 3) — the MLB numbers do not carry over.
    parser.add_argument("--prop-shrink", type=float, default=1.0,
                        help="prop confidence shrink toward 0.5 (1.0 = raw model)")
    parser.add_argument("--exclude-props", default="",
                        help="comma-separated prop markets to skip; '' bets all")
    # The intelligence layer (velocity/intel, docs/INTEL.md): every bet that
    # cleared the EV gate is judged against the game's context — unit matchups,
    # recent form, rest, and the injury report — and tiered into argued pick
    # sets. It confirms, demotes, or vetoes; it never promotes a bet the model
    # didn't like and never touches stakes.
    parser.add_argument("--intel", action=argparse.BooleanOptionalAction, default=True,
                        help="judge qualifying bets against stats/form/rest/injuries")
    parser.add_argument("--injuries-file",
                        help="normalized injuries parquet (the collect_fantasypros "
                             "artifact) — enables availability vetoes")
    # BettingPros prop snapshot (the collect-bettingpros artifact): their own
    # projection block judges every qualifying prop — outside corroboration
    # for the plays product, never a pick source.
    parser.add_argument("--bp-props-file",
                        help="banked bp_props parquet — arms the BettingPros "
                             "prop corroboration signal")
    # The publish gate's floors (docs/PUBLISH_GATE.md §3) are provisional —
    # "reasoned, not yet fitted" — so they are settable per run while
    # scripts/calibrate_publish_gate.py accumulates the boards to fit them on.
    # Moving a *default* stays a deliberate edit in velocity/intel/publish.py.
    parser.add_argument("--publish-min-conviction", type=float,
                        default=DEFAULT_MIN_CONVICTION,
                        help="composite floor a play must reach to post")
    parser.add_argument("--publish-min-context", type=float,
                        default=DEFAULT_MIN_CONTEXT,
                        help="context score that must corroborate a posted play")
    parser.add_argument("--publish-max-plays", type=int, default=DEFAULT_MAX_PLAYS,
                        help="nightly cap on posted plays (the guardrail, not a quota)")
    # Pick'em board: the slip-EV engine over the same prop sim + prop lines
    # (velocity/wagering/pickem_slate) — book-fair marginals, model
    # correlation. Slips below the EV floor are simply not persisted.
    parser.add_argument("--carousel", action="store_true",
                        help="stamp matchup cards with n/total slide badges "
                             "(post the slate as one ordered thread)")
    parser.add_argument("--pickem-top", type=int, default=8,
                        help="max ranked pick'em slips to persist (0 disables)")
    parser.add_argument("--pickem-min-ev", type=float, default=1.0,
                        help="min expected return multiple for a slip (1.0 = breakeven)")
    # Leg probabilities near the payout breakevens (54-58%) are exactly where
    # the devig methods disagree; the worst-case default publishes a leg only
    # when multiplicative, Shin, and power all clear it (docs/EDGE_RESEARCH.md
    # §4). Operators also tax same-game correlation now (reduced payouts /
    # blocked combos), so slips are capped at two legs per game by default and
    # flagged when any two legs share one.
    parser.add_argument("--pickem-devig", default="worst_case",
                        choices=["multiplicative", "additive", "shin", "power", "worst_case"],
                        help="leg-probability devig (worst_case = every method must agree)")
    parser.add_argument("--pickem-max-per-game", type=int, default=2,
                        help="max legs of one slip sharing a game (0 = no cap)")
    # Parlays: legs come only from bets that already cleared the single-bet gate, are
    # priced sim-exactly (correlated within a game, independent across), and must
    # clear their own higher EV bar. Same-game combos are flagged — books reprice
    # correlated SGPs below the product payout, so their EV is an upper bound.
    parser.add_argument("--parlay-max-legs", type=int, default=3,
                        help="max legs per parlay (0 disables parlays)")
    parser.add_argument("--parlay-min-ev", type=float, default=0.05,
                        help="min combined EV per unit for a parlay to be recommended")
    parser.add_argument("--max-parlays", type=int, default=5,
                        help="max parlays to recommend per slate")
    # Portfolio sizing (docs/WAGERING.md W2, docs/EDGE_RESEARCH.md §1.2): the
    # game slate and the prop slate are sized in two independent passes that
    # never see each other, so a game's correlated exposure can stack past any
    # sane aggregate. This stage routes the whole card through
    # portfolio.size_portfolio — correlation de-scaling within each game, the
    # per-game cap, and an aggregate slate cap — and persists the combined,
    # sized card as portfolio_{league}_{stamp}.parquet. The kill-switch stays
    # unreachable until the W1 ledger supplies bankroll state.
    parser.add_argument("--portfolio", action=argparse.BooleanOptionalAction, default=True,
                        help="size the combined card through the portfolio rules")
    parser.add_argument("--max-slate-fraction", type=float, default=0.25,
                        help="aggregate cap: max fraction of bankroll staked per slate")
    # The bankroll ledger (docs/WAGERING.md W1): one private parquet that
    # holds the seed, every recommendation, every placed bet and every
    # settlement. With it, --bankroll only seeds an empty ledger; the run
    # stakes off the ledger's bankroll, open exposure counts against the
    # slate cap, and the drawdown kill-switch finally has its inputs.
    parser.add_argument("--ledger", default=None,
                        help="bankroll ledger parquet (private); --bankroll seeds it when empty")
    parser.add_argument("--ledger-mode", choices=["manual", "auto"], default="manual",
                        help="manual: only bets the operator records (scripts/ledger.py "
                             "place) are placed; auto: every staked row is booked at its "
                             "recommended terms, so the bankroll compounds off the card")
    parser.add_argument("--out", help="folder to persist the slate parquet (private, not git)")
    return parser


# The NFL default is the Round-3 select-chosen blend weight (docs/MODEL_LAB.md:
# select ≤2019 picked w*=0.2, holdout Brier 0.2118 vs 0.2109 for the close
# itself). Every other league keeps the raw model: the anchoring evidence is
# NFL-specific, and the NCAAF totals cut was backtested on the raw model's
# disagreement, which a global anchor would silently re-gate.
# NCAAF joined the anchor on 2026-09-09. The ≥6-point totals filter selects
# games where the raw sim claims P(over) ≈ 0.64 (a 6-point gap is 0.36σ at
# sd 16.7) — a 0.14 edge — while the backtest says those bets win 53.0%
# (docs/BACKTEST_NCAAF.md): a realized edge of ~0.03. Staked raw, every one
# of them sat above the 0.12 adverse-selection ceiling and Kelly sized them
# 3–5× too large; anchored at 0.2 the claimed edge lands on the realized one
# and the points filter stays the selector (it reads fair_total, not the
# probability). Provisional until the S3 staking sweep fits the weight.
# NCAAF at 0.13: the S3 staking sweep on 3,733 out-of-sample totals at the
# ≥6-point filter (2018–2026, the levelled blend) — realized edge +0.026
# against +0.218 raw, so the claim matches the realization at w ≈ 0.12–0.13
# at every threshold from 6 to 10; the live 0.2 claimed 1.7× what it earned
# (docs/BACKTEST_NCAAF.md, the staking sweep).
DEFAULT_MODEL_WEIGHT_BY_LEAGUE = {"nfl": 0.2, "ncaaf": 0.13}


# Leagues in the content + CLV posture: their labs found no promoted edge
# (NCAAB null after FDR, NHL no closes backtest yet, WNBA tracked not staked —
# docs/STRATEGY_REVIEW.md §1.3), so every market prices and grades as paper.
DEFAULT_PAPER_BY_LEAGUE = {"ncaab": True, "nhl": True, "wnba": True}
GAME_MARKETS = ("moneyline", "spread", "total", "team_total_home", "team_total_away")
_TEAM_TOTALS = ("team_total_home", "team_total_away")


def _kelly_label(fraction: float) -> str:
    """``0.25`` reads as ``\u00bc``; anything unusual reads as itself."""
    return {0.25: "\u00bc", 0.5: "\u00bd", 0.75: "\u00be",
            1.0: "full"}.get(round(fraction, 4), f"{fraction:g}\u00d7")


def live_config_rows(
    args: argparse.Namespace, fit_kind: str, cfg: object | None
) -> list[tuple[str, str]]:
    """The run's own description of what it did — label/detail pairs.

    Replaces the hand-maintained ``MODEL_CONFIG`` table the Methods page
    imported from the retired plays app, which had drifted: it still
    described the NCAAF totals cut as ≥4 points after the default moved to
    6, and carried leagues that were dark or retired. Read from the parsed
    args and the built config, this cannot drift.
    """
    rows: list[tuple[str, str]] = [("Ratings", fit_kind)]
    weight = resolve_model_weight(args.model_weight, args.league)
    rows.append(("Market anchoring",
                 "raw model (w = 1.0)" if weight == 1.0
                 else f"belief = market + {weight:g} × (model − market)"))
    if args.league == "ncaaf":
        cuts = []
        if args.ncaaf_total_edge > 0:
            cuts.append(f"totals only at ≥ {args.ncaaf_total_edge:g} pts of disagreement")
        cuts.append("spreads " + ("on" if args.ncaaf_spreads else "sitting out"))
        cuts.append("moneylines " + ("on" if args.ncaaf_moneylines else "sitting out"))
        rows.append(("Selectivity", "; ".join(cuts)))
    prop_edge = resolve_prop_min_edge(args.prop_min_edge, args.min_edge)
    rows.append(("Edge gate", f"min edge {args.min_edge:g} (props {prop_edge:g}), "
                              "positive EV at the shopped price"))
    ceilings = []
    if args.max_edge > 0:
        ceilings.append(f"{args.max_edge:g} absolute")
    if args.max_relative_edge > 0:
        ceilings.append(f"{args.max_relative_edge:.0%} of the fair probability")
    rows.append(("Edge ceilings",
                 " · ".join(ceilings) + " — past either, paper" if ceilings else "off"))
    paper = resolve_paper_markets(args)
    venues = resolve_paper_venues(args)
    paper_label = ("every market — content + CLV posture" if "__all__" in paper
                   else ("team totals" if paper else "none"))
    if venues:
        paper_label += f" · exchanges ({', '.join(sorted(venues))})"
    rows.append(("Paper", paper_label))
    if args.league in FOOTBALL_SDS:
        rows.append(("Simulation", describe_sim(football_sim_config(args.league, args),
                                                args.league)))
    rows.append(("De-vig", f"{args.devig_anchor} anchor, multiplicative"))
    # Built from the config objects rather than written down. This row was a
    # string literal, which is exactly the drift the rest of this function
    # exists to prevent: it hardcoded the slate cap that `--max-slate-fraction`
    # moves, and it never mentioned the same-game correlation de-scaling at
    # all — the term that actually halves a stake when a game carries three
    # bets, and the most common reason a play is sized below its own Kelly.
    from velocity.wagering.portfolio import PortfolioConfig as _Portfolio
    from velocity.wagering.staking import StakingConfig as _Staking

    staking = _Staking()
    portfolio = _Portfolio(max_portfolio_fraction=args.max_slate_fraction)
    rows.append(("Staking",
                 f"{_kelly_label(staking.kelly_fraction)}-Kelly, "
                 f"{staking.max_bet_fraction:.0%} per bet, "
                 f"{portfolio.group_cap_fraction:.0%} per game, "
                 f"{portfolio.max_portfolio_fraction:.0%} per slate, "
                 f"one market class ≤ {portfolio.max_class_fraction:.0%} of the "
                 f"slate; same-game exposure de-scaled at "
                 f"\u03c1={portfolio.group_correlation:g}"))
    if cfg is not None and getattr(cfg, "ladder_tolerance", None):
        tol = float(cfg.ladder_tolerance)  # type: ignore[attr-defined]
        rows.append(("Exchange rungs", f"E8 shape gate: the sim may overstate a rung by "
                                       f"{tol:g} probability, or "
                                       f"{default_relative_tolerance(tol):.0%} of its own price, "
                                       "whichever is tighter; taker fees charged"))
    if getattr(args, "exchanges", False):
        share = getattr(args, "exchange_slate_share", 1.0)
        if resolve_paper_venues(args):
            rows.append(("Exchange stakes", "priced and graded on the board, staked at zero"))
        elif share >= 1.0:
            rows.append(("Exchange stakes", "live and uncapped beyond the slate's own limits"))
        else:
            rows.append(("Exchange stakes",
                         f"live, capped together at {share:.0%} of the slate cap "
                         f"({share * args.max_slate_fraction:.2%} of bankroll)"))
    return rows


# The sim's shape and dispersion per football league (docs/SYSTEM_REVIEW.md
# §2, M1). "normal" is the bivariate normal the sim always drew; "empirical"
# draws residual pairs from the banked walk-forward pool
# (datasets/{league}/sim_residuals.parquet). "constant" holds one sd per
# league; "sloped" moves it with the expected total. Both measured through
# the sim-shape gate (scripts/sim_lab.py, docs/MODEL_LAB.md) and neither
# promoted: the empirical draw trades a little spread shape for moneyline
# calibration (NFL) or totals shape (NCAAF), and the slope hurts totals in
# aggregate. The switches stay for the next round; the defaults are the
# gated sim.
DEFAULT_SIM_SHAPE_BY_LEAGUE = {"nfl": "normal", "ncaaf": "normal"}
DEFAULT_SIM_DISPERSION_BY_LEAGUE = {"nfl": "constant", "ncaaf": "constant"}
FOOTBALL_SDS = {"nfl": (DEFAULT_SD_MARGIN, DEFAULT_SD_TOTAL),
                "ncaaf": (NCAAF_SD_MARGIN, NCAAF_SD_TOTAL)}


# The NFL scoring level: "fit" shifts base_points through the model on the
# training window (velocity.models.level); "constant" is the 22.5 the model
# always assumed. Moves to "fit" only with the lab table (docs/MODEL_LAB.md).
DEFAULT_NFL_LEVEL = "fit"
NFL_LEVEL_SEASONS = 2


def resolve_nfl_level(explicit: str | None) -> str:
    return explicit or DEFAULT_NFL_LEVEL


# The college blend's scores half: "fit" levels its intercept on the trailing
# two seasons (velocity.models.level.calibrate_scores_level); "constant"
# keeps the ridge's own. Promoted by the college level round
# (docs/MODEL_LAB.md): the totals record at the ≥6 filter 52.6% → 53.3%,
# Brier flat, calibration better — the ridge's intercept lagged the
# post-2021 scoring drop by 1–2 points a game.
DEFAULT_NCAAF_LEVEL = "fit"


def resolve_ncaaf_level(explicit: str | None) -> str:
    return explicit or DEFAULT_NCAAF_LEVEL


def resolve_sim_shape(explicit: str | None, league: str) -> str:
    return explicit or DEFAULT_SIM_SHAPE_BY_LEAGUE.get(league, "normal")


def resolve_sim_dispersion(explicit: str | None, league: str) -> str:
    return explicit or DEFAULT_SIM_DISPERSION_BY_LEAGUE.get(league, "constant")


def football_sim_config(league: str, args: argparse.Namespace) -> SimConfig:
    """The football sim for this run: league sds, shape, dispersion, size.

    An empirical shape with no committed pool falls back to the normal and
    says so — a missing bank is a build gap, never a silent change of sim.
    """
    from velocity.models.residuals import load_residual_pool

    sd_margin, sd_total = FOOTBALL_SDS.get(league, (DEFAULT_SD_MARGIN, DEFAULT_SD_TOTAL))
    kwargs: dict[str, object] = {
        "n_sims": args.n_sims, "sd_margin": sd_margin, "sd_total": sd_total,
    }
    if resolve_sim_dispersion(getattr(args, "sim_dispersion", None), league) == "sloped":
        slope_m, slope_t, anchor = FOOTBALL_SD_SLOPES.get(league, (0.0, 0.0, 0.0))
        if anchor > 0:
            kwargs.update(sd_margin_slope=slope_m, sd_total_slope=slope_t,
                          sd_anchor_total=anchor)
    if resolve_sim_shape(getattr(args, "sim_shape", None), league) == "empirical":
        pool = load_residual_pool(league)
        if pool is None:
            print(f"no residual pool banked for {league}; simulating with the normal")
        else:
            kwargs["residuals"] = pool
    return SimConfig(**kwargs)  # type: ignore[arg-type]


def describe_sim(config: SimConfig, league: str) -> str:
    """The Methods row: what shape and width this run simulated with."""
    shape = ("normal" if config.residuals is None
             else f"empirical ({len(config.residuals)} banked residual pairs)")
    width = f"σ {config.sd_margin:g} margin / {config.sd_total:g} total"
    if config.sd_total_slope or config.sd_margin_slope:
        width += (f", {config.sd_total_slope:+.3f}/pt of expected total "
                  f"around {config.sd_anchor_total:.0f}")
    return f"{shape} · {width} · {config.n_sims:,} sims"


def resolve_paper(explicit: bool | None, league: str) -> bool:
    """Whether this run stakes nothing — the flag, else the league posture."""
    if explicit is not None:
        return explicit
    return DEFAULT_PAPER_BY_LEAGUE.get(league, False)


def resolve_paper_markets(args: argparse.Namespace) -> frozenset[str]:
    """The markets this run prices but never stakes (docs/STRATEGY_REVIEW.md S2)."""
    if resolve_paper(args.paper, args.league):
        return frozenset(GAME_MARKETS) | frozenset({"__all__"})
    return frozenset(_TEAM_TOTALS) if args.team_totals_paper else frozenset()


def resolve_paper_venues(args: argparse.Namespace) -> frozenset[str]:
    """The venues this run prices but never stakes — the exchanges, by default."""
    from velocity.store.schema import LADDER_BOOKS

    if not getattr(args, "exchanges", False):
        return frozenset()
    return frozenset(LADDER_BOOKS) if args.exchange_paper else frozenset()


def _prop_paper_markets(args: argparse.Namespace) -> frozenset[str]:
    """Prop markets this run prices but never stakes — the league's paper posture.

    Props have no market list of their own, so a paper league marks every
    prop market it prices by intercepting the config at stake time: the slate
    treats a market set containing ``"__all__"`` as "all of them".
    """
    return frozenset({"__all__"}) if resolve_paper(args.paper, args.league) else frozenset()


def resolve_model_weight(explicit: float | None, league: str) -> float:
    """The market-anchoring weight for this run — the flag, else the league default."""
    if explicit is not None:
        return explicit
    return DEFAULT_MODEL_WEIGHT_BY_LEAGUE.get(league, 1.0)


def ncaaf_base_points(games: pd.DataFrame, seasons: int = 2) -> float:
    """The NCAAF blend's per-team scoring level: half the trailing-two-season
    mean total (:func:`velocity.models.level.mean_points_per_team`).

    Replaces the hardcoded 28.5, a level constant from the pre-2023-clock-rules
    regime: college totals dropped ~4 points per game after the rule change
    (57–58 through 2018 → ~53 by 2023–25), and a fixed 28.5 pushed the blend's
    every projected total high — on the 2026 week-1 board it tilted the ≥4-point
    totals filter to 22 overs of 25 fired. The blend's *ratings* (offense/defense
    deviations) are unaffected; only the level they hang from tracks the data.
    """
    from velocity.models.level import mean_points_per_team

    return mean_points_per_team(games, seasons, fallback=28.5)


def resolve_prop_min_edge(explicit: float | None, min_edge: float) -> float:
    """The prop slate's edge bar — the flag, else 2× the game bar.

    Wider for the noisiest markets per DESIGN §6.2; the multiplier is a
    reasoned, provisional policy until the football shrink sweep re-tunes
    per-market values on the banked archive (docs/WAGERING.md Phase W4).
    """
    return float(explicit) if explicit is not None else 2.0 * min_edge


def parse_market_edges(pairs: Sequence[str]) -> dict[str, float]:
    """``MARKET=EDGE`` CLI pairs → the ``min_edge_by_market`` mapping."""
    out: dict[str, float] = {}
    for pair in pairs:
        market, sep, value = pair.partition("=")
        if not sep or not market.strip():
            raise SystemExit(f"--min-edge-market wants MARKET=EDGE, got {pair!r}")
        try:
            out[market.strip()] = float(value)
        except ValueError:
            raise SystemExit(
                f"--min-edge-market wants a numeric edge, got {pair!r}") from None
    return out


def main() -> None:
    args = build_parser().parse_args()

    now = datetime.now(UTC)
    generated_at = pd.Timestamp(now).tz_localize(None)

    if args.out:
        Path(args.out).mkdir(parents=True, exist_ok=True)

    ledger = _open_ledger(args, generated_at)

    schedule = _league_schedule(args, Path(args.data), generated_at) if args.data else None
    project, known_teams, ratings_frame, fit_kind = _build_projection(args, schedule)

    payload = _load_snapshot(args)
    lines = normalize_odds_events(payload)
    events = extract_events(payload)
    # Live football boards: team totals ride the per-event endpoint. Best-effort
    # — a failed fetch just leaves the three main markets on the board.
    if (args.team_totals and not args.snapshot_file
            and args.league in ("nfl", "ncaaf")):
        try:
            from velocity.ingest.theoddsapi import TheOddsAPIClient

            team_lines = TheOddsAPIClient.from_env().team_totals(args.league)
            if not team_lines.empty:
                lines = pd.concat([lines, team_lines], ignore_index=True)
                print(f"team totals: {len(team_lines)} lines joined the board")
        except Exception as exc:  # noqa: BLE001 - an optional derivative fetch
            print(f"team totals skipped: {exc}")
    # Exchange boards ride alongside the sportsbook board, re-keyed onto its
    # game ids so every venue's price for a game is shopped together
    # (docs/BUILD_EXCHANGES.md E6). Best-effort: a venue that fails is
    # reported and skipped, exactly like the team-totals fetch above.
    # A banked sportsbook board does not imply an offline run: both exchanges
    # are free and keyless, so they are pulled even when --snapshot-file
    # supplies the book side. --offline is the switch that means "no network".
    if args.exchanges and not args.offline and args.league in ("nfl", "ncaaf"):
        from velocity.ingest.exchanges import fetch_exchange_board

        exchange_lines, venue_notes = fetch_exchange_board(
            args.league, known_teams, events, generated_at
        )
        for venue, note in venue_notes.items():
            print(f"{venue}: {note['lines']} lines across {note['games']} board games")
        if not exchange_lines.empty:
            lines = pd.concat([lines, exchange_lines], ignore_index=True)
    elif args.exchanges:
        print("exchanges: skipped (--offline, or a league with no exchange board)")

    n_board = len(events)
    if args.max_days > 0 and not events.empty:
        kickoff = pd.to_datetime(events["kickoff"], errors="coerce")
        window = (kickoff >= generated_at - pd.Timedelta(hours=6)) & (
            kickoff <= generated_at + pd.Timedelta(days=args.max_days)
        )
        events = events[window].reset_index(drop=True)
    print(f"=== Live slate: {args.league.upper()} — {len(events)} of {n_board} board "
          f"games inside the {args.max_days:g}-day window ===")

    frame = pd.DataFrame()
    projections: dict = {}
    canonical = pd.DataFrame()
    game_log = None
    if events.empty:
        print("no games on the board (off-season or empty snapshot)")
    else:
        # NCAAF bets totals on points of disagreement (the backtested cut); NFL
        # leaves it off and gates on probability edge alone.
        total_edge = args.ncaaf_total_edge if args.league == "ncaaf" else 0.0
        model_weight = resolve_model_weight(args.model_weight, args.league)
        game_excludes: frozenset[str] = frozenset()
        if args.league == "ncaaf" and not args.ncaaf_spreads:
            game_excludes |= {"spread"}
            print("NCAAF spreads: sitting out (50.1% ATS flat, no edge at any "
                  "disagreement threshold — docs/BACKTEST_NCAAF.md); "
                  "--ncaaf-spreads re-enables")
        if args.league == "ncaaf" and not args.ncaaf_moneylines:
            game_excludes |= {"moneyline"}
            print("NCAAF moneylines: sitting out (never backtested; 60% of the first "
                  "live card's exposure — docs/STRATEGY_REVIEW.md §1.2); "
                  "--ncaaf-moneylines re-enables")
        paper_markets = resolve_paper_markets(args)
        if "__all__" in paper_markets:
            print(f"{args.league.upper()}: paper posture — every market priced and "
                  "graded, nothing staked (--no-paper to stake)")
        elif paper_markets:
            print("team totals: paper — priced and graded, staked at zero until "
                  "posted closes calibrate the gate (--no-team-totals-paper to stake)")
        paper_venues = resolve_paper_venues(args)
        if paper_venues:
            print(f"exchanges: {', '.join(sorted(paper_venues))} priced and graded on the "
                  "board, staked at zero (--no-exchange-paper to stake them)")
        elif getattr(args, "exchanges", False):
            from velocity.store.schema import LADDER_BOOKS

            share = args.exchange_slate_share
            print(f"exchanges: {', '.join(sorted(LADDER_BOOKS))} LIVE — staked, and "
                  f"capped together at {share:.0%} of the slate cap "
                  f"({share * args.max_slate_fraction:.2%} of bankroll). Rungs still "
                  "pass the E8b shape gate at their own distance and price "
                  "(--exchange-paper returns them to zero)")
        max_edge = args.max_edge if args.max_edge > 0 else None
        max_rel = args.max_relative_edge if args.max_relative_edge > 0 else None
        if max_edge is not None or max_rel is not None:
            print(f"edge ceilings: absolute {max_edge} · relative {max_rel} — a bigger "
                  "edge is logged as paper (adverse-selection guard)")
        cfg = SlateConfig(
            exclude_closing=False, min_edge=args.min_edge, starting_bankroll=args.bankroll,
            ladder_tolerance=args.ladder_tolerance if args.ladder_tolerance > 0 else None,
            league=args.league,
            model_weight=model_weight,
            min_edge_by_market=parse_market_edges(args.min_edge_market),
            exclude_markets=game_excludes,
            min_total_disagreement=total_edge,
            min_team_total_disagreement=args.team_total_edge,
            paper_markets=paper_markets,
            paper_venues=paper_venues,
            max_edge=max_edge,
            max_relative_edge=max_rel,
            devig_anchor=args.devig_anchor,
        )
        if model_weight != 1.0:
            print(f"market anchoring: belief = market + {model_weight:g} × "
                  f"(model − market); clearing min-edge {args.min_edge:g} takes "
                  f"{args.min_edge / model_weight:g} of raw disagreement")
        if total_edge > 0.0:
            print(f"NCAAF totals filter: model must differ from the number by "
                  f"≥ {total_edge:g} points")
        # Project once, then price off those projections (reused for the workbook).
        # College: the provider names carry nicknames ("Georgia Bulldogs",
        # "Duke Blue Devils") while the fitted model keys by school ("Georgia",
        # "Duke") — bridge by prefix match.
        aliases = None
        if args.league in ("ncaaf", "ncaab"):
            from velocity.wagering.live import nickname_aliases

            provider_names = set(events["home_team"]) | set(events["away_team"])
            aliases = nickname_aliases(provider_names, known_teams)
        elif args.league == "nhl":
            # Provider full names → NHL abbreviations (the model's keys).
            from velocity.ingest.hockey import NHL_TEAM_ALIASES

            aliases = dict(NHL_TEAM_ALIASES)
        # Neutral sites (docs/SYSTEM_REVIEW.md §3.2): the board never says where
        # a game is played; the schedule does, and every model takes the flag.
        from velocity.wagering.live import neutral_site_map

        neutral = neutral_site_map(events, schedule, known_teams, aliases)
        flagged = sorted(gid for gid, flag in neutral.items() if flag)
        if flagged:
            print(f"neutral sites: {len(flagged)} board game(s) priced without home field")
        projections, unresolved = project_board(
            events, project, known_teams, aliases, neutral_by_game=neutral
        )
        canonical = canonicalize_sides(lines, events)
        canonical = canonical[canonical["game_id"].astype(str).isin(projections)]
        games_min = events[["game_id", "kickoff"]].copy()
        games_min["game_id"] = games_min["game_id"].astype(str)
        game_log = build_slate(projections, canonical, games_min, cfg)
        frame = slate_to_frame(game_log)

        if frame.empty:
            print("no bets cleared the edge threshold.")
        else:
            staked = frame[frame["stake"] > 0]
            paper = frame[frame["stake"] <= 0]
            shown = staked.assign(stake_pct=(staked["stake"] / args.bankroll * 100).round(2))
            with pd.option_context("display.width", 160, "display.max_columns", None):
                print(f"\n{len(shown)} recommended bets (stake as % of {args.bankroll:.0f}):")
                print(shown.drop(columns=["note"]).to_string(index=False))
                if not paper.empty:
                    print(f"\n{len(paper)} paper rows — priced and graded, not staked:")
                    print(paper.drop(columns=["stake"]).to_string(index=False))
            print(f"\ntotal staked: {staked['stake'].sum():.2f}")

        if unresolved:
            print(f"\n{len(unresolved)} game(s) skipped — teams not in the model's universe:")
            for u in unresolved:
                print(f"  {u['away_team']} @ {u['home_team']} ({u['reason']})")

    # Player-prop slate — priced off the FantasyPros-driven correlated prop sim
    # when a projections snapshot is supplied. Best-effort: never breaks the
    # game slate.
    props_frame = None
    props_by_game: dict = {}
    key_to_name: dict[str, str] = {}
    prop_lines_used: pd.DataFrame | None = None
    watch_by_game: dict = {}
    if args.fp_projections and projections and not events.empty:
        props_frame, props_by_game, key_to_name, prop_lines_used = _prop_slate(
            args, events, projections, now, generated_at
        )
    elif args.league == "mlb" and projections and not events.empty:
        # The headline MLB prop (docs/PROPS.md): pitcher strikeouts, priced
        # from the banked starters history — no FantasyPros dependency. The
        # watch entries put the K board + staked calls on the sheets.
        props_frame, watch_by_game = _mlb_k_slate(
            args, events, projections, now, generated_at)

    # Pick'em board — the slip-EV engine over the same prop sim + prop lines.
    # Book-fair marginals, model correlation (velocity/wagering/pickem_slate).
    # Best-effort: never breaks the game or prop slates.
    if props_by_game and prop_lines_used is not None:
        _pickem_slate(args, props_by_game, prop_lines_used, now, generated_at)

    # Parlay slate — combine the qualifying single game bets into sim-exact
    # correlated parlays. Works offline too. Prop legs stay out deliberately:
    # the game sim and the prop sim are independent draws, so a mixed same-game
    # ticket would index-pair unrelated sample arrays — the exact phantom
    # correlation the parlay engine's docstring warns against. They join once
    # the two sims share a draw.
    if projections and args.parlay_max_legs >= 2:
        _parlay_slate(args, projections, game_log, now, generated_at)

    # Portfolio sizing — the combined card through correlation de-scaling and
    # the aggregate slate cap. Best-effort; the per-slate parquets keep their
    # solo-Kelly stakes for backtest comparability.
    if args.portfolio and (not frame.empty or (props_frame is not None
                                               and not props_frame.empty)):
        _portfolio_card(args, frame, props_frame, now, generated_at, ledger=ledger)

    # Intelligence layer — judge every qualifying bet against the game's
    # evidence and emit tiered, argued pick sets. Best-effort like every
    # surface after the game slate: a failure never breaks the slate. The
    # convictions feed the deep-dive verdict band's tier chips and rationale.
    convictions = None
    if args.intel and projections and args.data:
        convictions = _intel_layer(
            args, events, projections, game_log, props_frame, now, generated_at
        )

    # The PUBLISH gate — which of the staked plays are worth POSTING. A much
    # higher bar than bettable (velocity/intel/publish.py): tier, an edge
    # BAND with a ceiling, and no adverse line move. Most nights it returns
    # nothing, which is the intended output — "no picks is a pick".
    if convictions and args.out:
        try:
            from velocity.intel.publish import gate_summary, publish_slate

            reference = _previous_board(args, events)
            if reference is not None:
                print(f"publish gate: drift measured against the previous archived "
                      f"snapshot ({len(reference)} rows)")
            published, audit = publish_slate(
                convictions, canonical, reference,
                min_conviction=args.publish_min_conviction,
                min_context=args.publish_min_context,
                max_plays=args.publish_max_plays,
            )
            print(f"\npublish gate: {gate_summary(audit)}")
            for c in published:
                bet = c.bet
                point = "" if bet.point is None else f" {bet.point:+g}"
                print(f"  POST  {bet.market} {bet.side}{point} "
                      f"({bet.price:+.0f} {bet.book}) tier {c.tier}")
            stamp_gate = now.strftime("%Y%m%dT%H%M%SZ")
            audit.assign(league=args.league, generated_at=generated_at).to_parquet(
                Path(args.out) / f"publish_{args.league}_{stamp_gate}.parquet",
                index=False)
        except Exception as exc:  # noqa: BLE001 - a report surface, never the slate
            print(f"publish gate skipped: {exc}")

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        persisted = frame.assign(league=args.league, generated_at=generated_at)
        parquet = out_dir / f"slate_{args.league}_{stamp}.parquet"
        persisted.to_parquet(parquet, index=False)
        print(f"\nwrote {len(persisted)} slate rows to {parquet}")
        # The power-ratings table behind the fit — the site's Ratings page.
        if not ratings_frame.empty:
            ratings_frame.assign(league=args.league, generated_at=generated_at).to_parquet(
                out_dir / f"ratings_{args.league}_{stamp}.parquet", index=False
            )
            print(f"wrote {len(ratings_frame)} team ratings")
        # The "what's live" block, from the run itself rather than a table
        # someone has to remember to edit (the site's Methods page).
        config_rows = live_config_rows(args, fit_kind, cfg if not events.empty else None)
        pd.DataFrame(config_rows, columns=["label", "detail"]).assign(
            league=args.league, generated_at=generated_at
        ).to_parquet(out_dir / f"config_{args.league}_{stamp}.parquet", index=False)
        # Persist the game→teams+kickoff map so a later grader can join the
        # schedule feed's finals (a different id space) back onto these Odds-API
        # game ids.
        if not events.empty:
            games_cols = ["game_id", "home_team", "away_team", "kickoff"]
            games_map = events[games_cols].assign(league=args.league)
            games_map.to_parquet(out_dir / f"games_{args.league}_{stamp}.parquet", index=False)
        # Persist the per-game model numbers (win %, projected score, fair lines)
        # — the matchup-card data the plays app renders without re-simulating.
        if projections:
            proj_frame = _projections_frame(projections)
            proj_frame.assign(league=args.league, generated_at=generated_at).to_parquet(
                out_dir / f"projections_{args.league}_{stamp}.parquet", index=False
            )
            # Pregame total/margin distributions — tomorrow's Sim Check pins the
            # actual result at its true percentile on these.
            from velocity.report.social import distributions_frame

            distributions_frame(projections).assign(league=args.league).to_parquet(
                out_dir / f"distributions_{args.league}_{stamp}.parquet", index=False
            )
        _write_workbook(out_dir, stamp, args, events, projections, frame, props_frame,
                        generated_at)
        # Social market-vs-model cards — one shareable X-frame PNG per game plus
        # a captions file of post copy. Best-effort, like every report surface.
        # NFL identity = club codes + logos; NCAAF identity = school
        # abbreviation + official colors only (no marks — licensing posture,
        # see report/assets.py).
        if projections:
            _write_social_cards(
                args, events, projections, canonical, props_by_game, key_to_name,
                prop_lines_used, stamp,
                game_log=game_log, convictions=convictions,
                watch_by_game=watch_by_game,
            )


def _previous_board(args: argparse.Namespace, events: pd.DataFrame) -> pd.DataFrame | None:
    """The newest archived odds snapshot older than this run, for the drift rule.

    The hourly collector's parquets under ``--odds-dir`` are the only record
    of where the market *was*; on a single board the publish gate's
    adverse-drift rule had nothing to compare and never fired
    (docs/SYSTEM_REVIEW.md §4.4). Canonicalized against this run's events so
    the keys match. Best-effort: no archive, no reference, no rejection.
    """
    try:
        odds_dir = Path(args.odds_dir)
        if args.offline or not odds_dir.exists() or events.empty:
            return None
        snapshots = sorted(odds_dir.rglob("odds_lines_*.parquet"))
        if not snapshots:
            return None
        game_ids = set(events["game_id"].astype(str))
        for path in reversed(snapshots):
            snap = pd.read_parquet(path)
            if "league" in snap.columns:
                snap = snap[snap["league"].astype(str) == args.league]
            snap = snap[snap["game_id"].astype(str).isin(game_ids)]
            if snap.empty:
                continue
            return canonicalize_sides(snap, events)
        return None
    except Exception as exc:  # noqa: BLE001 - a gate input, never the slate
        print(f"previous board unavailable ({exc}); drift rule stands down")
        return None


def _open_ledger(args: argparse.Namespace, generated_at: pd.Timestamp) -> Any:
    """The bankroll ledger, when the run keeps one (docs/WAGERING.md W1).

    An empty ledger is seeded with ``--bankroll``; from then on the run's
    bankroll is the ledger's — the seed plus every adjustment and settlement
    — and ``--bankroll`` is ignored, so stakes compound off real state
    instead of a fresh notional every run. Best-effort: a ledger that fails
    to load is reported and the run stakes against the CLI bankroll.
    """
    if not args.ledger:
        return None
    try:
        from velocity.wagering.ledger import Ledger

        ledger = Ledger.load(args.ledger)
        if ledger.seed(args.bankroll, at=generated_at, note="seeded by the slate runner"):
            ledger.save()
            print(f"ledger: seeded {args.ledger} at {args.bankroll:.2f}")
        state = ledger.state()
        if state.current > 0:
            args.bankroll = state.current
        else:
            print(f"ledger: bankroll is {state.current:.2f} — the kill-switch will halt "
                  "the card; adjust the ledger (scripts/ledger.py adjust) to resume")
        print(f"ledger ({args.ledger_mode} mode): {state.describe()}")
        return ledger
    except Exception as exc:  # noqa: BLE001 - the ledger never breaks the slate
        print(f"ledger skipped ({exc}); staking against --bankroll {args.bankroll:.2f}")
        return None


def _clean_term(value: object) -> Any:
    """A term as the ledger wants it: ``None`` for a missing or NaN cell."""
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    return value


def _record_card(
    ledger: Any, card: pd.DataFrame, args: argparse.Namespace, stamp: str,
    generated_at: pd.Timestamp, halted: str | None,
) -> None:
    """Append the sized card to the ledger as ``recommended`` rows.

    In ``auto`` mode every staked row that is not already on the books —
    placed off an earlier card this week, skipped, or settled — is placed
    at its recommended terms, so the bankroll compounds off the card while
    nobody is placing by hand. A halted card is recorded and placed nowhere.
    """
    try:
        n_rec = ledger.recommend(card, league=args.league, stamp=stamp, at=generated_at)
        placed = 0
        if args.ledger_mode == "auto" and halted is None:
            todo = ledger.latest_recommendations(args.league)
            for row in todo[todo["status"] == "open"].to_dict("records"):
                # The row's own terms, not the ledger's guess. A bet_id is a
                # view and a view can have been recommended as several
                # contracts, so inheriting "the newest" is only right by
                # accident — and wrong the moment a ladder is on the board.
                ledger.place(row["bet_id"], float(row["stake"]), at=generated_at,
                             price=_clean_term(row.get("price")),
                             book=_clean_term(row.get("book")),
                             point=_clean_term(row.get("point")),
                             note="auto: booked at the recommended terms")
                placed += 1
        ledger.save()
        held = int((card.get("held", pd.Series(dtype=bool)) == True).sum())  # noqa: E712
        print(f"ledger: {n_rec} recommended row(s) appended"
              + (f", {placed} placed (auto)" if args.ledger_mode == "auto" else "")
              + (f", {held} already on the books" if held else "")
              + f"; {ledger.state().describe()}")
    except Exception as exc:  # noqa: BLE001 - the ledger never breaks the slate
        print(f"ledger append skipped ({exc})")


def _drawdown_halt(ledger: Any) -> str | None:
    """The kill-switch reason from the ledger alone, or ``None``.

    Split out so it can be read *outside* the sizing guard. Everything in
    :func:`_portfolio_card` runs under a broad ``except`` so that a sizing
    failure never breaks the slates — a good trade for sizing, and a bad one
    for the halt, which the same guard used to swallow along with it. A
    bankroll past the drawdown threshold could then go unannounced because
    something unrelated raised a few lines earlier, and the only signal was a
    one-line notice that read like housekeeping.

    The drawdown threshold is a :class:`PortfolioConfig` default and does not
    move with the slate cap or the venue caps, so reading it here gives the
    same answer the sized path would.
    """
    if ledger is None:
        return None
    try:
        from velocity.wagering.portfolio import PortfolioConfig, should_halt

        threshold = PortfolioConfig().max_drawdown_fraction
        state = ledger.state()
        if not should_halt(state.current, state.peak, threshold):
            return None
        return (f"drawdown {state.drawdown:.0%} ≥ {threshold:.0%} "
                f"(bankroll {state.current:.2f} from a peak of {state.peak:.2f})")
    except Exception as exc:  # noqa: BLE001 - an unreadable ledger is reported, not raised
        print(f"\n=== KILL-SWITCH UNREADABLE — could not evaluate the halt: {exc} ===")
        return None


def _portfolio_card(  # noqa: PLR0913, PLR0915 - the sizing seam takes the card's parts
    args: argparse.Namespace,
    frame: pd.DataFrame,
    props_frame: pd.DataFrame | None,
    now: datetime,
    generated_at: pd.Timestamp,
    ledger: Any = None,
) -> None:
    """Size the combined game + prop card through the portfolio rules.

    One correlation group per game (a spread, its total, its team totals, and
    its props share a group), the per-game cap, and the aggregate slate cap.
    With a ledger, the kill-switch and open exposure apply: a bankroll past
    the drawdown threshold zeroes the whole card *explicitly*, and money
    already on the table today counts against the slate cap. Prints the
    exposure summary and persists the sized combined card; the per-slate
    parquets keep their solo-Kelly stakes.
    """
    # Read before the guard, so no failure below can hide a halt.
    standing_halt = _drawdown_halt(ledger)
    announced = False
    try:
        from velocity.wagering.portfolio import (
            BetCandidate,
            PortfolioConfig,
            should_halt,
            size_portfolio,
        )

        parts = []
        if not frame.empty:
            parts.append(frame.assign(kind="game"))
        if props_frame is not None and not props_frame.empty:
            parts.append(props_frame.assign(kind="prop"))
        card = pd.concat(parts, ignore_index=True, sort=False)
        # Paper rows (stake 0 — a market not yet trusted, or an edge past the
        # ceiling) are graded, not sized: they take no share of the card.
        card = card[card["stake"] > 0].reset_index(drop=True)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        if card.empty:
            print("\n=== Portfolio-sized card — nothing staked (paper posture) ===")
            return
        from velocity.store.schema import LADDER_BOOKS

        def _venue_class(row: dict) -> str | None:
            """The exchange family, or ``None`` for a sportsbook (uncapped).

            Kalshi and Polymarket share one cap rather than holding one each:
            what is untested about them is the same sim, the same ladder and
            the same shape gate, and that shared risk is the larger one. Two
            separate caps would let the pair hold twice what the evidence
            supports while each looked individually bounded.
            """
            book = str(row.get("book", "")).strip().lower()
            return "exchange" if book in LADDER_BOOKS else None

        candidates = [
            BetCandidate(
                key=str(i),
                stake_fraction=float(row["stake"]) / args.bankroll,
                group=str(row["game_id"]),
                # One model assumption per class: a market for game bets, the
                # prop market for props. Capped at half the slate.
                market_class=(f"prop:{row['market']}" if row.get("kind") == "prop"
                              else str(row["market"])),
                venue=_venue_class(row),
            )
            for i, row in enumerate(card.to_dict("records"))
        ]
        # The exchanges' first live exposure, bounded as a share of the slate
        # cap. A share of 1 removes the cap rather than setting it to the whole
        # slate — the same arithmetic, but it says so.
        share = float(getattr(args, "exchange_slate_share", 1.0))
        venue_caps = {} if share >= 1.0 else {"exchange": max(0.0, share)}
        config = PortfolioConfig(max_portfolio_fraction=args.max_slate_fraction,
                                 venue_caps=venue_caps)

        # The ledger's say: the kill-switch, and the room left under the
        # slate cap once today's open bets are counted. A bet already on the
        # books (placed off an earlier card this week) is held, not doubled.
        # Seeded from the halt read before the guard, so the drawdown case is
        # already decided here even if something below would have raised.
        halted: str | None = standing_halt
        current = peak = None
        card["held"] = False
        if ledger is not None:
            from velocity.wagering.ledger import bet_id

            state = ledger.state()
            current, peak = state.current, state.peak
            # Is this view already on the books? The ledger owns that question
            # — matching on the view, then on implied probability, so a line
            # that merely ticked is the position already held while a rung
            # priced somewhere else entirely is a new one.
            holds = [
                ledger.holding(args.league, r["game_id"], r["market"], r["side"],
                               r.get("player"), price=_clean_term(r.get("price")))
                for r in card.to_dict("records")
            ]
            ids = [bet_id(args.league, r["game_id"], r["market"], r["side"], r.get("player"),
                          r.get("point"))
                   for r in card.to_dict("records")]
            card["held"] = [h is not None for h in holds]
            # Name the contract already on the books next to the one today's
            # card wanted, so a crowded-out venue is visible rather than a
            # count. This is how an inert exchange go-live was found.
            card["held_by"] = [
                None if h is None else
                f"{h.get('book') or '?'} "
                f"{'' if _clean_term(h.get('point')) is None else h['point']}".strip()
                for h in holds
            ]
            crowded = [
                f"{r['book']} {r['market']} {r['side']}"
                f"{'' if _clean_term(r.get('point')) is None else ' ' + str(r['point'])}"
                f" held by {r['held_by']}"
                for r in card.to_dict("records")
                if r.get("held") and r.get("held_by")
                and str(r["held_by"]).split(" ")[0] != str(r.get("book"))
            ]
            if crowded:
                print(f"held at a different venue ({len(crowded)}) — today's row never places:")
                for line in crowded[:8]:
                    print(f"    {line}")
                if len(crowded) > 8:
                    print(f"    … and {len(crowded) - 8} more")
            on_card = ledger.open_bets()
            elsewhere = float(on_card.loc[~on_card["bet_id"].isin(ids), "stake"].sum())
            if should_halt(current, peak, config.max_drawdown_fraction):
                halted = (f"drawdown {state.drawdown:.0%} ≥ "
                          f"{config.max_drawdown_fraction:.0%} (bankroll {current:.2f} "
                          f"from a peak of {peak:.2f})")
            elif elsewhere > 0:
                room = args.max_slate_fraction - elsewhere / args.bankroll
                if room <= 0:
                    halted = (f"open exposure {elsewhere:.2f} on other games already "
                              f"fills the {args.max_slate_fraction:.0%} slate cap")
                else:
                    # Rebuilt for the smaller slate cap — and it has to carry
                    # the venue caps with it. Constructing a bare config here
                    # silently dropped the exchange cap on exactly the days
                    # money was already on the table, which is when it matters.
                    config = PortfolioConfig(max_portfolio_fraction=room,
                                             venue_caps=config.venue_caps)
                    print(f"open exposure {elsewhere:.2f} on games off today's card "
                          f"leaves {room:.1%} of bankroll under the slate cap")
        if halted is not None:
            sized = {c.key: 0.0 for c in candidates}
            print(f"\n=== KILL-SWITCH — halted: {halted}; every stake zeroed ===")
            announced = True
        else:
            sized = size_portfolio(candidates, args.bankroll, config,
                                   current_bankroll=current, peak_bankroll=peak)
        card["stake_solo"] = card["stake"]
        card["stake"] = [round(sized[str(i)], 4) for i in range(len(card))]
        card["halted"] = halted is not None
        if halted is not None:
            card["note"] = f"halted: {halted}"

        solo_total = float(card["stake_solo"].sum())
        total = float(card["stake"].sum())
        per_game = card.groupby("game_id")["stake"].sum().sort_values(ascending=False)
        print(f"\n=== Portfolio-sized card — {len(card)} bets across "
              f"{card['game_id'].nunique()} games ===")
        print(f"solo-Kelly total {solo_total:.2f} → sized total {total:.2f} "
              f"({total / args.bankroll:.1%} of bankroll, cap "
              f"{args.max_slate_fraction:.0%}; correlated same-game exposure "
              f"de-scaled at ρ={config.group_correlation:g})")
        top = ", ".join(f"{gid}: {amt:.2f}" for gid, amt in per_game.head(3).items())
        print(f"largest game exposures — {top}")
        if bool(card["held"].any()):
            print(f"{int(card['held'].sum())} of these are already on the books from an "
                  "earlier card — held, not re-placed")

        if args.out:
            dest = Path(args.out) / f"portfolio_{args.league}_{stamp}.parquet"
            # The bankroll and slate cap ride along so the site's exposure
            # tile reads sized total / cap from the card itself.
            card.assign(league=args.league, generated_at=generated_at,
                        bankroll=float(args.bankroll),
                        slate_cap=float(args.max_slate_fraction)).to_parquet(
                dest, index=False
            )
            print(f"wrote the sized card to {dest}")
        if ledger is not None:
            _record_card(ledger, card, args, stamp, generated_at, halted)
    except Exception as exc:  # noqa: BLE001 - sizing never breaks the slates
        # Loud, and specific about what did not happen. This used to read
        # "portfolio sizing skipped: ..." — a line that looks like housekeeping
        # for an outcome that means no sized card, no ledger record and, before
        # the halt was hoisted above, no kill-switch either.
        print(f"\n=== PORTFOLIO SIZING FAILED ({type(exc).__name__}: {exc}) ===\n"
              "    No sized card was written and nothing was recorded to the "
              "ledger. The per-slate parquets keep their solo-Kelly stakes, "
              "which are NOT portfolio-capped — do not stake from them unsized.")
        if standing_halt is not None and not announced:
            print(f"=== KILL-SWITCH — halted: {standing_halt} ===\n"
                  "    Read from the ledger before sizing, so it stands "
                  "regardless of the failure above: stake nothing today.")


def _intel_layer(  # noqa: PLR0913 - the orchestration seam takes the slate's parts
    args: argparse.Namespace,
    events: pd.DataFrame,
    projections: dict,
    game_log: object,
    props_frame: pd.DataFrame | None,
    now: datetime,
    generated_at: pd.Timestamp,
) -> list | None:
    """Judge every qualifying bet against its game's context (velocity/intel).

    Builds the context library point-in-time (``as_of`` = this run) from the
    same committed datasets the model fit on, plus the optional injuries
    snapshot, then assesses the game slate and the prop slate and prints the
    tiered pick sets. Persists ``intel_{league}_{stamp}.parquet`` beside the
    slate. A missing dataset or snapshot only narrows the evidence (signals
    abstain); any failure leaves the slate untouched.
    """
    try:
        from velocity.intel import (
            ContextLibrary,
            assess_bets,
            build_pick_sets,
            default_game_signals,
            default_prop_signals,
            intel_frame,
            render_pick_sets,
        )
        from velocity.wagering.bet_log import Bet

        folder = Path(args.data)
        games = load_games(_find_games(folder), league=args.league)
        plays_path = _find_plays(folder)
        plays = None
        if plays_path is not None:
            from velocity.ingest.local import load_plays

            plays = load_plays(plays_path)
        injuries = None
        if args.injuries_file:
            injuries = pd.read_parquet(args.injuries_file)
            n_out = int(injuries["is_out"].sum()) if "is_out" in injuries.columns else 0
            print(f"\nintel: injuries snapshot loaded ({n_out} genuine outs)")
        else:
            print("\nintel: no injuries snapshot (--injuries-file) — availability "
                  "signals abstain")
        lib = ContextLibrary.build(games, plays, injuries, as_of=generated_at)

        kickoffs = {
            str(gid): kick
            for gid, kick in zip(
                events["game_id"].astype(str),
                pd.to_datetime(events["kickoff"], errors="coerce"),
                strict=True,
            )
        }
        contexts = {
            str(gid): lib.context_for(
                str(gid), proj.away_team, proj.home_team, kickoffs.get(str(gid))
            )
            for gid, proj in projections.items()
        }

        game_bets = list(game_log) if game_log is not None else []  # type: ignore[call-overload]
        prop_bets: list[Bet] = []
        if props_frame is not None and not props_frame.empty:
            for row in props_frame.to_dict("records"):
                point = row.get("point")
                fair = row.get("p_fair")
                prop_bets.append(Bet(
                    game_id=str(row["game_id"]), market=str(row["market"]),
                    side=str(row["side"]), book=str(row["book"]),
                    price=float(row["price"]), stake=float(row["stake"]),
                    p_model=float(row["p_model"]),
                    point=None if point is None or pd.isna(point) else float(point),
                    player=str(row["player"]),
                    p_fair=None if fair is None or pd.isna(fair) else float(fair),
                ))
        if not game_bets and not prop_bets:
            return None

        # The prop-matchup signal orients by the player's team; the FantasyPros
        # snapshot already carries model team codes for every priced player.
        player_teams: dict[str, str] = {}
        if prop_bets and args.fp_projections:
            fp = pd.read_parquet(args.fp_projections)
            if {"player_name", "team"} <= set(fp.columns):
                named = fp.dropna(subset=["player_name", "team"])
                named = named.drop_duplicates(subset=["player_name"])
                player_teams = {
                    str(r["player_name"]): str(r["team"])
                    for r in named.to_dict("records")
                }

        # "Every available resource": an independent public rating system
        # corroborating or arguing with each play. NCAAF gets SP+ (banked,
        # leak-gated to the latest finished season) — outside roster knowledge
        # the results-only fit lacks, weighted as confirmation, never a
        # promoter (the intel contract).
        game_signals: list = list(default_game_signals())
        sp_file = folder / "sp_ratings.parquet"
        if args.league == "ncaaf" and sp_file.exists():
            from velocity.ingest.ncaaf import sp_rating_table
            from velocity.intel import ExternalRatingSignal

            table, sp_season = sp_rating_table(pd.read_parquet(sp_file), generated_at)
            if table and sp_season is not None:
                game_signals.append(
                    ExternalRatingSignal(ratings=table, label=f"SP+ '{sp_season % 100:02d}")
                )
                print(f"intel: SP+ corroboration armed ({len(table)} teams, "
                      f"season {sp_season} finals)")

        convictions = assess_bets(game_bets, contexts, game_signals)
        prop_signals: list = list(default_prop_signals(player_teams))
        if args.bp_props_file:
            from velocity.intel import PropExternalSignal, PropLineOutlierSignal

            bp_frame = pd.read_parquet(args.bp_props_file)
            bp_signal = PropExternalSignal.from_frame(bp_frame)
            if bp_signal.index:
                prop_signals.append(bp_signal)
                print(f"intel: BettingPros corroboration armed "
                      f"({len(bp_signal.index)} projected props)")
            else:
                print("intel: BettingPros snapshot has no mappable projections "
                      "(pre-slug snapshot or free-tier fields) — signal abstains")
            outlier = PropLineOutlierSignal.from_frame(bp_frame)
            if outlier.index:  # consensus lines are free-tier — usually armed
                prop_signals.append(outlier)
                print(f"intel: consensus-outlier demotion armed "
                      f"({len(outlier.index)} consensus lines)")
        convictions += assess_bets(prop_bets, contexts, prop_signals)
        sets = build_pick_sets(convictions)
        print("\n" + render_pick_sets(
            sets, heading=f"{args.league.upper()} intelligence card"
        ))
        skipped = len(game_bets) + len(prop_bets) - len(convictions)
        if skipped:
            print(f"{skipped} bet(s) had no game context — not assessed, never guessed")

        if args.out and convictions:
            stamp = now.strftime("%Y%m%dT%H%M%SZ")
            dest = Path(args.out) / f"intel_{args.league}_{stamp}.parquet"
            intel_frame(convictions).assign(
                league=args.league, generated_at=generated_at
            ).to_parquet(dest, index=False)
            print(f"wrote {len(convictions)} intel rows to {dest}")
        return convictions
    except Exception as exc:  # noqa: BLE001 - the intel layer never breaks the slate
        print(f"intel layer skipped: {exc}")
        return None


def _write_workbook(  # noqa: PLR0913 - a report writer with several inputs
    out_dir: Path,
    stamp: str,
    args: argparse.Namespace,
    events: pd.DataFrame,
    projections: dict,
    frame: pd.DataFrame,
    props_frame: pd.DataFrame | None,
    generated_at: pd.Timestamp,
) -> None:
    """Write the slate as a formatted workbook alongside the parquet (best-effort)."""
    try:
        proj_disp = projections_display(projections, events)
        plays_disp = plays_display(frame, events, args.bankroll)
        props_disp = (
            props_display(props_frame, events, args.bankroll)
            if props_frame is not None and not props_frame.empty
            else None
        )
        dest = out_dir / f"slate_{args.league}_{stamp}.xlsx"
        export_slate_workbook(
            dest, proj_disp, plays_disp, props_disp,
            league=args.league, generated_at=str(generated_at), bankroll=args.bankroll,
        )
        print(f"wrote workbook to {dest}")
    except Exception as exc:  # noqa: BLE001 - the workbook is a convenience, never fatal
        print(f"workbook export skipped: {exc}")


def _projections_frame(projections: dict) -> pd.DataFrame:
    """One row of model numbers per projected game, for the plays app's cards."""
    rows = []
    for gid, proj in projections.items():
        rows.append({
            "game_id": str(gid),
            "away": proj.away_team,
            "home": proj.home_team,
            "n_sims": int(proj.sim.home_score.shape[0]),
            "mu_away": round(float(proj.mu_away), 2),
            "mu_home": round(float(proj.mu_home), 2),
            "p_home_win": round(float(proj.p_home_win()), 4),
            "fair_spread": round(float(proj.fair_spread()), 2),
            "fair_total": round(float(proj.fair_total()), 2),
        })
    return pd.DataFrame(rows)


def _prop_slate(
    args: argparse.Namespace,
    events: pd.DataFrame,
    projections: dict,
    now: datetime,
    generated_at: pd.Timestamp,
) -> tuple[pd.DataFrame | None, dict, dict[str, str], pd.DataFrame | None]:
    """Price the prop board off the FantasyPros-driven correlated sim.

    Returns ``(frame, props_by_game, key_to_name, prop_lines)`` — the persisted
    prop-slate frame (it carries the raw ``p_model``/``p_fair`` per bet, which
    is exactly what the shrink-sweep backtest replays) plus the per-game sims
    and the name index, which the social cards' watch strip reuses. Empty
    results when the board or projections don't materialize.
    """
    try:
        from velocity.models.props_football import game_props, name_index_from_fp
        from velocity.wagering.live import NFL_TEAM_ALIASES, resolve_team
        from velocity.wagering.props_slate import build_prop_slate, prop_slate_to_frame

        fp = pd.read_parquet(args.fp_projections)
        if "league" in fp.columns:
            fp = fp[fp["league"].astype(str) == args.league]
        if fp.empty:
            print(f"prop slate skipped: no {args.league} rows in {args.fp_projections}")
            return None, {}, {}, None
        from velocity.dfs.pipeline import is_season_long

        if is_season_long(fp):
            # Season totals would price a 4,800-yard passing prop as a weekly
            # mean — refuse rather than misprice (same guard as the DFS lineup).
            print("prop slate skipped: FP snapshot is season-long (week 0); "
                  "weekly props can't be priced from season totals")
            return None, {}, {}, None

        if args.prop_lines_file:
            prop_lines = pd.read_parquet(args.prop_lines_file)
        elif args.snapshot_file:
            print("prop slate skipped: offline run needs --prop-lines-file")
            return None, {}, {}, None
        else:
            from velocity.ingest.theoddsapi import TheOddsAPIClient

            prop_lines = TheOddsAPIClient.from_env().player_props(args.league)
        if prop_lines.empty:
            print("prop slate: no prop lines on the board")
            return None, {}, {}, None

        codes = sorted(set(NFL_TEAM_ALIASES.values()))
        fp_teams = set(fp["team"].astype(str))
        props_by_game: dict[str, object] = {}
        for event in events.to_dict("records"):
            gid = str(event["game_id"])
            if gid not in projections:
                continue
            home = resolve_team(str(event["home_team"]), codes, NFL_TEAM_ALIASES)
            away = resolve_team(str(event["away_team"]), codes, NFL_TEAM_ALIASES)
            if home not in fp_teams or away not in fp_teams:
                continue  # a team FP doesn't cover is skipped, never guessed
            props_by_game[gid] = game_props(fp, home, away, make_rng(),
                                            _prop_config(args))
        if not props_by_game:
            print("prop slate: no games matched FantasyPros team coverage")
            return None, {}, {}, None

        key_to_name = {}
        for r in fp.drop_duplicates(subset=["player_name"]).to_dict("records"):
            from velocity.models.props_football import player_key

            key_to_name[player_key(r.get("player_id"), r.get("player_name"))] = str(
                r.get("player_name")
            )
        log, unresolved = build_prop_slate(
            props_by_game,
            prop_lines,
            name_index_from_fp(fp),
            config=SlateConfig(
                exclude_closing=False,
                min_edge=resolve_prop_min_edge(args.prop_min_edge, args.min_edge),
                min_edge_by_market=parse_market_edges(args.min_edge_market),
                starting_bankroll=args.bankroll,
                prob_shrink=args.prop_shrink,
                exclude_markets=frozenset(
                    m.strip() for m in args.exclude_props.split(",") if m.strip()
                ),
                paper_markets=_prop_paper_markets(args),
                max_edge=args.max_edge if args.max_edge > 0 else None,
                max_relative_edge=args.max_relative_edge if args.max_relative_edge > 0 else None,
                devig_anchor=args.devig_anchor,
            ),
        )
        frame = prop_slate_to_frame(log)
        print(f"\n=== {args.league.upper()} props — {len(prop_lines)} lines, "
              f"{len(frame)} recommended (edge bar "
              f"{resolve_prop_min_edge(args.prop_min_edge, args.min_edge):g}) ===")
        if not frame.empty:
            with pd.option_context("display.width", 160, "display.max_columns", None):
                print(frame.to_string(index=False))
        if unresolved:
            print(f"{len(unresolved)} prop player(s) unresolved (skipped, never guessed)")
        if args.out:
            stamp = now.strftime("%Y%m%dT%H%M%SZ")
            dest = Path(args.out) / f"slate_{args.league}_props_{stamp}.parquet"
            frame.assign(league=args.league, generated_at=generated_at).to_parquet(
                dest, index=False
            )
            print(f"wrote {len(frame)} prop rows to {dest}")
        return frame, props_by_game, key_to_name, prop_lines
    except Exception as exc:  # noqa: BLE001 - the prop slate never breaks the game slate
        print(f"prop slate skipped: {exc}")
        return None, {}, {}, None


def _mlb_k_slate(
    args: argparse.Namespace,
    events: pd.DataFrame,
    projections: dict,
    now: datetime,
    generated_at: pd.Timestamp,
) -> tuple[pd.DataFrame | None, dict]:
    """Pitcher-strikeout props off the banked starters history.

    Returns ``(slate frame, watch_by_game)`` — the second piece feeds the
    sheets' TOP PROPS strip so the graphic carries the K board + calls.

    The headline MLB prop (docs/PROPS.md): expected Ks = shrunken expected
    batters faced × the starter's shrunken K rate × the opposing lineup's
    K tendency, priced as a negative binomial against the board's
    ``pitcher_strikeouts`` lines. Probables come from the keyless statsapi
    schedule; a game with no announced probable prices nothing.
    """
    try:
        from build_mlb_pitching import fetch_probables
        from velocity.models.props_mlb import PitcherKModel
        from velocity.wagering.props_slate import (
            build_name_index,
            build_prop_slate,
            prop_slate_to_frame,
        )

        starters_file = Path(args.data) / "starters.parquet"
        if not starters_file.exists():
            return None, {}
        if args.prop_lines_file:
            prop_lines = pd.read_parquet(args.prop_lines_file)
        elif args.snapshot_file:
            print("K prop slate skipped: offline run needs --prop-lines-file")
            return None, {}
        else:
            from velocity.ingest.theoddsapi import TheOddsAPIClient

            prop_lines = TheOddsAPIClient.from_env().player_props(
                args.league, markets="pitcher_strikeouts")
        if prop_lines.empty:
            print("K prop slate: no pitcher_strikeouts lines on the board")
            return None, {}

        starters = pd.read_parquet(starters_file)
        games = load_games(_find_games(Path(args.data)), league="mlb")
        model = PitcherKModel.fit(starters, games)

        from datetime import date, timedelta

        today = date.today()
        probables = fetch_probables(str(today), str(today + timedelta(days=1)))
        by_matchup = {(str(h), str(a)): sps for (h, a, _k), sps in probables.items()}
        props_by_game: dict[str, object] = {}
        for event in events.to_dict("records"):
            gid = str(event["game_id"])
            if gid not in projections:
                continue
            home, away = str(event["home_team"]), str(event["away_team"])
            home_sp, away_sp = by_matchup.get((home, away), (None, None))
            opponents = {}
            if home_sp:
                opponents[str(home_sp)] = away  # home SP faces the away lineup
            if away_sp:
                opponents[str(away_sp)] = home
            if opponents:
                props_by_game[gid] = model.for_game(opponents)
        if not props_by_game:
            print("K prop slate: no announced probables matched the board")
            return None, {}

        name_index = build_name_index(
            starters.rename(columns={"starter_id": "player_id",
                                     "starter_name": "player_name"}))
        log, unresolved = build_prop_slate(
            props_by_game, prop_lines, name_index,
            config=SlateConfig(
                exclude_closing=False,
                min_edge=resolve_prop_min_edge(args.prop_min_edge, args.min_edge),
                min_edge_by_market=parse_market_edges(args.min_edge_market),
                starting_bankroll=args.bankroll,
                prob_shrink=args.prop_shrink,
                paper_markets=_prop_paper_markets(args),
                max_edge=args.max_edge if args.max_edge > 0 else None,
                max_relative_edge=args.max_relative_edge if args.max_relative_edge > 0 else None,
                devig_anchor=args.devig_anchor,
            ),
        )
        frame = prop_slate_to_frame(log)
        watch_by_game = _k_watch_entries(
            model, props_by_game, starters, prop_lines, frame)
        print(f"\n=== MLB pitcher Ks — {len(prop_lines)} lines, "
              f"{len(frame)} recommended ===")
        if not frame.empty:
            with pd.option_context("display.width", 160, "display.max_columns", None):
                print(frame.to_string(index=False))
        if unresolved:
            print(f"{len(unresolved)} prop player(s) unresolved (skipped, never guessed)")
        if args.out:
            stamp = now.strftime("%Y%m%dT%H%M%SZ")
            dest = Path(args.out) / f"slate_{args.league}_props_{stamp}.parquet"
            frame.assign(league=args.league, generated_at=generated_at).to_parquet(
                dest, index=False)
            print(f"wrote {len(frame)} prop rows to {dest}")
        return frame, watch_by_game
    except Exception as exc:  # noqa: BLE001 - the prop slate never breaks the game slate
        print(f"K prop slate skipped: {exc}")
        return None, {}


def _k_watch_entries(
    model: object,
    props_by_game: dict,
    starters: pd.DataFrame,
    prop_lines: pd.DataFrame,
    staked: pd.DataFrame,
) -> dict:
    """The sheets' TOP PROPS entries for the MLB K vertical, per game.

    One entry per probable: his board consensus K line (model median when the
    board hasn't posted him), the model's P(over) and expected Ks — and, when
    the slate staked the line, the play detail the strip badges. Staked
    entries lead so the sheet is the one graphic that carries the calls.
    """
    from velocity.report.social import WatchEntry, _prop_lines_index
    from velocity.wagering.props_slate import _normalize

    names = dict(zip(starters["starter_id"].astype(str),
                     starters["starter_name"].astype(str), strict=True))
    line_index = _prop_lines_index(prop_lines)
    plays: dict[tuple[str, str], str] = {}
    if staked is not None and not staked.empty:
        for row in staked.to_dict("records"):
            detail = (f"{str(row['side']).upper()} · {row['price']:+.0f} · "
                      f"{row['stake']:.1f}u")
            plays[(str(row["game_id"]), _normalize(str(row["player"])))] = detail

    out: dict = {}
    for gid, game_props in props_by_game.items():
        entries = []
        for pid, opponent in game_props.opponents.items():
            dist = model.distribution(pid, opponent=opponent)  # type: ignore[attr-defined]
            name = names.get(str(pid))
            if dist is None or name is None:
                continue
            board_line = line_index.get(
                (str(gid), "pitcher_strikeouts", _normalize(name)))
            line = board_line if board_line is not None \
                else float(int(dist.mean) + 0.5)
            entries.append(WatchEntry(
                player=name,
                market="pitcher_strikeouts",
                line=float(line),
                p_over=float(game_props.prob_over(pid, "pitcher_strikeouts",
                                                  float(line))),
                mean=float(dist.mean),
                from_board=board_line is not None,
                play=plays.get((str(gid), _normalize(name))),
            ))
        entries.sort(key=lambda e: (e.play is None, -abs(e.p_over - 0.5)))
        if entries:
            out[str(gid)] = tuple(entries)
    return out


def _pickem_slate(
    args: argparse.Namespace,
    props_by_game: dict,
    prop_lines: pd.DataFrame,
    now: datetime,
    generated_at: pd.Timestamp,
) -> None:
    """Build and persist the pick'em legs board + ranked slips (best-effort).

    Legs pair the devigged book probability (the staking marginal) with the
    correlated prop sim; slips are ranked by calibrated-correlation EV. Two
    parquets join the slate artifact: ``slate_<lg>_pickem_legs_*`` (the whole
    qualifying board, phone-readable in the app) and ``slate_<lg>_pickem_*``
    (the ranked slips over the EV floor — empty is a result, not a failure).
    """
    if args.pickem_top <= 0:
        return
    try:
        from velocity.models.props_football import name_index_from_fp
        from velocity.wagering.pickem_slate import build_pickem_board

        fp = pd.read_parquet(args.fp_projections)
        if "league" in fp.columns:
            fp = fp[fp["league"].astype(str) == args.league]
        legs, slips = build_pickem_board(
            props_by_game, prop_lines, name_index_from_fp(fp),
            min_ev=args.pickem_min_ev, top=args.pickem_top,
            devig_method=args.pickem_devig,
            max_per_game=args.pickem_max_per_game or None,
        )
        print(f"\n=== {args.league.upper()} pick'em — {len(legs)} qualifying legs, "
              f"{len(slips)} slips over EV {args.pickem_min_ev:g} ===")
        if not slips.empty:
            with pd.option_context("display.width", 200, "display.max_columns", None):
                print(slips.to_string(index=False))
        if args.out:
            out_dir = Path(args.out)
            out_dir.mkdir(parents=True, exist_ok=True)
            stamp = now.strftime("%Y%m%dT%H%M%SZ")
            legs.assign(league=args.league, generated_at=generated_at).to_parquet(
                out_dir / f"slate_{args.league}_pickem_legs_{stamp}.parquet", index=False
            )
            slips.assign(league=args.league, generated_at=generated_at).to_parquet(
                out_dir / f"slate_{args.league}_pickem_{stamp}.parquet", index=False
            )
            print(f"wrote {len(legs)} pick'em legs + {len(slips)} slips")
    except Exception as exc:  # noqa: BLE001 - pick'em never breaks the slates
        print(f"pick'em board skipped: {exc}")


def _prop_config(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    from velocity.models.props_football import FootballPropConfig

    return FootballPropConfig(n_sims=args.n_sims)


def _write_social_cards(  # noqa: PLR0913 - a report writer with several inputs
    args: argparse.Namespace,
    events: pd.DataFrame,
    projections: dict,
    canonical: pd.DataFrame,
    props_by_game: dict,
    key_to_name: dict[str, str],
    prop_lines: pd.DataFrame | None,
    stamp: str,
    *,
    game_log: object = None,
    convictions: list | None = None,
    watch_by_game: dict | None = None,
) -> None:
    """Render the per-game social model cards + captions into the out folder.

    The season record chain (written by the grading step, which runs before the
    slate build) puts the receipt line on every card. Logos are fetched into a
    hidden cache under the out folder (never uploaded). Best-effort: a card
    problem never breaks the slate.
    """
    try:
        from velocity.report.daily_record import season_record_line
        from velocity.report.social import build_social_cards
        from velocity.report.social_png import render_cards

        record_line = None
        try:
            chain = sorted(Path(args.out).glob(f"cumulative_record_{args.league}_*.parquet"))
            if chain:
                record_line = season_record_line(pd.read_parquet(chain[-1]))
        except Exception as exc:  # noqa: BLE001 - the record line is optional decoration
            print(f"record line skipped: {exc}")
        asset_dir = Path(args.out) / ".assets"  # hidden: outside the upload globs
        aliases = None
        team_colors = None
        code_to_team: dict[str, str] = {}
        if args.league == "ncaaf":
            aliases, team_colors, code_to_team = _ncaaf_identity(events, asset_dir)
        elif args.league in ("mlb", "wnba", "ncaab", "nhl"):
            # Non-NFL identity: abbreviation + brand color blocks, no marks —
            # the NCAAF licensing posture (velocity/report/league_identity).
            # NCAAB has no curated table yet, so teams fall back to neutral
            # trigram codes — cards render, just uncolored.
            from velocity.report.league_identity import league_identity

            provider_names = sorted(
                set(events["home_team"].astype(str))
                | set(events["away_team"].astype(str))
            )
            aliases, team_colors, code_to_team = league_identity(
                args.league, provider_names
            )
        # The slate's staked plays (tier-chipped when the intel layer ran) —
        # computed BEFORE the card render so the hero card's matrix wears the
        # PLAY badges, then reused for the deep dive's verdict band + WHY.
        from velocity.report.deepdive import plays_from_bets

        tiers: dict[tuple[str, str, str], str] = {}
        why_signals: dict[str, list[str]] = {}
        if convictions:
            best: dict[str, float] = {}
            for c in convictions:
                bet = c.bet
                if bet.player is not None:
                    continue
                gid = str(bet.game_id)
                tiers[(gid, bet.market, bet.side)] = c.tier
                if not c.vetoed and c.score > best.get(gid, -1.0):
                    best[gid] = c.score
                    why_signals[gid] = [s.rationale for s in c.signals[:2]]
        plays_by_game = (
            plays_from_bets(game_log.bets, tiers=tiers)  # type: ignore[attr-defined]
            if game_log is not None else {}
        )
        # max_watch=6: the hero card renders its top three; the deep dive
        # carries the full six.
        cards = build_social_cards(
            projections, events,
            props_by_game=props_by_game, key_to_name=key_to_name,
            prop_lines=prop_lines, record_line=record_line, lines=canonical,
            aliases=aliases, team_colors=team_colors, max_watch=6,
            plays_by_game=plays_by_game, watch_by_game=watch_by_game,
        )
        paths = render_cards(cards, Path(args.out), stamp,
                             asset_dir=asset_dir, league=args.league,
                             number_slides=args.carousel)
        print(f"wrote {len(paths)} social card(s) to {args.out}")
        # game_id → path maps (render_cards returns paths index-aligned with
        # cards) — the sheet composer joins the deep dives onto these below.
        card_by_game = {str(card.game_id): path
                        for card, path in zip(cards, paths, strict=True)}
        dive_by_game: dict[str, Path] = {}

        # Deep Dive companions — the analytical page behind each matchup card
        # (form/EPA table, margin vs the market, extended props). Best-effort
        # like everything else on this surface.
        try:
            from velocity.ingest.local import load_games, load_plays
            from velocity.report.deepdive import build_deep_dives
            from velocity.report.deepdive_png import render_deep_dives

            games = load_games(_find_games(Path(args.data)), league=args.league)
            plays_path = _find_plays(Path(args.data)) if args.league == "nfl" else None
            plays = load_plays(plays_path) if plays_path is not None else None
            starters = probables = None
            if args.league == "mlb":
                # The reference-genre pitcher row: each probable's banked
                # line. Best-effort — a fetch failure just omits the row.
                try:
                    from datetime import date, timedelta

                    from build_mlb_pitching import fetch_probables

                    sp_file = Path(args.data) / "starters.parquet"
                    if sp_file.exists():
                        starters = pd.read_parquet(sp_file)
                        today = date.today()
                        probables = fetch_probables(
                            str(today), str(today + timedelta(days=1))
                        )
                except Exception as exc:  # noqa: BLE001 - cosmetic row only
                    print(f"probable-pitcher row skipped: {exc}")
            dives = build_deep_dives(cards, projections, games, plays,
                                     plays_by_game=plays_by_game,
                                     why_signals=why_signals,
                                     team_names=code_to_team,
                                     starters=starters, probables=probables,
                                     league=args.league)
            dive_paths = render_deep_dives(dives, Path(args.out), stamp,
                                           asset_dir=asset_dir, league=args.league)
            print(f"wrote {len(dive_paths)} deep dive card(s) to {args.out}")
            dive_by_game = {str(dive.card.game_id): path
                            for dive, path in zip(dives, dive_paths, strict=True)}
        except Exception as exc:  # noqa: BLE001 - the companion never blocks the card run
            print(f"deep dives skipped: {exc}")

        # The sheet: ONE all-inclusive graphic per game (card + deep dive
        # stacked), the artifact's only pregame PNG — the intermediate
        # renders and the deep-dive captions are consumed into it.
        from velocity.report.sheet_png import compose_sheets

        sheets = compose_sheets(card_by_game, dive_by_game, Path(args.out))
        social_captions = Path(args.out) / f"social_{args.league}_{stamp}_captions.md"
        if social_captions.exists():
            social_captions.rename(
                Path(args.out) / f"sheet_{args.league}_{stamp}_captions.md")
        for stale in (*card_by_game.values(), *dive_by_game.values()):
            stale.unlink(missing_ok=True)
        dive_captions = Path(args.out) / f"deepdive_{args.league}_{stamp}_captions.md"
        dive_captions.unlink(missing_ok=True)
        print(f"composed {len(sheets)} sheet(s)")
        manifest = [{"game_id": gid, "kind": "sheet", "file": path.name}
                    for gid, path in sheets.items()]
        if manifest:
            pd.DataFrame(manifest).assign(league=args.league).to_parquet(
                Path(args.out) / f"cardindex_{args.league}_{stamp}.parquet",
                index=False,
            )
    except Exception as exc:  # noqa: BLE001 - a report surface, never breaks the slate
        print(f"social cards skipped: {exc}")


def _ncaaf_identity(
    events: pd.DataFrame, asset_dir: Path
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Provider-name → school-abbreviation aliases, abbreviation → color map,
    and abbreviation → school (the datasets' team key, for the deep dive).

    Built from the cached CFBD identity table (``CFBD_API_KEY``); provider
    names bridge to schools via the same nickname-prefix logic the slate
    resolution uses. Any name the table can't place falls back to itself —
    the card renders the provider name at a smaller size rather than guessing.
    No logos anywhere on this path (school marks are licensed; abbreviation +
    colors are plain facts).
    """
    from velocity.report.assets import ncaaf_team_index
    from velocity.wagering.live import nickname_aliases

    provider_names = {
        str(n) for n in
        pd.concat([events["away_team"], events["home_team"]]).dropna()
    }
    aliases = {name: name for name in provider_names}
    team_colors: dict[str, str] = {}
    code_to_team: dict[str, str] = {}
    meta = ncaaf_team_index(os.environ.get("CFBD_API_KEY"), asset_dir)
    if meta:
        to_school = nickname_aliases(sorted(provider_names), sorted(meta))
        for provider, school in to_school.items():
            aliases[provider] = meta[school].abbreviation
        team_colors = {
            m.abbreviation: m.color for m in meta.values() if m.color
        }
        code_to_team = {m.abbreviation: school for school, m in meta.items()}
    return aliases, team_colors, code_to_team


def _parlay_slate(
    args: argparse.Namespace,
    projections: dict,
    game_log: object | None,
    now: datetime,
    generated_at: pd.Timestamp,
) -> None:
    """Build, print, and persist the parlay slate from the day's qualifying bets."""
    try:
        from velocity.wagering.parlay import (
            ParlayConfig,
            build_parlays,
            parlay_slate_to_frame,
        )

        candidates = list(game_log or [])
        if not candidates:
            return
        results_by_game = {str(gid): proj.sim for gid, proj in projections.items()}
        game_labels = {
            str(gid): f"{proj.away_team}@{proj.home_team}"
            for gid, proj in projections.items()
        }
        tickets = build_parlays(
            candidates,
            results_by_game,
            bankroll=args.bankroll,
            game_labels=game_labels,
            config=ParlayConfig(
                max_legs=max(args.parlay_max_legs, 2),
                min_ev=args.parlay_min_ev,
                max_parlays=args.max_parlays,
            ),
        )
        frame = parlay_slate_to_frame(tickets)
        print(f"\n=== {args.league.upper()} parlays — {len(candidates)} candidate legs, "
              f"{len(frame)} recommended ===")
        if frame.empty:
            print("no parlay cleared the combined-EV bar.")
        else:
            with pd.option_context("display.width", 200, "display.max_columns", None):
                print(frame.to_string(index=False))
            print("note: same_game=True payouts assume the product price; books "
                  "reprice correlated SGPs, so treat that EV as an upper bound.")
        if args.out and not frame.empty:
            dest = (
                Path(args.out)
                / f"slate_{args.league}_parlays_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet"
            )
            frame.assign(league=args.league, generated_at=generated_at).to_parquet(
                dest, index=False
            )
            print(f"wrote {len(frame)} parlay rows to {dest}")
    except Exception as exc:  # noqa: BLE001 - parlays are additive; never break the slate
        print(f"parlay slate skipped: {exc}")


if __name__ == "__main__":
    main()
