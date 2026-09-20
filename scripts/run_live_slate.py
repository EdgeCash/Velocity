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
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from velocity.eval.ladders import default_relative_tolerance, has_ladder_calibration
from velocity.features.scores import fit_scores_ratings
from velocity.ingest.bettingpros import DEFAULT_BP_MAX_AGE_MIN
from velocity.ingest.exchanges import EXCHANGE_LEAGUES
from velocity.ingest.local import load_games
from velocity.ingest.theoddsapi import extract_events, normalize_odds_events
from velocity.intel.publish import (
    DEFAULT_MAX_PLAYS,
    DEFAULT_MIN_CONTEXT,
    DEFAULT_MIN_CONVICTION,
)
from velocity.models.counts import MLB_COUNTS
from velocity.models.game_nfl import GameProjection
from velocity.models.game_scores import ScoresGameModel, ScoresModelConfig
from velocity.models.overtime import WNBA_OVERTIME
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
from velocity.wagering.live import (
    canonicalize_sides,
    project_board,
    rule_tiers_for,
    slate_to_frame,
)
from velocity.wagering.slate import SlateConfig, build_slate


def _find_games(folder: Path) -> Path:
    for ext in (".parquet", ".pq", ".csv"):
        candidate = folder / f"games{ext}"
        if candidate.exists():
            return candidate
    raise SystemExit(f"need a games file in {folder}/ to fit the model")


# Every Odds API call this script makes, for the credit ledger.
#
# The live slate spends credits from four separate call sites, each of which
# built its own client and dropped that client's ``usage`` on the floor when it
# went out of scope. So the two collectors banked ledgers and the live slate —
# which runs far more often than either — banked nothing, and any projection
# off those ledgers understated the real spend by however much this costs.
# An accounting with a known hole in it is worse than none, because it reads
# like a number.
_ODDS_USAGE: list[Mapping[str, object]] = []


def _odds_client() -> object:
    """A client whose usage reaches the ledger. Use this, never from_env directly."""
    from velocity.ingest.theoddsapi import TheOddsAPIClient  # network path

    client = TheOddsAPIClient.from_env()
    _ODDS_USAGE.append(client.usage)  # the client's own list, filled as it calls
    return client


def bank_odds_usage(out_dir: Path, tag: str) -> Path | None:
    """Write everything this run spent, flattened across all four call sites."""
    from velocity.ingest.theoddsapi import describe_usage, write_usage

    calls = [call for usage in _ODDS_USAGE for call in usage]
    if not calls:
        return None
    ledger = write_usage(calls, out_dir, tag)
    print(describe_usage(calls))
    if ledger is not None:
        print(f"credit ledger → {ledger}")
    return ledger


def _load_snapshot(args: argparse.Namespace) -> object:
    if args.snapshot_file:
        return json.loads(Path(args.snapshot_file).read_text())
    return _odds_client().odds_payload(args.league)


# A banked ``/odds`` payload is named ``odds_{league}_{YYYYmmdd}T{HHMMSS}Z.json``
# by the collector (scripts/collect_theoddsapi.py). The stamp is the only honest
# record of when the board was bought.
_SNAPSHOT_RE = re.compile(r"^odds_(?P<league>[a-z]+)_(?P<stamp>\d{8}T\d{6}Z)\.json$")
_PROP_BOARD_RE = re.compile(
    r"^props_(?P<league>[a-z]+)_(?P<stamp>\d{8}T\d{6}Z)\.parquet$")


def parse_snapshot_stamp(name: str, league: str) -> pd.Timestamp | None:
    """Capture time from a banked payload's filename, or ``None`` if not this league's."""
    match = _SNAPSHOT_RE.match(name)
    if match is None or match["league"] != league:
        return None
    return pd.Timestamp(datetime.strptime(match["stamp"], "%Y%m%dT%H%M%SZ"))


def newest_banked_board(
    root: Path, league: str, now: pd.Timestamp, max_age_minutes: float
) -> tuple[Path | None, pd.Timestamp | None]:
    """The freshest banked board for ``league`` → ``(path_if_fresh, newest_stamp)``.

    Freshness is read from the **stamp in the filename**, never the file's
    mtime. The workflow downloads these payloads out of Actions artifacts, so
    every file is written at extraction time: mtime records when we unzipped
    it, not when the board was bought. Selecting on mtime made the *oldest*
    download win the sort (the loop extracts newest-first, so the oldest lands
    last) and reported a two-day-old board as fresh — the slate then priced
    every league against prices that had already moved, and leagues whose
    games had all started produced an empty card.

    The newest stamp is returned even when it is too old, so the caller can say
    how stale the bank is instead of only that it missed.
    """
    best: tuple[Path, pd.Timestamp] | None = None
    if not root.exists():
        return None, None
    for path in root.rglob("odds_*.json"):
        stamp = parse_snapshot_stamp(path.name, league)
        if stamp is None:
            continue
        if best is None or stamp > best[1]:
            best = (path, stamp)
    if best is None:
        return None, None
    age_minutes = (now - best[1]).total_seconds() / 60.0
    return (best[0] if age_minutes <= max_age_minutes else None), best[1]


def newest_banked_props(
    root: Path, league: str, now: pd.Timestamp, max_age_minutes: float
) -> tuple[Path | None, pd.Timestamp | None]:
    """The freshest banked PROP board for ``league`` → ``(path_if_fresh, newest_stamp)``.

    :func:`newest_banked_board` for the props collector's output, and read off
    the filename stamp for the identical reason: these arrive by unzipping
    Actions artifacts, so every mtime is extraction time.
    """
    best: tuple[Path, pd.Timestamp] | None = None
    if not root.exists():
        return None, None
    for path in root.rglob("props_*.parquet"):
        match = _PROP_BOARD_RE.match(path.name)
        if match is None or match["league"] != league:
            continue
        stamp = pd.Timestamp(datetime.strptime(match["stamp"], "%Y%m%dT%H%M%SZ"))
        if best is None or stamp > best[1]:
            best = (path, stamp)
    if best is None:
        return None, None
    age_minutes = (now - best[1]).total_seconds() / 60.0
    return (best[0] if age_minutes <= max_age_minutes else None), best[1]


def resolve_banked_board(args: argparse.Namespace, now: pd.Timestamp) -> None:
    """Point ``--snapshot-file`` at the freshest banked board, when one qualifies.

    An explicit ``--snapshot-file`` always wins. Otherwise a ``--snapshot-dir``
    is searched, and the run falls through to a live pull when nothing in it is
    recent enough — paying for credits beats pricing a board that has moved.
    """
    if args.snapshot_file or not getattr(args, "snapshot_dir", None):
        return
    path, stamp = newest_banked_board(
        Path(args.snapshot_dir), args.league, now, args.board_max_age_min
    )
    if path is not None and stamp is not None:
        age = (now - stamp).total_seconds() / 60.0
        print(f"board: reusing banked payload {path} "
              f"(captured {stamp:%Y-%m-%d %H:%M}Z, {age:.0f}min old; no credits spent)")
        args.snapshot_file = str(path)
        return
    if stamp is None:
        print(f"board: no banked payload for {args.league} under "
              f"{args.snapshot_dir}; pulling live")
    else:
        age = (now - stamp).total_seconds() / 60.0
        print(f"board: newest banked {args.league} payload is {age:.0f}min old "
              f"(limit {args.board_max_age_min:g}); pulling live")


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
) -> tuple[Callable[[str, str], GameProjection], list[str], pd.DataFrame, str, object]:
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
        plays_mode = resolve_plays(args.nfl_plays, "nfl")
        if plays_mode != "all":
            # velocity.features.team.scrimmage_plays: "scrimmage" keeps the
            # offense's own snaps; "live" keeps the kicks and returns too and
            # drops only kneels, spikes, no-plays and unlabelled rows. The lab
            # decides which (docs/MODEL_LAB.md, the plays round).
            from velocity.features.team import scrimmage_plays

            before = len(plays)
            plays = scrimmage_plays(plays, "nfl", keep_kicks=plays_mode == "live")
            print(f"NFL plays ({plays_mode}): {before - len(plays)} rows dropped, "
                  f"{len(plays)} kept")
        shrink = resolve_turnover_shrink(args.nfl_turnover_shrink)
        if shrink < 1.0:
            # The play-context round (docs/MODEL_LAB.md): an interception or a
            # lost fumble is the least repeatable −4 EPA on the field; the fit
            # sees a fraction of it.
            from velocity.features.team import shrink_turnover_epa

            plays = shrink_turnover_epa(plays, shrink)
            print(f"NFL turnover EPA: ×{shrink:g} on interceptions and lost fumbles")
        offseason = resolve_offseason_weeks(args.nfl_offseason_weeks)
        weights = recency_weights(plays, DEFAULT_RECENCY_HALF_LIFE, offseason_weeks=offseason)
        if "passer_player_id" in plays.columns and plays["passer_player_id"].notna().any():
            # The promoted fit (docs/MODEL_LAB.md Round 3): QB decomposed out
            # of the offense, detected starter priced back in at projection.
            phase_lambda = resolve_phase_lambda(args.nfl_phase_lambda)
            ratings: object = fit_qb_ratings(
                plays, weights=weights,
                phase_col="play_type" if phase_lambda > 0 else None,
                phase_lambda=phase_lambda if phase_lambda > 0 else 1000.0)
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
        has_depth_source = bool(args.fp_projections or args.espn_depth_file)
        if has_depth_source and weeks_path.exists() and hasattr(ratings, "starters"):
            from dataclasses import replace

            from velocity.features.starters import describe_changes, starter_map

            fp_frame = pd.DataFrame(
                columns=["player_name", "team", "position", "stat", "value"]
            )
            if args.fp_projections:
                fp_frame = pd.read_parquet(args.fp_projections)
                if "league" in fp_frame.columns:
                    fp_frame = fp_frame[fp_frame["league"].astype(str) == "nfl"]
            # The depth chart STATES what the projected-workload inference
            # guesses, so where it covers a team it wins; disagreements are
            # logged, because each one is either a stale projection or a
            # starter change we would otherwise price blind.
            espn_depth = None
            if args.espn_depth_file:
                espn_depth = pd.read_parquet(args.espn_depth_file)
                if "league" in espn_depth.columns:
                    espn_depth = espn_depth[espn_depth["league"].astype(str) == "nfl"]
            weeks = pd.read_parquet(weeks_path)
            injuries = _starter_outs(args)
            overrides, notes = starter_map(
                fp_frame, weeks, injuries, espn_depth=espn_depth
            )
            # Only clubs the fit knows: FantasyPros lists free agents under "FA".
            overrides = {t: q for t, q in overrides.items() if t in ratings.teams}  # type: ignore[attr-defined]
            changes = describe_changes(overrides, ratings.starters, weeks)  # type: ignore[attr-defined]
            if overrides:
                ratings = replace(ratings, starters={**ratings.starters, **overrides})  # type: ignore[attr-defined,type-var]
            source = ("the ESPN depth chart" if espn_depth is not None and not espn_depth.empty
                      else "the FantasyPros depth")
            print(f"starter map: {len(overrides)} teams from {source}, "
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
        # The training window's games: the level and the scale both fit
        # through the model on the trailing seasons of it.
        window = load_games(_find_games(folder), league="nfl")
        window = window[window["season"] >= cutoff]
        if resolve_nfl_level(args.nfl_level) == "fit":
            from velocity.models.level import calibrate_level, level_shift

            # The trailing eight on-field weeks, shrunk toward the trailing
            # two seasons by 128 games (docs/MODEL_LAB.md, the level round):
            # the two-season level lagged the era by +0.7 and wandered ±3
            # within it; the window follows the drift and the shrink keeps
            # December's scoring out of September.
            shift = level_shift(nfl_model, window, seasons=NFL_LEVEL_SEASONS,
                                weeks=NFL_LEVEL_WEEKS, shrink_games=NFL_LEVEL_SHRINK_GAMES)
            nfl_model = calibrate_level(nfl_model, window, seasons=NFL_LEVEL_SEASONS,
                                        weeks=NFL_LEVEL_WEEKS,
                                        shrink_games=NFL_LEVEL_SHRINK_GAMES)
            kind += f", level {nfl_model.config.base_points:.2f} ({shift:+.2f} vs 22.5)"
            print(f"NFL level: base {nfl_model.config.base_points:.2f} pts/team "
                  f"(the fit ran {shift:+.2f} vs the constant on the trailing "
                  f"{NFL_LEVEL_WEEKS} weeks, shrunk toward {len(window)} games)")

        # The scale (velocity.models.level): the level fixed the intercept;
        # the residual bank says the deviations run wide — the total's by
        # half. Fitted on the banked out-of-sample rows, applied under the
        # situational wrappers so a bye is still worth its point.
        core: object = nfl_model
        nfl_scale = resolve_scale(args.nfl_scale, "nfl")
        if nfl_scale != "off":
            from velocity.models.level import next_week, phase_weeks, scale_model
            from velocity.models.residuals import load_residual_frame

            bank = load_residual_frame("nfl")
            if bank is None:
                print("no residual bank for nfl; projecting unscaled")
            else:
                weeks = phase_weeks(next_week(window), "nfl") if nfl_scale == "phase" else None
                shift = resolve_scale_shift(args.nfl_scale_shift, "nfl")
                core, calibration = scale_model(
                    nfl_model, bank, window, nfl_model.config.sim, weeks=weeks,
                    anchor_seasons=NFL_LEVEL_SEASONS, shift=shift)
                phase = f" (weeks {weeks[0]}–{weeks[1]})" if weeks else ""
                kind += (f", scale ×{calibration.margin_slope:.2f} margin "
                         f"{calibration.margin_shift:+.2f} / ×{calibration.total_slope:.2f} "
                         f"total{phase}")
                print(f"NFL scale{phase}: margin ×{calibration.margin_slope:.3f} "
                      f"{calibration.margin_shift:+.2f}, total ×{calibration.total_slope:.3f} "
                      f"on {calibration.n} banked games")

        # Rest spots (docs/MODEL_LAB.md Round 4): bye +1.0 / short week −1.0 on
        # top of the fit — small, consistent across every tested grid.
        from velocity.backtest.lab import RestAdjustedModel

        if schedule is None:
            schedule = load_games(_find_games(folder), league="nfl")
        rest_model = RestAdjustedModel(core, schedule)  # type: ignore[arg-type]

        # The injury burden (velocity.features.injuries): the share of a
        # team's touches ruled Out or Doubtful this week, from the committed
        # designations (refreshed daily from nflverse) and the usage bank,
        # at a few points per whole team's worth. The situational round:
        # the side missing 15%+ ran 1.6 points under the projection; 4 a
        # unit takes that to −0.6 with Brier and margin RMSE a hair better.
        injury_points = resolve_injury_points(args.nfl_injury_points)
        injuries_path = folder / "injuries.parquet"
        if injury_points > 0 and injuries_path.exists() and weeks_path.exists():
            from velocity.backtest.lab import InjuryBurdenModel
            from velocity.features.injuries import burden_by_team_week
            from velocity.models.level import next_week

            burden = burden_by_team_week(
                pd.read_parquet(injuries_path), pd.read_parquet(weeks_path))
            season_week = (int(window["season"].max()), next_week(window))
            this_week = burden[(burden["season"] == season_week[0])
                               & (burden["week"] == season_week[1])]
            rest_model = InjuryBurdenModel(  # type: ignore[assignment]
                rest_model, schedule, this_week, injury_points, fixed_season_week=season_week)
            heaviest = this_week.sort_values("burden", ascending=False).head(3)
            named = ", ".join(f"{t} {b:.0%}" for t, b in zip(heaviest["team"], heaviest["burden"],
                                                          strict=True))
            print(f"injury burden: week {season_week[1]} designations on {len(this_week)} "
                  f"teams at {injury_points:g} pts a unit" + (f" — heaviest {named}" if named
                                                              else " — none banked yet"))

        # Wind on totals (Round 5 constants, live forecast): best-effort — a
        # failed forecast fetch just leaves totals unadjusted.
        model: object = rest_model
        # Kept so the run can persist what the adjustment actually did to each
        # total (velocity.backtest.lab.WeatherAdjustedModel.weather_note). The
        # forecast was fetched, priced into every projection and then thrown
        # away, so nothing downstream could say why a windy total was low.
        weather_model: object = None
        try:
            if args.offline:  # a true no-network run (tests/CI)
                raise RuntimeError("offline run")
            from velocity.backtest.lab import WeatherAdjustedModel
            from velocity.features.weather import forecast_frame

            forecast = forecast_frame(days=max(args.max_days, 1) + 1)
            if not forecast.empty:
                # Wind (Round 5) and, from the situational round, the day's
                # precipitation: at 0.25 in the wet games had run 2.1 points
                # under the projection; a point a side takes that to −0.1
                # (docs/MODEL_LAB.md). --nfl-precip-points 0 switches it off.
                precip_points = resolve_precip_points(args.nfl_precip_points)
                model = WeatherAdjustedModel(rest_model, forecast,  # type: ignore[arg-type]
                                             points_per_mph=0.30,
                                             precip_points=precip_points)
                weather_model = model
                wet = int(pd.to_numeric(forecast["precip"], errors="coerce").ge(0.25).sum())
                print(f"weather forecast: {len(forecast)} stadium-days fetched, {wet} wet "
                      f"(≥ 0.25 in), rain at {precip_points:g} pts a side")
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
        return project_epa, list(ratings.teams), nfl_ratings, kind, weather_model

    games = load_games(_find_games(folder), league=args.league)
    # Per-league outcome-noise calibration. Football's constants are the
    # lab-validated ones; MLB (runs) and WNBA (points) use the leagues'
    # historical margin/total sigmas — content-surface defaults, honest but
    # not yet lab-tuned (their datasets carry no closing lines to tune on).
    sims = {
        "ncaaf": football_sim_config("ncaaf", args),
        # Baseball is simulated as COUNTS, not as a rounded normal on the
        # margin (velocity/models/counts.py). The normal scored a tie in 13.5%
        # of games — baseball has none in 7,149 banked — was a third
        # under-dispersed against a 4.53 walk-forward residual sd, and was
        # symmetric where the unbatted ninth inning is not. The sds below are
        # unused on that path and kept only because the config requires them.
        "mlb": SimConfig(sd_margin=4.5, sd_total=4.5, n_sims=args.n_sims,
                         counts=MLB_COUNTS),
        # Basketball cannot end level either, and its own defect was the
        # TOTAL: 15.0 against a walk-forward residual sd of 18.15, a fifth too
        # narrow, while the margin was nearly right. sd_total below is the
        # PRE-overtime number that lands the finished distribution on 18.15
        # (docs/BUILD_WNBA_SIM.md).
        "wnba": SimConfig(sd_margin=12.93, sd_total=17.6, n_sims=args.n_sims,
                          overtime=WNBA_OVERTIME),
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
    if args.league == "ncaaf":
        # The college recency round (docs/MODEL_LAB.md): the scores half
        # weighed flat through the college round; a half-life the lab sets.
        college_scores_hl = resolve_ncaaf_scores_half_life(args.ncaaf_scores_half_life)
        recency_hl = college_scores_hl if college_scores_hl > 0 else None
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
            special_teams=resolve_ncaaf_st_prior(args.ncaaf_st_prior),
        )
        if not pseudo.empty:
            fit_games = pd.concat([games, pseudo], ignore_index=True)
            if weights is not None:
                from velocity.features.scores import scores_recency_weights

                # The pseudo-games sit at week 0 of the season ahead, so the
                # prior counts as current under the same key.
                weights = scores_recency_weights(fit_games, recency_hl)  # type: ignore[arg-type]
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
        college_mode = resolve_plays(args.ncaaf_plays, "ncaaf")
        if college_mode != "all":
            from velocity.features.team import scrimmage_plays

            before = len(plays)
            plays = scrimmage_plays(plays, "ncaaf", keep_kicks=college_mode == "live")
            print(f"NCAAF plays ({college_mode}): {before - len(plays)} rows dropped, "
                  f"{len(plays)} kept")
        base = ncaaf_base_points(games)
        qb_lambda = resolve_ncaaf_qb_lambda(args.ncaaf_qb_lambda)
        half_life = resolve_ncaaf_epa_half_life(args.ncaaf_epa_half_life)
        with_passers = ("passer_player_id" in plays.columns
                        and plays["passer_player_id"].notna().any())
        use_qb = qb_lambda > 0 and with_passers
        cells = compress_plays(plays, by_passer=use_qb)
        weights = cells["n"].astype(float)
        epa_kind = "λ50"
        if half_life > 0:
            # Recency on the EPA half (docs/MODEL_LAB.md, the college recency
            # round): the promoted fit had weighed a four-season window flat.
            from velocity.features.team import recency_weights as _recency

            gap = resolve_ncaaf_epa_offseason_weeks(args.ncaaf_epa_offseason_weeks)
            weights = weights * _recency(cells, half_life, offseason_weeks=gap)
            epa_kind += f"/hl{half_life:g}" + (f"+gap{gap:g}" if gap > 0 else "")
        if use_qb:
            # The college QB term (docs/MODEL_LAB.md, the college QB round):
            # the passer decomposed out of the offense on passer cells, the
            # detected starter — the passer with the most dropbacks in the
            # team's latest game — priced back in.
            from velocity.features.team import fit_qb_ratings

            epa_ratings: object = fit_qb_ratings(
                cells, ridge_lambda=50.0, qb_lambda=qb_lambda, weights=weights, count_col="n")
            epa_kind += f"/q{qb_lambda:g} QB"
        else:
            epa_ratings = fit_ratings(cells, ridge_lambda=50.0, weights=weights)
        epa_model = NFLGameModel(
            epa_ratings,  # type: ignore[arg-type]
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
        # The early-season blend weight (docs/MODEL_LAB.md, the early-weight
        # round): through week 4 the EPA half opens the season on a quarter
        # of last season's tail while the scores half carries the SP+ prior,
        # so the EPA half gets less of September.
        from velocity.models.level import EARLY_WEEK_BY_LEAGUE
        from velocity.models.level import next_week as _next_week

        blend_weight = 0.5
        early_weight = resolve_ncaaf_early_weight(args.ncaaf_early_weight)
        if _next_week(games) <= EARLY_WEEK_BY_LEAGUE["ncaaf"]:
            blend_weight = early_weight
        model = BlendedGameModel(epa_model, scores_model, blend_weight, sim)
        kind = (f"EPA×scores blend ({epa_kind}/λ{ridge:g}, w={blend_weight:g}, "
                f"base {base:.1f}) on {len(plays)} plays")
        college_scale = resolve_scale(args.ncaaf_scale, "ncaaf")
        if college_scale != "off":
            from velocity.models.level import next_week, phase_weeks, scale_model
            from velocity.models.residuals import load_residual_frame

            bank = load_residual_frame("ncaaf")
            if bank is None:
                print("no residual bank for ncaaf; projecting unscaled")
            else:
                # The phase of the week about to be played: the bank rows of
                # the same phase fit the scale (docs/MODEL_LAB.md, the college
                # composites round — calibration error 0.0095 → 0.0081 and
                # the ≥6 totals record 53.4% → 53.6% over the whole-bank fit).
                weeks = (phase_weeks(next_week(games), "ncaaf")
                         if college_scale == "phase" else None)
                # The home-margin shift (docs/MODEL_LAB.md, the home-margin
                # round): the slope fitted without its intercept had been
                # inflating the college home edge by two points a game.
                shift = resolve_scale_shift(args.ncaaf_scale_shift, "ncaaf")
                model, calibration = scale_model(
                    model, bank, games, sim, weeks=weeks, anchor_seasons=NFL_LEVEL_SEASONS,
                    shift=shift)
                phase = f" (weeks {weeks[0]}–{weeks[1]})" if weeks else ""
                kind += (f", scale ×{calibration.margin_slope:.2f} margin "
                         f"{calibration.margin_shift:+.2f} / ×{calibration.total_slope:.2f} "
                         f"total{phase}")
                print(f"NCAAF scale{phase}: margin ×{calibration.margin_slope:.3f} "
                      f"{calibration.margin_shift:+.2f}, total ×{calibration.total_slope:.3f} "
                      f"on {calibration.n} banked games")

    print(f"{args.league.upper()} ratings: {kind}, {len(games)} games")

    def project(home: str, away: str, neutral_site: bool = False) -> GameProjection:
        return model.project(  # type: ignore[attr-defined,return-value]
            home, away, rng=make_rng(), neutral_site=neutral_site
        )

    # No weather wrapper outside the NFL: the lab measured wind on NFL totals
    # (docs/MODEL_LAB.md Round 5) and velocity/report/venues.py has no college
    # stadium coordinates, so there is nothing to record rather than a zero.
    return project, list(scores_model.ratings.teams), _ratings_frame(
        args.league, model, scores_model), kind, None


def build_parser() -> argparse.ArgumentParser:
    """The live-slate CLI — a function so tests can pin the policy defaults."""
    parser = argparse.ArgumentParser(description="Live slate of staked recommendations")
    parser.add_argument("--league", choices=["nfl", "ncaaf", "mlb", "wnba", "ncaab", "nhl"],
                        required=True)
    parser.add_argument("--data", help="folder with a games file to fit the model")
    parser.add_argument("--snapshot-file", help="saved Odds API /odds JSON (offline mode)")
    # The hourly collector already bought today's board, so re-reading it costs
    # nothing where it is still current. Freshness is judged on the stamp in the
    # filename — see ``newest_banked_board`` for why mtime is not usable here.
    parser.add_argument("--snapshot-dir",
                        help="folder of banked collect-theoddsapi artifacts; the freshest "
                             "odds_{league}_{stamp}.json inside it is reused when it is "
                             "younger than --board-max-age-min (ignored with --snapshot-file)")
    parser.add_argument("--board-max-age-min", type=float, default=75.0,
                        help="how old a banked board may be before the run pulls live "
                             "instead (default: 75 minutes)")
    parser.add_argument("--n-sims", type=int, default=10_000)
    parser.add_argument("--ncaaf-level", choices=["fit", "constant"], default=None,
                        help="the college blend's scores-half level: fitted on the trailing "
                             "two seasons, or the ridge's own intercept")
    parser.add_argument("--nfl-level", choices=["fit", "constant"], default=None,
                        help="NFL scoring level: fitted through the model on the training "
                             "window, or the 22.5 constant (default: fit)")
    parser.add_argument("--nfl-plays", choices=["scrimmage", "live", "all"], default=None,
                        help="which plays the NFL ratings fit sees: the offense's own "
                             "snaps; every live play (kicks kept, kneels/spikes/no-plays "
                             "dropped); or every row (default: the lab's pick)")
    parser.add_argument("--ncaaf-plays", choices=["scrimmage", "live", "all"], default=None,
                        help="the same choice for the college blend's EPA half "
                             "(default: the lab's pick)")
    parser.add_argument("--nfl-scale", choices=["fit", "phase", "off"], default=None,
                        help="rescale the NFL projection's margin and total deviations by "
                             "the slopes fitted on the residual bank (velocity.models.level): "
                             "on the whole bank, on the bank rows of the phase of the season "
                             "being projected, or not at all (default: the lab's pick)")
    parser.add_argument("--ncaaf-scale", choices=["fit", "phase", "off"], default=None,
                        help="the same for the college blend (default: the lab's pick)")
    parser.add_argument("--nfl-scale-shift", choices=["on", "off"], default=None,
                        help="keep the scale's home-margin intercept (fitted on home-and-away "
                             "bank rows, applied to home-and-away games) in the NFL chain "
                             "(default: the lab's pick)")
    parser.add_argument("--ncaaf-scale-shift", choices=["on", "off"], default=None,
                        help="the same for the college chain (default: the lab's pick)")
    parser.add_argument("--ncaaf-early-weight", type=float, default=None,
                        help="the EPA half's weight in the college blend through week 4 "
                             "(0.5 after; default: the lab's pick)")
    parser.add_argument("--ncaaf-epa-half-life", type=float, default=None,
                        help="recency half-life, in on-field weeks, on the college EPA "
                             "half's plays (0 weighs the window flat; default: the lab's "
                             "pick)")
    parser.add_argument("--ncaaf-epa-offseason-weeks", type=float, default=None,
                        help="extra weeks of age the college EPA half's recency key puts "
                             "between seasons (default: the lab's pick)")
    parser.add_argument("--ncaaf-scores-half-life", type=float, default=None,
                        help="recency half-life, in on-field weeks, on the college scores "
                             "half's games (0 weighs them flat; default: the lab's pick)")
    parser.add_argument("--ncaaf-st-prior", choices=["on", "off"], default=None,
                        help="fold SP+'s special-teams rating into the prior's pseudo-games "
                             "(default: the lab's pick)")
    parser.add_argument("--ncaaf-qb-lambda", type=float, default=None,
                        help="decompose the passer out of the college EPA half at this QB "
                             "ridge, the detected starter priced back in (0 keeps the team "
                             "fit; default: the lab's pick)")
    parser.add_argument("--nfl-precip-points", type=float, default=None,
                        help="points off each NFL team on a forecast of ≥ 0.25 in of rain "
                             "(0 switches it off; default: the lab's pick)")
    parser.add_argument("--nfl-offseason-weeks", type=float, default=None,
                        help="extra weeks of age the NFL recency key puts between seasons "
                             "(0 steps the empty week slots only; default: the lab's pick)")
    parser.add_argument("--nfl-phase-lambda", type=float, default=None,
                        help="the joint phase ridge: each team's pass-phase deviation on "
                             "offense and defense in the NFL fit, shrunk at this ridge (0 "
                             "keeps the all-plays fit; default: the lab's pick)")
    parser.add_argument("--nfl-turnover-shrink", type=float, default=None,
                        help="scale the EPA of interceptions and lost fumbles by this factor "
                             "before the NFL ratings fit (1 keeps them whole; default: the "
                             "lab's pick)")
    parser.add_argument("--nfl-injury-points", type=float, default=None,
                        help="points off an NFL team per whole team's worth of touches ruled "
                             "Out or Doubtful this week (0 switches it off; default: the "
                             "lab's pick)")
    parser.add_argument("--sim-shape", choices=["normal", "empirical"], default=None,
                        help="football sim draw: bivariate normal, or the banked "
                             "walk-forward residual pool (default: the gate's pick per league)")
    parser.add_argument("--sim-dispersion", choices=["constant", "sloped"], default=None,
                        help="football sim sd: one per league, or moving with the "
                             "expected total (default: the gate's pick per league)")
    parser.add_argument("--sim-keys", choices=["none", "lattice"], default=None,
                        help="football sim key numbers: none, or the banked margin "
                             "lattice resampling the normal's draws (default: the "
                             "gate's pick per league)")
    parser.add_argument("--sim-skew", choices=["none", "fit"], default=None,
                        help="football sim totals skew: none, or the sinh-arcsinh "
                             "skew fitted on the banked residuals' totals (default: "
                             "the gate's pick per league)")
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
    # A college game with an FCS side is priced and graded but not staked:
    # the promoted totals filter pays on FBS-vs-FBS games (55.4% at ≥6 since
    # 2022) and loses on FBS-vs-FCS ones (47.4% on 152), where one side's
    # rating rests on a handful of games (docs/PROJECTION_AUDIT.md §2.3).
    parser.add_argument("--ncaaf-fcs", action=argparse.BooleanOptionalAction, default=False,
                        help="stake college games with an FCS side (default off: paper)")
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
    parser.add_argument("--ncaaf-total-edge", type=float, default=None,
                        help="NCAAF: min points of total disagreement to bet (0 = off; "
                             "default: the wager lab's pick)")
    parser.add_argument("--nfl-total-edge", type=float, default=None,
                        help="NFL: min points of total disagreement to bet (0 = off; "
                             "default: the wager lab's pick)")
    parser.add_argument("--ncaaf-total-sides", default=None,
                        help="which sides of the college total the filter admits, "
                             "comma-separated (default: the wager lab's pick — under)")
    parser.add_argument("--nfl-total-sides", default=None,
                        help="the same for the NFL total (default: over,under)")
    parser.add_argument("--model-weight-market", action="append", default=[],
                        metavar="MARKET=WEIGHT",
                        help="anchoring weight for one market (spread, total, moneyline), "
                             "overriding the league's fitted table; repeatable")
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
    # The BettingPros board (docs/DATA_PROVIDERS.md). The collector has banked
    # multi-book game lines every three hours since it was built and nothing
    # read them: the live board came from The Odds API alone, so a BP price was
    # never shopped and never graded. These flags put that board back on the
    # card. It is PAPER by default for the same reason the exchanges were: the
    # rows are read off a snapshot up to a cadence old, and S2's rule is that
    # money does not follow a market whose evidence is not in yet. Priced,
    # logged and graded at stake zero, the CLV record accrues; --bp-stake is
    # the switch to flip once it says something.
    parser.add_argument("--bp-lines-file",
                        help="banked bp_lines parquet — puts the BettingPros board on the card")
    parser.add_argument("--bp-events-file",
                        help="banked bp_events parquet (the team/kickoff map for --bp-lines-file)")
    parser.add_argument("--bp-books-file",
                        help="banked bp_books parquet — resolves bp:<id> keys to bp:<name>")
    parser.add_argument("--bp-stake", action=argparse.BooleanOptionalAction, default=False,
                        help="stake the BettingPros rows instead of papering them. Tighten "
                             "--bp-max-age-min first: a banked price that has moved is not "
                             "a price we can take.")
    parser.add_argument("--bp-max-age-min", type=float, default=DEFAULT_BP_MAX_AGE_MIN,
                        help="refuse a banked BettingPros board older than this many minutes")
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
    parser.add_argument("--ncaaf-player-games",
                        default="datasets/ncaaf/player_games.parquet",
                        help="banked college player-games; the NCAAF prop "
                             "projection's source (FantasyPros has no college)")
    parser.add_argument("--prop-lines-file",
                        help="banked PropLines parquet (offline prop board)")
    parser.add_argument("--prop-lines-dir",
                        help="folder of banked prop boards; the freshest one "
                             "inside --board-max-age-min is priced, spending "
                             "no credits. The only NCAAF source — that league "
                             "never pulls prop lines live")
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
                             "artifact) — enables availability vetoes. NFL only: "
                             "FantasyPros has no other league.")
    # ESPN's injury report (velocity/ingest/espn.py) — keyless, and the only
    # injury source that covers every league we price. Before it, the intel
    # layer's availability signals abstained on all five non-NFL leagues: an
    # MLB card was priced with no idea a listed starter was on the 60-day IL.
    parser.add_argument("--espn-injuries-file",
                        help="banked espn_injuries parquet — availability vetoes "
                             "for every league, not just the NFL")
    # The depth chart the QB starter map would rather read than infer. Without
    # it the map takes FantasyPros' busiest projected passer, which is a proxy
    # that disagrees with the depth chart exactly when the projections are
    # stale — the case it exists to catch.
    parser.add_argument("--espn-depth-file",
                        help="banked espn_depth parquet — names each team's QB1 from "
                             "the depth chart instead of projected workload")
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
    parser.add_argument("--publish-by-rule", choices=["on", "off"], default=None,
                        help="post only plays a rule with a walk-forward record admits, "
                             "ranked by rule tier then edge, the conviction and context "
                             "floors standing down (default: on)")
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
# Market anchoring: p_belief = p_market + w·(p_model − p_market). A league
# absent here resolves to 1.0 — the raw model price, no pull toward the market.
#
# MLB joined this table in 2026-09. It had been running at 1.0 with no
# probability shrink either, both levers raw, on the league carrying the
# largest real exposure — and the lab's own MLB rounds put the model at Brier
# 0.2485 against the de-vigged closing moneyline's 0.2491, which is parity
# rather than an edge. It sits at the NFL's 0.2 as a deliberately conservative
# holding position, not a fitted one: choosing it properly is a sweep against
# banked closing moneylines, and those live in the private historical-odds
# artifact rather than in datasets/. That sweep also has to be re-run rather
# than read off the record, because the evidence above was produced by the
# rounded-normal sim that velocity/models/counts.py replaced — a sim that
# priced every home moneyline 2.8 points low and the run line 4-5 points off
# (docs/BUILD_MLB.md §8). Until it runs, the exposure is anchored.
DEFAULT_MODEL_WEIGHT_BY_LEAGUE = {"nfl": 0.2, "ncaaf": 0.13, "mlb": 0.2}


# Leagues in the content + CLV posture: their labs found no promoted edge
# (NCAAB null after FDR, NHL no closes backtest yet, WNBA tracked not staked —
# docs/STRATEGY_REVIEW.md §1.3), so every market prices and grades as paper.
DEFAULT_PAPER_BY_LEAGUE = {"ncaab": True, "nhl": True, "wnba": True}
# Leagues whose props come from the FantasyPros-fed correlated football sim
# (velocity/models/props_football.py). Every other league either has its own
# prop model — MLB's pitcher-K slate, priced off the banked starters history —
# or no prop board at all, and in neither case does --fp-projections mean
# anything to it.
FOOTBALL_PROP_LEAGUES = ("nfl", "ncaaf")
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
        edge_pts = resolve_total_edge(args, args.league)
        if edge_pts > 0:
            sides = "/".join(sorted(resolve_total_sides(args, args.league)))
            cuts.append(f"totals only at ≥ {edge_pts:g} pts of disagreement ({sides})")
        for market, w in sorted(resolve_model_weights_by_market(
                args.model_weight_market, args.league).items()):
            cuts.append(f"{market} anchored at {w:g}" if w > 0 else f"{market} off the board")
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
    # The BettingPros books are papered by the board builder, not by an arg, so
    # they are read back off the config that was actually built — the whole
    # point of this function.
    bp_venues = sorted(
        v for v in getattr(cfg, "paper_venues", frozenset()) or frozenset()
        if str(v).startswith("bp:")
    )
    if bp_venues:
        paper_label += f" · BettingPros ({len(bp_venues)} book(s))"
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
#
# "lattice" is the one sim-shape candidate that did clear the bar (the
# lattice round, the derivative re-check and the promotion round in
# docs/MODEL_LAB.md): the normal's own rounded draws resampled by how much
# more often football lands on each absolute margin, banked per league in
# datasets/{league}/lattice.parquet (scripts/build_lattice.py). It moves no
# μ and no sd — a 3-point favourite's push on exactly 3 goes from 3.1% to
# 7.7% because the normal was pricing the key numbers at 40% of their size.
# Both leagues, since the tail round: with the table's tail bin corrected
# the NFL opened every spread side on the ladder gate (50 → 58 of 58) while
# college closed eight, because a third of college margins sit in that bin
# and its weight overstated the favourite's blowouts at the market's sharper
# numbers. With the tail left alone (the bank's default now) both leagues
# open every spread side and the NFL shoulder falls further, 0.019 → 0.014.
#
# "fit" skews the TOTAL's draw (velocity/models/skew.py) by the sinh-arcsinh
# skew fitted on the banked residuals' totals — the one asymmetry football
# has (a game runs away upward and not downward), on the normal path only.
# The skew round measured it right about the shape and dominated by the
# totals level; the level round then fixed a third of that level, and the
# skew re-test (docs/MODEL_LAB.md) decides the default below.
DEFAULT_SIM_SHAPE_BY_LEAGUE = {"nfl": "normal", "ncaaf": "normal"}
DEFAULT_SIM_DISPERSION_BY_LEAGUE = {"nfl": "constant", "ncaaf": "constant"}
DEFAULT_SIM_KEYS_BY_LEAGUE = {"nfl": "lattice", "ncaaf": "lattice"}
DEFAULT_SIM_SKEW_BY_LEAGUE = {"nfl": "none", "ncaaf": "none"}
FOOTBALL_SDS = {"nfl": (DEFAULT_SD_MARGIN, DEFAULT_SD_TOTAL),
                "ncaaf": (NCAAF_SD_MARGIN, NCAAF_SD_TOTAL)}


# The NFL scoring level: "fit" shifts base_points through the model on the
# training window (velocity.models.level); "constant" is the 22.5 the model
# always assumed. Moves to "fit" only with the lab table (docs/MODEL_LAB.md).
DEFAULT_NFL_LEVEL = "fit"
NFL_LEVEL_SEASONS = 2
# The level round (docs/MODEL_LAB.md): the trailing eight on-field weeks,
# across the season boundary, blended toward the two-season level by 128
# games — 0.035 of totals RMSE over the two-season fit, better in eight
# seasons of twelve, the season-to-season wander cut from 1.44 to 1.16.
NFL_LEVEL_WEEKS = 8
NFL_LEVEL_SHRINK_GAMES = 128.0


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


# Which plays the ratings fits see (velocity.features.team.scrimmage_plays)
# and whether the projection's deviations are rescaled by the residual bank's
# slopes (velocity.models.level.ScaleCalibration). Set by the lab
# (docs/MODEL_LAB.md, the plays-and-scale round):
#
# * NFL plays stay "all": the scrimmage-only fit LOST — Brier 0.2224 against
#   0.2195, margin RMSE 13.53 against 13.33. The kicks and punts carry field
#   position the ratings want; the audit's kneel finding was real and the
#   remedy was wrong.
# * NFL scale "fit": totals RMSE 13.90 → 13.59 (the close: 13.23), margin
#   and Brier unchanged — the projection's total had been claiming twice the
#   deviation it earned, and the fitted slope (~0.50) returns it.
# * NCAAF scale "fit": calibration error 0.0203 → 0.0095, margin RMSE 18.52
#   → 18.46, totals RMSE 17.36 → 17.22, and the staked ≥6 totals record
#   52.9% → 53.4% on ~4,100 bets. College plays stay "all" too: the
#   scrimmage cut was a wash on every column (the college frame is 0.9%
#   non-scrimmage rows).
# * NCAAF scale "phase" (the college composites round): the scale fitted on
#   the bank rows of the phase being projected — through week 4, or after —
#   beats the whole-bank fit on every column: calibration error 0.0095 →
#   0.0081, margin RMSE 18.46 → 18.43, and the ≥6 totals record 53.4% →
#   53.6%. The NFL's phase scale is in the lab, not yet promoted.
DEFAULT_PLAYS_BY_LEAGUE = {"nfl": "all", "ncaaf": "all"}
DEFAULT_SCALE_BY_LEAGUE = {"nfl": "fit", "ncaaf": "phase"}
# Rain on NFL totals (the situational round, docs/MODEL_LAB.md): a point a
# side on a forecast of ≥ 0.25 in. The wet games (394 of 4,080) had run 2.1
# points under the projection; this takes them to −0.1 with the aggregate
# totals RMSE and information weight both better. The divisional discount
# tested beside it over-corrected a bias the model does not have.
DEFAULT_NFL_PRECIP_POINTS = 1.0


def resolve_precip_points(explicit: float | None) -> float:
    return DEFAULT_NFL_PRECIP_POINTS if explicit is None else max(0.0, float(explicit))


# The injury burden on NFL points (the situational round): 4 points per whole
# team's worth of touches ruled out, so a 15% burden — the 90th percentile of
# team-weeks — costs 0.6. Measured at 4 / 8 / 16: 8 over-corrects the games
# it targets (+0.5 the other way) and 16 is worse everywhere.
DEFAULT_NFL_INJURY_POINTS = 4.0


def resolve_injury_points(explicit: float | None) -> float:
    return DEFAULT_NFL_INJURY_POINTS if explicit is None else max(0.0, float(explicit))


# The turnover-EPA shrink before the NFL ratings fit (the play-context round,
# docs/MODEL_LAB.md): the factor on interceptions' and lost fumbles' EPA,
# 1.0 = the plays as recorded. At 0.5, with the residual bank rebuilt on the
# shrunk core, every accuracy column of the promoted chain improved: Brier
# 0.2184 → 0.2181, calibration error 0.0164 → 0.0157, margin RMSE 13.26 →
# 13.25, total RMSE 13.55 → 13.52 (the close 13.23) and the total's
# information weight +0.08 → +0.09. Garbage-time down-weighting, an EPA
# winsor, the QB-credited EPA and a fitted home edge all lost beside it.
DEFAULT_NFL_TURNOVER_SHRINK = 0.5


# The joint phase ridge in the NFL fit (docs/MODEL_LAB.md, the phase round):
# pass-phase deviations per team on offense and defense, shrunk at this
# ridge; 0 = the all-plays fit.
DEFAULT_NFL_PHASE_LAMBDA = 0.0


def resolve_phase_lambda(explicit: float | None) -> float:
    return DEFAULT_NFL_PHASE_LAMBDA if explicit is None else max(0.0, float(explicit))


def resolve_turnover_shrink(explicit: float | None) -> float:
    if explicit is None:
        return DEFAULT_NFL_TURNOVER_SHRINK
    return min(1.0, max(0.0, float(explicit)))


# The college QB term: the passer decomposed out of the EPA half's offense
# at this QB ridge (0 = the team fit); the passer ids come from cfbfastR's
# player stats (scripts/attach_ncaaf_passers.py, topped up by the refresh).
# Measured and not promoted (docs/MODEL_LAB.md, the college QB round): over
# the flat fit it took 0.26 off the margin RMSE, but beside the six-week
# recency its margin gain shrinks to 0.05 and it costs the total 0.04–0.09.
# Available as --ncaaf-qb-lambda for the day an announced-starter feed
# makes a priced QB change worth more than a cleaner team estimate.
DEFAULT_NCAAF_QB_LAMBDA = 0.0


def resolve_ncaaf_qb_lambda(explicit: float | None) -> float:
    return DEFAULT_NCAAF_QB_LAMBDA if explicit is None else max(0.0, float(explicit))


# Recency on the college EPA half, in on-field weeks (0 = the flat window the
# fit ran on through the composites round). The college recency round
# (docs/MODEL_LAB.md): a six-week half-life, with the college residual bank
# rebuilt on the recency core, took the FBS walk-forward from Brier 0.2002
# to 0.1881, margin RMSE 17.73 → 16.88 (the close 15.64) and total RMSE
# 17.25 → 16.85 (the close 16.30) — the largest single gain the lab has
# recorded. The sweep is flat between 4 and 8 and climbs steadily beyond.
DEFAULT_NCAAF_EPA_HALF_LIFE = 6.0


def resolve_ncaaf_epa_half_life(explicit: float | None) -> float:
    return DEFAULT_NCAAF_EPA_HALF_LIFE if explicit is None else max(0.0, float(explicit))


# The recency round's other knobs (docs/MODEL_LAB.md), defaults set by the lab:
# the offseason gap in each recency key (extra weeks of age between one
# season's last week and the next season's first), recency on the college
# scores half (0 = flat), and SP+ special teams in the prior's pseudo-games.
# College (docs/MODEL_LAB.md, the recency round): a six-week offseason gap
# in the EPA half's key, a 34-week half-life on the scores half and SP+
# special teams in the prior, together, with the college bank rebuilt on
# the combined core: Brier 0.1881 → 0.1866, margin RMSE 16.88 → 16.82,
# total RMSE 16.85 → 16.81 over the six-week-recency chain. The
# calibration error climbs 0.019 → 0.029 with the bank rebuilt: the sim's
# margin sd no longer matches a sharper model's residuals — the next item.
# NFL: an eight-week offseason gap in the recency key, with the NFL bank
# rebuilt on the gapped core: Brier 0.2181 → 0.2176, calibration error
# 0.0157 → 0.0141, margin RMSE 13.25 → 13.23, total RMSE 13.52 → 13.51.
# The half-life itself stays at 17 (12 ties it on the margin and loses the
# margin's information weight; 8 and 25 lose outright).
DEFAULT_NFL_OFFSEASON_WEEKS = 8.0
DEFAULT_NCAAF_EPA_OFFSEASON_WEEKS = 6.0
DEFAULT_NCAAF_SCORES_HALF_LIFE = 34.0
DEFAULT_NCAAF_ST_PRIOR = True


def resolve_offseason_weeks(explicit: float | None) -> float:
    return DEFAULT_NFL_OFFSEASON_WEEKS if explicit is None else max(0.0, float(explicit))


def resolve_ncaaf_epa_offseason_weeks(explicit: float | None) -> float:
    return DEFAULT_NCAAF_EPA_OFFSEASON_WEEKS if explicit is None else max(0.0, float(explicit))


def resolve_ncaaf_scores_half_life(explicit: float | None) -> float:
    return DEFAULT_NCAAF_SCORES_HALF_LIFE if explicit is None else max(0.0, float(explicit))


def resolve_ncaaf_st_prior(explicit: str | None) -> bool:
    return DEFAULT_NCAAF_ST_PRIOR if explicit is None else explicit == "on"


def resolve_plays(explicit: str | None, league: str) -> str:
    return explicit or DEFAULT_PLAYS_BY_LEAGUE.get(league, "all")


def resolve_scale(explicit: str | None, league: str) -> str:
    return explicit or DEFAULT_SCALE_BY_LEAGUE.get(league, "off")


# The scale's home-margin intercept, kept and applied to home-and-away games
# (velocity.models.level.ScaleCalibration.margin_shift). The home-margin
# round (docs/MODEL_LAB.md): the college slope of 1.35 fitted without its
# intercept had inflated the home edge by two points a game; keeping it
# takes the margin RMSE 16.82 → 16.71 and the calibration error 0.029 →
# 0.019 (0.014 with the sim's dispersion re-measured on the shifted chain).
# The NFL bank's intercept is noise around zero and the shift costs it
# calibration — off there.
DEFAULT_SCALE_SHIFT_BY_LEAGUE = {"nfl": False, "ncaaf": True}


# The EPA half's weight in the college blend through week 4 (0.5 after). The
# early-weight round (docs/MODEL_LAB.md): a wash over the flat fit, worth
# 0.03 on the margin over the recency chain — 0.4 (0.3 ties it on the
# margin and loses on the total; 0.6 loses).
DEFAULT_NCAAF_EARLY_WEIGHT = 0.4


def resolve_ncaaf_early_weight(explicit: float | None) -> float:
    if explicit is None:
        return DEFAULT_NCAAF_EARLY_WEIGHT
    return min(1.0, max(0.0, float(explicit)))


def resolve_scale_shift(explicit: str | None, league: str) -> bool:
    if explicit is None:
        return DEFAULT_SCALE_SHIFT_BY_LEAGUE.get(league, False)
    return explicit == "on"


def resolve_sim_shape(explicit: str | None, league: str) -> str:
    return explicit or DEFAULT_SIM_SHAPE_BY_LEAGUE.get(league, "normal")


def resolve_sim_dispersion(explicit: str | None, league: str) -> str:
    return explicit or DEFAULT_SIM_DISPERSION_BY_LEAGUE.get(league, "constant")


def resolve_sim_keys(explicit: str | None, league: str) -> str:
    return explicit or DEFAULT_SIM_KEYS_BY_LEAGUE.get(league, "none")


def resolve_sim_skew(explicit: str | None, league: str) -> str:
    return explicit or DEFAULT_SIM_SKEW_BY_LEAGUE.get(league, "none")


def football_sim_config(league: str, args: argparse.Namespace) -> SimConfig:
    """The football sim for this run: league sds, shape, dispersion, size.

    An empirical shape with no committed pool falls back to the normal and
    says so — a missing bank is a build gap, never a silent change of sim.
    The lattice is the same: it reads the banked table or says it could not.
    It corrects the normal draw only, so an empirical shape switches it off.
    The totals skew is fitted on the bank's totals at run time (the pool's
    own shape already carries it, so the empirical path skips it too).
    """
    from velocity.models.keynumbers import load_lattice_weights
    from velocity.models.residuals import load_residual_frame, load_residual_pool
    from velocity.models.skew import fit_epsilon

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
    if (resolve_sim_keys(getattr(args, "sim_keys", None), league) == "lattice"
            and "residuals" not in kwargs):
        lattice = load_lattice_weights(league)
        if lattice is None:
            print(f"no margin lattice banked for {league}; simulating without key numbers")
        else:
            kwargs["lattice"] = lattice
    if (resolve_sim_skew(getattr(args, "sim_skew", None), league) == "fit"
            and "residuals" not in kwargs):
        bank = load_residual_frame(league)
        if bank is None:
            print(f"no residual bank for {league}; simulating totals without skew")
        else:
            kwargs["total_skew"] = fit_epsilon(bank["resid_total"].to_numpy())
    return SimConfig(**kwargs)  # type: ignore[arg-type]


def describe_sim(config: SimConfig, league: str) -> str:
    """The Methods row: what shape and width this run simulated with."""
    shape = ("normal" if config.residuals is None
             else f"empirical ({len(config.residuals)} banked residual pairs)")
    if config.lattice is not None:
        shape += f" + key numbers (banked lattice to |margin| {config.lattice.max_abs})"
    if config.total_skew:
        shape += f" + totals skew ε {config.total_skew:+.2f} (fitted on the bank)"
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


def fbs_teams(folder: Path) -> set[str]:
    """The FBS programs, as the latest committed SP+ season rates them."""
    sp_file = folder / "sp_ratings.parquet"
    if not sp_file.exists():
        return set()
    sp = pd.read_parquet(sp_file, columns=["season", "team"])
    if sp.empty:
        return set()
    latest = sp[sp["season"] == sp["season"].max()]
    return set(latest["team"].astype(str))


def fcs_paper_games(
    args: argparse.Namespace, projections: Mapping[str, GameProjection]
) -> dict[str, str]:
    """``{game_id: reason}`` for the college games with an FCS side, unless staked.

    The FBS list is the latest SP+ season's; with no ratings file on disk
    nothing is papered, and the run says nothing — a missing prior is a
    build gap, not a policy.
    """
    if args.league != "ncaaf" or getattr(args, "ncaaf_fcs", False) or not args.data:
        return {}
    fbs = fbs_teams(Path(args.data))
    if not fbs:
        return {}
    out: dict[str, str] = {}
    for gid, proj in projections.items():
        if proj.home_team not in fbs or proj.away_team not in fbs:
            side = proj.away_team if proj.home_team in fbs else proj.home_team
            out[str(gid)] = f"FCS side {side}: totals 47% at the filter, no promoted edge"
    return out


def resolve_paper_markets(args: argparse.Namespace) -> frozenset[str]:
    """The markets this run prices but never stakes (docs/STRATEGY_REVIEW.md S2)."""
    if resolve_paper(args.paper, args.league):
        return frozenset(GAME_MARKETS) | frozenset({"__all__"})
    markets = frozenset(_TEAM_TOTALS) if args.team_totals_paper else frozenset()
    return markets | ncaaf_side_paper(args)


def ncaaf_side_paper(args: argparse.Namespace) -> frozenset[str]:
    """NCAAF spreads and moneylines, papered rather than dropped.

    Both sit out on real evidence, and papering does **not** reopen that:
    spreads walk-forward at 50.1% ATS flat with no edge at any disagreement
    threshold, and the moneyline round was worse still — raw −4.8% over 2,807
    bets, −36% at ≥+1000, the model's Brier 0.217 against the market's 0.183
    (docs/STRATEGY_REVIEW.md S3). Neither is staked here.

    What changes is that they were sitting out by being **excluded**, which
    produced no row at all — no price, no edge, no closing-line value, nothing
    to grade. Papering costs no exposure and buys the measurement: every side
    is priced, logged and graded for CLV at stake zero. On that evidence most
    of those rows should keep confirming the exclusion, and a standing record
    saying so is worth more than an assumption nobody can check.

    The ``--ncaaf-spreads`` / ``--ncaaf-moneylines`` flags still stake them.
    """
    if args.league != "ncaaf":
        return frozenset()
    paper = set()
    if not args.ncaaf_spreads:
        paper.add("spread")
    if not args.ncaaf_moneylines:
        paper.add("moneyline")
    return frozenset(paper)


def resolve_paper_venues(args: argparse.Namespace) -> frozenset[str]:
    """The venues this run prices but never stakes.

    Two reasons an exchange venue sits at stake zero. The operator can ask for
    it with ``--exchange-paper``. And a league whose ladder shape table has not
    been fitted is held there whatever the flag says, because the E8b gate is
    asymmetric in exactly the wrong direction when it has nothing to read: a
    spread or total rung gets no bias and is refused, while a moneyline — the
    one market with no number to be miscalibrated about — passes ungated. So
    the half of a new league's board that would ship first is the half nothing
    is checking. Paper until measured.
    """
    from velocity.store.schema import LADDER_BOOKS

    if not getattr(args, "exchanges", False):
        return frozenset()
    if args.exchange_paper or not has_ladder_calibration(args.league):
        return frozenset(LADDER_BOOKS)
    return frozenset()


def _prop_paper_markets(args: argparse.Namespace) -> frozenset[str]:
    """Prop markets this run prices but never stakes — the league's paper posture.

    Props have no market list of their own, so a paper league marks every
    prop market it prices by intercepting the config at stake time: the slate
    treats a market set containing ``"__all__"`` as "all of them".
    """
    return frozenset({"__all__"}) if resolve_paper(args.paper, args.league) else frozenset()


# The publish gate runs by rule tier (docs/OUTPUT_AUDIT.md §3 #5): a play
# posts only when a rule with a walk-forward record admits it, and the
# running order is tier then edge. The conviction and context floors stand
# down — the intel backtest measured them as a null (docs/BACKTEST_INTEL.md);
# the injury veto, the edge band and the drift check keep their say.
DEFAULT_PUBLISH_BY_RULE = True


def resolve_publish_by_rule(explicit: str | None) -> bool:
    return DEFAULT_PUBLISH_BY_RULE if explicit is None else explicit == "on"


def resolve_model_weight(explicit: float | None, league: str) -> float:
    """The market-anchoring weight for this run — the flag, else the league default."""
    if explicit is not None:
        return explicit
    return DEFAULT_MODEL_WEIGHT_BY_LEAGUE.get(league, 1.0)


# Per-market anchoring weights (docs/OUTPUT_AUDIT.md §3 #2), fitted by the
# wager lab as the weight that maps a promoted rule's claimed edge onto its
# walk-forward record (velocity.backtest.wagers.rule_weight): the NFL total's
# 4-point cut earns 0.29 of its raw disagreement over 637 bets, the college
# under-only 4-point cut 0.21 over 1,255. The close would put nothing on the
# model's spreads (+0.03 NFL, −0.09 college) or moneylines (−0.01) — at 0
# the belief is the market's and those markets leave the board; the lab is
# where they earn their way back. Leagues not listed keep the league weight.
DEFAULT_MODEL_WEIGHT_BY_MARKET: dict[str, dict[str, float]] = {
    "nfl": {"spread": 0.0, "total": 0.29, "moneyline": 0.0},
    "ncaaf": {"spread": 0.0, "total": 0.21, "moneyline": 0.0},
}
# The totals filters (docs/OUTPUT_AUDIT.md §2.2): points of disagreement with
# the number, and the sides admitted. NFL: 4 either side (54.3% on 641, nine
# seasons of fifteen; the unders 55.6%). College: 4 on the under alone
# (53.6% on 1,263, seven of twelve) — the overs are 50.0% at every threshold,
# which is why the shipped either-side 6-point rule paid in five seasons of
# twelve.
DEFAULT_TOTAL_EDGE_BY_LEAGUE = {"nfl": 4.0, "ncaaf": 4.0}
# Unders only in both leagues. College's edge was always on the under side
# (docs/OUTPUT_AUDIT.md §2.2); the NFL's over side at 4+ read 52.8% on the
# previous ledger and 49.8% (−3.3% at the juice) on the level round's — a
# coin flip either way against 56.2% and +9.4% for the unders, 11 seasons
# of 15 above break-even (docs/MODEL_LAB.md, the level round's wager lab).
DEFAULT_TOTAL_SIDES_BY_LEAGUE = {"nfl": frozenset({"under"}),
                                 "ncaaf": frozenset({"under"})}


def resolve_model_weights_by_market(pairs: Sequence[str], league: str) -> dict[str, float]:
    """The league's fitted per-market weights, with ``MARKET=WEIGHT`` overrides."""
    out = dict(DEFAULT_MODEL_WEIGHT_BY_MARKET.get(league, {}))
    for pair in pairs:
        market, sep, value = pair.partition("=")
        if not sep or not market.strip():
            raise SystemExit(f"--model-weight-market wants MARKET=WEIGHT, got {pair!r}")
        try:
            out[market.strip()] = max(0.0, float(value))
        except ValueError:
            raise SystemExit(
                f"--model-weight-market wants a numeric weight, got {pair!r}") from None
    return out


def resolve_total_edge(args: argparse.Namespace, league: str) -> float:
    """Points of total disagreement to bet — the league's flag, else the lab's pick."""
    explicit = getattr(args, f"{league}_total_edge", None)
    if explicit is not None:
        return max(0.0, float(explicit))
    return DEFAULT_TOTAL_EDGE_BY_LEAGUE.get(league, 0.0)


def resolve_total_sides(args: argparse.Namespace, league: str) -> frozenset[str]:
    """The sides of the total the filter admits — the league's flag, else the lab's pick."""
    explicit = getattr(args, f"{league}_total_sides", None)
    if explicit:
        sides = frozenset(part.strip().lower() for part in explicit.split(",") if part.strip())
        if not sides <= {"over", "under"}:
            raise SystemExit(f"--{league}-total-sides wants over and/or under, got {explicit!r}")
        return sides
    return DEFAULT_TOTAL_SIDES_BY_LEAGUE.get(league, frozenset({"over", "under"}))


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


def _window_events(
    events: pd.DataFrame, now: pd.Timestamp, max_days: float
) -> pd.DataFrame:
    """The board games inside the pricing window: six hours back to ``max_days`` ahead.

    ``max_days <= 0`` keeps the whole board, as it always has. The window also
    decides whether the ratings are fitted at all (see ``main``), so it is one
    function rather than the same filter written twice.
    """
    if max_days <= 0 or events.empty:
        return events
    kickoff = pd.to_datetime(events["kickoff"], errors="coerce")
    window = (kickoff >= now - pd.Timedelta(hours=6)) & (
        kickoff <= now + pd.Timedelta(days=max_days)
    )
    return events[window].reset_index(drop=True)


# What the weather record carries, per game on the board.
WEATHER_COLUMNS = [
    "game_id", "home_team", "away_team", "kickoff", "wind_mph", "precip_in",
    "temp_f", "wind_points", "precip_points", "total_points",
]


def weather_frame(
    weather_model: object,
    events: pd.DataFrame,
    known_teams: Iterable[str] = (),
    aliases: dict[str, str] | None = None,
) -> pd.DataFrame:
    """One row per board game: the forecast the model used, and what it moved.

    ``weather_model`` is the :class:`WeatherAdjustedModel` the fit built, or
    ``None`` for a league with no weather wrapper — which is every league but
    the NFL today, since the lab measured wind on NFL totals and there is no
    college stadium coordinate table. A ``None`` model yields an empty frame
    rather than a table of zeroes: "not adjusted" and "adjusted by nothing"
    are different claims and only one of them is true.

    Team names are resolved through :func:`resolve_team` — the same call
    :func:`project_board` makes — because the board and the model do not spell
    teams the same way. The forecast frame is keyed by nflverse abbreviation
    ("GB") while The Odds API sends club names ("Green Bay Packers"), so
    looking the raw board name up finds nothing, every row comes back NaN,
    and the record is silently empty on every live run.

    Numbers come from the wrapper's own ``weather_note``, never recomputed
    here — a second implementation is how a shown adjustment drifts from the
    applied one.
    """
    note = getattr(weather_model, "weather_note", None)
    if note is None or events is None or events.empty:
        return pd.DataFrame(columns=WEATHER_COLUMNS)
    from velocity.wagering.live import resolve_team

    known = list(known_teams)
    rows = []
    for event in events.to_dict("records"):
        board_name = str(event.get("home_team", ""))
        home = resolve_team(board_name, known, aliases) or board_name
        kickoff = event.get("kickoff")
        detail = note(home, kickoff)
        rows.append({
            "game_id": str(event.get("game_id", "")),
            # The board's spelling, because every other persisted frame and
            # the site join on it; the model's spelling did the lookup above.
            "home_team": board_name,
            "away_team": str(event.get("away_team", "")),
            "kickoff": pd.Timestamp(kickoff) if kickoff is not None else pd.NaT,
            "wind_mph": detail["wind_mph"],
            "precip_in": detail["precip_in"],
            "temp_f": detail["temp_f"],
            "wind_points": detail["wind_points"],
            "precip_points": detail["precip_points"],
            "total_points": detail["total_points"],
        })
    frame = pd.DataFrame(rows, columns=WEATHER_COLUMNS)
    # A game the forecast frame never covered carries no numbers at all; it
    # is dropped rather than published as a calm day.
    return frame[frame["wind_mph"].notna()].reset_index(drop=True)


def _committed_teams(schedule: pd.DataFrame | None) -> list[str]:
    """Every team in the committed games frame: the fit's universe, without the fit."""
    if schedule is None or schedule.empty:
        return []
    teams = set(schedule["home_team"].astype(str)) | set(schedule["away_team"].astype(str))
    return sorted(teams)


def _no_projection(home: str, away: str) -> GameProjection:
    """Stands in for the projection when no ratings were fitted (an empty board)."""
    raise RuntimeError(f"no ratings were fitted: the board was empty ({away} @ {home})")


def main() -> None:
    args = build_parser().parse_args()

    now = datetime.now(UTC)
    generated_at = pd.Timestamp(now).tz_localize(None)

    # Resolve the banked board before anything reads --snapshot-file: every
    # downstream "are we on a saved board" test keys on that one attribute.
    resolve_banked_board(args, generated_at)

    if args.out:
        Path(args.out).mkdir(parents=True, exist_ok=True)

    ledger = _open_ledger(args, generated_at)

    schedule = _league_schedule(args, Path(args.data), generated_at) if args.data else None

    payload = _load_snapshot(args)
    lines = normalize_odds_events(payload)
    events = extract_events(payload)

    # The board is read BEFORE the ratings are fitted, because the fit is the
    # expensive half of the run and an empty board leaves it nothing to price.
    # NCAAB's promoted fit (pace×efficiency over 111k games plus the Torvik
    # pseudo-games) takes eleven minutes on a hosted runner, and for the five
    # months of its off-season it ran to completion and then found zero games
    # inside the window — a third of every live-slate run spent on a number
    # nobody read. The 2026-09-19 16:53 run was inside exactly that fit when
    # GitHub reclaimed the runner (exit 143), which cost the slate artifact,
    # the email and the site. A league with nothing inside the window skips
    # the fit; the team universe the exchange and BettingPros re-keys want
    # comes straight from the committed games frame instead.
    if _window_events(events, generated_at, args.max_days).empty:
        print(f"ratings: fit skipped — no {args.league.upper()} game inside the "
              f"{args.max_days:g}-day window, nothing to price")
        project = _no_projection
        known_teams = _committed_teams(schedule)
        ratings_frame = pd.DataFrame()
        fit_kind = "skipped (no games on the board)"
        weather_model = None
    else:
        project, known_teams, ratings_frame, fit_kind, weather_model = _build_projection(
            args, schedule)
    # Live football boards: team totals ride the per-event endpoint. Best-effort
    # — a failed fetch just leaves the three main markets on the board.
    if (args.team_totals and not args.snapshot_file
            and args.league in ("nfl", "ncaaf")):
        try:
            team_lines = _odds_client().team_totals(args.league)
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
    if args.exchanges and not args.offline and args.league in EXCHANGE_LEAGUES:
        from velocity.ingest.exchanges import fetch_exchange_board

        exchange_lines, venue_notes = fetch_exchange_board(
            args.league, known_teams, events, generated_at
        )
        for venue, note in venue_notes.items():
            unresolved = note.get("unresolved_sides", 0)
            tail = f" ({unresolved} row(s) dropped — side unresolved)" if unresolved else ""
            print(f"{venue}: {note['lines']} lines across {note['games']} board games{tail}")
        if not exchange_lines.empty:
            lines = pd.concat([lines, exchange_lines], ignore_index=True)
    elif args.exchanges:
        print("exchanges: skipped (--offline, or a league with no exchange board)")

    # The BettingPros board — banked by the 3-hourly collector, re-keyed onto
    # this board's game ids the same way an exchange board is. Its books are
    # the same sportsbooks The Odds API quotes, so every key is prefixed
    # ``bp:``: the feed is part of the book's identity, and a grader must be
    # able to tell which feed a bet's number came from.
    bp_paper_venues: frozenset[str] = frozenset()
    if args.bp_lines_file and args.bp_events_file:
        try:
            from velocity.ingest.bettingpros import bp_board, bp_book_keys

            book_names = {}
            if args.bp_books_file and Path(args.bp_books_file).exists():
                books_frame = pd.read_parquet(args.bp_books_file)
                book_names = dict(
                    zip(books_frame["book_id"].astype(str),
                        books_frame["book_name"].astype(str), strict=False)
                )
            bp_lines, bp_notes = bp_board(
                pd.read_parquet(args.bp_lines_file),
                pd.read_parquet(args.bp_events_file),
                known_teams,
                events,
                now=generated_at,
                league=args.league,
                max_age_minutes=args.bp_max_age_min,
                book_names=book_names,
            )
            if bp_notes["stale"]:
                age = bp_notes["age_min"]
                seen = "no timestamp" if age is None else f"{age:g} min old"
                print(f"bettingpros: board refused ({seen}, limit "
                      f"{args.bp_max_age_min:g} min) — a price that has moved is not "
                      "a price we can take")
            elif bp_lines.empty:
                print(f"bettingpros: no rows aligned onto the board "
                      f"({bp_notes['games']} game(s) matched)")
            else:
                unresolved = bp_notes["unresolved_sides"]
                tail = f" ({unresolved} row(s) dropped — side unresolved)" if unresolved else ""
                print(f"bettingpros: {bp_notes['lines']} lines across "
                      f"{bp_notes['games']} board games, {bp_notes['age_min']:g} min old"
                      f"{tail}")
                lines = pd.concat([lines, bp_lines], ignore_index=True)
                if not args.bp_stake:
                    bp_paper_venues = bp_book_keys(bp_lines)
                    print(f"bettingpros: priced and graded on the board, staked at zero "
                          f"({len(bp_paper_venues)} book(s)) — --bp-stake stakes them")
        except Exception as exc:  # noqa: BLE001 - an optional banked board
            print(f"bettingpros board skipped: {exc}")

    n_board = len(events)
    events = _window_events(events, generated_at, args.max_days)
    print(f"=== Live slate: {args.league.upper()} — {len(events)} of {n_board} board "
          f"games inside the {args.max_days:g}-day window ===")

    frame = pd.DataFrame()
    projections: dict = {}
    # Board-name → model-name, built below where there is a board to price.
    # Initialized here because the weather record is written outside that
    # branch and must not depend on having reached it.
    aliases: dict[str, str] | None = None
    canonical = pd.DataFrame()
    game_log = None
    if events.empty:
        print("no games on the board (off-season or empty snapshot)")
        # ...which of the two it is, the committed schedule can say. A league
        # that was playing on this date in the seasons already banked and has
        # published nothing for a week is a stalled feed, not an off-season
        # (velocity/report/league_health.py). Best-effort: a health line never
        # blocks a run that has nothing to price anyway.
        try:
            from velocity.report.league_health import league_health

            health = league_health(
                schedule if schedule is not None else pd.DataFrame(),
                pd.Timestamp(generated_at), args.league)
            print(f"  {health.describe()}")
            if health.suspicious:
                print("::warning title=League dark::"
                      f"{args.league}: {health.describe()}")
        except Exception as exc:  # noqa: BLE001 - a health line never blocks
            print(f"  league health unavailable ({exc})")
    else:
        # Both football leagues bet totals on points of disagreement (the
        # wager lab's cuts, docs/OUTPUT_AUDIT.md): the NFL at 4 either side,
        # college at 4 on the under alone.
        total_edge = resolve_total_edge(args, args.league)
        total_sides = resolve_total_sides(args, args.league)
        model_weight = resolve_model_weight(args.model_weight, args.league)
        weights_by_market = resolve_model_weights_by_market(
            args.model_weight_market, args.league)
        # The college sides are PAPER, not excluded (docs/STRATEGY_REVIEW.md
        # S2): staked at zero on the same evidence as before, but priced and
        # graded so the record can eventually re-test the verdicts that put
        # them there. No game market is excluded outright any more — an
        # excluded market produces no row, and a market with no record can
        # never earn its way back.
        for market, weight in sorted(weights_by_market.items()):
            if weight <= 0.0:
                print(f"{args.league.upper()} {market}s: off the board — the close would put "
                      f"no weight on the model's number (docs/OUTPUT_AUDIT.md §2.2); "
                      f"--model-weight-market {market}=W puts them back")
        if args.league == "ncaaf" and not args.ncaaf_spreads:
            print("NCAAF spreads: paper if they reach the board — priced and graded, "
                  "staked at zero (50.1% ATS flat, inverted at every disagreement "
                  "size — docs/OUTPUT_AUDIT.md); --ncaaf-spreads stakes them")
        if args.league == "ncaaf" and not args.ncaaf_moneylines:
            print("NCAAF moneylines: paper if they reach the board — priced and graded, "
                  "staked at zero (negative in every price bucket 2021–2025, and 60% of "
                  "the first live card's exposure — docs/STRATEGY_REVIEW.md §1.2); "
                  "--ncaaf-moneylines stakes them")
        paper_markets = resolve_paper_markets(args)
        if "__all__" in paper_markets:
            print(f"{args.league.upper()}: paper posture — every market priced and "
                  "graded, nothing staked (--no-paper to stake)")
        elif paper_markets:
            print("team totals: paper — priced and graded, staked at zero until "
                  "posted closes calibrate the gate (--no-team-totals-paper to stake)")
        # The exchange venues and the BettingPros books are papered for
        # different reasons and announce themselves separately (BP printed its
        # own line as the board was built); the config takes the union.
        exchange_paper = resolve_paper_venues(args)
        paper_venues = exchange_paper | bp_paper_venues
        if exchange_paper:
            why = ("--exchange-paper" if args.exchange_paper
                   else f"no ladder shape table fitted for {args.league}")
            print(f"exchanges: {', '.join(sorted(exchange_paper))} priced and graded on the "
                  f"board, staked at zero ({why})")
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
            model_weight_by_market=weights_by_market,
            min_edge_by_market=parse_market_edges(args.min_edge_market),
            exclude_markets=frozenset(),  # nothing is dropped; see paper_markets
            min_total_disagreement=total_edge,
            total_sides=total_sides,
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
        for market, weight in sorted(weights_by_market.items()):
            if weight > 0.0:
                print(f"  {market}: anchored at {weight:g} — the weight that maps the "
                      f"promoted rule's claim onto its walk-forward record")
        if total_edge > 0.0:
            sides = "/".join(sorted(total_sides))
            print(f"{args.league.upper()} totals filter: model must differ from the number "
                  f"by ≥ {total_edge:g} points; sides admitted: {sides}")
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
        paper_games = fcs_paper_games(args, projections)
        if paper_games:
            from dataclasses import replace as _replace

            print(f"FCS opponents: {len(paper_games)} game(s) priced and graded, staked at "
                  "zero (--ncaaf-fcs stakes them)")
            cfg = _replace(cfg, paper_games=paper_games)
        canonical = canonicalize_sides(lines, events)
        canonical = canonical[canonical["game_id"].astype(str).isin(projections)]
        games_min = events[["game_id", "kickoff"]].copy()
        games_min["game_id"] = games_min["game_id"].astype(str)
        game_log = build_slate(projections, canonical, games_min, cfg)
        # The rule tier each play earns (velocity.wagering.tiers): the
        # curated list's ranking, carried on the slate row into the graded
        # record so closing-line value accrues by tier.
        rule_tiers = rule_tiers_for(game_log, projections, args.league)
        frame = slate_to_frame(game_log, rule_tiers)

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
    prop_roster: pd.DataFrame | None = None
    watch_by_game: dict = {}
    # Which prop model runs is a property of the LEAGUE, not of which files
    # happened to be passed. It used to be the latter, and that was a live
    # trap: --fp-projections carries every league the collector banks, so
    # supplying it for MLB took the football path on baseball players, found
    # nothing, and — because this is an if/elif — silently skipped the pitcher-K
    # slate that MLB actually has. The only thing standing between us and that
    # was live-slate.yml gating the flag on `league = nfl`, a load-bearing
    # condition in a shell script with nothing saying so. The flag is gone
    # from the condition entirely now: NCAAF has no FantasyPros file to pass
    # and never will, so each league's source is resolved inside the slate
    # (_prop_projection_frame) and says why when it has none.
    if args.league in FOOTBALL_PROP_LEAGUES and projections and not events.empty:
        (props_frame, props_by_game, key_to_name, prop_lines_used,
         prop_roster) = _prop_slate(args, events, projections, now, generated_at)
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
            by_rule = resolve_publish_by_rule(args.publish_by_rule)
            published, audit = publish_slate(
                convictions, canonical, reference,
                min_conviction=args.publish_min_conviction,
                min_context=args.publish_min_context,
                max_plays=args.publish_max_plays,
                rule_tiers=rule_tiers if by_rule else None,
            )
            mode = ("by rule tier — a play posts only with a rule that has a "
                    "walk-forward record (docs/OUTPUT_AUDIT.md §3 #5)"
                    if by_rule else "by conviction")
            print(f"\npublish gate ({mode}): {gate_summary(audit)}")
            for c in published:
                bet = c.bet
                point = "" if bet.point is None else f" {bet.point:+g}"
                key = (str(bet.game_id), str(bet.market), str(bet.side))
                tier = rule_tiers.get(key)
                label = (f"rule tier {tier.tier} ({tier.record})" if tier is not None
                         else f"tier {c.tier}")
                print(f"  POST  {bet.market} {bet.side}{point} "
                      f"({bet.price:+.0f} {bet.book}) {label}")
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
        # What the weather did to each total, from the wrapper that did it.
        # The forecast is bought, priced into every projection and was then
        # discarded, so a windy total could not explain itself anywhere
        # downstream (docs/FOOTBALL_PAL.md).
        weather_rows = weather_frame(weather_model, events, known_teams, aliases)
        if not weather_rows.empty:
            weather_rows.assign(league=args.league, generated_at=generated_at).to_parquet(
                out_dir / f"weather_{args.league}_{stamp}.parquet", index=False
            )
            moved = int((weather_rows["total_points"] != 0).sum())
            print(f"wrote weather for {len(weather_rows)} game(s); "
                  f"{moved} total(s) adjusted")
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
                rule_tiers=rule_tiers,
                watch_by_game=watch_by_game, prop_roster=prop_roster,
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

        def _candidates(rows: list[dict], skip: set[int] | None = None) -> list:
            """The card's rows as portfolio candidates, minus the ones held.

            A row already on the books cannot be placed again, so giving it a
            share of the slate spends budget nothing can use and crowds out
            the rows that can. ``skip`` is empty until the ledger has said
            which rows it already holds.
            """
            skip = skip or set()
            return [
                BetCandidate(
                    key=str(i),
                    stake_fraction=float(row["stake"]) / args.bankroll,
                    group=str(row["game_id"]),
                    # One model assumption per class: a market for game bets,
                    # the prop market for props. Capped at half the slate.
                    market_class=(f"prop:{row['market']}" if row.get("kind") == "prop"
                                  else str(row["market"])),
                    venue=_venue_class(row),
                )
                for i, row in enumerate(rows)
                if i not in skip
            ]

        rows = card.to_dict("records")
        candidates = _candidates(rows)
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
            card["held"] = [h is not None for h in holds]
            # Re-cut the candidates now the ledger has named what it holds.
            candidates = _candidates(rows, {i for i, h in enumerate(holds) if h is not None})
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
            # EVERY open position counts against the cap, including the ones
            # whose contract is also on today's card. An earlier version
            # subtracted those, reasoning that a bet already on the books is
            # held rather than doubled — true of SIZING and false of RISK.
            # The money is on the table either way, so excluding it let the
            # cap be exceeded by exactly that amount, every run: measured over
            # 27 live runs the cap under-counted real exposure in 10 of them,
            # by up to 3.59 of a ~105 bankroll (3.4%, a seventh of the whole
            # 25% cap), and open exposure sat at 27–31% for eight days
            # straight against a cap that is supposed to bind at 25%.
            #
            # Held rows are instead dropped from the CANDIDATES below, which
            # is where "held, not doubled" actually belongs: they cannot be
            # placed again, so spending the slate's budget on them crowds out
            # bets that could be.
            on_card = ledger.open_bets()
            committed = float(on_card["stake"].sum())
            if should_halt(current, peak, config.max_drawdown_fraction):
                halted = (f"drawdown {state.drawdown:.0%} ≥ "
                          f"{config.max_drawdown_fraction:.0%} (bankroll {current:.2f} "
                          f"from a peak of {peak:.2f})")
            elif committed > 0:
                room = args.max_slate_fraction - committed / args.bankroll
                if room <= 0:
                    halted = (f"open exposure {committed:.2f} already fills the "
                              f"{args.max_slate_fraction:.0%} slate cap")
                else:
                    # Rebuilt for the smaller slate cap — and it has to carry
                    # the venue caps with it. Constructing a bare config here
                    # silently dropped the exchange cap on exactly the days
                    # money was already on the table, which is when it matters.
                    config = PortfolioConfig(max_portfolio_fraction=room,
                                             venue_caps=config.venue_caps)
                    print(f"open exposure {committed:.2f} leaves {room:.1%} of "
                          f"bankroll under the slate cap")
        if halted is not None:
            sized = {c.key: 0.0 for c in candidates}
            print(f"\n=== KILL-SWITCH — halted: {halted}; every stake zeroed ===")
            announced = True
        else:
            sized = size_portfolio(candidates, args.bankroll, config,
                                   current_bankroll=current, peak_bankroll=peak)
        card["stake_solo"] = card["stake"]
        # A held row is not in ``sized`` at all, and stakes at zero: the
        # position it names is already on the books at its own terms.
        card["stake"] = [round(sized.get(str(i), 0.0), 4) for i in range(len(card))]
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


def _starter_outs(args: argparse.Namespace) -> pd.DataFrame | None:
    """The injury frame the NFL starter map demotes an out QB1 with.

    ``outs_by_team`` keys on a ``team`` column of nflverse-ish codes. The
    FantasyPros frame already has one; the ESPN frame carries the code as
    ``team_abbreviation``, and its two NFL divergences (LAR/WSH) are the same
    two FantasyPros has, so the map's own fixups cover it after a rename.

    ESPN is preferred where both exist: it is the report the depth chart comes
    from, so an out flagged there and a depth chart read in the same run cannot
    disagree with each other about who exists.
    """
    if args.espn_injuries_file:
        frame = pd.read_parquet(args.espn_injuries_file)
        if "league" in frame.columns:
            frame = frame[frame["league"].astype(str) == "nfl"]
        if not frame.empty:
            return frame.rename(columns={"team_abbreviation": "team"})
    if args.injuries_file:
        return pd.read_parquet(args.injuries_file)
    return None


def _injury_report(args: argparse.Namespace, games: pd.DataFrame) -> pd.DataFrame | None:
    """The availability evidence for this league — ESPN, FantasyPros, or neither.

    Two sources, deliberately not merged into one blended view:

    * **ESPN** (``--espn-injuries-file``) is keyless and covers every league we
      price. It is the only reason an MLB, WNBA, NCAAF, NCAAB or NHL card has
      any availability evidence at all — before it those five abstained
      entirely, which on a baseball card meant no idea a listed starter was on
      the 60-day IL. Its teams arrive in ESPN's key space and are resolved onto
      the model's, scoped to this league because a team abbreviation is only
      unique inside one.
    * **FantasyPros** (``--injuries-file``) is NFL-only and already wired.

    Where both exist (the NFL), the frames are concatenated rather than
    reconciled. The consumer takes genuine outs and looks them up by team, so a
    player both sources call out appears twice and vetoes once; a player only
    one of them has is still seen. Reconciling two designations into a single
    truth is a judgement neither feed licenses us to make.
    """
    frames: list[pd.DataFrame] = []
    if args.espn_injuries_file:
        from velocity.ingest.espn import resolve_injury_teams

        known = sorted(
            set(games["home_team"].astype(str)) | set(games["away_team"].astype(str))
        )
        raw = pd.read_parquet(args.espn_injuries_file)
        espn, unresolved = resolve_injury_teams(raw, known, args.league)
        if espn.empty:
            print(f"\nintel: ESPN report carries no {args.league.upper()} rows "
                  "that resolve to the model's teams")
        else:
            print(f"\nintel: ESPN injuries loaded — {int(espn['is_out'].sum())} genuine "
                  f"outs across {espn['team'].nunique()} team(s)")
            frames.append(espn)
        if unresolved:
            # A team nobody can look up is invisible, and silently invisible is
            # how a whole league's report goes missing unnoticed.
            print(f"  {len(unresolved)} ESPN team(s) unresolved: "
                  f"{', '.join(unresolved[:6])}"
                  f"{' …' if len(unresolved) > 6 else ''}")
    if args.injuries_file:
        fp = pd.read_parquet(args.injuries_file)
        n_out = int(fp["is_out"].sum()) if "is_out" in fp.columns else 0
        print(f"intel: FantasyPros injuries loaded ({n_out} genuine outs)")
        frames.append(fp)
    if not frames:
        print("\nintel: no injuries snapshot (--espn-injuries-file / --injuries-file) "
              "— availability signals abstain")
        return None
    return pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]


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
        injuries = _injury_report(args, games)
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


def _resolve_prop_lines_path(
    args: argparse.Namespace, now: pd.Timestamp
) -> Path | None:
    """The banked prop board to price, or ``None`` to fall through.

    An explicit ``--prop-lines-file`` always wins. Otherwise ``--prop-lines-dir``
    is searched under the same freshness bar the game board uses — a prop line
    that moved six hours ago is not a price, and pricing against it is worse
    than not pricing at all.
    """
    if args.prop_lines_file:
        return Path(args.prop_lines_file)
    root = getattr(args, "prop_lines_dir", None)
    if not root:
        return None
    path, stamp = newest_banked_props(
        Path(root), args.league, now, args.board_max_age_min
    )
    if path is not None and stamp is not None:
        age = (now - stamp).total_seconds() / 60.0
        print(f"prop board: reusing banked {path} (captured "
              f"{stamp:%Y-%m-%d %H:%M}Z, {age:.0f}min old; no credits spent)")
        return path
    if stamp is not None:
        age = (now - stamp).total_seconds() / 60.0
        print(f"prop board: newest banked {args.league} board is {age:.0f}min "
              f"old (bar {args.board_max_age_min:g}min) — too stale to price")
    return None


def _prop_team_resolver(
    args: argparse.Namespace, events: pd.DataFrame, known: set[str]
) -> Callable[[str], str | None]:
    """Board team name → the projection's team key, by the league's own rule.

    The NFL path is the alias table. College cannot use it: the Odds API
    writes "Georgia Bulldogs" and the bank keys by school ("Georgia"), which
    is exactly what :func:`nickname_aliases` was built for — longest
    prefix-matching school wins, so "Georgia Southern Eagles" cannot land on
    Georgia. A name that matches nothing resolves to ``None`` and the caller
    skips that game rather than guessing a team.
    """
    from velocity.wagering.live import NFL_TEAM_ALIASES, nickname_aliases, resolve_team

    if args.league == "ncaaf":
        board = {str(n) for column in ("home_team", "away_team")
                 for n in events[column].astype(str)} if not events.empty else set()
        table = nickname_aliases(board, known)
        return lambda name: table.get(str(name))

    codes = sorted(set(NFL_TEAM_ALIASES.values()))
    return lambda name: resolve_team(str(name), codes, NFL_TEAM_ALIASES)


def _prop_projection_frame(args: argparse.Namespace) -> pd.DataFrame | None:
    """The per-player prop projection for this league, in FantasyPros' shape.

    NFL reads the banked FantasyPros snapshot. NCAAF cannot: the FantasyPros
    public API has no college endpoint at all, which is why the league filter
    came back empty and the prop slate skipped every run while the collector
    kept buying the board (audit finding 6). It reads the banked college
    player-games instead, projected over the same six-game recency window the
    college DFS board uses — nothing downstream can tell the difference,
    because both emit the long ``(player, stat, value)`` frame the sim eats.

    ``None`` with a printed reason when the league has no usable source.
    """
    if args.league == "ncaaf":
        from velocity.models.props_ncaaf import player_prop_means

        path = Path(args.ncaaf_player_games)
        if not path.exists():
            print(f"prop slate skipped: no college player bank at {path}")
            return None
        fp = player_prop_means(pd.read_parquet(path))
        if fp.empty:
            print(f"prop slate skipped: {path} projects no active players")
            return None
        print(f"ncaaf props: {fp['player_id'].nunique()} active players over "
              f"{fp['team'].nunique()} teams, projected from {path}")
        return fp

    if not args.fp_projections:
        print("prop slate skipped: --fp-projections not supplied")
        return None
    fp = pd.read_parquet(args.fp_projections)
    if "league" in fp.columns:
        fp = fp[fp["league"].astype(str) == args.league]
    if fp.empty:
        print(f"prop slate skipped: no {args.league} rows in {args.fp_projections}")
        return None
    from velocity.dfs.pipeline import is_season_long

    if is_season_long(fp):
        # Season totals would price a 4,800-yard passing prop as a weekly
        # mean — refuse rather than misprice (same guard as the DFS lineup).
        print("prop slate skipped: FP snapshot is season-long (week 0); "
              "weekly props can't be priced from season totals")
        return None
    return fp


def _prop_slate(
    args: argparse.Namespace,
    events: pd.DataFrame,
    projections: dict,
    now: datetime,
    generated_at: pd.Timestamp,
) -> tuple[pd.DataFrame | None, dict, dict[str, str], pd.DataFrame | None,
           pd.DataFrame | None]:
    """Price the prop board off the FantasyPros-driven correlated sim.

    Returns ``(frame, props_by_game, key_to_name, prop_lines, roster)`` — the
    persisted prop-slate frame (it carries the raw ``p_model``/``p_fair`` per
    bet, which is exactly what the shrink-sweep backtest replays) plus the
    per-game sims and the name index, which the social cards' watch strip
    reuses. ``roster`` is the projection frame reduced to identity (key,
    name, position, team): the prop sim is keyed by player key alone and
    carries no position, so the matchup card cannot fill a QB/RB/WR shape
    without it. Empty results when the board or projections don't
    materialize.
    """
    try:
        from velocity.models.props_football import game_props, name_index_from_fp
        from velocity.report.matchup import roster_from_projections
        from velocity.wagering.props_slate import build_prop_slate, prop_slate_to_frame

        fp = _prop_projection_frame(args)
        if fp is None:
            return None, {}, {}, None, None

        lines_path = _resolve_prop_lines_path(args, generated_at)
        if lines_path is not None:
            prop_lines = pd.read_parquet(lines_path)
        elif args.snapshot_file:
            print("prop slate skipped: offline run needs --prop-lines-file")
            return None, {}, {}, None, None
        elif args.league == "ncaaf":
            # Never a live pull. The collector already buys this exact board
            # twice a day (audit finding 6 is that we bought it and never read
            # it), so pricing it costs nothing more — but pulling it AGAIN
            # here would double the spend to fix a finding about wasted spend.
            print("prop slate skipped: no fresh banked ncaaf prop board; "
                  "not pulling live (the collector's board is the one to price)")
            return None, {}, {}, None, None
        else:
            prop_lines = _odds_client().player_props(args.league)
        if prop_lines.empty:
            print("prop slate: no prop lines on the board")
            return None, {}, {}, None, None

        fp_teams = set(fp["team"].astype(str))
        to_team = _prop_team_resolver(args, events, fp_teams)
        props_by_game: dict[str, object] = {}
        for event in events.to_dict("records"):
            gid = str(event["game_id"])
            if gid not in projections:
                continue
            home = to_team(str(event["home_team"]))
            away = to_team(str(event["away_team"]))
            if home not in fp_teams or away not in fp_teams:
                continue  # a team the bank doesn't cover is skipped, never guessed
            props_by_game[gid] = game_props(fp, home, away, make_rng(),
                                            _prop_config(args))
        if not props_by_game:
            print("prop slate: no games matched the projection's team coverage")
            return None, {}, {}, None, None

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
        return (frame, props_by_game, key_to_name, prop_lines,
                roster_from_projections(fp))
    except Exception as exc:  # noqa: BLE001 - the prop slate never breaks the game slate
        print(f"prop slate skipped: {exc}")
        return None, {}, {}, None, None


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
            prop_lines = _odds_client().player_props(
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
    """The prop sim's dispersion for this league — college's is not the NFL's.

    Re-fitting `scripts/fit_prop_dispersion.py` on the college bank puts the
    team volume swing at roughly twice the NFL's (pass σ 0.242 against 0.118),
    which is what college blowouts and tempo gaps should do. Running the NFL
    config on a college board would simulate distributions about half as wide
    as they are, and a too-narrow distribution does not fail loudly — it
    manufactures edge, on every market at once.
    """
    if args.league == "ncaaf":
        from velocity.models.props_ncaaf import ncaaf_prop_config

        return ncaaf_prop_config(n_sims=args.n_sims)
    from velocity.models.props_football import FootballPropConfig

    return FootballPropConfig(n_sims=args.n_sims)


def _slate_week_label(games: pd.DataFrame | None) -> str:
    """``"Week 14"`` for the slate about to be played, or "" when unknown.

    Preferred source is the schedule itself: the modal week among the newest
    season's games with no final yet is the week being priced, whatever the
    byes did. The committed frames carry only COMPLETED games, though (checked:
    datasets/nfl/games.parquet holds week 1 of 2026 and nothing forward), so
    the fallback is the newest completed week plus one. That reads the max
    rather than counting distinct weeks, so a bye week — which other clubs
    still play — cannot shift it.

    It can overshoot by one in the gap after a season's last week, which is a
    wrong masthead on a card for a game that does not exist. Nothing else on
    the card depends on it, and "" is the answer whenever the frame cannot
    say.
    """
    if games is None or games.empty or "week" not in games.columns:
        return ""
    try:
        season = int(games["season"].max())
        current = games[games["season"] == season]
        unplayed = current[current["home_score"].isna() & current["away_score"].isna()]
        if not unplayed.empty:
            return f"Week {int(unplayed['week'].mode().iloc[0])}"
        played = current[current["home_score"].notna()]
        if played.empty:
            return ""
        return f"Week {int(played['week'].max()) + 1}"
    except Exception:  # noqa: BLE001 - a masthead label, never worth raising
        return ""


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
    rule_tiers: dict | None = None,
    watch_by_game: dict | None = None,
    prop_roster: pd.DataFrame | None = None,
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
        code_to_mascot: dict[str, str] = {}
        if args.league == "ncaaf":
            aliases, team_colors, code_to_team, code_to_mascot = _ncaaf_identity(
                events, asset_dir)
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
        # The slate's staked plays, each chipped with the rule tier it earned
        # (velocity.wagering.tiers — the letter and the walk-forward record
        # the publish gate ranks on, not the intel conviction tier the
        # backtest measured as a null) — computed BEFORE the card render so
        # the hero card's matrix wears the PLAY badges, then reused for the
        # deep dive's verdict band + WHY. The intel layer keeps one line on
        # the card: its veto, when it fired on a game's play.
        from velocity.report.deepdive import plays_from_bets

        why_signals: dict[str, list[str]] = {}
        for c in convictions or ():
            bet = c.bet
            if bet.player is not None or not c.vetoed:
                continue
            gid = str(bet.game_id)
            for signal in c.signals:
                if signal.veto and len(why_signals.get(gid, [])) < 2:
                    why_signals.setdefault(gid, []).append(f"VETO — {signal.rationale}")
        plays_by_game = (
            plays_from_bets(game_log.bets, tiers=rule_tiers or {})  # type: ignore[attr-defined]
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
            league=args.league,
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

        # Form and EPA inputs, loaded once and shared by the deep dive and the
        # standalone matchup card. Hoisted out of the dive's own block so one
        # surface failing never silently costs the other its inputs.
        games = plays = None
        try:
            from velocity.ingest.local import load_games, load_plays

            games = load_games(_find_games(Path(args.data)), league=args.league)
            # Both football leagues, not just the NFL. College play-by-play has
            # been committed all along; what kept it out was epa_form matching
            # only nflverse's pass/run labels against CFBD's outcome labels
            # ("Pass Reception", "Rushing Touchdown"), which returned an empty
            # table rather than raising. epa_form reads both now. Still gated
            # to football: EPA per play is not a baseball or hockey measure.
            if args.league in FOOTBALL_PROP_LEAGUES:
                plays_path = _find_plays(Path(args.data))
                plays = load_plays(plays_path) if plays_path is not None else None
        except Exception as exc:  # noqa: BLE001 - both consumers degrade without it
            print(f"form inputs unavailable: {exc}")

        # Deep Dive companions — the analytical page behind each matchup card
        # (form/EPA table, margin vs the market, extended props). Best-effort
        # like everything else on this surface.
        try:
            from velocity.report.deepdive import build_deep_dives
            from velocity.report.deepdive_png import render_deep_dives

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

        # The matchup card — the 4:5 portrait graphic for @MatchUpLabs. Its own
        # artifact, NOT folded into the sheet: the sheet is the analyst's page
        # and this is the one a reader forms a view from on its own. Logos are
        # club marks only; the college path passes no ESPN ids, so NCAAF cards
        # render the school abbreviation in its brand color and no mark at all
        # (the licensing posture report/assets.py sets, kept here).
        #
        # Football only, and deliberately so: every label on it is a football
        # noun (PASS OFFENSE, RUSH DEFENSE, a QB/RB/WR row shape). Rendered for
        # baseball it would not fail — it would print those headings over empty
        # panels, which is worse than not posting a card.
        try:
            if args.league not in FOOTBALL_PROP_LEAGUES:
                raise RuntimeError(f"{args.league} is not a football league")
            from velocity.report.matchup import build_matchup_cards
            from velocity.report.matchup_png import render_matchup_cards

            matchups = build_matchup_cards(
                cards, projections, games, plays,
                week_label=_slate_week_label(games),
                league=args.league,
                props_by_game=props_by_game, roster=prop_roster,
                team_names=code_to_team, mascots=code_to_mascot,
                # The stamp is left to the builder's default (now, UTC), which
                # is what the line on the card claims: when this graphic was
                # generated, not when the slate run began.
            )
            made = render_matchup_cards(matchups, Path(args.out), stamp,
                                        asset_dir=asset_dir, league=args.league)
            print(f"wrote {len(made)} matchup card(s) to {args.out}")
        except Exception as exc:  # noqa: BLE001 - never blocks the card run
            print(f"matchup cards skipped: {exc}")

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
) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    """Provider-name → school-abbreviation aliases, abbreviation → color map,
    abbreviation → school (the datasets' team key, for the deep dive), and
    abbreviation → mascot (the matchup card's nickname line).

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
    code_to_mascot: dict[str, str] = {}
    meta = ncaaf_team_index(os.environ.get("CFBD_API_KEY"), asset_dir)
    if meta:
        to_school = nickname_aliases(sorted(provider_names), sorted(meta))
        for provider, school in to_school.items():
            aliases[provider] = meta[school].abbreviation
        team_colors = {
            m.abbreviation: m.color for m in meta.values() if m.color
        }
        code_to_team = {m.abbreviation: school for school, m in meta.items()}
        # The mascot cannot be recovered by splitting a display name -- college
        # nicknames are routinely two words -- so it rides along from CFBD,
        # which states school and mascot separately.
        code_to_mascot = {m.abbreviation: m.mascot for m in meta.values() if m.mascot}
    return aliases, team_colors, code_to_team, code_to_mascot


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

    # Last, and outside every per-surface try: a call that failed downstream
    # still spent its credit, and those are exactly the ones worth banking.
    # Never let the accounting break the slate it is accounting for.
    if args.out:
        try:
            bank_odds_usage(
                Path(args.out),
                f"{args.league}_{now.strftime('%Y%m%dT%H%M%SZ')}",
            )
        except Exception as exc:  # noqa: BLE001 - the ledger is never the point
            print(f"credit ledger skipped: {exc}")


if __name__ == "__main__":
    main()
