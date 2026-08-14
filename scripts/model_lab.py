"""Benchmark NFL model variants through the identical walk-forward gate.

Runs every variant in :func:`velocity.backtest.lab.nfl_variants` (or a chosen
subset) over the committed datasets, and prints one comparison table: Brier and
calibration (is it a good forecaster?), flat ATS/O/U vs the close (is it a
bettable edge?), and the spread/total disagreement sweeps (does selectivity —
the market-regression idea — rescue an edge the flat record hides?).

    python scripts/model_lab.py --data datasets/nfl
    python scripts/model_lab.py --data datasets/nfl --variants baseline,recency-17

Results feed docs/MODEL_LAB.md; nothing is promoted into the live slate
without winning here first.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from velocity.backtest.engine import BacktestConfig, walk_forward
from velocity.backtest.lab import ats_ou_vs_close, disagreement_sweep, nfl_variants
from velocity.ingest.local import load_games, load_plays


def _find(folder: Path, stem: str) -> Path:
    for ext in (".parquet", ".pq", ".csv"):
        candidate = folder / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    raise SystemExit(f"need a {stem} file in {folder}/")


def _empty_lines() -> pd.DataFrame:
    cols = ["line_id", "game_id", "book", "market", "side",
            "price", "point", "timestamp", "is_closing"]
    df = pd.DataFrame(columns=cols)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="NFL model-variant benchmark")
    parser.add_argument("--data", default="datasets/nfl", help="folder with games+plays")
    parser.add_argument("--n-sims", type=int, default=4000)
    parser.add_argument("--min-train-games", type=int, default=20)
    parser.add_argument("--variants", default="",
                        help="comma-separated subset (default: all)")
    parser.add_argument("--eval-from", type=int, default=None,
                        help="only predict seasons ≥ this (training still sees earlier)")
    parser.add_argument("--train-window", type=int, default=0,
                        help="cap training to the trailing N seasons (0 = all history)")
    parser.add_argument("--sweeps", action="store_true",
                        help="print the per-variant disagreement sweeps")
    parser.add_argument("--out", help="optional folder for the comparison parquets")
    args = parser.parse_args()

    folder = Path(args.data)
    games = load_games(_find(folder, "games"), league="nfl")
    plays = load_plays(_find(folder, "plays"))
    lines = _empty_lines()
    if args.eval_from is not None:
        # Predict only recent seasons; the engine still trains on everything
        # before each predicted week (plays are passed unrestricted).
        games = games[games["season"] >= args.eval_from].reset_index(drop=True)

    chosen = nfl_variants(args.n_sims, schedule=games)
    if args.variants:
        names = [v.strip() for v in args.variants.split(",") if v.strip()]
        unknown = [n for n in names if n not in chosen]
        if unknown:
            raise SystemExit(f"unknown variant(s) {unknown}; have {sorted(chosen)}")
        chosen = {name: chosen[name] for name in names}

    seasons = sorted(games["season"].unique())
    print(f"model lab: {len(games)} games / {len(plays)} plays, seasons {seasons[0]}–{seasons[-1]}")
    print(f"variants: {list(chosen)}\n")

    def _windowed(factory, window: int):  # type: ignore[no-untyped-def]
        """Cap the factory's training frame to the trailing ``window`` seasons.

        Keeps per-week fit cost constant over a long evaluation and puts every
        variant on the identical data horizon.
        """
        if window <= 0:
            return factory

        def wrapped(train: pd.DataFrame):  # type: ignore[no-untyped-def]
            cutoff = int(train["season"].max()) - window + 1
            return factory(train[train["season"] >= cutoff])

        return wrapped

    rows = []
    sweeps = {}
    ledgers = {}
    for name, (train_kind, factory) in chosen.items():
        train_frame = games if train_kind == "games" else plays
        result = walk_forward(
            games, train_frame, lines, _windowed(factory, args.train_window),
            BacktestConfig(min_train_games=args.min_train_games),
        )
        row: dict[str, object] = {"variant": name}
        for key in ("n_games", "brier", "log_loss", "calibration_error"):
            if key in result.metrics:
                row[key] = result.metrics[key]
        row.update(ats_ou_vs_close(result.projections, games))
        rows.append(row)
        ledgers[name] = result.projections
        sweeps[name] = {
            market: disagreement_sweep(result.projections, games, market=market)
            for market in ("spread", "total")
        }
        print(f"  {name}: done ({int(row.get('n_games', 0))} games)")

    # Market blend (nfelo's market-regression insight): sweep
    # p_blend = w·model + (1−w)·market per ledger, weight chosen on the select
    # window and judged on the holdout — the accuracy ceiling for staking.
    from velocity.backtest.lab import market_blend_sweep

    blends = {name: market_blend_sweep(projections, games)
              for name, projections in ledgers.items()}

    table = pd.DataFrame(rows)
    with pd.option_context("display.width", 160, "display.max_columns", None):
        print("\n=== Variant comparison (walk-forward, out-of-sample) ===")
        print(table.to_string(index=False))
        if args.sweeps:
            for name, by_market in sweeps.items():
                for market, sweep in by_market.items():
                    print(f"\n--- {name} · {market} disagreement sweep ---")
                    print(sweep.to_string(index=False))
        for name, blend in blends.items():
            if blend.empty:
                continue
            print(f"\n--- {name} · market blend sweep (select ≤2019 / holdout) ---")
            print(blend.to_string(index=False))
            best = blend.loc[blend["brier_select"].idxmin()]
            print(f"    select-chosen weight w={best['weight']:.1f} → "
                  f"holdout Brier {best['brier_holdout']:.4f} "
                  f"(pure model {blend.loc[blend['weight'] == 1.0, 'brier_holdout'].iloc[0]:.4f}, "
                  f"pure market {blend.loc[blend['weight'] == 0.0, 'brier_holdout'].iloc[0]:.4f})")

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        table.to_parquet(out / "variant_comparison.parquet", index=False)
        for name, by_market in sweeps.items():
            for market, sweep in by_market.items():
                sweep.to_parquet(out / f"sweep_{name}_{market}.parquet", index=False)
        for name, projections in ledgers.items():
            projections.to_parquet(out / f"projections_{name}.parquet", index=False)
        for name, blend in blends.items():
            if not blend.empty:
                blend.to_parquet(out / f"blend_{name}.parquet", index=False)
        print(f"\nwrote comparison tables to {out}")


if __name__ == "__main__":
    main()
