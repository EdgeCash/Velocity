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
from velocity.models.simulate_baseball import BaseballSimConfig
from velocity.util.seed import make_rng
from velocity.wagering.live import MLB_TEAM_ALIASES, build_live_slate, slate_to_frame
from velocity.wagering.slate import SlateConfig


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
    config = BaseballSimConfig(n_sims=args.n_sims, starter_outs=18)
    if args.snapshot_file:
        return league_average_model(codes, n_sims=args.n_sims)
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
    unresolved: list[dict[str, str]] = []
    if events.empty:
        print("no games on the board (off-season or empty snapshot)")
    else:
        log, unresolved = build_live_slate(
            events,
            lines,
            project,
            known_teams,
            SlateConfig(
                exclude_closing=False, min_edge=args.min_edge, starting_bankroll=args.bankroll
            ),
            aliases=aliases,
        )
        frame = slate_to_frame(log)
        if frame.empty:
            print("no bets cleared the edge threshold.")
        else:
            frame = frame.assign(stake_pct=(frame["stake"] / args.bankroll * 100).round(2))
            with pd.option_context("display.width", 140, "display.max_columns", None):
                print(f"\n{len(frame)} recommended bets (stake as % of {args.bankroll:.0f}):")
                print(frame.to_string(index=False))
            print(f"\ntotal staked: {frame['stake'].sum():.2f} ({frame['stake_pct'].sum():.1f}%)")

        if unresolved:
            print(f"\n{len(unresolved)} game(s) skipped — teams not in the model's universe:")
            for u in unresolved:
                print(f"  {u['away_team']} @ {u['home_team']} ({u['reason']})")

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        persisted = frame.assign(league=args.league, generated_at=generated_at)
        dest = out_dir / f"slate_{args.league}_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet"
        persisted.to_parquet(dest, index=False)
        print(f"\nwrote {len(persisted)} slate rows to {dest}")


if __name__ == "__main__":
    main()
