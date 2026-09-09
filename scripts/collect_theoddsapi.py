"""Snapshot The Odds API game lines to a private parquet (the CLV archive builder).

The Odds API is the feed with real **history**, so it is the source for the
closing-line archive that powers CLV and the market backtest. Snapshotting the
live board on a schedule (cheap: ``/odds`` costs 1 credit per market per region)
builds the line-movement history toward close; true historical backfill uses the
pricier ``/historical`` endpoint on demand.

Runs as a **GitHub Actions** job (where ``THE_ODDS_API`` lives) and uploads its
output as a **private Actions artifact** — it never commits, because the repo is
public and paid odds data must not land in it. Triggering the workflow manually
(``workflow_dispatch``) doubles as the in-CI verification that the key works,
since the sandbox can't see the secret.

Credits are finite (100k/month) — this prints the remaining count each run.

    THE_ODDS_API=... python scripts/collect_theoddsapi.py --out artifacts/odds
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.ingest.theoddsapi import TheOddsAPIClient, normalize_odds_events, unwrap

LEAGUES = ("nfl", "ncaaf")


def collect(
    leagues: tuple[str, ...], collected_at: pd.Timestamp, out_raw: Path | None = None
) -> tuple[pd.DataFrame, str | None]:
    """Return a canonical ``Lines`` frame for ``leagues`` plus the remaining-credit count.

    The raw ``/odds`` payload is banked verbatim alongside the parquet when
    ``out_raw`` is given. That costs **nothing extra** — ``client.odds`` is only
    ``normalize_odds_events`` over this same payload — and it lets the live
    slate build its board from a banked snapshot instead of spending its own
    credits on a second call for data this collector already bought
    (docs/DATA_PROVIDERS.md). The raw form is what carries event metadata
    (teams, kickoff), which the normalized Lines frame drops.
    """
    client = TheOddsAPIClient.from_env()
    tag = collected_at.strftime("%Y%m%dT%H%M%SZ")
    frames: list[pd.DataFrame] = []
    remaining: str | None = None
    for league in leagues:
        payload = client.odds_payload(league)
        remaining = client.remaining or remaining
        if out_raw is not None:
            (out_raw / f"odds_{league}_{tag}.json").write_text(json.dumps(payload))
        lines = normalize_odds_events(unwrap(payload), is_closing=False)
        lines = lines.assign(league=league, collected_at=collected_at)
        frames.append(lines)
        print(
            f"  {league}: {len(lines)} lines "
            f"({lines['game_id'].nunique()} games, {lines['book'].nunique()} books)"
        )
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return df, remaining


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot The Odds API game lines")
    parser.add_argument("--out", default="artifacts/odds", help="output folder (private, not git)")
    parser.add_argument("--leagues", nargs="+", default=list(LEAGUES), help="leagues to snapshot")
    args = parser.parse_args()

    now = datetime.now(UTC)
    stamp = pd.Timestamp(now).tz_localize(None)
    print(f"The Odds API snapshot @ {now.isoformat()}")
    out = Path(args.out)
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    df, remaining = collect(tuple(args.leagues), stamp, raw)

    dest = out / f"odds_lines_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet"
    df.to_parquet(dest, index=False)
    print(f"wrote {len(df)} rows to {dest}")
    if remaining is not None:
        print(f"credits remaining this month: {remaining}")
    if df.empty:
        print("note: no live lines right now (off-season or no board posted yet)")


if __name__ == "__main__":
    main()
