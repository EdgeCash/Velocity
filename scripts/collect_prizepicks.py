"""Snapshot the live PrizePicks pick'em board to a private parquet (the collector).

PrizePicks has **no historical archive** — a board line exists only while it's
live — so building the history the slip-EV engine needs means snapshotting the
current board on a schedule, exactly like the BettingPros collector. Each run
takes one snapshot per requested league and writes a single timestamped
parquet plus the raw JSON payloads.

It is designed to run as a **GitHub Actions** job and to upload its output as
a **private Actions artifact**. It deliberately does *not* commit anything:
the repo is public, and both ToS hygiene and our own edge say board snapshots
never land in it.

No credentials exist for this feed (the board API is keyless); the client is
deliberately polite — one request per league per run, spaced, with backoff —
and reports bot-protection blocks instead of fighting them.

    python scripts/collect_prizepicks.py --out artifacts/pp --leagues NFL CFB
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.ingest.prizepicks import PrizePicksClient, normalize_projections

# PrizePicks league names as they spell them (see /leagues): football first.
DEFAULT_LEAGUES = ("NFL", "CFB")


def main() -> None:  # pragma: no cover - network orchestration
    parser = argparse.ArgumentParser(description="Snapshot the PrizePicks board")
    parser.add_argument("--out", default="artifacts/pp", help="output folder (private, not git)")
    parser.add_argument("--leagues", nargs="+", default=list(DEFAULT_LEAGUES),
                        help="PrizePicks league names (as /leagues spells them)")
    args = parser.parse_args()

    now = datetime.now(UTC)
    stamp = pd.Timestamp(now).tz_localize(None)
    tag = now.strftime("%Y%m%dT%H%M%SZ")
    print(f"PrizePicks snapshot @ {now.isoformat()}")

    client = PrizePicksClient()
    try:
        available = client.leagues()
    except Exception as exc:  # noqa: BLE001 - a blocked /leagues sinks the whole run
        raise SystemExit(f"could not list PrizePicks leagues ({exc})") from exc
    by_lower = {name.lower(): (name, lid) for name, lid in available.items()}

    out = Path(args.out)
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    frames: list[pd.DataFrame] = []
    failed: list[str] = []
    for league in args.leagues:
        found = by_lower.get(league.lower())
        if found is None:
            # Off-season leagues drop off /leagues entirely — not an error.
            print(f"  {league}: not on the board right now (off-season?)")
            continue
        name, league_id = found
        try:
            payload = client.projections(league_id)
        except Exception as exc:  # noqa: BLE001 - one league never blocks another
            print(f"  {name}: fetch failed ({exc})")
            failed.append(name)
            continue
        (raw_dir / f"pp_{name.lower()}_{tag}.json").write_text(json.dumps(payload))
        board = normalize_projections(payload).assign(collected_at=stamp)
        frames.append(board)
        n_std = int((board["odds_type"] == "standard").sum()) if not board.empty else 0
        print(f"  {name}: {len(board)} board lines ({n_std} standard, "
              f"{board['player_id'].nunique()} players)")

    if failed and not frames:
        raise SystemExit(f"every league fetch failed: {failed}")

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    dest = out / f"pp_board_{tag}.parquet"
    df.to_parquet(dest, index=False)
    print(f"wrote {len(df)} rows to {dest}")
    if df.empty:
        print("note: empty board (off-season or nothing posted yet) — the "
              "snapshot still banks so the schedule keeps running")


if __name__ == "__main__":
    main()
