"""Grade persisted football prop slates and sweep the confidence shrink.

Feed it the live runner's persisted prop-slate parquets (they carry the raw
``p_model``/``p_fair``/``price`` per bet), point it at the week's actuals, and
it settles every bet and prints the per-market shrink sweep — the table that
decides ``prop_shrink_by_market`` / ``exclude_markets`` for the live slate
(docs/FOOTBALL_CUTOVER.md Phase 3, the MLB calibration loop ported).

    # offline (a saved weekly-stats parquet supplies actuals):
    python scripts/backtest_props_football.py --props artifacts/slate \
        --season 2026 --week 1 --stats-file weekly.parquet

    # live (fetch nflverse weekly stats for the season):
    python scripts/backtest_props_football.py --props artifacts/slate \
        --season 2026 --week 1
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd
from velocity.backtest.props_football import grade_prop_ledger, sweep_shrink

_PROPS_PATTERN = re.compile(r"slate_[a-z]+_props_\d{8}T\d{6}Z\.parquet")


def _load_props(path: Path) -> pd.DataFrame:
    """All prop-slate rows under ``path`` (a folder searched recursively, or a file)."""
    if path.is_file():
        return pd.read_parquet(path)
    files = sorted(p for p in path.rglob("*.parquet") if _PROPS_PATTERN.fullmatch(p.name))
    if not files:
        raise SystemExit(f"no prop-slate parquets under {path}")
    return pd.concat([pd.read_parquet(p) for p in files], ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Grade prop slates + shrink sweep")
    parser.add_argument("--props", required=True,
                        help="prop-slate parquet, or a folder of them (searched recursively)")
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--stats-file", help="saved weekly-stats parquet (offline actuals)")
    parser.add_argument("--shrinks", default="1.0,0.8,0.65,0.5,0.35",
                        help="comma-separated candidate shrinks")
    parser.add_argument("--min-edge", type=float, default=0.02)
    parser.add_argument("--out", help="optional parquet for the graded ledger")
    args = parser.parse_args()

    props = _load_props(Path(args.props))
    print(f"loaded {len(props)} prop bet(s)")

    if args.stats_file:
        weekly = pd.read_parquet(args.stats_file)
    else:
        from velocity.ingest.nfl import load_weekly_stats  # network path

        weekly = load_weekly_stats([args.season])
    weekly = weekly[(weekly["season"] == args.season) & (weekly["week"] == args.week)]
    print(f"actuals: {len(weekly)} player stat line(s) for {args.season} week {args.week}")

    graded = grade_prop_ledger(props, weekly)
    settled = graded[graded["result"].isin(["win", "loss", "push"])]
    pending = int((graded["result"] == "pending").sum())
    wins = int((settled["result"] == "win").sum())
    losses = int((settled["result"] == "loss").sum())
    print(f"graded: {wins}-{losses} ({pending} pending) · "
          f"flat-stake profit {settled['profit'].sum():+.2f}u")

    if "p_model" in graded.columns and "p_fair" in graded.columns:
        shrinks = [float(s) for s in args.shrinks.split(",") if s.strip()]
        table = sweep_shrink(graded, shrinks, min_edge=args.min_edge)
        print("\nShrink sweep (flat 1u stakes; ALL + per market):")
        print(table.to_string(index=False))
    else:
        print("no p_model/p_fair columns — sweep skipped (raw archive rows, not a slate)")

    if args.out:
        graded.to_parquet(args.out, index=False)
        print(f"wrote {len(graded)} graded rows to {args.out}")


if __name__ == "__main__":
    main()
