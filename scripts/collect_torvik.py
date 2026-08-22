"""Bank Torvik NCAAB ratings — live season-to-date, or as-of-date archives.

The NCAAB bootstrap's data collector (docs/BUILD_NCAAB.md phase N1): fetch
Bart Torvik's team-results ratings and bank them as parquet. Two modes:

    # today's season-to-date ratings:
    python scripts/collect_torvik.py --year 2026 --out artifacts/ncaab

    # the as-of-date archive (leak-free backtest inputs), one file per day:
    python scripts/collect_torvik.py --asof 2025-02-01 --out artifacts/ncaab

The timemachine archive is what makes NCAAB uniquely backtestable for free —
daily ratings exactly as they stood each morning. The author asks bulk
scrapers to make contact first; this collector fetches one payload per
invocation, which is the polite cadence. Failures print and exit non-zero;
nothing is ever partially written.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from velocity.ingest.ncaab import TorvikClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Bank Torvik NCAAB ratings")
    parser.add_argument("--year", type=int, help="season year for the live ratings")
    parser.add_argument("--asof", help="archive date (YYYY-MM-DD) for the timemachine")
    parser.add_argument("--out", required=True, help="output folder")
    args = parser.parse_args()

    if bool(args.year) == bool(args.asof):
        raise SystemExit("pass exactly one of --year (live) or --asof (archive)")

    client = TorvikClient()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.year:
        frame = client.team_results(args.year)
        dest = out / f"torvik_{args.year}.parquet"
    else:
        frame = client.team_results_asof(args.asof)
        stamp = args.asof.replace("-", "")
        dest = out / f"torvik_asof_{stamp}.parquet"
    if frame.empty:
        raise SystemExit("empty ratings payload — nothing banked")
    frame.to_parquet(dest, index=False)
    print(f"banked {len(frame)} team ratings to {dest}")


if __name__ == "__main__":
    main()
