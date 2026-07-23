"""Snapshot MLB player-prop lines to a private parquet (the prop CLV archive).

The Odds API serves props from a per-event endpoint, so this pulls the current
event list and each event's prop board, normalizes to canonical ``PropLines``, and
writes one timestamped parquet. It is the prop-market analogue of
``collect_theoddsapi.py`` and the archive a later prop-CLV backtest grades against.

Runs as a GitHub Action (where ``THE_ODDS_API`` lives) and uploads a PRIVATE
artifact — never commits, since the repo is public and paid odds must not land in
it. Empty (no props posted yet) is a success, not a failure.

    THE_ODDS_API=... python scripts/collect_mlb_props.py --out artifacts/mlb_props
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.ingest.theoddsapi import DEFAULT_PROP_MARKETS, TheOddsAPIClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot MLB player-prop lines")
    parser.add_argument("--out", default="artifacts/mlb_props", help="output folder (private)")
    parser.add_argument("--markets", default=DEFAULT_PROP_MARKETS, help="prop market keys")
    args = parser.parse_args()

    now = datetime.now(UTC)
    stamp = pd.Timestamp(now).tz_localize(None)
    print(f"MLB props snapshot @ {now.isoformat()}")

    client = TheOddsAPIClient.from_env()
    props = client.player_props("mlb", args.markets).assign(league="mlb", collected_at=stamp)
    print(
        f"  {len(props)} prop lines "
        f"({props['game_id'].nunique()} games, {props['player'].nunique()} players)"
    )

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dest = out / f"mlb_props_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet"
    props.to_parquet(dest, index=False)
    print(f"wrote {len(props)} rows to {dest}")
    if client.remaining is not None:
        print(f"credits remaining this month: {client.remaining}")
    if props.empty:
        print("note: no props on the board right now (off-day or not yet posted)")


if __name__ == "__main__":
    main()
