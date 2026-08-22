"""Build the NFL injuries history dataset from nflverse weekly reports.

The keystone dataset the intelligence layer was waiting on
(docs/INTEL.md §6, docs/EDGE_RESEARCH.md §7 item 6): every week's official
game-status designations (Out/Doubtful/Questionable) back through the
committed play-by-play span, banked as one small parquet. With it, the
injury and QB-veto signals fire point-in-time in backtests instead of
abstaining — the channel becomes measurable, not presumed.

    python scripts/build_injury_history.py --start 2011 --end 2025 \
        --out datasets/nfl/injuries.parquet

Free public data (nflverse release assets), small enough to commit.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from velocity.ingest.nfl import load_injury_reports


def main() -> None:
    parser = argparse.ArgumentParser(description="Bank nflverse weekly injury reports")
    parser.add_argument("--start", type=int, default=2011)
    parser.add_argument("--end", type=int, default=2025)
    parser.add_argument("--out", default="datasets/nfl/injuries.parquet")
    args = parser.parse_args()

    years = list(range(args.start, args.end + 1))
    frames = []
    for year in years:
        try:
            frame = load_injury_reports([year])
            frames.append(frame)
            print(f"{year}: {len(frame)} designations "
                  f"({int(frame['is_out'].sum())} out/doubtful)")
        except Exception as exc:  # noqa: BLE001 - a missing early season is a gap, not a failure
            print(f"{year}: skipped ({exc})")
    if not frames:
        raise SystemExit("no injury reports fetched — nothing banked")

    import pandas as pd

    combined = pd.concat(frames, ignore_index=True)
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(dest, index=False)
    print(f"banked {len(combined)} rows ({combined['season'].min():.0f}–"
          f"{combined['season'].max():.0f}) to {dest}")


if __name__ == "__main__":
    main()
