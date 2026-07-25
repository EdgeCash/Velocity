"""Bank historical odds snapshots into a CLV archive — burn Odds API credits well.

The Odds API is the one feed with a real historical archive, and historical calls
cost more credits than live ones — so this spends them deliberately: for each day
in a range, at a few snapshot times, it pulls the historical board and banks it
three ways —

* the **raw JSON** verbatim (nothing the credits bought is ever lost),
* a normalized ``Lines`` parquet (game markets), and
* an ``events`` parquet (game_id, teams, kickoff) to rejoin finals later.

Later, an offline step picks each game's closing line (the snapshot nearest before
its first pitch) from the banked snapshots and grades it — no more credits needed.
Pull a few times per day (e.g. afternoon + evening UTC) so every game has a
snapshot close to its start.

    # live (needs THE_ODDS_API), a week of MLB at 3 snapshot times a day:
    python scripts/collect_historical_odds.py --league mlb \
        --start 2026-07-16 --end 2026-07-22 --times 18:00,21:00,23:30 --out archive/hist

    # offline — re-process a banked raw snapshot into parquet (no credits):
    python scripts/collect_historical_odds.py --from-file snap.json \
        --snapshot 2026-07-22T23:30:00Z --league mlb --out archive/hist
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from velocity.ingest.theoddsapi import extract_events, normalize_odds_events, unwrap


def _dates(start: str, end: str) -> list[str]:
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    if d1 < d0:
        raise SystemExit("--end is before --start")
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def _process(raw: object, snapshot: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Normalize a raw historical payload into (closing lines, events), tagged by snapshot."""
    lines = normalize_odds_events(unwrap(raw), is_closing=True).assign(snapshot=snapshot)
    events = extract_events(raw).assign(snapshot=snapshot)
    return lines, events


def _write(out_dir: Path, league: str, tag: str, raw: object,
           lines: pd.DataFrame, events: pd.DataFrame) -> None:
    (out_dir / "raw").mkdir(parents=True, exist_ok=True)
    (out_dir / "raw" / f"hist_{league}_{tag}.json").write_text(json.dumps(raw))
    lines.to_parquet(out_dir / f"lines_{league}_{tag}.parquet", index=False)
    events.to_parquet(out_dir / f"events_{league}_{tag}.parquet", index=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bank historical odds snapshots (CLV archive)")
    parser.add_argument("--league", default="mlb")
    parser.add_argument("--start", help="range start YYYY-MM-DD (UTC)")
    parser.add_argument("--end", help="range end YYYY-MM-DD (UTC), inclusive")
    parser.add_argument("--times", default="18:00,21:00,23:30",
                        help="comma-separated UTC snapshot times per day (HH:MM)")
    parser.add_argument("--markets", default="h2h,spreads,totals")
    parser.add_argument("--out", default="archive/hist", help="output folder")
    parser.add_argument("--from-file", help="offline: process a banked raw snapshot JSON")
    parser.add_argument("--snapshot", help="ISO label for --from-file (e.g. 2026-07-22T23:30:00Z)")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.from_file:  # offline re-processing — no network, no credits
        raw = json.loads(Path(args.from_file).read_text())
        snapshot = args.snapshot or "unknown"
        lines, events = _process(raw, snapshot)
        _write(out_dir, args.league, snapshot.replace(":", "").replace("-", ""), raw, lines, events)
        print(f"processed {args.from_file}: {len(lines)} lines, {len(events)} games")
        return

    if not (args.start and args.end):
        raise SystemExit("provide --start and --end (or --from-file for offline processing)")

    from velocity.ingest.theoddsapi import TheOddsAPIClient  # network path

    client = TheOddsAPIClient.from_env()
    times = [t.strip() for t in args.times.split(",") if t.strip()]
    total_lines = 0
    for day in _dates(args.start, args.end):
        for t in times:
            iso = f"{day}T{t}:00Z"
            try:
                raw = client.historical_odds_payload(args.league, iso, args.markets)
            except Exception as exc:  # noqa: BLE001 - one snapshot failing shouldn't stop the run
                print(f"{iso}: fetch failed ({exc})")
                continue
            lines, events = _process(raw, iso)
            _write(out_dir, args.league, iso.replace(":", "").replace("-", ""), raw, lines, events)
            total_lines += len(lines)
            print(f"{iso}: {len(lines)} lines, {len(events)} games "
                  f"(credits left: {client.remaining})")
    print(f"\nbanked {total_lines} closing-line rows to {out_dir}")


if __name__ == "__main__":
    main()
