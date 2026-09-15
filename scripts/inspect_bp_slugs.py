"""What the BettingPros prop board actually serves — read from a banked snapshot.

``BP_PROP_SLUG_TO_MARKET`` was written from reasoning rather than observation,
and ``docs/INTEL.md`` §6 has carried "confirming the slug table against a real
post-deploy snapshot" as an open item ever since. An unmapped slug abstains
silently and correctly, which is exactly why the gap is invisible: a board
where four of five markets contribute nothing reads like a healthy one.

The collector has been banking the answer every three hours. This reads it —
from the normalized ``bp_props_*.parquet`` or from the raw ``props_*.json``
beside it — and prints which slugs the board serves, how many rows each
carries, which are mapped, and whether the market they map to is one the props
stack actually prices.

    python scripts/inspect_bp_slugs.py artifacts/bp/bp_props_nfl_*.parquet
    python scripts/inspect_bp_slugs.py artifacts/bp/raw/props_nfl_*.json

No network, no credentials: it only reads files the collector already wrote.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from velocity.ingest.bettingpros import (
    describe_slug_coverage,
    normalize_props,
    slug_coverage,
)


def load(path: Path) -> pd.DataFrame:
    """A banked snapshot → the normalized props frame, whichever form it is in.

    A raw ``/props`` payload has no ``market_slug`` (the collector stamps it
    from the ``/markets`` listing at snapshot time), so every row reports as
    "(no slug)" — which is itself the finding when a run's market listing
    failed. The parquet is the better input where both exist.
    """
    if path.suffix == ".json":
        return normalize_props(json.loads(path.read_text()))
    return pd.read_parquet(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report BettingPros prop slug coverage from a banked snapshot"
    )
    parser.add_argument("paths", nargs="+", type=Path,
                        help="bp_props_*.parquet and/or raw props_*.json files")
    parser.add_argument("--csv", type=Path,
                        help="also write the per-slug table here")
    args = parser.parse_args()

    tables = []
    for path in args.paths:
        if not path.exists():
            print(f"{path}: not found")
            continue
        frame = load(path)
        sport = str(frame["sport"].dropna().iloc[0]) if (
            "sport" in frame.columns and frame["sport"].notna().any()
        ) else path.stem
        print(f"\n{path}  ({len(frame)} rows)")
        for line in describe_slug_coverage(frame, sport):
            print(line)
        tables.append(slug_coverage(frame).assign(source=path.name))

    if args.csv and tables:
        out = pd.concat(tables, ignore_index=True)
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(args.csv, index=False)
        print(f"\nwrote {len(out)} slug rows to {args.csv}")


if __name__ == "__main__":
    main()
