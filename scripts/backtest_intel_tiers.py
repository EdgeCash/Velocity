"""Tier backtest over committed local datasets — the intel layer's evidence run.

Replays the walk-forward backtest (same factories and slicing as
``run_backtest_local.py``), takes the model's pick against the closing number
in the games frame for every week, gates it exactly like the live EV gate,
judges it through the intelligence layer point-in-time, and grades it against
the realized score. Prints the record by tier, by context bucket, and the
per-season robustness view. See ``velocity/backtest/intel_tiers.py`` and
``docs/INTEL.md`` §6.

Not a test (reads files). Run from the repo root::

    python scripts/backtest_intel_tiers.py --league nfl   --data datasets/nfl
    python scripts/backtest_intel_tiers.py --league ncaaf --data datasets/ncaaf
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from velocity.backtest.intel_tiers import TierBacktestConfig, tier_backtest
from velocity.features.scores import fit_scores_ratings
from velocity.features.team import fit_ratings, team_pace
from velocity.ingest.local import load_games, load_plays
from velocity.models.game_ncaaf import NCAAFGameModel, NCAAFModelConfig
from velocity.models.game_nfl import NFLGameModel, NFLModelConfig
from velocity.models.game_scores import ScoresGameModel, ScoresModelConfig
from velocity.models.simulate import SimConfig

BREAK_EVEN = 0.5238  # win rate that returns zero at −110


def _find(folder: Path, stem: str) -> Path | None:
    for ext in (".parquet", ".pq", ".csv"):
        candidate = folder / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    return None


def _nfl_factory(n_sims: int):
    def factory(train_plays: pd.DataFrame) -> NFLGameModel:
        return NFLGameModel(fit_ratings(train_plays), NFLModelConfig(sim=SimConfig(n_sims=n_sims)))

    return factory


def _ncaaf_factory(n_sims: int):
    sim = SimConfig(sd_margin=16.0, sd_total=13.6, n_sims=n_sims)

    def factory(train_plays: pd.DataFrame) -> NCAAFGameModel:
        return NCAAFGameModel(
            fit_ratings(train_plays), team_pace(train_plays), NCAAFModelConfig(sim=sim)
        )

    return factory


def _scores_factory(n_sims: int, league: str):
    sim = (
        SimConfig(sd_margin=17.0, sd_total=16.0, n_sims=n_sims)
        if league == "ncaaf"
        else SimConfig(n_sims=n_sims)
    )

    def factory(train_games: pd.DataFrame) -> ScoresGameModel:
        return ScoresGameModel(fit_scores_ratings(train_games), ScoresModelConfig(sim=sim))

    return factory


def _print_table(title: str, frame: pd.DataFrame) -> None:
    print(f"\n  {title} (break-even {BREAK_EVEN:.1%} at -110):")
    if frame.empty:
        print("    no bets")
        return
    for row in frame.to_dict("records"):
        label = str(row.get("tier", row.get("context", "")))
        mark = ""
        if pd.notna(row["win_rate"]):
            mark = " ✅" if row["win_rate"] > BREAK_EVEN else ""
        print(
            f"    {label:14s} {row['bets']:>5d} bets   "
            f"win {row['win_rate']:.1%}   roi {row['roi']:+.3f}   "
            f"ctx {row['mean_context']:+.2f}{mark}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Intelligence-layer tier backtest")
    parser.add_argument("--league", choices=["nfl", "ncaaf"], required=True)
    parser.add_argument("--data", required=True, help="folder with games/plays files")
    parser.add_argument("--n-sims", type=int, default=10_000)
    parser.add_argument("--min-train-games", type=int, default=20)
    parser.add_argument(
        "--rating", choices=["epa", "scores"], default="epa",
        help="epa needs a plays file; scores fits on games only",
    )
    parser.add_argument("--min-edge", type=float, default=0.02)
    parser.add_argument("--out", help="folder to persist the per-pick parquet")
    args = parser.parse_args()

    folder = Path(args.data)
    games_path = _find(folder, "games")
    if games_path is None:
        raise SystemExit(f"need a games file in {folder}/ (see datasets/README.md)")
    games = load_games(games_path, league=args.league)

    plays_path = _find(folder, "plays")
    plays = load_plays(plays_path) if plays_path is not None else None
    if args.rating == "scores":
        train_frame, factory = games, _scores_factory(args.n_sims, args.league)
    else:
        if plays is None:
            raise SystemExit(f"--rating epa needs a plays file in {folder}/")
        train_frame = plays
        factory = (
            _nfl_factory(args.n_sims) if args.league == "nfl"
            else _ncaaf_factory(args.n_sims)
        )

    config = TierBacktestConfig(
        min_edge=args.min_edge, min_train_games=args.min_train_games
    )
    result = tier_backtest(games, train_frame, factory, config, context_plays=plays)

    picks = result.picks
    print(f"=== Intel tier backtest: {args.league.upper()} from {args.data} — "
          f"{len(picks)} qualifying picks ===")
    _print_table("by tier", result.by_tier)
    _print_table("by context bucket (context score alone)", result.by_context)

    if not result.by_tier_season.empty:
        print("\n  per-season robustness (seasons above break-even, by tier):")
        for tier, part in result.by_tier_season.groupby("tier"):
            above = int((part["win_rate"] > BREAK_EVEN).sum())
            print(f"    tier {tier}: {above}/{len(part)} seasons")

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"intel_tiers_{args.league}.parquet"
        picks.to_parquet(dest, index=False)
        print(f"\nwrote {len(picks)} picks to {dest}")


if __name__ == "__main__":
    main()
