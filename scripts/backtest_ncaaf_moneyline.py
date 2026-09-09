"""The NCAAF moneyline test the S2 exclusion is waiting on (STRATEGY_REVIEW S3.1).

    python scripts/backtest_ncaaf_moneyline.py \\
        --projections /tmp/lab_ncaaf/projections_blend-epa50.parquet \\
        --lines /tmp/cfbd_lines/games_lines.parquet

Walk-forward projections (the lab's out-of-sample ``p_home_win`` per game)
against CFBD's consensus closing moneylines, de-vigged multiplicatively:
for every game the model's side (raw, and anchored at the live w = 0.2)
where its edge over the fair probability clears ``--min-edge``, graded at
the closing price at one unit flat. Reported by price bucket — the longshot
buckets are where sixty per cent of the first live card sat — with the
claimed edge beside the realized one, per season, and Brier for the model,
the market and the anchored belief. No stake sizing: this is whether the
probabilities are worth anything at the price, not how much to bet.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from velocity.eval.metrics import brier_score
from velocity.wagering.odds import american_to_decimal

BUCKETS = [(-10_000, -300, "fav ≤ −300"), (-300, -150, "fav −300..−150"),
           (-150, 150, "±150"), (150, 300, "dog +150..+300"),
           (300, 1000, "dog +300..+1000"), (1000, 100_000, "dog ≥ +1000")]


def devig_pair(home_ml: float, away_ml: float) -> tuple[float, float]:
    """Multiplicative de-vig of a two-way moneyline → (p_home, p_away)."""
    ih, ia = 1.0 / american_to_decimal(home_ml), 1.0 / american_to_decimal(away_ml)
    total = ih + ia
    return ih / total, ia / total


def bucket_of(price: float) -> str:
    for lo, hi, label in BUCKETS:
        if lo < price <= hi:
            return label
    return "?"


def build_ledger(
    projections: pd.DataFrame, lines: pd.DataFrame, *, weight: float, min_edge: float
) -> pd.DataFrame:
    frame = projections.merge(
        lines[["game_id", "season", "home_ml", "away_ml", "home_score", "away_score"]]
        .rename(columns={"season": "line_season"}),
        on="game_id", how="inner",
    ).dropna(subset=["home_ml", "away_ml", "p_home_win", "home_score", "away_score"])
    rows = []
    for r in frame.to_dict("records"):
        fair_home, fair_away = devig_pair(float(r["home_ml"]), float(r["away_ml"]))
        p_home = float(r["p_home_win"])
        belief_home = fair_home + weight * (p_home - fair_home)
        home_won = float(r["home_score"]) > float(r["away_score"])
        for side, price, fair, belief, won in (
            ("home", float(r["home_ml"]), fair_home, belief_home, home_won),
            ("away", float(r["away_ml"]), fair_away, 1.0 - belief_home, not home_won),
        ):
            edge = belief - fair
            if edge < min_edge:
                continue
            payout = american_to_decimal(price) - 1.0
            rows.append({
                "season": int(r["season"]), "game_id": str(r["game_id"]), "side": side,
                "price": price, "bucket": bucket_of(price), "p_fair": fair,
                "p_belief": belief, "edge": edge, "won": won,
                "profit": payout if won else -1.0,
                # What the edge was worth if the belief were exact.
                "claimed_ev": belief * payout - (1.0 - belief),
            })
    return pd.DataFrame(rows)


def summarize(ledger: pd.DataFrame, by: str) -> pd.DataFrame:
    if ledger.empty:
        return pd.DataFrame()
    out = (ledger.groupby(by)
           .agg(n=("won", "size"), hit=("won", "mean"), roi=("profit", "mean"),
                claimed_ev=("claimed_ev", "mean"), mean_edge=("edge", "mean"),
                mean_price=("price", "mean"))
           .reset_index())
    # Standard error of the ROI at flat stakes.
    se = ledger.groupby(by)["profit"].std() / np.sqrt(ledger.groupby(by)["profit"].size())
    out["roi_se"] = se.to_numpy()
    out["t"] = out["roi"] / out["roi_se"].replace(0, np.nan)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="NCAAF moneyline walk-forward test")
    parser.add_argument("--projections", required=True)
    parser.add_argument("--lines", required=True)
    parser.add_argument("--min-edge", type=float, default=0.02)
    parser.add_argument("--eval-from", type=int, default=2018)
    args = parser.parse_args()

    projections = pd.read_parquet(args.projections)
    lines = pd.read_parquet(args.lines)
    lines["game_id"] = lines["game_id"].astype(str)
    projections["game_id"] = projections["game_id"].astype(str)
    projections = projections[projections["season"] >= args.eval_from]

    merged = projections.merge(lines[["game_id", "home_ml", "away_ml"]], on="game_id",
                               how="inner").dropna(subset=["home_ml", "away_ml"])
    fair = np.array([devig_pair(h, a)[0]
                     for h, a in zip(merged["home_ml"], merged["away_ml"], strict=True)])
    y = merged["home_win"].to_numpy(dtype=float)
    p = merged["p_home_win"].to_numpy(dtype=float)
    print(f"{len(merged)} games with a closing moneyline, seasons "
          f"{merged['season'].min()}–{merged['season'].max()}")
    print(f"Brier — model {brier_score(p, y):.4f} · market {brier_score(fair, y):.4f} · "
          f"anchored w=0.2 {brier_score(fair + 0.2 * (p - fair), y):.4f} · "
          f"w=0.5 {brier_score(fair + 0.5 * (p - fair), y):.4f}")

    for weight in (1.0, 0.2):
        ledger = build_ledger(projections, lines, weight=weight, min_edge=args.min_edge)
        label = "raw model" if weight == 1.0 else f"anchored w={weight:g}"
        roi, claimed = ledger["profit"].mean(), ledger["claimed_ev"].mean()
        print(f"\n=== {label}, edge ≥ {args.min_edge:g}: {len(ledger)} bets, "
              f"ROI {roi:+.3f} (claimed EV {claimed:+.3f}) ===")
        with pd.option_context("display.width", 160, "display.max_columns", None):
            by_bucket = summarize(ledger, "bucket")
            order = {label: i for i, (_lo, _hi, label) in enumerate(BUCKETS)}
            by_bucket = by_bucket.sort_values("bucket", key=lambda s: s.map(order))
            print(by_bucket.round(3).to_string(index=False))
            print("\nby season:")
            print(summarize(ledger, "season").round(3).to_string(index=False))


if __name__ == "__main__":
    main()
