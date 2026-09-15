"""Snapshot The Odds API game lines to a private parquet (the CLV archive builder).

The Odds API is the feed with real **history**, so it is the source for the
closing-line archive that powers CLV and the market backtest. Snapshotting the
live board on a schedule (cheap: ``/odds`` costs 1 credit per market per region)
builds the line-movement history toward close; true historical backfill uses the
pricier ``/historical`` endpoint on demand.

Runs as a **GitHub Actions** job (where ``THE_ODDS_API`` lives) and uploads its
output as an **Actions artifact** — it never commits, because paid odds data
must not land in git, whose history is permanent. Triggering the workflow manually
(``workflow_dispatch``) doubles as the in-CI verification that the key works,
since the sandbox can't see the secret.

Credits are finite (100k/month), so each run banks a **credit ledger** beside
the data: one row per call with what it cost (``x-requests-last``) and what was
left. A printed "remaining" ages out of a run log; a banked series is what
answers whether the plan is the right size (``scripts/report_odds_credits.py``).

    THE_ODDS_API=... python scripts/collect_theoddsapi.py --out artifacts/odds
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.ingest.scrub import scrub_secrets
from velocity.ingest.theoddsapi import (
    TheOddsAPIClient,
    describe_usage,
    normalize_odds_events,
    unwrap,
    write_usage,
)

LEAGUES = ("nfl", "ncaaf")


def collect(
    leagues: tuple[str, ...], collected_at: pd.Timestamp, out_raw: Path | None = None,
    *, regions: str = "us",
) -> tuple[pd.DataFrame, str | None, list[dict[str, object]]]:
    """Return ``Lines`` for ``leagues``, the remaining-credit count, and the credit ledger.

    The raw ``/odds`` payload is banked verbatim alongside the parquet when
    ``out_raw`` is given. That costs **nothing extra** — ``client.odds`` is only
    ``normalize_odds_events`` over this same payload — and it lets the live
    slate build its board from a banked snapshot instead of spending its own
    credits on a second call for data this collector already bought
    (docs/DATA_PROVIDERS.md). The raw form is what carries event metadata
    (teams, kickoff), which the normalized Lines frame drops.
    """
    # ``regions`` is The Odds API's region list: "us", or "us,eu" for
    # Pinnacle — the sharp close the grader prefers (docs/SYSTEM_REVIEW.md
    # §4.3). Every extra region is one more credit per market per pull.
    client = dataclasses.replace(TheOddsAPIClient.from_env(), regions=regions)
    tag = collected_at.strftime("%Y%m%dT%H%M%SZ")
    frames: list[pd.DataFrame] = []
    remaining: str | None = None
    for league in leagues:
        payload = client.odds_payload(league)
        remaining = client.remaining or remaining
        if out_raw is not None:
            # Scrubbed at the banking boundary. The Odds API carries its key
            # in the QUERY STRING, which is the string an API echoes back — the
            # shape that put BettingPros' partner key into 89 artifacts. No echo
            # is observed in today's payloads; this does not depend on that
            # staying true (velocity/ingest/scrub.py).
            (out_raw / f"odds_{league}_{tag}.json").write_text(
                json.dumps(scrub_secrets(payload))
            )
        lines = normalize_odds_events(unwrap(payload), is_closing=False)
        lines = lines.assign(league=league, collected_at=collected_at)
        frames.append(lines)
        print(
            f"  {league}: {len(lines)} lines "
            f"({lines['game_id'].nunique()} games, {lines['book'].nunique()} books)"
        )
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return df, remaining, client.usage


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot The Odds API game lines")
    parser.add_argument("--out", default="artifacts/odds",
                        help="output folder (artifact, never git)")
    parser.add_argument("--leagues", nargs="+", default=list(LEAGUES), help="leagues to snapshot")
    parser.add_argument("--regions", default="us",
                        help="The Odds API regions: 'us', or 'us,eu' for Pinnacle (each "
                             "region is one more credit per market per pull)")
    args = parser.parse_args()

    now = datetime.now(UTC)
    stamp = pd.Timestamp(now).tz_localize(None)
    print(f"The Odds API snapshot @ {now.isoformat()}")
    out = Path(args.out)
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    df, remaining, usage = collect(tuple(args.leagues), stamp, raw, regions=args.regions)

    dest = out / f"odds_lines_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet"
    df.to_parquet(dest, index=False)
    print(f"wrote {len(df)} rows to {dest}")
    ledger = write_usage(usage, out, now.strftime("%Y%m%dT%H%M%SZ"))
    print(describe_usage(usage))
    if ledger is not None:
        print(f"credit ledger → {ledger}")
    if remaining is not None:
        print(f"credits remaining this month: {remaining}")
    if df.empty:
        print("note: no live lines right now (off-season or no board posted yet)")


if __name__ == "__main__":
    main()
