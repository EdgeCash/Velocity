"""Harvest HISTORICAL DraftKings salaries by walking draft-group ids.

The finding that makes DFS backtesting possible without buying data: DK's
draftables endpoint still serves **retired** draft groups. Any past group id
returns its full board — players, salaries, positions, and the competition
start time that dates it. DK is its own historical salary database; it just
has no index, so the ids have to be walked.

Ids are assigned chronologically but interleaved across every sport, so a
scan finds a mix and this keeps the ones that look like the league asked
for. Sparse ranges 404, which is normal and cheap.

Pairing this with the box-score banks (which carry the DK scoring line for
every batter and starter) gives salaries AND actual points — the two halves
of a real lineup backtest.

    # find where a date lives, then scan around it
    python scripts/harvest_dk_history.py --probe 2026-06-01
    python scripts/harvest_dk_history.py --from-id 149000 --to-id 149400 \
        --league mlb --out artifacts/dk_history
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from velocity.dfs.salaries import normalize_draftables

_URL = ("https://api.draftkings.com/draftgroups/v1/draftgroups/"
        "{gid}/draftables?format=json")
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
# Positions that are DISTINCTIVE to one league — generic overlap is not
# enough, because DK reuses letters across sports ("C" is a catcher in MLB
# and a centre in NHL) and ships multi-eligibility combos like "PG/SG".
_SIGNATURES = {
    "mlb": {"SP", "1B", "SS"},
    "nfl": {"QB", "WR", "TE"},
    "nba": {"PG", "SF"},
    "nhl": {"W", "D", "G"},
}


def fetch_group(gid: int, timeout: int = 30) -> dict | None:
    """One draft group's payload, or None when the id is retired/absent."""
    request = urllib.request.Request(_URL.format(gid=gid), headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read())
    except urllib.error.HTTPError:
        return None
    except Exception:  # noqa: BLE001 - a flaky id never sinks the walk
        return None


def board_format(payload: dict) -> str:
    """"showdown" or "classic", inferred from the board's own shape.

    A showdown board is self-describing without the lobby: DK lists every
    player twice, once at the captain slot for exactly 1.5x the flex salary.
    A classic board also repeats players (an RB shows up again for FLEX) but
    always at the SAME salary, so the ratio is the tell. Retired groups have
    no lobby entry left to ask, which is why this reads the payload.
    """
    prices: dict[str, set[int]] = {}
    for draftable in payload.get("draftables") or []:
        pid = str(draftable.get("playerDkId") or draftable.get("displayName"))
        salary = draftable.get("salary")
        if salary is None:
            continue
        prices.setdefault(pid, set()).add(int(salary))
    if not prices:
        return "classic"
    doubled = sum(
        1 for values in prices.values()
        if len(values) == 2 and max(values) == round(min(values) * 1.5)
    )
    return "showdown" if doubled >= 0.5 * len(prices) else "classic"


def classify(payload: dict) -> tuple[str | None, pd.Timestamp | None]:
    """(league, start time) inferred from the board itself."""
    draftables = payload.get("draftables") or []
    if not draftables:
        return None, None
    # Expand multi-eligibility combos ("2B/SS" -> {"2B", "SS"}) before matching.
    positions: set[str] = set()
    for draftable in draftables:
        for part in str(draftable.get("position") or "").split("/"):
            token = part.strip().upper()
            if token:
                positions.add(token)
    league = None
    for name, signature in _SIGNATURES.items():
        if signature <= positions:  # every distinctive marker present
            league = name
            break
    start = None
    competitions = payload.get("competitions") or []
    raw = (competitions[0].get("startTime") if competitions
           else (draftables[0].get("competition") or {}).get("startTime"))
    if raw:
        stamp = pd.to_datetime(raw, errors="coerce", utc=True)
        start = None if pd.isna(stamp) else stamp.tz_localize(None)
    return league, start


def _walk(
    ids: range, *, workers: int, sleep: float
) -> Iterator[tuple[int, dict | None]]:
    """Fetch a range of ids, in order, optionally several at a time.

    The walk is pure network latency, so a handful of workers turns hours
    into minutes. Order is preserved so progress logs stay chronological;
    ``--sleep`` still paces each worker, so raising both at once is the way
    to stay polite while going faster.
    """
    if workers <= 1:
        for gid in ids:
            yield gid, fetch_group(gid)
            time.sleep(sleep)
        return

    def one(gid: int) -> tuple[int, dict | None]:
        payload = fetch_group(gid)
        time.sleep(sleep)
        return gid, payload

    with ThreadPoolExecutor(max_workers=workers) as pool:
        yield from pool.map(one, ids)


def main() -> None:
    parser = argparse.ArgumentParser(description="Harvest historical DK salaries")
    parser.add_argument("--from-id", type=int, help="first draft group id")
    parser.add_argument("--to-id", type=int, help="last draft group id (inclusive)")
    parser.add_argument("--league", default="mlb", help="league to keep")
    parser.add_argument("--out", default="artifacts/dk_history")
    parser.add_argument("--sleep", type=float, default=0.15)
    parser.add_argument("--workers", type=int, default=1,
                        help="concurrent fetches (the walk is all latency)")
    parser.add_argument("--format", default="any",
                        choices=("any", "classic", "showdown"),
                        help="keep only boards of this shape")
    parser.add_argument("--probe", help="report what dates a few sample ids "
                                        "cover, to locate a range")
    args = parser.parse_args()

    if args.probe:
        target = pd.Timestamp(args.probe)
        print(f"probing for draft groups near {target.date()} ...")
        # Coarse sweep: report the date each sampled id lands on so a range
        # can be chosen by hand. Ids run chronologically across all sports.
        for gid in range(140000, 156000, 1000):
            payload = fetch_group(gid)
            if payload is None:
                print(f"  {gid}: (retired)")
                continue
            league, start = classify(payload)
            print(f"  {gid}: {league or '?':4s} {start}")
            time.sleep(args.sleep)
        return

    if args.from_id is None or args.to_id is None:
        raise SystemExit("--from-id and --to-id are required (or use --probe)")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []
    kept = scanned = 0
    for gid, payload in _walk(range(args.from_id, args.to_id + 1),
                              workers=args.workers, sleep=args.sleep):
        scanned += 1
        if payload is None:
            continue
        league, start = classify(payload)
        if league != args.league:
            continue
        frame = normalize_draftables(payload, str(gid))
        if frame.empty or frame["salary"].sum() == 0:
            continue  # salary-free formats carry no backtest signal
        fmt = board_format(payload)
        if args.format not in ("any", fmt):
            continue
        frames.append(frame.assign(league=league, slate_start=start, format=fmt))
        kept += 1
        if kept % 10 == 0:
            print(f"  {kept} {args.league} boards kept ({scanned} ids scanned)")

    if not frames:
        print(f"no {args.league} boards with salaries in "
              f"[{args.from_id}, {args.to_id}]")
        return
    banked = pd.concat(frames, ignore_index=True)
    # The format is part of the name: a classic sweep and a showdown sweep
    # over the same id range are different datasets and must not clobber
    # each other (they did once).
    dest = (out / f"dk_history_{args.league}_{args.format}_"
                  f"{args.from_id}_{args.to_id}.parquet")
    banked.to_parquet(dest, index=False)
    span = (banked["slate_start"].min(), banked["slate_start"].max())
    print(f"\nkept {kept} boards / {len(banked):,} salary rows "
          f"({scanned} ids scanned)")
    print(f"covering {span[0]} .. {span[1]}")
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
