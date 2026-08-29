"""Sweep the publish gate's floors against banked audit frames.

The gate's conviction/context floors are provisional (docs/PUBLISH_GATE.md §3
— "reasoned, not yet fitted"). Every live run banks a ``publish_*.parquet``
audit frame beside the slate; point this at that folder once a few weeks have
accumulated and it reports the volume every candidate floor pair would have
produced, so the constants in ``velocity/intel/publish.py`` are moved on data:

    python scripts/calibrate_publish_gate.py --slates out/

Volume only — the audit frames carry no outcomes, so win rates stay with the
CLV grader. Offline, no network.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from velocity.intel.calibrate import load_audits, rejection_summary, sweep_floors
from velocity.intel.publish import (
    DEFAULT_MAX_PLAYS,
    DEFAULT_MIN_CONTEXT,
    DEFAULT_MIN_CONVICTION,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish-gate floor sweep over banked audit frames")
    parser.add_argument("--slates", required=True,
                        help="folder holding the runner's publish_*.parquet audits")
    parser.add_argument("--max-plays", type=int, default=DEFAULT_MAX_PLAYS,
                        help="nightly cap applied inside the sweep")
    args = parser.parse_args()

    audit = load_audits(Path(args.slates))
    if audit.empty:
        raise SystemExit(f"no publish_*.parquet audit frames under {args.slates}/ "
                         "— run the live slate with --out first")

    nights = audit["night"].nunique()
    print(f"{len(audit)} candidates across {nights} night(s), "
          f"{int(audit['published'].sum())} published by the live gate\n")

    print("rejections by rule:")
    print(rejection_summary(audit).to_string(index=False))

    table = sweep_floors(audit, max_plays=args.max_plays)
    print(f"\nvolume by floor pair (nightly cap {args.max_plays}; current "
          f"constants: conviction {DEFAULT_MIN_CONVICTION:g}, "
          f"context {DEFAULT_MIN_CONTEXT:g}):")
    with pd.option_context("display.width", 120):
        print(table.to_string(index=False))
    print("\nMoving a constant is still a human edit in velocity/intel/publish.py "
          "— this sweep is the evidence, not the decision.")


if __name__ == "__main__":
    main()
