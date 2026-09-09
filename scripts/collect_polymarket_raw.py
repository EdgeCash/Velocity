"""Bank raw Polymarket football data — events + order books, verbatim (keyless).

The day-one raw collector (docs/BUILD_EXCHANGES.md E1): Polymarket's CLOB keeps
NO historical order books, so spread/liquidity history exists only if we bank
it ourselves — this runs before the Polymarket normalizer (E3) exists, on
purpose. Deliberately dumb: fetch, dump verbatim, no schema. The E3 normalizer
backfills parquet from these banked payloads later.

Gamma lists each game as one event with its markets nested; every market's
``clobTokenIds`` (a JSON-encoded string) keys the CLOB batch ``POST /books``
(≤500 tokens/call — both verified live 2026-09-09).

    python scripts/collect_polymarket_raw.py --out artifacts/exchanges
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
TAGS = {"nfl": 450, "cfb": 100351}
_USER_AGENT = "velocity-research/0.1 (odds research; contact via repo)"
_TIMEOUT = 60
_PAGE = 100
_MAX_PAGES = 8
_BOOK_BATCH = 500


def _request(url: str, body: bytes | None = None) -> Any:
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
        return json.loads(resp.read())


def fetch_events(tag_id: int, sleep_seconds: float) -> list[dict]:
    """All open events under one league tag, offset pages merged."""
    events: list[dict] = []
    for page in range(_MAX_PAGES):
        url = f"{GAMMA}/events?tag_id={tag_id}&closed=false&limit={_PAGE}&offset={page * _PAGE}"
        batch = _request(url)
        events.extend(batch)
        if len(batch) < _PAGE:
            break
        time.sleep(sleep_seconds)
    return events


def token_ids(events: list[dict]) -> list[str]:
    """Every outcome-token id nested in the events (clobTokenIds is JSON-in-JSON)."""
    out: list[str] = []
    seen: set[str] = set()
    for event in events:
        for market in event.get("markets") or []:
            raw = market.get("clobTokenIds")
            if not raw:
                continue
            try:
                ids = json.loads(raw)
            except (TypeError, ValueError):
                continue
            for tid in ids:
                if tid and tid not in seen:
                    seen.add(tid)
                    out.append(str(tid))
    return out


def fetch_books(tokens: list[str], sleep_seconds: float) -> list[dict]:
    """Order books for every token, batch POSTs of ≤500."""
    books: list[dict] = []
    for start in range(0, len(tokens), _BOOK_BATCH):
        chunk = tokens[start : start + _BOOK_BATCH]
        body = json.dumps([{"token_id": t} for t in chunk]).encode()
        books.extend(_request(f"{CLOB}/books", body))
        time.sleep(sleep_seconds)
    return books


def main() -> None:
    parser = argparse.ArgumentParser(description="Bank raw Polymarket events + books")
    parser.add_argument("--out", default="artifacts/exchanges", help="output folder (private)")
    parser.add_argument("--sleep", type=float, default=0.25, help="pause between requests")
    args = parser.parse_args()

    now = datetime.now(UTC)
    tag_stamp = now.strftime("%Y%m%dT%H%M%SZ")
    raw = Path(args.out) / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    print(f"Polymarket raw snapshot @ {now.isoformat()}")
    for league, tag_id in TAGS.items():
        events = fetch_events(tag_id, args.sleep)
        tokens = token_ids(events)
        books = fetch_books(tokens, args.sleep)
        (raw / f"polymarket_events_{league}_{tag_stamp}.json").write_text(json.dumps(events))
        (raw / f"polymarket_books_{league}_{tag_stamp}.json").write_text(json.dumps(books))
        print(f"  {league}: {len(events)} events, {len(tokens)} tokens, {len(books)} books")


if __name__ == "__main__":
    main()
