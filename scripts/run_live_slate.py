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


def _build_projection(
    args: argparse.Namespace,
) -> tuple[Callable[[str, str], GameProjection], list[str]]:
    """Fit the scores ratings from the committed games file → ``(project, teams)``."""
    if not args.data:
        raise SystemExit(f"--data is required for {args.league} (a folder with a games file)")
    games = load_games(_find_games(Path(args.data)), league=args.league)
    sim = (
        SimConfig(sd_margin=17.0, sd_total=16.0, n_sims=args.n_sims)
        if args.league == "ncaaf"
        else SimConfig(n_sims=args.n_sims)
    )
    model = ScoresGameModel(fit_scores_ratings(games), ScoresModelConfig(sim=sim))

    def project(home: str, away: str) -> GameProjection:
        return model.project(home, away, rng=make_rng())

    return project, list(model.ratings.teams)


def main() -> None:
    parser = argparse.ArgumentParser(description="Live slate of staked recommendations")
    parser.add_argument("--league", choices=["nfl", "ncaaf"], required=True)
    parser.add_argument("--data", help="folder with a games file to fit the model")
    parser.add_argument("--snapshot-file", help="saved Odds API /odds JSON (offline mode)")
    parser.add_argument("--n-sims", type=int, default=10_000)
    parser.add_argument("--min-edge", type=float, default=0.02)
    parser.add_argument("--bankroll", type=float, default=100.0)
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
    print(f"=== Live slate: {args.league.upper()} — {len(events)} games on the board ===")

    frame = pd.DataFrame()
    projections: dict = {}
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
        projections, unresolved = project_board(events, project, known_teams)
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
    if args.fp_projections and projections and not events.empty:
        props_frame = _prop_slate(args, events, projections, now, generated_at)

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
        _write_workbook(out_dir, stamp, args, events, projections, frame, props_frame,
                        generated_at)


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
) -> pd.DataFrame | None:
    """Price the prop board off the FantasyPros-driven correlated sim.

    Returns the persisted prop-slate frame (it carries the raw ``p_model`` and
    ``p_fair`` per bet, which is exactly what the shrink-sweep backtest
    replays), or ``None`` when the board or projections don't materialize.
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
            return None

        if args.prop_lines_file:
            prop_lines = pd.read_parquet(args.prop_lines_file)
        elif args.snapshot_file:
            print("prop slate skipped: offline run needs --prop-lines-file")
            return None
        else:
            from velocity.ingest.theoddsapi import TheOddsAPIClient

            prop_lines = TheOddsAPIClient.from_env().player_props(args.league)
        if prop_lines.empty:
            print("prop slate: no prop lines on the board")
            return None

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
            return None

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
        return frame
    except Exception as exc:  # noqa: BLE001 - the prop slate never breaks the game slate
        print(f"prop slate skipped: {exc}")
        return None


def _prop_config(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    from velocity.models.props_football import FootballPropConfig

    return FootballPropConfig(n_sims=args.n_sims)


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
