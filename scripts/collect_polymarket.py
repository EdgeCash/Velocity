"""Snapshot the Polymarket football board — raw JSON + canonical parquet (keyless).

Polymarket's CLOB keeps **no** historical order books, so the only spread and
liquidity history that will ever exist is what we bank ourselves — this is the
day-one collector (docs/BUILD_EXCHANGES.md E1/E4) and every missed hour is
history lost. Each run banks the raw Gamma events and CLOB books verbatim (so
improved normalizers can re-process later) plus normalized ``Lines``/
``PropLines`` parquet tagged ``snapshot``/``collected_at``/``league``.

Runs as a GitHub Actions job into a private artifact; nothing is committed
(docs/DATA_PROVIDERS.md). No secret needed — reads are unauthenticated.

    python scripts/collect_polymarket.py --out artifacts/exchanges
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.ingest.polymarket import (
    LEAGUE_BY_SLUG_PREFIX,
    TAG_IDS,
    PolymarketClient,
    normalize_polymarket_events,
    normalize_polymarket_props,
    token_ids,
)


def collect(
    leagues: tuple[str, ...], collected_at: pd.Timestamp, out_raw: Path
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Snapshot each league's board; bank raw payloads; return (lines, props)."""
    client = PolymarketClient()
    tag = collected_at.strftime("%Y%m%dT%H%M%SZ")
    line_frames: list[pd.DataFrame] = []
    prop_frames: list[pd.DataFrame] = []

    for league in leagues:
        # Gamma names the college tag "cfb"; the store's league is "ncaaf".
        canonical = LEAGUE_BY_SLUG_PREFIX.get(league, league)
        events = client.events(league)
        tokens = token_ids(events)
        books = client.books(tokens)
        (out_raw / f"polymarket_events_{league}_{tag}.json").write_text(json.dumps(events))
        (out_raw / f"polymarket_books_{league}_{tag}.json").write_text(json.dumps(books))

        lines = normalize_polymarket_events(events, books, collected_at)
        props = normalize_polymarket_props(events, books, collected_at)
        line_frames.append(lines.assign(league=canonical, collected_at=collected_at, snapshot=tag))
        prop_frames.append(props.assign(league=canonical, collected_at=collected_at, snapshot=tag))
        print(
            f"  {league}: {len(events)} events, {len(tokens)} tokens, {len(books)} books "
            f"-> {len(lines)} game lines, {len(props)} prop lines"
        )

    lines_df = pd.concat(line_frames, ignore_index=True) if line_frames else pd.DataFrame()
    props_df = pd.concat(prop_frames, ignore_index=True) if prop_frames else pd.DataFrame()
    return lines_df, props_df


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot the Polymarket football board")
    parser.add_argument("--out", default="artifacts/exchanges", help="output folder (private)")
    parser.add_argument("--leagues", nargs="+", default=list(TAG_IDS), help="Gamma league tags")
    args = parser.parse_args()

    now = datetime.now(UTC)
    stamp = pd.Timestamp(now).tz_localize(None)
    out = Path(args.out)
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    print(f"Polymarket board snapshot @ {now.isoformat()}")
    lines, props = collect(tuple(args.leagues), stamp, raw)

    tag = now.strftime("%Y%m%dT%H%M%SZ")
    lines.to_parquet(out / f"polymarket_lines_{tag}.parquet", index=False)
    props.to_parquet(out / f"polymarket_props_{tag}.parquet", index=False)
    print(f"wrote {len(lines)} game lines, {len(props)} prop lines")
    if lines.empty and props.empty:
        print("note: empty board right now (off-season or between postings)")


if __name__ == "__main__":
    main()
