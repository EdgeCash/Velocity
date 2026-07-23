"""Grade an archived slate on CLV + calibration — the measurement loop, end to end.

Takes a slate the live runner persisted (plus its games map), joins final scores
from StatsAPI and — when given the closing snapshot — the closing lines, then
prints the scorecard: record + ROI, CLV by market, and a calibration table. This
is what turns "we added a plausible factor" into a number over the test period.

    # offline (a saved StatsAPI schedule JSON supplies finals):
    python scripts/grade_archive.py --slate slate.parquet --games games.parquet \
        --schedule-file schedule.json --closing-file close.json

    # live (fetch finals from StatsAPI for a date range):
    python scripts/grade_archive.py --slate slate.parquet --games games.parquet \
        --start 2026-07-23 --end 2026-07-23

The game and player markets share the same grading; props need a player-aware
finals source (not wired here yet), so this scores the game slate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from velocity.ingest.mlb import normalize_schedule
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
        return normalize_schedule(json.loads(Path(args.schedule_file).read_text()))
    from velocity.ingest.mlb import load_schedule  # network path

    return load_schedule(args.start, args.end)


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade an archived slate on CLV + calibration")
    parser.add_argument("--slate", required=True, help="persisted slate parquet")
    parser.add_argument("--games", required=True, help="persisted games-map parquet")
    parser.add_argument("--schedule-file", help="saved StatsAPI schedule JSON (offline finals)")
    parser.add_argument("--start", help="finals date range start YYYY-MM-DD (live)")
    parser.add_argument("--end", help="finals date range end YYYY-MM-DD (live)")
    parser.add_argument("--closing-file", help="closing Odds API snapshot JSON (for CLV)")
    parser.add_argument("--out", help="optional parquet to persist the graded bet rows")
    args = parser.parse_args()

    slate = pd.read_parquet(args.slate)
    games_map = pd.read_parquet(args.games)
    finals = finals_for_slate(games_map, _schedule(args))
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
