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
from velocity.models.simulate import (
    DEFAULT_SD_MARGIN,
    DEFAULT_SD_TOTAL,
    NCAAF_SD_MARGIN,
    NCAAF_SD_TOTAL,
    SimConfig,
)

# (league, dataset) pairs the table covers. A league whose games carry no
# closing numbers has nothing to measure and does not belong here.
SOURCES: tuple[tuple[str, str], ...] = (
    ("nfl", "datasets/nfl/games.parquet"),
    ("ncaaf", "datasets/ncaaf/games.parquet"),
)
MAX_OFFSET = 28.5
# The sims being gated, at their promoted constants. The table is a statement
# about THESE, so a league whose sd moves needs its table regenerated — which
# is what the freshness test enforces.
#
# 8,000 draws a game: the banked number is a mean over thousands of games, so
# its Monte Carlo error is under 1e-4, well inside the 5e-4 the freshness test
# allows. The seed is fixed inside `simulated_tails`, so two runs on the same
# datasets agree exactly and a constant never moves for no reason.
SIMS: dict[str, SimConfig] = {
    "nfl": SimConfig(n_sims=8000, sd_margin=DEFAULT_SD_MARGIN,
                     sd_total=DEFAULT_SD_TOTAL),
    "ncaaf": SimConfig(n_sims=8000, sd_margin=NCAAF_SD_MARGIN,
                       sd_total=NCAAF_SD_TOTAL),
}


def measure(path: str, market: str, league: str) -> tuple[pd.DataFrame, int, float]:
    """The calibration table for one dataset and market, plus its n and sd."""
    games = pd.read_parquet(path)
    column = "spread_line" if market == "spread" else "total_line"
    frame = games.dropna(
        subset=["home_score", "away_score", "spread_line", "total_line"])
    outcome = (
        frame["home_score"] - frame["away_score"]
        if market == "spread"
        else frame["home_score"] + frame["away_score"]
    )
    residual = (outcome - frame[column]).astype(float)
    table = residual_calibration(
        games, market, max_offset=MAX_OFFSET, reference=SIMS[league])
    # The cheaper side of the contract is the one a deep rung is priced at, so
    # it is the denominator that matters for what an error costs.
    table["price"] = table[["model_over", "model_under"]].min(axis=1)
    table["ev_error"] = table["error"] / table["price"]
    return table, len(frame), float(residual.std(ddof=1))


LITERAL_HEAD = "OFFSET_BIAS: Mapping[tuple[str, str], Mapping[float, tuple[float, float]]] = {"
TARGET = Path("velocity/eval/ladders.py")


def literal(root: Path) -> str:
    """The whole ``OFFSET_BIAS`` assignment, as it should appear in the module."""
    lines = [LITERAL_HEAD]
    for league, relative in SOURCES:
        for market in ("spread", "total"):
            table, n, sd = measure(str(root / relative), market, league)
            lines.append(f"    # n={n} completed games, residual sd {sd:.2f}")
            lines.append(f'    ("{league}", "{market}"): {{')
            rows = list(table.itertuples())
            for start in range(0, len(rows), 3):
                lines.append(
                    "        "
                    + " ".join(
                        f"{r.offset}: ({r.over_bias:+.4f}, {r.under_bias:+.4f}),"
                        for r in rows[start : start + 3]
                    )
                )
            lines.append("    },")
    lines.append("}")
    return "\n".join(lines) + "\n"


def rewrite(root: Path) -> bool:
    """Replace the literal in ladders.py in place. True when the file changed.

    Wholesale replacement is safe ONLY because the generated block holds
    nothing hand-written: the notes that used to live inside it now sit above
    the assignment, where this cannot reach them. Putting a note back inside
    means losing it on the next refresh.
    """
    path = root / TARGET
    source = path.read_text()
    start = source.index(LITERAL_HEAD)
    end = source.index("\n}\n", start) + len("\n}\n")
    fresh = literal(root)
    if source[start:end] == fresh:
        return False
    path.write_text(source[:start] + fresh + source[end:])
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="print the full measurement")
    parser.add_argument("--write", action="store_true",
                        help="rewrite velocity/eval/ladders.py in place")
    parser.add_argument("--root", default=".", help="repo root holding datasets/")
    args = parser.parse_args()
    root = Path(args.root)

    if args.report:
        for league, relative in SOURCES:
            for market in ("spread", "total"):
                table, n, sd = measure(str(root / relative), market, league)
                print(f"== {league} {market}  n={n}  residual sd {sd:.2f}")
                print(
                    table[
                        ["offset", "over_bias", "under_bias", "error", "price", "ev_error"]
                    ].to_string(index=False, float_format=lambda v: f"{v:+.4f}")
                )
        return

    if args.write:
        changed = rewrite(root)
        print(f"{TARGET}: {'rewritten' if changed else 'already current'}")
        return

    print(literal(root), end="")


if __name__ == "__main__":
    main()
