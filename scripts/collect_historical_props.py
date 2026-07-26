"""Bank historical MLB prop snapshots into the soft-market CLV archive.

Props are a per-event endpoint, so a historical props pull is two steps per snapshot:
list the events that existed at that timestamp (cheap, 1 credit), then pull each
event's historical prop board (10 × markets × regions — the historical multiplier).
For each day × snapshot time it banks three ways, mirroring the game backfill:

* the **raw** per-event JSON verbatim (nothing the credits bought is lost),
* a normalized ``PropLines`` parquet (marked closing), and
* an ``events`` parquet (game_id, teams, kickoff) so the prop backtest can pick
  entry/closing boards and join box scores.

This is credit-expensive, so it targets a *window* (a couple of weeks), not the whole
season. Pull a few times per day so most games have a pre-first-pitch snapshot.

    # live (needs THE_ODDS_API): two weeks of MLB props at 3 snapshot times a day
    python scripts/collect_historical_props.py --start 2026-07-10 --end 2026-07-24 \
        --times 16:00,20:00,23:30 --out artifacts/hist_props

    # offline — re-process one banked raw per-event payload into props (no credits):
    python scripts/collect_historical_props.py --from-file event.json \
        --snapshot 2026-07-22T23:30:00Z --out artifacts/hist_props
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from velocity.ingest.theoddsapi import (
    DEFAULT_PROP_MARKETS,
    events_of,
    extract_events,
    normalize_player_props,
)


def _dates(start: str, end: str) -> list[str]:
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    if d1 < d0:
        raise SystemExit("--end is before --start")
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def _tag(iso: str) -> str:
    return iso.replace(":", "").replace("-", "")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bank historical MLB prop snapshots (CLV archive)")
    parser.add_argument("--league", default="mlb")
    parser.add_argument("--start", help="range start YYYY-MM-DD (UTC)")
    parser.add_argument("--end", help="range end YYYY-MM-DD (UTC), inclusive")
    parser.add_argument("--times", default="16:00,20:00,23:30",
                        help="comma-separated UTC snapshot times per day (HH:MM)")
    parser.add_argument("--markets", default=DEFAULT_PROP_MARKETS, help="prop market keys")
    parser.add_argument("--out", default="artifacts/hist_props", help="output folder")
    parser.add_argument("--from-file", help="offline: normalize one banked raw per-event JSON")
    parser.add_argument("--snapshot", help="ISO label for --from-file (e.g. 2026-07-22T23:30:00Z)")
    args = parser.parse_args()

    out_dir = Path(args.out)
    (out_dir / "raw").mkdir(parents=True, exist_ok=True)

    if args.from_file:  # offline re-processing — no network, no credits
        raw = json.loads(Path(args.from_file).read_text())
        snapshot = args.snapshot or "unknown"
        props = normalize_player_props(events_of(raw), is_closing=True).assign(snapshot=snapshot)
        props.to_parquet(out_dir / f"props_{args.league}_{_tag(snapshot)}.parquet", index=False)
        print(f"processed {args.from_file}: {len(props)} prop lines")
        return

    if not (args.start and args.end):
        raise SystemExit("provide --start and --end (or --from-file for offline processing)")

    from velocity.ingest.theoddsapi import TheOddsAPIClient  # network path

    client = TheOddsAPIClient.from_env()
    times = [t.strip() for t in args.times.split(",") if t.strip()]
    total_props = 0
    for day in _dates(args.start, args.end):
        for t in times:
            iso = f"{day}T{t}:00Z"
            try:
                ev_payload = client.historical_events_payload(args.league, iso)
            except Exception as exc:  # noqa: BLE001 - a bad snapshot shouldn't stop the run
                print(f"{iso}: events list failed ({exc})")
                continue
            events = extract_events(ev_payload).assign(snapshot=iso)

            prop_frames: list[pd.DataFrame] = []
            for event_id in events["game_id"].tolist():
                try:
                    raw = client.historical_event_odds_payload(
                        args.league, str(event_id), iso, args.markets
                    )
                except Exception as exc:  # noqa: BLE001 - one event failing shouldn't stop the rest
                    print(f"  {iso} {event_id}: fetch failed ({exc})")
                    continue
                raw_path = out_dir / "raw" / f"hist_props_{args.league}_{_tag(iso)}_{event_id}.json"
                raw_path.write_text(json.dumps(raw))
                prop_frames.append(normalize_player_props(events_of(raw), is_closing=True))

            props = (
                pd.concat(prop_frames, ignore_index=True) if prop_frames
                else normalize_player_props([])
            ).assign(snapshot=iso)
            props.to_parquet(out_dir / f"props_{args.league}_{_tag(iso)}.parquet", index=False)
            events.to_parquet(out_dir / f"events_{args.league}_{_tag(iso)}.parquet", index=False)
            total_props += len(props)
            print(f"{iso}: {len(events)} events, {len(props)} prop lines "
                  f"(credits left: {client.remaining})")
    print(f"\nbanked {total_props} historical prop rows to {out_dir}")


if __name__ == "__main__":
    main()
