"""Grade an archived slate on CLV + calibration — the measurement loop, end to end.

Takes a slate the live runner persisted (plus its games map), joins final scores
from the league's schedule feed and — when given the closing snapshot — the
closing lines, then prints the scorecard: record + ROI, CLV by market, and a
calibration table. This is what turns "we added a plausible factor" into a
number over the test period.

    # offline (a saved games parquet/CSV supplies finals):
    python scripts/grade_archive.py --slate slate.parquet --games games.parquet \
        --schedule-file games_with_finals.parquet --closing-file close.json

    # live (fetch finals from the schedule feed for the slate's season):
    python scripts/grade_archive.py --slate slate.parquet --games games.parquet \
        --league nfl --season 2026

The game and player markets share the same grading; props need a player-aware
finals source (not wired here yet), so this scores the game slate.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pandas as pd
from velocity.report.results import finals_for_slate
from velocity.report.scorecard import (
    calibration_table,
    clv_by_market,
    grade_slate,
    summarize,
)


def _closing_lines(snapshot_file: str) -> pd.DataFrame | None:
    """Canonical closing lines (game_id/market/side/point/price) from an Odds snapshot."""
    from velocity.ingest.theoddsapi import extract_events, normalize_odds_events
    from velocity.wagering.live import canonicalize_sides

    payload = json.loads(Path(snapshot_file).read_text())
    lines = normalize_odds_events(payload)
    events = extract_events(payload)
    if lines.empty:
        return None
    canon = canonicalize_sides(lines, events)
    keep = [c for c in ("game_id", "market", "side", "point", "price") if c in canon.columns]
    return canon[keep]


def _schedule(args: argparse.Namespace) -> pd.DataFrame:
    if args.schedule_file:
        path = Path(args.schedule_file)
        return (
            pd.read_parquet(path) if path.suffix in (".parquet", ".pq") else pd.read_csv(path)
        )
    if args.league == "nfl":
        from velocity.ingest.nfl import load_schedules  # network path

        return load_schedules([args.season])
    from velocity.ingest.ncaaf import load_games  # network path

    api_key = os.environ.get("CFBD_API_KEY", "")
    if not api_key:
        raise SystemExit("CFBD_API_KEY is required for live NCAAF finals")
    return load_games([args.season], api_key)


def _aliases(args: argparse.Namespace, schedule: pd.DataFrame) -> dict[str, str]:
    if args.league == "nfl":
        from velocity.wagering.live import NFL_TEAM_ALIASES

        return dict(NFL_TEAM_ALIASES)
    teams = set(schedule["home_team"].astype(str)) | set(schedule["away_team"].astype(str))
    return {name: name for name in teams}


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade an archived slate on CLV + calibration")
    parser.add_argument("--slate", required=True, help="persisted slate parquet")
    parser.add_argument("--games", required=True, help="persisted games-map parquet")
    parser.add_argument("--league", default="nfl", choices=["nfl", "ncaaf"])
    parser.add_argument("--season", type=int, help="season year for live finals")
    parser.add_argument("--schedule-file",
                        help="saved Games-shaped parquet/CSV with finals (offline)")
    parser.add_argument("--closing-file", help="closing Odds API snapshot JSON (for CLV)")
    parser.add_argument("--out", help="optional parquet to persist the graded bet rows")
    args = parser.parse_args()
    if not args.schedule_file and args.season is None:
        raise SystemExit("--season is required when fetching live finals")

    slate = pd.read_parquet(args.slate)
    games_map = pd.read_parquet(args.games)
    schedule = _schedule(args)
    finals = finals_for_slate(games_map, schedule, aliases=_aliases(args, schedule))
    closing = _closing_lines(args.closing_file) if args.closing_file else None

    slate = slate[slate["game_id"].astype(str).isin(finals["game_id"])]
    if slate.empty:
        print("no graded games (no slate rows matched a played, resolved game)")
        return
    graded = grade_slate(slate, finals, closing)

    print(f"=== Scorecard — {len(graded)} bets ===")
    for key, value in summarize(graded).items():
        print(f"  {key:>16}: {value}")
    print("\nCLV by market:")
    print(clv_by_market(graded).to_string(index=False))
    table = calibration_table(graded)
    if not table.empty:
        print("\nCalibration (model p vs realized):")
        print(table.to_string(index=False))

    if args.out:
        graded.to_parquet(args.out, index=False)
        print(f"\nwrote {len(graded)} graded rows to {args.out}")


if __name__ == "__main__":
    main()
