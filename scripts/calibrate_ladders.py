#!/usr/bin/env python3
"""Regenerate the E8 ladder-calibration table from the committed datasets.

:data:`velocity.eval.ladders.OFFSET_BIAS` is a banked constant so the gate has
an opinion without reading a parquet on every slate, and
``test_committed_table_matches_a_fresh_measurement`` fails when it drifts from
the data. This prints the literal to paste back, because hand-transcribing
four tables of signed pairs is exactly how one of them ends up wrong — and a
sign error here hands the gate the safe tail's error on the dangerous side.

    python3 scripts/calibrate_ladders.py            # the literal, ready to paste
    python3 scripts/calibrate_ladders.py --report   # the full measurement

The report's ``ev_error`` column is what the gate's relative bar answers to: a
rung's EV per unit staked moves by its probability error divided by its price,
so that column — not ``error`` — says what the deep tail is exposed to.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from velocity.eval.ladders import residual_calibration

# (league, dataset) pairs the table covers. A league whose games carry no
# closing numbers has nothing to measure and does not belong here.
SOURCES: tuple[tuple[str, str], ...] = (
    ("nfl", "datasets/nfl/games.parquet"),
    ("ncaaf", "datasets/ncaaf/games.parquet"),
)
MAX_OFFSET = 28.5


def measure(path: str, market: str) -> tuple[pd.DataFrame, int, float]:
    """The calibration table for one dataset and market, plus its n and sd."""
    games = pd.read_parquet(path)
    column = "spread_line" if market == "spread" else "total_line"
    frame = games.dropna(subset=["home_score", "away_score", column])
    outcome = (
        frame["home_score"] - frame["away_score"]
        if market == "spread"
        else frame["home_score"] + frame["away_score"]
    )
    residual = (outcome - frame[column]).astype(float)
    table = residual_calibration(games, market, max_offset=MAX_OFFSET)
    # The cheaper side of the contract is the one a deep rung is priced at, so
    # it is the denominator that matters for what an error costs.
    table["price"] = table[["normal_over", "normal_under"]].min(axis=1)
    table["ev_error"] = table["error"] / table["price"]
    return table, len(frame), float(residual.std(ddof=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="print the full measurement")
    parser.add_argument("--root", default=".", help="repo root holding datasets/")
    args = parser.parse_args()
    root = Path(args.root)

    if not args.report:
        print("OFFSET_BIAS: Mapping[tuple[str, str], Mapping[float, tuple[float, float]]] = {")
    for league, relative in SOURCES:
        for market in ("spread", "total"):
            table, n, sd = measure(str(root / relative), market)
            if args.report:
                print(f"== {league} {market}  n={n}  residual sd {sd:.2f}")
                print(
                    table[
                        ["offset", "over_bias", "under_bias", "error", "price", "ev_error"]
                    ].to_string(index=False, float_format=lambda v: f"{v:+.4f}")
                )
                continue
            print(f"    # n={n} completed games, residual sd {sd:.2f}")
            print(f'    ("{league}", "{market}"): {{')
            rows = list(table.itertuples())
            for start in range(0, len(rows), 3):
                print(
                    "        "
                    + " ".join(
                        f"{r.offset}: ({r.over_bias:+.4f}, {r.under_bias:+.4f}),"
                        for r in rows[start : start + 3]
                    )
                )
            print("    },")
    if not args.report:
        print("}")


if __name__ == "__main__":
    main()
