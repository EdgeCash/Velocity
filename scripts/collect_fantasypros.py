"""Snapshot FantasyPros player projections to a private parquet + dump raw shape.

FantasyPros consensus projections feed the props model as an external prior. This
snapshots the current projections for both leagues into a private parquet and —
because we don't have a pinned FantasyPros schema — can also dump the raw JSON of
the first player so the tolerant normalizer can be tightened against the real
response.

Runs as a **GitHub Actions** job (where ``FP_API_KEY`` lives) and uploads its
output as a **private Actions artifact**; it never commits. Triggering the
workflow manually (``workflow_dispatch``) with ``--inspect`` doubles as the in-CI
verification + schema discovery, since the sandbox can't see the secret.

    FP_API_KEY=... python scripts/collect_fantasypros.py --season 2026 --out artifacts/fp
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.ingest.fantasypros import FantasyProsClient, normalize_projections

LEAGUES = ("nfl", "ncaaf")


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot FantasyPros projections")
    parser.add_argument("--season", type=int, required=True, help="projection season, e.g. 2026")
    parser.add_argument("--week", type=int, default=0, help="0 = full-season projections")
    parser.add_argument("--leagues", nargs="+", default=list(LEAGUES))
    parser.add_argument("--out", default="artifacts/fp", help="output folder (private, not git)")
    parser.add_argument(
        "--inspect", action="store_true", help="print the raw shape of the first player (dry-run)"
    )
    args = parser.parse_args()

    client = FantasyProsClient.from_env()
    now = datetime.now(UTC)
    stamp = pd.Timestamp(now).tz_localize(None)
    print(f"FantasyPros snapshot @ {now.isoformat()} (season {args.season}, week {args.week})")

    frames: list[pd.DataFrame] = []
    failed: list[str] = []
    for league in args.leagues:
        try:
            raw = client.raw_projections(league, args.season, week=args.week)
        except Exception as exc:  # noqa: BLE001 - one league's feed never blocks the other
            # Seen live: the NCAAF path 404s while NFL answers — a dead league
            # endpoint must not sink the snapshot the other league produced.
            print(f"  {league}: fetch failed ({exc}); skipping")
            failed.append(league)
            continue
        if args.inspect:
            players = raw.get("players") or raw.get("data") or [] if isinstance(raw, dict) else raw
            top_keys = sorted(raw.keys()) if isinstance(raw, dict) else "(list payload)"
            print(f"  [{league}] top-level keys: {top_keys}")
            if players:
                print(f"  [{league}] first player raw:\n{json.dumps(players[0], indent=2)[:1200]}")
        df = normalize_projections(raw, season=args.season, week=args.week)
        df = df.assign(league=league, collected_at=stamp)
        frames.append(df)
        print(f"  {league}: {len(df)} projection rows, {df['player_name'].nunique()} players")

    if failed and len(failed) == len(args.leagues):
        raise SystemExit(f"every league fetch failed: {failed}")

    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    dest_dir = Path(args.out)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"fp_projections_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet"
    out.to_parquet(dest, index=False)
    print(f"wrote {len(out)} rows to {dest}")
    if out.empty:
        print("note: no projections returned (off-season, wrong season/week, "
              "or a limited public API tier)")


if __name__ == "__main__":
    main()
