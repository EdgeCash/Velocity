"""Report The Odds API credit consumption from banked ledgers — the plan-size answer.

Every network collector run banks an ``odds_credits_*.parquet``: one row per
credit-spending call with what it cost (``x-requests-last``) and what was left.
This reads a folder of them and answers the only question that matters for a
$60/month subscription — **are we on the right plan** — three ways:

* where the credits go (per endpoint kind × league, dearest first),
* what a month costs at the observed rate, against the plan, and
* what the *marginal* pieces cost, so a cut can be priced before it is made.

    python scripts/report_odds_credits.py --ledgers artifacts/odds --plan 100000

Read the projection honestly, in two respects.

It extrapolates the observed window, so a window covering only a Tuesday says
nothing about a Sunday NFL slate. Bank a full week — ideally one with a busy
weekend — before taking a number to a plan change. The report refuses to project
from a window shorter than ``--min-hours`` (default 24) rather than print a
confident number built on an afternoon.

And it is a floor, not a ceiling: the ledger records calls whose response was
read, so a call that errored out is missing from it, and the window is measured
between the first and last *recorded* call rather than across the whole billing
period. Treat a comfortable verdict as comfortable, not as exact.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from velocity.ingest.theoddsapi import project_monthly, usage_frame, usage_summary


def load_ledgers(folder: Path, pattern: str = "odds_credits_*.parquet") -> pd.DataFrame:
    """Concatenate every banked ledger under ``folder`` (recursive) into one frame.

    Runs land in per-run artifact folders, so this walks the tree rather than one
    directory. A folder with no ledgers returns the empty ledger frame — nothing
    to report is a valid answer, not an error.
    """
    frames = [pd.read_parquet(path) for path in sorted(folder.rglob(pattern))]
    if not frames:
        return usage_frame([])
    return usage_frame(pd.concat(frames, ignore_index=True))


def marginal_costs(usage: pd.DataFrame) -> pd.DataFrame:
    """Per-league credit totals — what dropping a league would actually save.

    The plan conversation is about cuts, and a cut is made by league or by
    endpoint kind, not by call. This prices the league axis; :func:`usage_summary`
    prices the kind axis.
    """
    summary = usage_summary(usage)
    if summary.empty:
        return pd.DataFrame(
            {
                "league": pd.Series(dtype=str),
                "credits": pd.Series(dtype="int64"),
                "calls": pd.Series(dtype="int64"),
                "share_pct": pd.Series(dtype=float),
            }
        )
    by_league = summary.groupby("league", as_index=False).agg(
        credits=("credits", "sum"), calls=("calls", "sum")
    )
    total = int(by_league["credits"].sum())
    by_league["share_pct"] = (
        (100.0 * by_league["credits"] / total).round(1) if total else 0.0
    )
    return by_league.sort_values("credits", ascending=False).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Report The Odds API credit consumption")
    parser.add_argument("--ledgers", default="artifacts/odds",
                        help="folder holding banked odds_credits_*.parquet (searched recursively)")
    parser.add_argument("--plan", type=int, default=100_000,
                        help="monthly credit allowance to compare against")
    parser.add_argument("--min-hours", type=float, default=24.0,
                        help="refuse to project from a window shorter than this")
    args = parser.parse_args()

    folder = Path(args.ledgers)
    usage = load_ledgers(folder)
    if usage.empty:
        print(f"no credit ledgers under {folder} — run a collector first")
        return

    print(f"credit ledger: {len(usage)} calls from {folder}")
    span = usage["at"].dropna()
    if not span.empty:
        print(f"window: {span.min()} → {span.max()} UTC")

    print("\nwhere the credits go (dearest first):")
    print(usage_summary(usage).to_string(index=False))

    print("\nby league:")
    print(marginal_costs(usage).to_string(index=False))

    proj = project_monthly(usage, plan=args.plan, min_hours=args.min_hours)
    print(f"\nobserved: {int(proj['credits'])} credits over {proj['days']:.2f} days")
    if pd.isna(proj["monthly"]):
        print(
            f"projection: withheld — the window is under {args.min_hours:g}h. "
            "A month's number from less says more about the sample than the plan."
        )
        return
    print(f"rate: {proj['per_day']:.0f} credits/day → {proj['monthly']:.0f}/month")
    print(f"plan: {int(proj['plan'])}/month — projected use {proj['plan_use_pct']:.1f}%")
    # The recommendation is deliberately a band, not a number: a projection off
    # one window is an estimate, and headroom is what absorbs a busy weekend.
    if proj["plan_use_pct"] < 25:
        print("verdict: heavily over-provisioned — a smaller tier covers this with room")
    elif proj["plan_use_pct"] < 60:
        print("verdict: comfortable headroom — the next tier down is worth pricing")
    elif proj["plan_use_pct"] < 90:
        print("verdict: right-sized — the headroom is doing real work")
    else:
        print("verdict: tight — a busy weekend could exhaust the month")


if __name__ == "__main__":
    main()
