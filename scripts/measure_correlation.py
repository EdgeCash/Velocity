"""What is ρ for two bets on the same game? — the measurement, both halves.

:mod:`velocity.wagering.portfolio` de-scales a correlation group by
``1/(1+(n-1)ρ)`` with a flat ``ρ = 0.5`` for every pair on a game. This script
measures what ρ actually is, from two sources, and reports how far the flat
assumption is off. It changes nothing: staking still applies 0.5.

    # the historical half — committed datasets, large sample, outcomes only
    python scripts/measure_correlation.py

    # the live half — settled ledger rows, our own book of business
    python scripts/measure_correlation.py --ledger artifacts/ledger/ledger.parquet

The live half is empty until football settles. Exchange rungs placed on a
Thursday card grade on the weekend, so an empty realized table early in the week
is the correct answer rather than a failure, and the script says so.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from velocity.eval.correlation import (
    PairMeasurement,
    historical_correlation,
    ladder_separation_curve,
    realized_correlation,
)

LEAGUES = ("nfl", "ncaaf")


def _table(rows: list[PairMeasurement]) -> None:
    print(f"  {'pair':<38} {'rho':>7} {'95% CI':>18} {'n':>7} "
          f"{'scale':>7} {'flat':>6} {'stake err':>10}")
    for m in rows:
        ci = f"[{m.lo:+.3f}, {m.hi:+.3f}]"
        zero = "  (zero in CI)" if m.includes_zero else ""
        scale = "    inf" if m.scale == float("inf") else f"{m.scale:>7.2f}"
        print(f"  {m.pair:<38} {m.rho:>+7.3f} {ci:>18} {m.n:>7} "
              f"{scale} {m.flat_scale:>6.2f} {m.stake_error:>+9.0%}{zero}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure same-game bet correlation")
    parser.add_argument("--datasets", default="datasets",
                        help="folder holding <league>/games.parquet")
    parser.add_argument("--league", action="append", choices=LEAGUES,
                        help="restrict to one league (repeatable); default both")
    parser.add_argument("--ledger", default=None,
                        help="ledger parquet — adds the realized half")
    parser.add_argument("--reps", type=int, default=2000,
                        help="bootstrap resamples for the intervals")
    args = parser.parse_args()

    leagues = tuple(args.league) if args.league else LEAGUES
    root = Path(args.datasets)

    for league in leagues:
        path = root / league / "games.parquet"
        if not path.exists():
            print(f"\n=== {league.upper()}: no games dataset at {path}")
            continue
        games = pd.read_parquet(path)
        print(f"\n=== {league.upper()} historical — {path} ({len(games)} games)")
        rows = historical_correlation(games, reps=args.reps)
        if rows:
            _table(rows)
        else:
            print("  nothing gradeable in the dataset")
        curve = ladder_separation_curve(games)
        if not curve.empty:
            print("\n  same-side total rungs, per-season stability of the curve:")
            print(f"  {'apart':>6} {'pooled rho':>11} {'seasons':>8} "
                  f"{'min':>7} {'max':>7} {'sd':>6}")
            for r in curve.itertuples():
                print(f"  {r.separation:>5.0f}p {r.rho:>+11.3f} {r.seasons:>8} "
                      f"{r.rho_min:>+7.2f} {r.rho_max:>+7.2f} {r.sd:>6.3f}")

    if not args.ledger:
        print("\n(no --ledger given; the realized half needs settled ledger rows)")
        return 0

    ledger_path = Path(args.ledger)
    if not ledger_path.exists():
        print(f"\n=== realized: no ledger at {ledger_path}")
        return 0
    led = pd.read_parquet(ledger_path)
    settled = led[led["record_type"] == "settled"] if "record_type" in led.columns else led
    print(f"\n=== realized — {ledger_path} ({len(settled)} settled row(s))")
    for league in leagues:
        part = settled[settled["league"].astype(str).str.lower() == league] \
            if "league" in settled.columns else settled
        rows = realized_correlation(part, reps=args.reps)
        if rows:
            print(f"\n  {league.upper()} ({len(part)} settled)")
            _table(rows)
        else:
            print(f"\n  {league.upper()}: no pair class has settled often enough yet "
                  f"({len(part)} settled row(s)) — expected until football grades")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
