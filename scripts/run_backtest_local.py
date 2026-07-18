"""Walk-forward backtest over committed local datasets (NFL EPA or NCAAF).

Reads real games + plays (+ optional lines) from a ``datasets/<league>/`` folder,
fits the full-strength model — opponent-adjusted **EPA ratings** for the NFL, or
ratings + pace (+ priors when a recruiting file is present) for NCAAF — and runs
the walk-forward engine, printing the calibration / CLV / ATS report.

Not a test (reads files, no network of its own beyond nothing). Run from the repo
root once your data is in place::

    python scripts/run_backtest_local.py --league nfl  --data datasets/nfl
    python scripts/run_backtest_local.py --league ncaaf --data datasets/ncaaf
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from velocity.backtest.engine import BacktestConfig, walk_forward
from velocity.features.team import fit_ratings, team_pace
from velocity.ingest.local import load_games, load_plays, read_data_file
from velocity.models.game_ncaaf import NCAAFGameModel, NCAAFModelConfig
from velocity.models.game_nfl import NFLGameModel, NFLModelConfig
from velocity.models.simulate import SimConfig
from velocity.store.schema import Lines


def _find(folder: Path, stem: str) -> Path | None:
    for ext in (".parquet", ".pq", ".csv"):
        candidate = folder / f"{stem}{ext}"
        if candidate.exists():
            return candidate
    return None


def _empty_lines() -> pd.DataFrame:
    cols = [
        "line_id", "game_id", "book", "market", "side",
        "price", "point", "timestamp", "is_closing",
    ]
    df = pd.DataFrame(columns=cols)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


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


def run(league: str, data_dir: str, n_sims: int, min_train_games: int) -> dict[str, float]:
    folder = Path(data_dir)
    games_path = _find(folder, "games")
    plays_path = _find(folder, "plays")
    if games_path is None or plays_path is None:
        found = [p.name for p in folder.glob("*") if p.suffix in (".csv", ".parquet", ".pq")]
        raise SystemExit(
            f"need games and plays files in {folder}/ (see datasets/README.md). Found: {found}"
        )

    games = load_games(games_path, league=league)
    plays = load_plays(plays_path)
    lines_path = _find(folder, "lines")
    if lines_path is not None:
        raw_lines = read_data_file(lines_path)
        raw_lines["timestamp"] = pd.to_datetime(raw_lines["timestamp"], errors="coerce")
        lines = Lines.validate(raw_lines)
    else:
        lines = _empty_lines()

    factory = _nfl_factory(n_sims) if league == "nfl" else _ncaaf_factory(n_sims)
    result = walk_forward(
        games, plays, lines, factory, BacktestConfig(min_train_games=min_train_games)
    )
    metrics = dict(result.metrics)
    metrics.update(_ats_vs_close(result.projections, games))
    return metrics


def _ats_vs_close(projections: pd.DataFrame, games: pd.DataFrame) -> dict[str, float]:
    """Against-the-spread / over-under record vs the closing lines carried in games.

    Uses the projection's fair spread/total against ``spread_line`` (nflverse
    convention: positive favors home) and ``total_line``. No line *archive*
    needed — this is the market-beating test for sides and totals.
    """
    out: dict[str, float] = {}
    cols = [c for c in ("spread_line", "total_line") if c in games.columns]
    if projections.empty or not cols:
        return out
    keep = ["game_id", "home_score", "away_score", *cols]
    df = projections.merge(games[keep], on="game_id", how="inner")
    margin = df["home_score"] - df["away_score"]
    total = df["home_score"] + df["away_score"]

    if "spread_line" in df.columns:
        model = -df["fair_spread"]
        cover = margin - df["spread_line"]
        pick_home = model > df["spread_line"]
        win = ((pick_home & (cover > 0)) | (~pick_home & (cover < 0))) & (cover != 0)
        decided = df["spread_line"].notna() & (model != df["spread_line"]) & (cover != 0)
        out["ats_spread"] = float(win[decided].mean()) if decided.any() else float("nan")
        out["ats_spread_n"] = float(decided.sum())
    if "total_line" in df.columns:
        model = df["fair_total"]
        diff = total - df["total_line"]
        pick_over = model > df["total_line"]
        win = ((pick_over & (diff > 0)) | (~pick_over & (diff < 0))) & (diff != 0)
        decided = df["total_line"].notna() & (model != df["total_line"]) & (diff != 0)
        out["ou_total"] = float(win[decided].mean()) if decided.any() else float("nan")
        out["ou_total_n"] = float(decided.sum())
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Local walk-forward backtest")
    parser.add_argument("--league", choices=["nfl", "ncaaf"], required=True)
    parser.add_argument("--data", required=True, help="folder with games/plays[/lines] files")
    parser.add_argument("--n-sims", type=int, default=10_000)
    parser.add_argument("--min-train-games", type=int, default=20)
    args = parser.parse_args()

    metrics = run(args.league, args.data, args.n_sims, args.min_train_games)
    print(f"=== Local backtest: {args.league.upper()} from {args.data} ===")
    for key, value in metrics.items():
        print(f"  {key:22s} {value:.4f}")
    if "brier" in metrics and "brier_baseline" in metrics:
        edge = metrics["brier_baseline"] - metrics["brier"]
        verdict = "informative" if edge > 0 else "no edge"
        print(f"\n  projection beats baseline by {edge:+.4f} Brier ({verdict})")
    if not np.isnan(metrics.get("line_clv_mean", np.nan)):
        print(f"  mean closing-line value: {metrics['line_clv_mean']:+.3f} points")
    if "ats_spread" in metrics:
        print(
            f"  vs closing spread: {metrics['ats_spread']:.1%} ATS on "
            f"{int(metrics['ats_spread_n'])} games (break-even 52.4%)"
        )
    if "ou_total" in metrics:
        print(
            f"  vs closing total:  {metrics['ou_total']:.1%} O/U on "
            f"{int(metrics['ou_total_n'])} games (break-even 52.4%)"
        )


if __name__ == "__main__":
    main()
