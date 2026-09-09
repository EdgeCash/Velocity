"""The operator's ledger CLI — see the bankroll, book the bets you took, sync it.

The slate runner appends what it recommended and the grader settles what
was placed; this is the one place a *person* writes to the ledger
(docs/WAGERING.md W1). Everything is an append — a correction is a new
record — and the ledger is private data (paid prices), living in the slate
artifact and the site's R2 bucket, never in the repo.

    python scripts/ledger.py show                      # bankroll, open bets, P&L
    python scripts/ledger.py todo --league nfl          # the newest card with bet ids
    python scripts/ledger.py place --bet-id ID --stake 2 --price -108 --book fanduel
    python scripts/ledger.py skip --bet-id ID
    python scripts/ledger.py settle --bet-id ID --result win   # a manual correction
    python scripts/ledger.py adjust --amount 50 --note "deposit"
    python scripts/ledger.py pull / push                # the R2 round trip (wrangler)
    python scripts/ledger.py merge --into L copies/*.parquet   # union several copies

``push`` merges the remote copy in before writing, so a placement recorded
on a laptop and a settlement recorded by the workflow never overwrite each
other: the union by record identity is the ledger.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.wagering.ledger import Ledger, merge_ledgers

DEFAULT_PATH = "artifacts/ledger/ledger.parquet"
# The durable copy, beside the season chain in the site's bucket
# (.github/workflows/live-slate.yml).
R2_KEY = "velocity-wasm/ledger/ledger.parquet"
_PLACEABLE = ("league", "game_id", "market", "side", "player")


def _now() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(UTC)).tz_localize(None)


def _wrangler(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["npx", "--yes", "wrangler@4", "r2", "object", *args, "--remote"],
                          capture_output=True, text=True, check=False)


def pull(path: Path) -> bool:
    """Fetch the R2 copy and merge it into ``path``; False when there is none."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".remote.parquet")
    result = _wrangler("get", R2_KEY, "--file", str(tmp))
    if result.returncode != 0 or not tmp.exists():
        print(f"no remote ledger ({result.stderr.strip().splitlines()[-1:] or 'not found'})")
        return False
    local = Ledger.load(path)
    merged = merge_ledgers(local.frame, pd.read_parquet(tmp))
    tmp.unlink()
    Ledger(merged, path).save()
    print(f"pulled: {len(merged)} record(s) in {path}")
    return True


def push(path: Path) -> None:
    """Merge the remote copy in, then write the union back to R2."""
    pull(path)
    ledger = Ledger.load(path)
    result = _wrangler("put", R2_KEY, "--file", str(path))
    if result.returncode != 0:
        sys.exit(f"push failed: {result.stderr.strip()}")
    print(f"pushed {len(ledger)} record(s) to {R2_KEY}")


def show(ledger: Ledger) -> None:
    state = ledger.state()
    print(state.describe())
    open_ = ledger.open_bets()
    if not open_.empty:
        cols = ["bet_id", "kind", "market", "side", "player", "point", "book", "price",
                "stake", "placed_at"]
        print(f"\nopen bets ({len(open_)}):")
        print(open_[cols].to_string(index=False))
    by_league = ledger.pnl(("league",))
    if not by_league.empty:
        print("\nsettled P&L by league:")
        print(by_league.to_string(index=False))
        print("\nsettled P&L by market:")
        print(ledger.pnl(("league", "market")).to_string(index=False))
    tail = ledger.frame.tail(8)[["recorded_at", "record_type", "league", "market", "side",
                                 "player", "stake", "price", "result", "profit", "note"]]
    print(f"\nlast {len(tail)} record(s) of {len(ledger)}:")
    print(tail.to_string(index=False))


def todo(ledger: Ledger, league: str | None) -> None:
    card = ledger.latest_recommendations(league)
    if card.empty:
        print("no recommendations on the ledger yet")
        return
    cols = ["status", "bet_id", "market", "side", "player", "point", "book", "price", "stake",
            "slate_stamp"]
    for lg, part in card.groupby("league"):
        print(f"\n{str(lg).upper()} — card {part['slate_stamp'].iloc[0]}: "
              f"{int((part['status'] == 'open').sum())} open, "
              f"{int((part['status'] == 'placed').sum())} placed, "
              f"{int((part['status'] == 'skipped').sum())} skipped")
        print(part[cols].to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Read and write the bankroll ledger")
    parser.add_argument("--ledger", default=DEFAULT_PATH, help="the ledger parquet")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show", help="bankroll, open bets, P&L, the last records")
    p_todo = sub.add_parser("todo", help="the newest card with bet ids and status")
    p_todo.add_argument("--league")
    p_seed = sub.add_parser("seed", help="open an empty ledger at a bankroll")
    p_seed.add_argument("--amount", type=float, required=True)
    p_place = sub.add_parser("place", help="record a bet you took")
    p_place.add_argument("--bet-id", help="from `todo`; or give the fields below")
    p_place.add_argument("--stake", type=float, required=True)
    p_place.add_argument("--price", type=float, help="American; default: the recommended")
    p_place.add_argument("--book")
    p_place.add_argument("--point", type=float)
    p_place.add_argument("--note")
    for field in _PLACEABLE:
        p_place.add_argument(f"--{field.replace('_', '-')}", dest=f"f_{field}",
                             help="for a bet the card never listed")
    p_skip = sub.add_parser("skip", help="decline a recommendation (closed at zero)")
    p_skip.add_argument("--bet-id", required=True)
    p_skip.add_argument("--note")
    p_settle = sub.add_parser("settle", help="settle an open bet by hand")
    p_settle.add_argument("--bet-id", required=True)
    p_settle.add_argument("--result", choices=["win", "loss", "push", "tie"], required=True)
    p_settle.add_argument("--note")
    p_adjust = sub.add_parser("adjust", help="deposit (+), withdrawal (−), or correction")
    p_adjust.add_argument("--amount", type=float, required=True)
    p_adjust.add_argument("--note")
    sub.add_parser("pull", help="merge the R2 copy into the local ledger")
    sub.add_parser("push", help="merge the R2 copy in, then write the union to R2")
    p_merge = sub.add_parser("merge", help="union several copies into one")
    p_merge.add_argument("--into", required=True, help="the ledger to write")
    p_merge.add_argument("copies", nargs="*", help="parquet copies to fold in")
    args = parser.parse_args()

    path = Path(args.ledger)
    if args.command == "pull":
        pull(path)
        return
    if args.command == "push":
        push(path)
        return
    if args.command == "merge":
        into = Path(args.into)
        copies = [pd.read_parquet(p) for p in args.copies if Path(p).exists()]
        base = pd.read_parquet(into) if into.exists() else None
        merged = merge_ledgers(base, *copies)
        Ledger(merged, into).save()
        print(f"merged {len(copies)} cop(y/ies) into {into}: {len(merged)} record(s)")
        return

    ledger = Ledger.load(path)
    now = _now()
    if args.command == "show":
        show(ledger)
        return
    if args.command == "todo":
        todo(ledger, args.league)
        return
    if args.command == "seed":
        if not ledger.seed(args.amount, at=now, note="seeded by the operator"):
            sys.exit(f"already seeded at {ledger.seed_amount():.2f}")
        print(f"seeded at {args.amount:.2f}")
    elif args.command == "place":
        fields = {k: getattr(args, f"f_{k}") for k in _PLACEABLE
                  if getattr(args, f"f_{k}") is not None}
        row = ledger.place(args.bet_id, args.stake, at=now, price=args.price, book=args.book,
                           point=args.point, note=args.note, fields=fields or None)
        print(f"placed {row['bet_id']}: {row['stake']:.2f} at {row['price']} "
              f"({row['book'] or 'book unknown'})")
    elif args.command == "skip":
        row = ledger.skip(args.bet_id, at=now, note=args.note)
        print(f"skipped {row['bet_id']}")
    elif args.command == "settle":
        settled = ledger.settle(pd.DataFrame([{"bet_id": args.bet_id, "result": args.result,
                                               "note": args.note or "settled by hand"}]),
                                at=now)
        if settled.empty:
            sys.exit(f"{args.bet_id} is not an open bet")
        print(f"settled {args.bet_id} as {args.result}: {settled.iloc[0]['profit']:+.2f}")
    elif args.command == "adjust":
        ledger.adjust(args.amount, at=now, note=args.note)
        print(f"adjusted by {args.amount:+.2f}")
    ledger.save()
    print(ledger.state().describe())


if __name__ == "__main__":
    main()
