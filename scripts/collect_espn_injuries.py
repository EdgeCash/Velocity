"""Snapshot ESPN's injury report for every league we price (the collector).

Injuries reached this system from one place: FantasyPros, NFL only. So the
intel layer's availability signals — the ones that veto a bet when the player
it depends on is not playing — abstained on every other league. An MLB card
was priced with no idea that a listed starter was on the 60-day IL.

ESPN publishes an injury report per sport, keyless and quota-free, covering
all six. This snapshots each into one timestamped parquet.

Unlike the paid feeds, there is nothing to keep out of the repo on licensing
grounds — but the output still lands in a private Actions artifact alongside
them, because it is operational data with no reason to be in git and the
consumers already know how to find it there.

    python scripts/collect_espn_injuries.py --out artifacts/espn

``--leagues`` narrows it; the default is every league with an ESPN path.
"""

from __future__ import annotations

import argparse
import time
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.ingest.espn import ESPN_LEAGUE_PATHS, ESPNClient, normalize_injuries


def _with_retries(fn, label: str, attempts: tuple[int, ...] = (0, 5, 15)):  # type: ignore[no-untyped-def]
    """Call ``fn``, retrying transport errors with backoff.

    ESPN is keyless, so there is no quota to burn and no 4xx that means "stop
    asking" — a failure here is a blip or an outage, and both are worth a
    second look. The whole run is a handful of requests either way.
    """
    for attempt, delay in enumerate(attempts):
        if delay:
            print(f"  {label}: retrying in {delay}s")
            time.sleep(delay)
        try:
            return fn()
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == len(attempts) - 1:
                raise
            print(f"  {label}: {exc}")
    raise RuntimeError("unreachable")


def collect(leagues: tuple[str, ...], stamp: pd.Timestamp) -> tuple[pd.DataFrame, list[str]]:
    """One frame across ``leagues``, plus the leagues that failed outright."""
    client = ESPNClient()
    frames: list[pd.DataFrame] = []
    failed: list[str] = []
    for league in leagues:
        if league not in ESPN_LEAGUE_PATHS:
            print(f"  {league}: no ESPN path; skipping")
            continue
        try:
            payload = _with_retries(
                lambda lg=league: client.raw_injuries(lg), f"{league} injuries"
            )
        except Exception as exc:  # noqa: BLE001 - one league never sinks the rest
            print(f"  {league}: failed after retries ({exc})")
            failed.append(league)
            continue
        frame = normalize_injuries(payload, league)
        frames.append(frame.assign(collected_at=stamp))
        n_out = int(frame["is_out"].sum()) if not frame.empty else 0
        print(f"  {league}: {len(frame)} rows, {n_out} genuine outs, "
              f"{frame['team_name'].nunique()} teams")
        # College football publishes almost nothing — programs are not obliged
        # to, and most do not. Saying so keeps a thin report from reading as a
        # broken fetch the first time somebody looks.
        if league in ("ncaaf", "ncaab") and len(frame) < 25:
            print(f"    note: {league} reports are sparse by nature — college "
                  "programs are not required to publish injuries, and most do not")
        time.sleep(1)  # courteous spacing on an unauthenticated public API
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return out, failed


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot ESPN injury reports")
    parser.add_argument("--out", default="artifacts/espn",
                        help="output folder (private artifact, not git)")
    parser.add_argument("--leagues", nargs="+", default=list(ESPN_LEAGUE_PATHS),
                        help=f"leagues to snapshot (default: {' '.join(ESPN_LEAGUE_PATHS)})")
    args = parser.parse_args()

    now = datetime.now(UTC)
    stamp = pd.Timestamp(now).tz_localize(None)
    print(f"ESPN injuries snapshot @ {now.isoformat()}")
    frame, failed = collect(tuple(args.leagues), stamp)

    if failed and len(failed) == len(args.leagues):
        raise SystemExit(f"every league failed: {failed}")
    if failed:
        print(f"note: partial snapshot — {failed} skipped; the schedule retries them")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    dest = out / f"espn_injuries_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet"
    frame.to_parquet(dest, index=False)
    print(f"wrote {len(frame)} rows to {dest}")
    if frame.empty:
        print("note: no injury rows at all — off-season across every league, or a "
              "payload shape change worth looking at")


if __name__ == "__main__":
    main()
