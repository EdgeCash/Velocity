"""Snapshot live BettingPros game lines to a private parquet (the collector).

BettingPros has **no historical archive** — a line only exists while it's live —
so building line history means snapshotting the current board on a schedule. This
script takes one snapshot of the three game markets (spread / total / moneyline)
for both leagues and writes a single timestamped parquet.

It is designed to run as a **GitHub Actions** job (where the ``BP_*`` secrets
live) and to upload its output as a **private Actions artifact**. It deliberately
does *not* commit anything: the repo is public and paid line data must never land
in it (provider ToS, and it would leak our edge).

Credentials come from the environment only — ``BP_API_KEY`` and the optional
``BP_USER_ID`` / ``BP_USER_KEY`` premium pair — never a literal.

    BP_API_KEY=... python scripts/collect_bettingpros.py --out artifacts/bp
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.ingest.bettingpros import BettingProsClient

SPORTS = ("NFL", "NCAAF")


def collect(sports: tuple[str, ...], collected_at: pd.Timestamp) -> pd.DataFrame:
    """Return one canonical ``Lines`` frame for ``sports``, tagged with league + snapshot time."""
    client = BettingProsClient.from_env()
    frames: list[pd.DataFrame] = []
    for sport in sports:
        lines = client.game_lines(sport)
        lines = lines.assign(league=sport.lower(), collected_at=collected_at)
        frames.append(lines)
        print(
            f"  {sport}: {len(lines)} lines "
            f"({lines['game_id'].nunique()} games, {lines['book'].nunique()} books)"
        )
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot BettingPros game lines")
    parser.add_argument("--out", default="artifacts/bp", help="output folder (private, not git)")
    parser.add_argument(
        "--sports", nargs="+", default=list(SPORTS), help="sports to snapshot (default NFL NCAAF)"
    )
    args = parser.parse_args()

    now = datetime.now(UTC)
    stamp = pd.Timestamp(now).tz_localize(None)
    print(f"BettingPros snapshot @ {now.isoformat()}")
    df = collect(tuple(args.sports), stamp)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fname = f"bp_lines_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet"
    dest = out / fname
    df.to_parquet(dest, index=False)
    print(f"wrote {len(df)} rows to {dest}")
    if df.empty:
        # Off-season / no board yet is not an error — the job still succeeds so the
        # schedule keeps running; the artifact just carries an empty frame.
        print("note: no live lines right now (off-season or no board posted yet)")


if __name__ == "__main__":
    main()
