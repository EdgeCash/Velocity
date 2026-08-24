"""Snapshot Baseball Savant Statcast leaderboards (the HR model's skill input).

Batter and pitcher batted-ball quality for the requested seasons, written as
one parquet the live slate reads at price time. Free, keyless, and keyed by
MLBAM player id — the same id space as the box-score banks, so it joins with
no name matching.

    python scripts/collect_mlb_statcast.py --out artifacts/statcast
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.ingest.savant import fetch_statcast


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot Statcast leaderboards")
    parser.add_argument("--seasons", nargs="+", type=int, default=[],
                        help="seasons to pull (default: current + prior year)")
    parser.add_argument("--out", default="artifacts/statcast", help="output folder")
    parser.add_argument("--min-bip", type=int, default=10,
                        help="minimum batted balls in play per player")
    args = parser.parse_args()

    now = datetime.now(UTC)
    seasons = args.seasons or [now.year, now.year - 1]
    print(f"Statcast snapshot @ {now.isoformat()} (seasons {seasons})")

    frames: list[pd.DataFrame] = []
    for season in seasons:
        for side in ("batter", "pitcher"):
            try:
                frame = fetch_statcast(season, side, min_bip=args.min_bip)
            except Exception as exc:  # noqa: BLE001 - one pull never blocks the rest
                print(f"  {season} {side}: fetch failed ({exc}); skipping")
                continue
            print(f"  {season} {side}: {len(frame)} players")
            frames.append(frame)
    if not frames:
        print("no Statcast rows returned; nothing written")
        return

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    combined = pd.concat(frames, ignore_index=True).assign(
        collected_at=pd.Timestamp(now).tz_localize(None))
    dest = out / f"statcast_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet"
    combined.to_parquet(dest, index=False)
    print(f"wrote {len(combined)} Statcast rows to {dest}")


if __name__ == "__main__":
    main()
