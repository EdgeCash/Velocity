"""Walk-forward MLB **prop CLV** backtest over the banked prop archive.

Reads the prop CLV archive that ``collect_historical_props.py`` banked (per-snapshot
``props_*.parquet`` + ``events_*.parquet``), replays it day by day through the
point-in-time model, and prints the prop scorecard: CLV by prop market and, when box
scores are supplied, the graded record + ROI + calibration.

    # grade-free CLV read (needs StatsAPI for the as-of model; no odds credits):
    python scripts/backtest_props_mlb.py --archive artifacts/hist_props --season 2026

Prop CLV needs no box scores — :meth:`Bet.line_clv` / :meth:`Bet.price_clv` measure the
entry price/number against the close, and CLV is the durable read on edge (per #23). So
by default this is the grade-free CLV read: each day's **entry** prop board is priced
with the point-in-time model (``build_mlb_asof``), the **closing** prop board is
attached, and CLV is reported by market. Pass ``--boxscores`` (a ``normalize_boxscore``
frame spanning the archive) to also grade win/loss + ROI + ECE. The pricing/CLV logic is
offline-tested in ``tests/test_backtest_props_mlb.py``; this script only loads and prints.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def _load_concat(archive: Path, stem: str) -> pd.DataFrame:
    """Concatenate every banked ``{stem}_*.parquet`` snapshot under the archive dir.

    Recursive: a downloaded Actions artifact extracts each run's files into its own
    subdir, so the parquet can sit one level down — ``rglob`` finds them wherever they
    land, and the snapshot-tagged filenames don't collide across runs.
    """
    files = sorted(archive.rglob(f"{stem}_*.parquet"))
    if not files:
        raise SystemExit(f"no {stem}_*.parquet under {archive}")
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Walk-forward MLB prop CLV backtest over the banked prop archive"
    )
    parser.add_argument("--archive", required=True,
                        help="dir of banked props_/events_ parquet (an extracted artifact)")
    parser.add_argument("--season", type=int, required=True, help="season year for as-of stats")
    parser.add_argument("--boxscores",
                        help="optional parquet of normalize_boxscore rows to grade win/loss")
    parser.add_argument("--grade", action="store_true",
                        help="fetch box scores from StatsAPI for the archive's games and grade "
                             "win/loss + ROI + calibration (no odds credits; free StatsAPI)")
    parser.add_argument("--out", help="optional parquet to persist the prop ledger")
    parser.add_argument("--n-sims", type=int, default=2000,
                        help="Monte Carlo draws per game (default 2000; lower = faster)")
    parser.add_argument("--cache-dir",
                        help="dir to memoize per-day as-of stat fetches (skips network on re-runs)")
    parser.add_argument("--shrink-sweep",
                        help="comma-separated confidence shrinks to compare in one walk, "
                             "e.g. '1.0,0.7,0.5,0.35' (1.0 = raw model)")
    args = parser.parse_args()

    archive = Path(args.archive)
    lines = _load_concat(archive, "props")
    events = _load_concat(archive, "events").drop_duplicates("game_id")

    from velocity.backtest.props_mlb import load_archive_boxscores, walk_forward_props_mlb

    if args.boxscores:
        boxscores = pd.read_parquet(args.boxscores)
    elif args.grade:
        boxscores = load_archive_boxscores(events)
        print(f"fetched box scores for {boxscores['game_id'].nunique()} games")
    else:
        boxscores = None
    graded = boxscores is not None
    print(f"loaded {len(lines)} prop line rows across {events['game_id'].nunique()} games"
          f"{' (grading with box scores)' if graded else ' (grade-free CLV read)'}")

    if args.shrink_sweep:
        _run_sweep(lines, events, boxscores, args)
        return

    report = walk_forward_props_mlb(
        lines, events, args.season,
        boxscores=boxscores, n_sims=args.n_sims, cache_dir=args.cache_dir,
    )

    print(f"\n=== Prop scorecard — {len(report.ledger)} bets ===")
    for key, value in report.summary.items():
        print(f"  {key:>16}: {value}")
    if not report.clv_by_market.empty:
        print("\nCLV by prop market:")
        print(report.clv_by_market.to_string(index=False))
    if not report.calibration.empty:
        print("\nCalibration (model p vs realized):")
        print(report.calibration.to_string(index=False))

    if args.out:
        report.ledger.to_parquet(args.out, index=False)
        print(f"\nwrote {len(report.ledger)} prop rows to {args.out}")


def _run_sweep(lines, events, boxscores, args) -> None:  # pragma: no cover - network
    """Score the prop archive across confidence-shrink levels and print a comparison."""
    from velocity.backtest.props_mlb import prop_market_sweep, walk_forward_props_mlb_sweep

    shrinks = [float(s) for s in args.shrink_sweep.split(",")]
    reports = walk_forward_props_mlb_sweep(
        lines, events, args.season, shrinks,
        boxscores=boxscores, n_sims=args.n_sims, cache_dir=args.cache_dir,
    )
    graded = "roi" in next(iter(reports.values())).summary
    print("\n=== Prop confidence-shrink sweep (1.0 = raw model) ===")
    if graded:  # box scores in hand → the full record + ROI + ECE, plus CLV
        print(f"{'shrink':>7} {'n_bets':>7} {'wins':>6} {'losses':>7} "
              f"{'roi':>8} {'mean_clv':>9} {'pct_clv+':>9} {'ece':>7}")
        for s in shrinks:
            m = reports[s].summary
            print(f"{s:>7.2f} {m['n_bets']:>7} {m['wins']:>6} {m['losses']:>7} "
                  f"{m['roi']:>8} {m['mean_price_clv']:>9} "
                  f"{m['pct_positive_clv']:>9} {m['ece']:>7}")
    else:  # grade-free → CLV only
        print(f"{'shrink':>7} {'n_bets':>7} {'mean_price_clv':>15} "
              f"{'mean_line_clv':>14} {'pct_clv+':>9}")
        for s in shrinks:
            m = reports[s].summary
            print(f"{s:>7.2f} {m['n_bets']:>7} {m['mean_price_clv']:>15} "
                  f"{m['mean_line_clv']:>14} {m['pct_positive_clv']:>9}")

    # Per-market: which shrink each prop market wants on its own (total_bases vs the rest).
    breakdown = prop_market_sweep(reports)
    if not breakdown.empty:
        metric = "roi" if graded else "mean_price_clv"
        print(f"\nPer-market {metric} by shrink (pick each market's own optimum):")
        pivot = breakdown.pivot(index="market", columns="shrink", values=metric)
        print(pivot.to_string())


if __name__ == "__main__":
    main()
