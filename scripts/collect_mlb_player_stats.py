"""Snapshot MLB season-to-date player stats as the DFS projections frame.

Free statsapi (the same source the pipeline trusts for probables and box
scores) — season hitting + pitching totals for every player, written in the
FantasyPros long shape so ``build_dfs_lineup.py`` prices the MLB board from
it directly (``--fp`` input). Runs inside the live-slate workflow right
before the DFS step, so the rates are same-day fresh; nothing here is paid
data, but the output stays in the private artifacts flow with everything
else the slate emits.

    python scripts/collect_mlb_player_stats.py --out artifacts/mlbstats
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.ingest.mlbstats import fetch_season_stats, stats_long_frame


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Snapshot MLB season player stats (statsapi)")
    parser.add_argument("--season", type=int, default=0,
                        help="season year (0 = current UTC year)")
    parser.add_argument("--out", default="artifacts/mlbstats",
                        help="output folder")
    args = parser.parse_args()

    now = datetime.now(UTC)
    season = args.season or now.year
    print(f"MLB season-stats snapshot @ {now.isoformat()} (season {season})")

    hitting = fetch_season_stats(season, "hitting")
    pitching = fetch_season_stats(season, "pitching")
    frame = stats_long_frame(hitting, pitching).assign(
        league="mlb", collected_at=pd.Timestamp(now).tz_localize(None))
    print(f"  hitting: {hitting['player_name'].nunique() if not hitting.empty else 0}"
          f" players · pitching: "
          f"{pitching['player_name'].nunique() if not pitching.empty else 0} players")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dest = out / f"mlb_player_stats_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet"
    frame.to_parquet(dest, index=False)
    print(f"wrote {len(frame)} stat rows to {dest}")


if __name__ == "__main__":
    main()
