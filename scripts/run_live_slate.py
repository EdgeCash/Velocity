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
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.features.scores import fit_scores_ratings
from velocity.ingest.local import load_games
from velocity.ingest.theoddsapi import extract_events, normalize_odds_events
from velocity.models.game_nfl import GameProjection
from velocity.models.game_scores import ScoresGameModel, ScoresModelConfig
from velocity.models.simulate import SimConfig
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


def _build_projection(
    args: argparse.Namespace,
) -> tuple[Callable[[str, str], GameProjection], list[str]]:
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
        nfl_model = NFLGameModel(ratings, NFLModelConfig(sim=SimConfig(n_sims=args.n_sims)))  # type: ignore[arg-type]
        print(f"NFL ratings: {kind} fit on {len(plays)} plays "
              f"(seasons {cutoff}+, half-life {DEFAULT_RECENCY_HALF_LIFE:g} wks)")

        # Rest spots (docs/MODEL_LAB.md Round 4): bye +1.0 / short week −1.0 on
        # top of the fit — small, consistent across every tested grid.
        from velocity.backtest.lab import RestAdjustedModel

        schedule = load_games(_find_games(folder), league="nfl")
        rest_model = RestAdjustedModel(nfl_model, schedule)

        # Wind on totals (Round 5 constants, live forecast): best-effort — a
        # failed forecast fetch just leaves totals unadjusted.
        model: object = rest_model
        try:
            if args.snapshot_file:  # offline runs (tests/CI) skip the network
                raise RuntimeError("offline snapshot run")
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
            home: str, away: str, kickoff: object = None
        ) -> GameProjection:
            return model.project(  # type: ignore[attr-defined,return-value]
                home, away, rng=make_rng(), kickoff=kickoff
            )

        return project_epa, list(ratings.teams)

    games = load_games(_find_games(folder), league=args.league)
    # Per-league outcome-noise calibration. Football's constants are the
    # lab-validated ones; MLB (runs) and WNBA (points) use the leagues'
    # historical margin/total sigmas — content-surface defaults, honest but
    # not yet lab-tuned (their datasets carry no closing lines to tune on).
    sims = {
        "ncaaf": SimConfig(sd_margin=17.0, sd_total=16.0, n_sims=args.n_sims),
        "mlb": SimConfig(sd_margin=3.2, sd_total=4.6, n_sims=args.n_sims),
        "wnba": SimConfig(sd_margin=12.5, sd_total=15.0, n_sims=args.n_sims),
    }
    sim = sims.get(args.league, SimConfig(n_sims=args.n_sims))
    # NCAAF: λ=10 promoted by the college lab; MLB: λ=100 promoted by the
    # summer lab (docs/MODEL_LAB.md MLB Round 1 — heavy shrinkage wins in a
    # league whose true team spread is small). WNBA: recency half-life 8
    # week-buckets promoted (WNBA Round 1 — an interior optimum, 12/16/24
    # all worse). The NFL scores path is only a no-plays fallback and keeps
    # the default.
    ridge = {"ncaaf": 10.0, "mlb": 100.0, "wnba": 10.0}.get(args.league, 25.0)
    weights = None
    if args.league == "wnba":
        from velocity.features.scores import scores_recency_weights

        weights = scores_recency_weights(games, 8.0)
    scores_model = ScoresGameModel(
        fit_scores_ratings(games, ridge_lambda=ridge, weights=weights),
        ScoresModelConfig(sim=sim),
    )
    model: object = scores_model
    kind = f"scores fit (λ={ridge:g})" + (", recency-8" if weights is not None else "")

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
        epa_model = NFLGameModel(
            fit_ratings(cells, ridge_lambda=50.0, weights=cells["n"].astype(float)),
            NFLModelConfig(base_points=28.5, plays_per_game=65.0,
                           hfa_points=2.5, sim=sim),
        )
        model = BlendedGameModel(epa_model, scores_model, 0.5, sim)
        kind = f"EPA×scores blend (λ50/λ{ridge:g}, w=0.5) on {len(plays)} plays"

    print(f"{args.league.upper()} ratings: {kind}, {len(games)} games")

    def project(home: str, away: str) -> GameProjection:
        return model.project(home, away, rng=make_rng())  # type: ignore[attr-defined,return-value]

    return project, list(scores_model.ratings.teams)


def main() -> None:
    parser = argparse.ArgumentParser(description="Live slate of staked recommendations")
    parser.add_argument("--league", choices=["nfl", "ncaaf", "mlb", "wnba"],
                        required=True)
    parser.add_argument("--data", help="folder with a games file to fit the model")
    parser.add_argument("--snapshot-file", help="saved Odds API /odds JSON (offline mode)")
    parser.add_argument("--n-sims", type=int, default=10_000)
    parser.add_argument("--min-edge", type=float, default=0.02)
    parser.add_argument("--bankroll", type=float, default=100.0)
    # The August board carries the whole season's games at stale opening
    # numbers (the first live run priced 272 NFL events and "staked" 20x the
    # bankroll). A slate is this week's games: only events kicking off inside
    # the window are priced, staked, and carded.
    parser.add_argument("--max-days", type=float, default=6.0,
                        help="only price games kicking off within this many days (0 = all)")
    # NCAAF selectivity, in POINTS of total disagreement — the edge exactly as
    # backtested (docs/BACKTEST_NCAAF.md): flat totals 51.6%, but 52.8% when the
    # model differs from the number by ≥4 points (5,477 bets, positive in 7 of 10
    # seasons) and 53.4% at ≥6. Applies to full-game totals only; NCAAF spreads
    # showed no edge at any threshold, so nothing is bet there on this cut.
    parser.add_argument("--ncaaf-total-edge", type=float, default=4.0,
                        help="NCAAF: min points of total disagreement to bet (0 = off)")
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
    parser.add_argument("--out", help="folder to persist the slate parquet (private, not git)")
    args = parser.parse_args()

    now = datetime.now(UTC)
    generated_at = pd.Timestamp(now).tz_localize(None)

    if args.out:
        Path(args.out).mkdir(parents=True, exist_ok=True)

    project, known_teams = _build_projection(args)

    payload = _load_snapshot(args)
    lines = normalize_odds_events(payload)
    events = extract_events(payload)
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
        cfg = SlateConfig(
            exclude_closing=False, min_edge=args.min_edge, starting_bankroll=args.bankroll,
            min_total_disagreement=total_edge,
        )
        if total_edge > 0.0:
            print(f"NCAAF totals filter: model must differ from the number by "
                  f"≥ {total_edge:g} points")
        # Project once, then price off those projections (reused for the workbook).
        # NCAAF: the provider names carry nicknames ("Georgia Bulldogs") while the
        # CFBD-fit model keys by school ("Georgia") — bridge by prefix match.
        aliases = None
        if args.league == "ncaaf":
            from velocity.wagering.live import nickname_aliases

            provider_names = set(events["home_team"]) | set(events["away_team"])
            aliases = nickname_aliases(provider_names, known_teams)
        projections, unresolved = project_board(events, project, known_teams, aliases)
        canonical = canonicalize_sides(lines, events)
        canonical = canonical[canonical["game_id"].astype(str).isin(projections)]
        games_min = events[["game_id", "kickoff"]].copy()
        games_min["game_id"] = games_min["game_id"].astype(str)
        game_log = build_slate(projections, canonical, games_min, cfg)
        frame = slate_to_frame(game_log)

        if frame.empty:
            print("no bets cleared the edge threshold.")
        else:
            shown = frame.assign(stake_pct=(frame["stake"] / args.bankroll * 100).round(2))
            with pd.option_context("display.width", 160, "display.max_columns", None):
                print(f"\n{len(shown)} recommended bets (stake as % of {args.bankroll:.0f}):")
                print(shown.to_string(index=False))
            print(f"\ntotal staked: {frame['stake'].sum():.2f}")

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
    if args.fp_projections and projections and not events.empty:
        props_frame, props_by_game, key_to_name, prop_lines_used = _prop_slate(
            args, events, projections, now, generated_at
        )

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

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        persisted = frame.assign(league=args.league, generated_at=generated_at)
        parquet = out_dir / f"slate_{args.league}_{stamp}.parquet"
        persisted.to_parquet(parquet, index=False)
        print(f"\nwrote {len(persisted)} slate rows to {parquet}")
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
            )


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
                exclude_closing=False, min_edge=args.min_edge,
                starting_bankroll=args.bankroll,
                prob_shrink=args.prop_shrink,
                exclude_markets=frozenset(
                    m.strip() for m in args.exclude_props.split(",") if m.strip()
                ),
            ),
        )
        frame = prop_slate_to_frame(log)
        print(f"\n=== {args.league.upper()} props — {len(prop_lines)} lines, "
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
                dest, index=False
            )
            print(f"wrote {len(frame)} prop rows to {dest}")
        return frame, props_by_game, key_to_name, prop_lines
    except Exception as exc:  # noqa: BLE001 - the prop slate never breaks the game slate
        print(f"prop slate skipped: {exc}")
        return None, {}, {}, None


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
        elif args.league in ("mlb", "wnba"):
            # Summer leagues: abbreviation + brand color blocks, no marks —
            # the NCAAF licensing posture (velocity/report/league_identity).
            from velocity.report.league_identity import league_identity

            provider_names = sorted(
                set(events["home_team"].astype(str))
                | set(events["away_team"].astype(str))
            )
            aliases, team_colors, code_to_team = league_identity(
                args.league, provider_names
            )
        # max_watch=6: the hero card renders its top three; the deep dive
        # carries the full six.
        cards = build_social_cards(
            projections, events,
            props_by_game=props_by_game, key_to_name=key_to_name,
            prop_lines=prop_lines, record_line=record_line, lines=canonical,
            aliases=aliases, team_colors=team_colors, max_watch=6,
        )
        paths = render_cards(cards, Path(args.out), stamp,
                             asset_dir=asset_dir, league=args.league,
                             number_slides=args.carousel)
        print(f"wrote {len(paths)} social card(s) to {args.out}")

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
            dives = build_deep_dives(cards, projections, games, plays,
                                     team_names=code_to_team)
            dive_paths = render_deep_dives(dives, Path(args.out), stamp,
                                           asset_dir=asset_dir, league=args.league)
            print(f"wrote {len(dive_paths)} deep dive card(s) to {args.out}")
        except Exception as exc:  # noqa: BLE001 - the companion never blocks the card run
            print(f"deep dives skipped: {exc}")
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
