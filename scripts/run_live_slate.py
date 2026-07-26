"""Live slate runner — today's board → staked bet recommendations.

Ties the whole system together: fit the projection model on committed history,
pull the current board from The Odds API (or a saved snapshot for offline runs),
and run the identical wagering engine the backtest used to produce a staked slate
of recommended bets, plus any games it couldn't resolve to the model's teams.

    # offline, from a saved Odds API /odds payload:
    python scripts/run_live_slate.py --league nfl --data datasets/nfl \
        --snapshot-file snap.json

    # MLB needs no committed dataset (the model is simulated from lineups):
    python scripts/run_live_slate.py --league mlb --snapshot-file snap.json

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
from velocity.models.game_mlb import league_average_model
from velocity.models.game_nfl import GameProjection
from velocity.models.game_scores import ScoresGameModel, ScoresModelConfig
from velocity.models.simulate import SimConfig
from velocity.models.simulate_baseball import (
    DEFAULT_HFA,
    DEFAULT_TTO_PENALTY,
    BaseballSimConfig,
)
from velocity.report.slate_xlsx import (
    export_slate_workbook,
    plays_display,
    projections_display,
    props_display,
)
from velocity.util.seed import make_rng
from velocity.wagering.live import (
    MLB_TEAM_ALIASES,
    canonicalize_sides,
    project_board,
    slate_to_frame,
)
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


def _mlb_model(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    """The MLB model + player-name index: StatsAPI when live, else the baseline.

    An offline run (``--snapshot-file``) uses the league-average baseline — no
    network. A live run builds today's model from StatsAPI (season stats + probable
    lineups), falling back to the baseline if that fetch fails so the slate still
    runs. Returns ``(model, name_to_id | None)`` — one build serves the game
    slate, the prop slate, and parlay pricing off the same simulation.
    """
    codes = sorted(set(MLB_TEAM_ALIASES.values()))
    config = BaseballSimConfig(
        n_sims=args.n_sims, starter_outs=18, hfa=DEFAULT_HFA, tto_penalty=DEFAULT_TTO_PENALTY
    )
    if args.snapshot_file:
        from velocity.report.park_factors import run_environment_maps

        hr_factors, run_env_tilts = run_environment_maps()  # park-static (offline: no weather)
        return league_average_model(
            codes, n_sims=args.n_sims,
            park_hr_factors=hr_factors, run_env_tilts=run_env_tilts,
        ), None
    try:
        from velocity.models.mlb_build import build_live_mlb

        now = datetime.now(UTC)
        model, names = build_live_mlb(now.strftime("%Y-%m-%d"), now.year, config=config)
        print(f"built MLB model from StatsAPI lineups ({len(model.known_teams)} clubs)")
        return model, names
    except Exception as exc:  # noqa: BLE001 - any live-data failure degrades gracefully
        print(f"warning: live lineup build failed ({exc}); using league-average baseline")
        return league_average_model(codes, n_sims=args.n_sims), None


def _build_projection(
    args: argparse.Namespace,
) -> tuple[
    Callable[[str, str], GameProjection],
    list[str],
    dict[str, str] | None,
    object | None,
    dict[str, str] | None,
]:
    """Return ``(project, known_teams, aliases, mlb_model, mlb_names)`` per league.

    Football fits the scores ratings from a committed games file; MLB simulates
    from lineups. The MLB model is returned so the runner can fold today's weather
    into its per-home run environment before pricing; football returns ``None``.
    MLB's ``project`` is the segment-aware :meth:`MLBGameModel.project`, so the
    slate prices the F5 and first-inning (NRFI/YRFI) markets off the same sim.
    """
    if args.league == "mlb":
        model, names = _mlb_model(args)
        return model.project, model.known_teams, MLB_TEAM_ALIASES, model, names

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

    return project, list(model.ratings.teams), None, None, None


def main() -> None:
    parser = argparse.ArgumentParser(description="Live slate of staked recommendations")
    parser.add_argument("--league", choices=["nfl", "ncaaf", "mlb"], required=True)
    parser.add_argument("--data", help="folder with a games file to fit the model (nfl/ncaaf)")
    parser.add_argument("--snapshot-file", help="saved Odds API /odds JSON (offline mode)")
    parser.add_argument("--n-sims", type=int, default=10_000)
    parser.add_argument("--min-edge", type=float, default=0.02)
    parser.add_argument("--bankroll", type=float, default=100.0)
    # Confidence calibration for the MLB game markets. The walk-forward shrink sweep
    # (#23) found the raw model over-confident — CLV positive but ROI −3.7% from
    # over-staking; shrinking the model probability toward 0.5 pulled ROI to
    # break-even and CLV to +9%. 0.35 sits between the break-even knee (~0.30) and a
    # conservative hedge (0.40). In-sample; re-tune once a second season is banked.
    parser.add_argument("--mlb-shrink", type=float, default=0.35,
                        help="MLB confidence shrink toward 0.5 (1.0 = raw model)")
    # Prop calibration, from the per-market prop shrink sweep (#23). Props are more
    # over-confident than game markets: aggregate ROI/ECE optimize near 0.5, and the
    # per-market read is decisive — pitcher_strikeouts/outs and hits carry CLV at ~0.5,
    # but total_bases loses at *every* shrink (the sim can't call the extra-base
    # distribution game-to-game), so it is excluded rather than shrunk. In-sample;
    # re-tune once a second season is banked.
    parser.add_argument("--mlb-prop-shrink", type=float, default=0.5,
                        help="MLB prop confidence shrink toward 0.5 (1.0 = raw model)")
    parser.add_argument("--mlb-exclude-props", default="total_bases",
                        help="comma-separated prop markets to skip (no-edge); '' bets all")
    # Derivative board (F5 moneyline/run line/total + NRFI/YRFI). Live MLB fetches it
    # per-event alongside the props (one credit-efficient pass); offline runs can
    # supply banked per-event payloads.
    parser.add_argument("--derivatives-file",
                        help="saved per-event odds payloads JSON (offline F5/NRFI board)")
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

    project, known_teams, aliases, mlb_model, mlb_names = _build_projection(args)

    payload = _load_snapshot(args)
    lines = normalize_odds_events(payload)
    events = extract_events(payload)
    print(f"=== Live slate: {args.league.upper()} — {len(events)} games on the board ===")

    # The MLB soft board: player props + derivative game markets (F5 moneyline/run
    # line/total, NRFI/YRFI). Both ride the per-event endpoint, so one live pass
    # serves both; an offline run can supply banked per-event payloads instead.
    prop_lines: pd.DataFrame | None = None
    if args.league == "mlb" and not events.empty:
        derivatives = pd.DataFrame()
        if args.derivatives_file:
            derivatives = _load_derivative_lines(args.derivatives_file)
            print(f"derivative board (file): {len(derivatives)} lines")
        elif not args.snapshot_file:
            try:
                from velocity.ingest.theoddsapi import TheOddsAPIClient

                prop_lines, derivatives = TheOddsAPIClient.from_env().soft_board("mlb")
                print(f"soft board: {len(prop_lines)} prop lines, "
                      f"{len(derivatives)} derivative lines")
            except Exception as exc:  # noqa: BLE001 - soft board is additive, never fatal
                print(f"soft board fetch skipped: {exc}")
        if not derivatives.empty:
            lines = pd.concat([lines, derivatives], ignore_index=True)

    # Fold today's first-pitch weather into the MLB model's run environment (temp →
    # HR, roof gate), so projections — not just the cards — are weather-aware. Live
    # only; the offline snapshot keeps the park-static environment.
    weather_by_game: dict[str, object] = {}
    if mlb_model is not None and not args.snapshot_file and not events.empty:
        weather_by_game = _apply_weather_run_env(mlb_model, events)

    frame = pd.DataFrame()
    projections: dict = {}
    canonical = pd.DataFrame()
    unresolved: list[dict[str, str]] = []
    game_log = None
    if events.empty:
        print("no games on the board (off-season or empty snapshot)")
    else:
        # Calibrated confidence for MLB (see --mlb-shrink); football stays raw (1.0),
        # its own calibration is untuned here.
        shrink = args.mlb_shrink if args.league == "mlb" else 1.0
        cfg = SlateConfig(
            exclude_closing=False, min_edge=args.min_edge, starting_bankroll=args.bankroll,
            prob_shrink=shrink,
        )
        # Project once, then price off those projections (reused for the workbook).
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

    # MLB player-prop slate — live only (props need the StatsAPI name index + a
    # live prop board); the offline snapshot path prices game markets only. Props
    # price off the *same* projections as the game slate (one simulation), which
    # is also what makes prop legs parlay-able against game legs below.
    props_frame = None
    prop_log = None
    if (
        args.league == "mlb"
        and mlb_names is not None
        and prop_lines is not None
        and not prop_lines.empty
        and projections
    ):
        props_frame, prop_log = _mlb_prop_slate(
            args, projections, prop_lines, mlb_names, now, generated_at
        )

    # Parlay slate — combine the qualifying single bets (game markets, segments,
    # props) into sim-exact correlated parlays. Works offline too (game legs only).
    if args.league == "mlb" and projections and args.parlay_max_legs >= 2:
        _mlb_parlay_slate(args, projections, game_log, prop_log, mlb_names, now, generated_at)

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        persisted = frame.assign(league=args.league, generated_at=generated_at)
        parquet = out_dir / f"slate_{args.league}_{stamp}.parquet"
        persisted.to_parquet(parquet, index=False)
        print(f"\nwrote {len(persisted)} slate rows to {parquet}")
        # Persist the game→teams+kickoff map so a later grader can join StatsAPI
        # finals (a different id space) back onto these Odds-API game ids.
        if not events.empty:
            games_cols = ["game_id", "home_team", "away_team", "kickoff"]
            games_map = events[games_cols].assign(league=args.league)
            games_map.to_parquet(out_dir / f"games_{args.league}_{stamp}.parquet", index=False)
        _write_workbook(out_dir, stamp, args, events, projections, frame, props_frame, generated_at)
        if args.league == "mlb" and not events.empty:
            _write_cards(out_dir, stamp, args, events, projections, canonical, now,
                         generated_at, weather_by_game)


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


def _write_cards(  # noqa: PLR0913 - a report writer with several inputs
    out_dir: Path,
    stamp: str,
    args: argparse.Namespace,
    events: pd.DataFrame,
    projections: dict,
    canonical: pd.DataFrame,
    now: datetime,
    generated_at: pd.Timestamp,
    weather_by_game: dict | None = None,
) -> None:
    """Render the per-game matchup cards to an HTML page (best-effort, MLB only).

    The team records, starter lines, and CDN logo/headshot ids come from a live
    StatsAPI context fetch; an offline run (or a failed fetch) still renders the
    cards from the projections and board, minus the descriptive header.
    """
    try:
        from velocity.report.card_html import write_cards_html
        from velocity.report.cards import GridSources, build_cards

        contexts = []
        grid = GridSources()
        if not args.snapshot_file:
            from velocity.ingest.mlb_context import load_context

            try:
                contexts = load_context(now.strftime("%Y-%m-%d"))
            except Exception as exc:  # noqa: BLE001 - context is header decoration
                print(f"card context fetch skipped: {exc}")
            grid = _mlb_grid_sources(events, contexts, now, weather_by_game or {})
        cards = build_cards(
            events, projections, canonical, contexts,
            aliases=MLB_TEAM_ALIASES, grid=grid,
        )
        dest = out_dir / f"cards_{args.league}_{stamp}.html"
        write_cards_html(dest, cards, league=args.league, generated_at=str(generated_at))
        print(f"wrote {len(cards)} matchup cards to {dest}")
    except Exception as exc:  # noqa: BLE001 - the cards page is a convenience, never fatal
        print(f"cards export skipped: {exc}")


def _mlb_grid_sources(  # pragma: no cover - network
    events: pd.DataFrame, contexts: list, now: datetime, weather: dict
):  # type: ignore[no-untyped-def]
    """Fetch the descriptive-grid data tiers, each independent and best-effort.

    A failure in any one feed (StatsAPI stats, FanGraphs/Statcast) contributes
    nothing for that tier — the card omits those rows rather than break. ``weather``
    (keyed by game_id) is fetched once by the caller and reused here, so the cards
    and the projections share one Open-Meteo pull.
    """
    from velocity.ingest.mlb_advanced import load_advanced
    from velocity.ingest.mlb_stats import load_team_hitting, load_team_pitching, load_team_splits
    from velocity.report.cards import GridSources
    from velocity.wagering.live import resolve_team

    season = now.year
    codes = sorted(set(MLB_TEAM_ALIASES.values()))

    def _try(label: str, fn):  # type: ignore[no-untyped-def]
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - one tier failing never blocks the rest
            print(f"grid: {label} skipped ({exc})")
            return None

    hitting = _try("team hitting", lambda: tuple(load_team_hitting(season))) or ()
    pitching = _try("team pitching", lambda: tuple(load_team_pitching(season))) or ()
    advanced = _try("advanced metrics", lambda: load_advanced(season)) or {}

    # Platoon + recent-form splits for just the clubs playing today (id from context).
    splits: dict[str, object] = {}
    for ctx in contexts:
        for team in (ctx.away, ctx.home):
            code = resolve_team(team.name, codes, MLB_TEAM_ALIASES)
            if code and code not in splits and team.team_id:
                got = _try(f"{code} splits", lambda tid=team.team_id: load_team_splits(tid, season))
                if got is not None:
                    splits[code] = got

    return GridSources(
        hitting=hitting, pitching=pitching,
        splits=splits, advanced=advanced, weather=weather,  # type: ignore[arg-type]
    )


def _apply_weather_run_env(model, events: pd.DataFrame) -> dict:  # pragma: no cover - network
    # type: ignore[no-untyped-def]
    """Fetch first-pitch weather per game, fold it into the model's run environment.

    Returns ``{game_id: Weather}`` so the cards reuse the same pull. Temperature
    lifts/suppresses HR and a closed roof neutralizes it; the model's per-home HR
    factor and run-env tilt are rebuilt from park × weather before pricing.
    """
    from velocity.ingest.mlb_weather import load_weather
    from velocity.report.park_factors import run_environment_maps
    from velocity.wagering.live import resolve_team

    codes = sorted(set(MLB_TEAM_ALIASES.values()))
    weather_by_game: dict[str, object] = {}
    weather_by_home: dict[str, tuple[float | None, bool]] = {}
    for event in events.to_dict("records"):
        home_code = resolve_team(str(event["home_team"]), codes, MLB_TEAM_ALIASES)
        if not home_code:
            continue
        try:
            first_pitch = pd.Timestamp(event["kickoff"]).to_pydatetime()
            w = load_weather(home_code, first_pitch)
        except Exception as exc:  # noqa: BLE001 - weather is best-effort; park-static stands in
            print(f"projection weather skipped for {home_code}: {exc}")
            continue
        if w is None:
            continue
        weather_by_game[str(event["game_id"])] = w
        weather_by_home[home_code] = (w.temp_f, w.indoors)

    if weather_by_home:
        hr_factors, run_env_tilts = run_environment_maps(weather_by_home)
        model.park_hr_factors = hr_factors
        model.run_env_tilts = run_env_tilts
        print(f"folded weather into the run environment for {len(weather_by_home)} park(s)")
    return weather_by_game


def _load_derivative_lines(path: str) -> pd.DataFrame:
    """Normalize saved per-event odds payloads into the derivative ``Lines`` frame.

    Accepts either a ``{event_id: payload}`` mapping (the collector's banked
    shape) or a bare list of per-event payloads; each payload may be the raw
    per-event object or a historical ``{data: …}`` wrapper.
    """
    from velocity.ingest.theoddsapi import events_of, normalize_derivative_markets

    payload = json.loads(Path(path).read_text())
    raws = payload.values() if isinstance(payload, dict) else payload
    events: list[dict] = []
    for raw in raws:
        events.extend(events_of(raw))
    return normalize_derivative_markets(events)


def _mlb_prop_slate(
    args: argparse.Namespace,
    projections: dict,
    prop_lines: pd.DataFrame,
    name_to_id: dict[str, str],
    now: datetime,
    generated_at: pd.Timestamp,
) -> tuple[pd.DataFrame | None, object | None]:
    """Price the prop board off the game slate's projections; return (frame, log).

    The projections already carry every player's sample arrays (one simulation
    per game), so no second model build or re-simulation happens here.
    """
    try:
        from velocity.models.props_mlb import BaseballProps
        from velocity.wagering.props_slate import build_prop_slate, prop_slate_to_frame

        props_by_game = {gid: BaseballProps(proj.result) for gid, proj in projections.items()}
        log, _ = build_prop_slate(
            props_by_game,
            prop_lines,
            name_to_id,
            config=SlateConfig(
                exclude_closing=False, min_edge=args.min_edge, starting_bankroll=args.bankroll,
                prob_shrink=args.mlb_prop_shrink,
                exclude_markets=frozenset(
                    m.strip() for m in args.mlb_exclude_props.split(",") if m.strip()
                ),
            ),
        )
        frame = prop_slate_to_frame(log)
        print(f"\n=== MLB props — {len(prop_lines)} lines, {len(frame)} recommended ===")
        if not frame.empty:
            with pd.option_context("display.width", 160, "display.max_columns", None):
                print(frame.to_string(index=False))
        if args.out:
            dest = Path(args.out) / f"slate_mlb_props_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet"
            frame.assign(league="mlb", generated_at=generated_at).to_parquet(dest, index=False)
            print(f"wrote {len(frame)} prop rows to {dest}")
        return frame, log
    except Exception as exc:  # noqa: BLE001 - prop slate is best-effort; never break the game slate
        print(f"prop slate skipped: {exc}")
        return None, None


def _mlb_parlay_slate(  # noqa: PLR0913 - a report writer with several inputs
    args: argparse.Namespace,
    projections: dict,
    game_log: object | None,
    prop_log: object | None,
    name_to_id: dict[str, str] | None,
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

        candidates = list(game_log or []) + list(prop_log or [])
        if not candidates:
            return
        results_by_game = {str(gid): proj.result for gid, proj in projections.items()}
        game_labels = {
            str(gid): f"{proj.away_team}@{proj.home_team}"
            for gid, proj in projections.items()
        }
        tickets = build_parlays(
            candidates,
            results_by_game,
            bankroll=args.bankroll,
            name_to_id=name_to_id or {},
            game_labels=game_labels,
            config=ParlayConfig(
                max_legs=max(args.parlay_max_legs, 2),
                min_ev=args.parlay_min_ev,
                max_parlays=args.max_parlays,
            ),
        )
        frame = parlay_slate_to_frame(tickets)
        print(f"\n=== MLB parlays — {len(candidates)} candidate legs, "
              f"{len(frame)} recommended ===")
        if frame.empty:
            print("no parlay cleared the combined-EV bar.")
        else:
            with pd.option_context("display.width", 200, "display.max_columns", None):
                print(frame.to_string(index=False))
            print("note: same_game=True payouts assume the product price; books "
                  "reprice correlated SGPs, so treat that EV as an upper bound.")
        if args.out and not frame.empty:
            dest = Path(args.out) / f"slate_mlb_parlays_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet"
            frame.assign(league="mlb", generated_at=generated_at).to_parquet(dest, index=False)
            print(f"wrote {len(frame)} parlay rows to {dest}")
    except Exception as exc:  # noqa: BLE001 - parlays are additive; never break the slate
        print(f"parlay slate skipped: {exc}")


if __name__ == "__main__":
    main()
