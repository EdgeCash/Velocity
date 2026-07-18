"""Real-data NFL backtest on nflverse schedules (scores-based ratings).

Full play-by-play (EPA) lives on nflverse release assets that some network
policies block; the schedule + final scores + closing lines live in the git tree
and are reachable. This script runs a genuine walk-forward on that reachable data
using the schedule-only scores rating (:mod:`velocity.features.scores`), and
reports the acceptance metrics that do not require line-movement history:
projection calibration/Brier vs the market baseline, plus an against-the-spread
record against the actual closing spread.

It is a script, not a test — it hits the network, so it stays out of the offline
gate. Run it from the repo root::

    python scripts/run_real_backtest.py --seasons 2023

When the EPA release assets are reachable, swap ``scores_factory`` for the EPA
path (fit_ratings on load_pbp) with no other change.
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from velocity.eval.metrics import brier_score, expected_calibration_error, log_loss
from velocity.features.scores import fit_scores_ratings
from velocity.ingest.nfl import NFLVERSE_SCHEDULE_URL, normalize_schedules
from velocity.models.game_scores import ScoresGameModel, ScoresModelConfig
from velocity.models.simulate import SimConfig
from velocity.util.seed import make_rng

# Closing betting columns carried in the nflverse schedule file.
_SPREAD_COL = "spread_line"  # nflverse convention: positive = home favored


def load_real_games(seasons: list[int]) -> pd.DataFrame:
    raw = pd.read_csv(NFLVERSE_SCHEDULE_URL, low_memory=False)
    raw = raw[raw["season"].isin(set(seasons))]
    games = normalize_schedules(raw)
    # Carry the closing spread through for the against-the-spread evaluation.
    spread = raw.set_index("game_id")[_SPREAD_COL] if _SPREAD_COL in raw.columns else None
    if spread is not None:
        games = games.merge(
            spread.rename("close_spread"), left_on="game_id", right_index=True, how="left"
        )
    return games


def run(seasons: list[int], min_train_games: int = 30, n_sims: int = 10_000) -> dict[str, float]:
    games = load_real_games(seasons)
    played = games.dropna(subset=["home_score", "away_score"])
    points = played[["season", "week"]].drop_duplicates().sort_values(["season", "week"])

    probs: list[float] = []
    outcomes: list[float] = []
    ats_results: list[str] = []

    for season, week in points.itertuples(index=False):
        train = played[
            (played["season"] < season) | ((played["season"] == season) & (played["week"] < week))
        ]
        if len(train) < min_train_games:
            continue
        model = ScoresGameModel(
            fit_scores_ratings(train), ScoresModelConfig(sim=SimConfig(n_sims=n_sims))
        )
        rng = make_rng(1729 + int(week))
        week_games = played[(played["season"] == season) & (played["week"] == week)]
        for g in week_games.itertuples(index=False):
            proj = model.project(
                g.home_team, g.away_team, neutral_site=bool(g.neutral_site), rng=rng
            )
            p_home = proj.p_home_win()
            actual_home = 1.0 if g.home_score > g.away_score else 0.0
            probs.append(p_home)
            outcomes.append(actual_home)

            close_spread = getattr(g, "close_spread", np.nan)
            if not pd.isna(close_spread):
                # nflverse `spread_line` is positive when the HOME team is
                # favored (its expected margin). Our model's expected home margin
                # is -fair_spread(). Bet the side the model favors more than the
                # market, then grade the actual margin against the closing number.
                market_home_margin = float(close_spread)
                model_home_margin = -proj.fair_spread()
                actual_margin = g.home_score - g.away_score
                home_cover = actual_margin - market_home_margin  # >0 → home covers
                if model_home_margin > market_home_margin:  # model likes home
                    ats_results.append(
                        "win" if home_cover > 0 else "loss" if home_cover < 0 else "push"
                    )
                elif model_home_margin < market_home_margin:  # model likes away
                    ats_results.append(
                        "win" if home_cover < 0 else "loss" if home_cover > 0 else "push"
                    )

    p = np.array(probs)
    y = np.array(outcomes)
    base = float(y.mean())
    decided = [r for r in ats_results if r in ("win", "loss")]
    wins = sum(r == "win" for r in decided)
    metrics = {
        "n_games": float(len(p)),
        "home_win_rate": base,
        "brier": brier_score(p, y),
        "brier_baseline": brier_score(np.full_like(p, base), y),
        "log_loss": log_loss(p, y),
        "calibration_error": expected_calibration_error(p, y),
        "ats_bets": float(len(decided)),
        "ats_win_rate": (wins / len(decided)) if decided else float("nan"),
    }
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Real-data NFL scores-based backtest")
    parser.add_argument("--seasons", type=int, nargs="+", default=[2023])
    parser.add_argument("--min-train-games", type=int, default=30)
    parser.add_argument("--n-sims", type=int, default=10_000)
    args = parser.parse_args()

    metrics = run(args.seasons, args.min_train_games, args.n_sims)
    print(f"=== Real-data backtest: seasons {args.seasons} (scores-based ratings) ===")
    for key, value in metrics.items():
        print(f"  {key:20s} {value:.4f}")
    edge = metrics["brier_baseline"] - metrics["brier"]
    print(f"\n  projection beats baseline by {edge:+.4f} Brier "
          f"({'informative' if edge > 0 else 'no edge'})")
    if not np.isnan(metrics["ats_win_rate"]):
        print(f"  against-the-spread vs close: {metrics['ats_win_rate']:.1%} "
              f"on {int(metrics['ats_bets'])} bets (break-even ≈ 52.4%)")


if __name__ == "__main__":
    main()
