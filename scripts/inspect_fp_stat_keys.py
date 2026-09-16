"""What stat keys the FantasyPros feed serves — read from a banked snapshot.

The sibling of ``scripts/inspect_bp_slugs.py``, and it answers the question
that one raised. That report showed five NFL prop slugs abstaining on every
row; two of them — ``rushing-attempts`` (57 rows, the largest) and
``passing-attempts`` (28) — are ordinary count props with banked actuals to
calibrate against, blocked only on whether FantasyPros projects the volume at
all. Nothing in this codebase reads a rush-attempt or pass-attempt projection,
so nobody knew.

``FP_API_KEY`` is a GitHub Actions secret the sandbox never sees, so this does
not fetch: the collector has been banking ``fp_projections_*.parquet`` as an
Actions artifact all along, and this reads one. Download the artifact from a
"Collect FantasyPros projections" run and point this at it.

    python scripts/inspect_fp_stat_keys.py artifacts/fp/fp_projections_*.parquet

The same census prints in that workflow's own log on every run — the collector
calls ``describe_stat_keys`` under ``--inspect``, which the workflow always
passes — so the usual way to get this answer is to read the run, not to run
this. This exists for the case where you want the table as a CSV, or want to
compare two snapshots.

No network, no credentials: it only reads files the collector already wrote.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from velocity.ingest.fantasypros import describe_stat_keys, stat_key_census


def load(path: Path) -> pd.DataFrame:
    """A banked snapshot → the long projections frame.

    The collector writes one parquet per run carrying every league it fetched,
    tagged with a ``league`` column; older snapshots predate that column and
    are reported whole.
    """
    return pd.read_parquet(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report FantasyPros stat-key coverage from a banked snapshot"
    )
    parser.add_argument("paths", nargs="+", type=Path,
                        help="fp_projections_*.parquet files")
    parser.add_argument("--csv", type=Path,
                        help="also write the per-stat table here")
    args = parser.parse_args()

    tables = []
    for path in args.paths:
        if not path.exists():
            print(f"{path}: not found")
            continue
        frame = load(path)
        print(f"\n{path}  ({len(frame)} rows)")
        leagues = (
            sorted(frame["league"].dropna().astype(str).unique())
            if "league" in frame.columns else [""]
        )
        for league in leagues:
            part = frame[frame["league"].astype(str) == league] if league else frame
            for line in describe_stat_keys(part, league):
                print(line)
            tables.append(
                stat_key_census(part).assign(source=path.name, league=league or "(all)")
            )

    if args.csv and tables:
        out = pd.concat(tables, ignore_index=True)
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(args.csv, index=False)
        print(f"\nwrote {len(out)} stat rows to {args.csv}")


if __name__ == "__main__":
    main()
