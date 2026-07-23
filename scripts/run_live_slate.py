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
from velocity.models.simulate_baseball import DEFAULT_HFA, BaseballSimConfig
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
    """The MLB model: real StatsAPI lineups/rates when live, else the baseline.

    An offline run (``--snapshot-file``) uses the league-average baseline — no
    network. A live run builds today's model from StatsAPI (season stats + probable
    lineups), falling back to the baseline if that fetch fails so the slate still
    runs.
    """
    codes = sorted(set(MLB_TEAM_ALIASES.values()))
    config = BaseballSimConfig(n_sims=args.n_sims, starter_outs=18, hfa=DEFAULT_HFA)
    if args.snapshot_file:
        from velocity.report.park_factors import park_hr_factors

        return league_average_model(
            codes, n_sims=args.n_sims, park_hr_factors=park_hr_factors()
        )
    try:
        from velocity.models.mlb_build import build_live_mlb_model

        now = datetime.now(UTC)
        model = build_live_mlb_model(now.strftime("%Y-%m-%d"), now.year, config=config)
        print(f"built MLB model from StatsAPI lineups ({len(model.known_teams)} clubs)")
        return model
    except Exception as exc:  # noqa: BLE001 - any live-data failure degrades gracefully
        print(f"warning: live lineup build failed ({exc}); using league-average baseline")
        return league_average_model(codes, n_sims=args.n_sims)


def _build_projection(
    args: argparse.Namespace,
) -> tuple[Callable[[str, str], GameProjection], list[str], dict[str, str] | None]:
    """Return ``(project, known_teams, aliases)`` for the requested league.

    Football fits the scores ratings from a committed games file; MLB simulates
    from lineups, so it needs no ``--data`` — it uses the league-average baseline
    until real per-team lineups/rates are wired in.
    """
    if args.league == "mlb":
        model = _mlb_model(args)
        return model.project_full, model.known_teams, MLB_TEAM_ALIASES

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

    return project, list(model.ratings.teams), None


def main() -> None:
    parser = argparse.ArgumentParser(description="Live slate of staked recommendations")
    parser.add_argument("--league", choices=["nfl", "ncaaf", "mlb"], required=True)
    parser.add_argument("--data", help="folder with a games file to fit the model (nfl/ncaaf)")
    parser.add_argument("--snapshot-file", help="saved Odds API /odds JSON (offline mode)")
    parser.add_argument("--n-sims", type=int, default=10_000)
    parser.add_argument("--min-edge", type=float, default=0.02)
    parser.add_argument("--bankroll", type=float, default=100.0)
    parser.add_argument("--out", help="folder to persist the slate parquet (private, not git)")
    args = parser.parse_args()

    now = datetime.now(UTC)
    generated_at = pd.Timestamp(now).tz_localize(None)

    project, known_teams, aliases = _build_projection(args)

    payload = _load_snapshot(args)
    lines = normalize_odds_events(payload)
    events = extract_events(payload)
    print(f"=== Live slate: {args.league.upper()} — {len(events)} games on the board ===")

    frame = pd.DataFrame()
    projections: dict = {}
    canonical = pd.DataFrame()
    unresolved: list[dict[str, str]] = []
    if events.empty:
        print("no games on the board (off-season or empty snapshot)")
    else:
        cfg = SlateConfig(
            exclude_closing=False, min_edge=args.min_edge, starting_bankroll=args.bankroll
        )
        # Project once, then price off those projections (reused for the workbook).
        projections, unresolved = project_board(events, project, known_teams, aliases)
        canonical = canonicalize_sides(lines, events)
        canonical = canonical[canonical["game_id"].astype(str).isin(projections)]
        games_min = events[["game_id", "kickoff"]].copy()
        games_min["game_id"] = games_min["game_id"].astype(str)
        frame = slate_to_frame(build_slate(projections, canonical, games_min, cfg))

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

    # MLB player-prop slate — live only (props need the StatsAPI model + a live
    # prop board); the offline snapshot path prices game markets only.
    props_frame = None
    if args.league == "mlb" and not args.snapshot_file and not events.empty:
        props_frame = _mlb_prop_slate(args, events, now, generated_at)

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        persisted = frame.assign(league=args.league, generated_at=generated_at)
        parquet = out_dir / f"slate_{args.league}_{stamp}.parquet"
        persisted.to_parquet(parquet, index=False)
        print(f"\nwrote {len(persisted)} slate rows to {parquet}")
        _write_workbook(out_dir, stamp, args, events, projections, frame, props_frame, generated_at)
        if args.league == "mlb" and not events.empty:
            _write_cards(out_dir, stamp, args, events, projections, canonical, now, generated_at)


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
            grid = _mlb_grid_sources(events, contexts, now)
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
    events: pd.DataFrame, contexts: list, now: datetime
):  # type: ignore[no-untyped-def]
    """Fetch the descriptive-grid data tiers, each independent and best-effort.

    A failure in any one feed (StatsAPI stats, FanGraphs/Statcast, Open-Meteo)
    contributes nothing for that tier — the card omits those rows rather than break.
    """
    from velocity.ingest.mlb_advanced import load_advanced
    from velocity.ingest.mlb_stats import load_team_hitting, load_team_pitching, load_team_splits
    from velocity.ingest.mlb_weather import load_weather
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

    # First-pitch weather per game, keyed by game_id (home park + kickoff).
    weather: dict[str, object] = {}
    for event in events.to_dict("records"):
        home_code = resolve_team(str(event["home_team"]), codes, MLB_TEAM_ALIASES)
        first_pitch = pd.Timestamp(event["kickoff"]).to_pydatetime()
        if home_code:
            got = _try(
                f"{home_code} weather",
                lambda hc=home_code, fp=first_pitch: load_weather(hc, fp),
            )
            if got is not None:
                weather[str(event["game_id"])] = got

    return GridSources(
        hitting=hitting, pitching=pitching,
        splits=splits, advanced=advanced, weather=weather,  # type: ignore[arg-type]
    )


def _mlb_prop_slate(  # pragma: no cover - network
    args: argparse.Namespace,
    events: pd.DataFrame,
    now: datetime,
    generated_at: pd.Timestamp,
) -> pd.DataFrame | None:
    """Build and persist the MLB prop slate; return its frame (or None on failure)."""
    try:
        from velocity.ingest.theoddsapi import TheOddsAPIClient
        from velocity.models.mlb_build import build_live_mlb
        from velocity.wagering.props_slate import mlb_prop_slate, prop_slate_to_frame

        sim_config = BaseballSimConfig(n_sims=args.n_sims, starter_outs=18, hfa=DEFAULT_HFA)
        model, name_to_id = build_live_mlb(now.strftime("%Y-%m-%d"), now.year, config=sim_config)
        prop_lines = TheOddsAPIClient.from_env().player_props("mlb")
        log, _ = mlb_prop_slate(
            model,
            events,
            prop_lines,
            name_to_id,
            config=SlateConfig(
                exclude_closing=False, min_edge=args.min_edge, starting_bankroll=args.bankroll
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
        return frame
    except Exception as exc:  # noqa: BLE001 - prop slate is best-effort; never break the game slate
        print(f"prop slate skipped: {exc}")
        return None


if __name__ == "__main__":
    main()
