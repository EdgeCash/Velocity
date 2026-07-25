"""Walk-forward MLB CLV backtest over the banked odds archive — the season report.

Reads the CLV archive that ``collect_historical_odds.py`` banked (per-snapshot
``lines_*.parquet`` + ``events_*.parquet``), replays it day by day through the
point-in-time model, and prints the season scorecard: record + ROI, CLV by market,
and a calibration table. This is the acceptance number for #23 — did the model beat
the closing line over the test window, and is it calibrated?

    python scripts/backtest_mlb.py --archive artifacts/hist --season 2026

Each game-day is priced with its own season-to-date model (``build_mlb_asof``), so
nothing the model sees postdates the game. Finals are resolved once from the StatsAPI
schedule spanning the archive. The pricing/grading logic is offline-tested in
``tests/test_backtest_mlb.py``; this script only loads the archive and prints.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def _load_concat(archive: Path, stem: str) -> pd.DataFrame:
    """Concatenate every banked ``{stem}_*.parquet`` snapshot under the archive dir.

    Recursive: a downloaded Actions artifact extracts each run's files into its own
    subdir, so the parquet can sit one level down — ``rglob`` finds them wherever
    they land, and the snapshot-tagged filenames don't collide across runs.
    """
    files = sorted(archive.rglob(f"{stem}_*.parquet"))
    if not files:
        raise SystemExit(f"no {stem}_*.parquet under {archive}")
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward MLB CLV backtest over the archive")
    parser.add_argument("--archive", required=True, help="dir of banked lines_/events_ parquet")
    parser.add_argument("--season", type=int, required=True, help="season year for as-of stats")
    parser.add_argument("--out", help="optional parquet to persist the graded ledger")
    parser.add_argument("--n-sims", type=int, default=2000,
                        help="Monte Carlo draws per game (default 2000; lower = faster)")
    parser.add_argument("--cache-dir",
                        help="dir to memoize per-day as-of stat fetches (skips network on re-runs)")
    args = parser.parse_args()

    from velocity.backtest.mlb import walk_forward_mlb

    archive = Path(args.archive)
    lines = _load_concat(archive, "lines")
    events = _load_concat(archive, "events").drop_duplicates("game_id")
    print(f"loaded {len(lines)} line rows across {events['game_id'].nunique()} games")

    report = walk_forward_mlb(
        lines, events, args.season, n_sims=args.n_sims, cache_dir=args.cache_dir
    )

    print(f"\n=== Season scorecard — {len(report.ledger)} bets ===")
    for key, value in report.summary.items():
        print(f"  {key:>16}: {value}")
    if not report.clv_by_market.empty:
        print("\nCLV by market:")
        print(report.clv_by_market.to_string(index=False))
    if not report.calibration.empty:
        print("\nCalibration (model p vs realized):")
        print(report.calibration.to_string(index=False))

    if args.out:
        report.ledger.to_parquet(args.out, index=False)
        print(f"\nwrote {len(report.ledger)} graded rows to {args.out}")


if __name__ == "__main__":
    main()
