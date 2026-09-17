"""The wager lab: score the curated list's rules against the close, walk-forward.

The model lab gates the projection; this gates the plays. It reads the
promoted chain's out-of-sample projections (``datasets/{league}/
projections_promoted.parquet`` — the model lab's ``projections_live-{league}-
promoted.parquet``, copied in when the chain changes) and the games frame's
closing lines and prices, and prints:

* the anchoring weight per market — the least-squares weight the close
  would put on the model's number (0 = the close knows it all);
* the calibration of a picked side's probability, raw and anchored;
* every standard rule's record — bets, win rate, ROI at the real juice,
  seasons cleared — and the per-season table of the rules asked for.

Usage::

    python scripts/wager_lab.py --league nfl
    python scripts/wager_lab.py --league ncaaf --rules total-under-4pt,total-either-6pt
    python scripts/wager_lab.py --league nfl --projections /path/to/projections.parquet --out DIR

Regenerating the projections after a promotion::

    python scripts/model_lab.py --league nfl --variants live-nfl-promoted \\
        --n-sims 4000 --train-window 4 --out /tmp/lab
    cp /tmp/lab/projections_live-nfl-promoted.parquet datasets/nfl/projections_promoted.parquet
    # college: --league ncaaf --data datasets/ncaaf --eval-population fbs,
    # variant live-ncaaf-promoted
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import pandas as pd
from velocity.backtest.wagers import (
    WagerRule,
    anchoring_weight,
    calibration_table,
    grade_frame,
    records_frame,
    rule_weight,
    score_rule,
    standard_rules,
)


def parse_rule(text: str, weights: dict[str, float]) -> WagerRule:
    """``market-side[-Npt][-edgeE[@wW]]`` → a rule; the weight defaults to the market's fit."""
    parts = text.split("-")
    market, side = parts[0], parts[1] if len(parts) > 1 else "either"
    points, edge, weight = 0.0, None, weights.get(market, 1.0)
    for part in parts[2:]:
        if part.endswith("pt"):
            points = float(part[:-2])
        elif part.startswith("edge"):
            spec = part[4:]
            if "@w" in spec:
                edge_text, weight_text = spec.split("@w")
                edge, weight = float(edge_text), float(weight_text)
            else:
                edge = float(spec)
    return WagerRule(market, side, min_points=points, min_edge=edge, weight=weight)


def main() -> None:
    parser = argparse.ArgumentParser(description="score selection rules against the close")
    parser.add_argument("--league", default="nfl", choices=["nfl", "ncaaf"])
    parser.add_argument("--projections", default=None,
                        help="walk-forward projections parquet (default: the committed "
                             "promoted chain's)")
    parser.add_argument("--games", default=None, help="games parquet (default: the league's)")
    parser.add_argument("--rules", default="",
                        help="comma-separated rules to print per season, e.g. "
                             "total-under-4pt,spread-either-6pt,total-either-edge0.02")
    parser.add_argument("--seasons-from", type=int, default=None,
                        help="score from this season on (default: every projected season)")
    parser.add_argument("--out", default=None, help="folder for the records parquet")
    args = parser.parse_args()

    folder = Path("datasets") / args.league
    projections = pd.read_parquet(args.projections or folder / "projections_promoted.parquet")
    games = pd.read_parquet(args.games or folder / "games.parquet")
    frame = grade_frame(projections, games)
    if args.seasons_from is not None:
        frame = frame[frame["season"] >= args.seasons_from].reset_index(drop=True)
    print(f"wager lab: {args.league}, {len(frame)} projected games with a close, "
          f"seasons {int(frame['season'].min())}–{int(frame['season'].max())}")

    weights = {m: anchoring_weight(frame, m) for m in ("spread", "total", "moneyline")}
    print("\nanchoring weight the close would put on the model (0 = it knows it all):")
    for market, w in weights.items():
        print(f"  {market:9s} {w:+.3f}")

    for market in ("spread", "total", "moneyline"):
        w = weights[market]
        if market == "moneyline" and math.isnan(w):  # no moneyline closes
            continue
        print(f"\n{market}: what a picked side's probability is worth (raw model)")
        print(calibration_table(frame, market).to_string(index=False))
        if not math.isnan(w) and w > 0:
            print(f"{market}: anchored at w={w:.2f}")
            print(calibration_table(frame, market, weight=w).to_string(index=False))

    records = [score_rule(frame, rule) for rule in standard_rules(args.league, weights)]
    table = records_frame(records)
    pd.set_option("display.width", 200)
    print("\n=== rules (ROI per unit at the real juice where the frame has it, else −110) ===")
    print(table.to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    for text in [t for t in args.rules.split(",") if t]:
        record = score_rule(frame, parse_rule(text, weights))
        print(f"\n--- {record.rule.name}: {record.n} bets, {record.win_rate:.3f}, "
              f"ROI {record.roi:+.3f}, {record.seasons_above_break_even}/{record.seasons} "
              f"seasons above break-even; the weight that maps its claim onto its "
              f"record: {rule_weight(frame, record.rule):.2f} ---")
        print(record.per_season.to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        table.to_parquet(out / f"wager_rules_{args.league}.parquet", index=False)
        print(f"\nwrote {out / f'wager_rules_{args.league}.parquet'}")


if __name__ == "__main__":
    main()
