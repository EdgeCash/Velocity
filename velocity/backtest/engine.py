"""Walk-forward backtest engine.

For each prediction week the engine:

1. Assembles the **point-in-time** training set — every play strictly before that
   week (earlier weeks this season and all prior seasons). The prediction week's
   own plays are physically excluded, so there is no lookahead.
2. Fits a model on that training set via a caller-supplied ``model_factory`` (the
   engine stays league-agnostic — pass an NFL or NCAAF factory).
3. Projects each game of the week and wagers it through the standard slate path
   (:func:`velocity.wagering.slate.build_slate`), which itself only ever reads
   pre-kickoff lines.
4. Grades the week's bets against the actual results and **compounds** the
   bankroll into the next week's staking.

The result carries the per-game projection ledger, the per-bet wager ledger, the
weekly bankroll curve, and a metrics summary (calibration, CLV, Brier, ROI,
drawdown) — the full acceptance report for a phase.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Protocol

import numpy as np
import pandas as pd

from velocity.eval.metrics import (
    brier_score,
    clv_stats,
    expected_calibration_error,
    hit_rate,
    log_loss,
    max_drawdown,
    roi,
)
from velocity.models.game_nfl import GameProjection
from velocity.util.seed import DEFAULT_SEED, make_rng
from velocity.wagering.slate import SlateConfig, build_slate

# A model exposes just this: project a matchup into a priced GameProjection.
ModelFactory = Callable[[pd.DataFrame], "ProjectionModel"]


class ProjectionModel(Protocol):
    """Structural protocol: anything with ``project`` works as a backtest model."""

    def project(
        self,
        home_team: str,
        away_team: str,
        *,
        neutral_site: bool = False,
        rng: np.random.Generator | None = None,
    ) -> GameProjection: ...


@dataclass(frozen=True)
class BacktestConfig:
    """Walk-forward knobs."""

    slate: SlateConfig = field(default_factory=SlateConfig)
    min_train_games: int = 6
    starting_bankroll: float = 100.0
    seed: int = DEFAULT_SEED


@dataclass(frozen=True)
class BacktestResult:
    """Everything a phase's acceptance review needs, in four frames + a summary."""

    projections: pd.DataFrame
    ledger: pd.DataFrame
    bankroll_curve: pd.DataFrame
    metrics: dict[str, float]


def walk_forward(
    games: pd.DataFrame,
    plays: pd.DataFrame,
    lines: pd.DataFrame,
    model_factory: ModelFactory,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """Run the walk-forward backtest and return its full result."""
    config = config or BacktestConfig()
    played = games.dropna(subset=["home_score", "away_score"])
    points = played[["season", "week"]].drop_duplicates().sort_values(["season", "week"])

    bankroll = config.starting_bankroll
    proj_rows: list[dict[str, object]] = []
    ledger_frames: list[pd.DataFrame] = []
    curve_rows: list[dict[str, object]] = []

    for season, week in points.itertuples(index=False):
        before_week = (plays["season"] == season) & (plays["week"] < week)
        train = plays[(plays["season"] < season) | before_week]
        if train["game_id"].nunique() < config.min_train_games:
            continue

        model = model_factory(train)
        week_games = played[(played["season"] == season) & (played["week"] == week)]
        rng = make_rng(config.seed + int(week))

        projections: dict[str, GameProjection] = {}
        for g in week_games.itertuples(index=False):
            proj = model.project(
                g.home_team, g.away_team, neutral_site=bool(g.neutral_site), rng=rng
            )
            projections[g.game_id] = proj
            home_win = 1.0 if g.home_score > g.away_score else 0.0
            proj_rows.append(
                {
                    "season": season,
                    "week": week,
                    "game_id": g.game_id,
                    "p_home_win": proj.p_home_win(),
                    "home_win": home_win,
                    "fair_spread": proj.fair_spread(),
                    "fair_total": proj.fair_total(),
                }
            )

        week_lines = lines[lines["game_id"].isin(set(week_games["game_id"]))]
        slate_cfg = replace(config.slate, starting_bankroll=bankroll)
        week_log = build_slate(projections, week_lines, week_games, slate_cfg)
        settled = week_log.settle(week_games, starting_bankroll=bankroll)
        if not settled.empty:
            settled = settled.copy()
            settled.insert(0, "week", week)
            settled.insert(0, "season", season)
            ledger_frames.append(settled)
            graded = settled[settled["result"] != "pending"]
            if not graded.empty:
                bankroll = float(graded["bankroll"].iloc[-1])

        curve_rows.append({"season": season, "week": week, "bankroll": bankroll})

    projections_df = pd.DataFrame(proj_rows)
    ledger_df = (
        pd.concat(ledger_frames, ignore_index=True) if ledger_frames else _empty_ledger()
    )
    curve_df = pd.DataFrame(curve_rows)
    metrics = _summarize(projections_df, ledger_df, curve_df, config.starting_bankroll)
    return BacktestResult(
        projections=projections_df,
        ledger=ledger_df,
        bankroll_curve=curve_df,
        metrics=metrics,
    )


def _empty_ledger() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "season", "week", "game_id", "market", "side", "book", "price",
            "point", "stake", "p_model", "result", "profit", "bankroll",
            "price_clv", "line_clv",
        ]
    )


def _summarize(
    projections: pd.DataFrame,
    ledger: pd.DataFrame,
    curve: pd.DataFrame,
    starting_bankroll: float,
) -> dict[str, float]:
    metrics: dict[str, float] = {}
    if not projections.empty:
        p = projections["p_home_win"].to_numpy()
        y = projections["home_win"].to_numpy()
        metrics["n_games"] = float(len(projections))
        metrics["brier"] = brier_score(p, y)
        metrics["log_loss"] = log_loss(p, y)
        metrics["calibration_error"] = expected_calibration_error(p, y)
        # Market baseline: always predicting the base rate of home wins.
        base = float(y.mean())
        metrics["brier_baseline"] = brier_score(np.full_like(p, base), y)

    graded = ledger[ledger["result"].isin(["win", "loss", "push"])] if not ledger.empty else ledger
    metrics["n_bets"] = float(len(graded))
    if not graded.empty:
        metrics["roi"] = roi(graded["profit"], graded["stake"])
        metrics["hit_rate"] = hit_rate(graded["result"])
        # Price CLV lives on moneylines; spreads/totals move on the number, so
        # report line CLV too, plus the overall rate of beating the close.
        metrics["price_clv_mean"] = clv_stats(graded["price_clv"])["mean_clv"]
        metrics["line_clv_mean"] = clv_stats(graded["line_clv"])["mean_clv"]
        # Prefer line CLV (spreads/totals move on the number, not the -110 price);
        # fall back to price CLV for moneylines, where the number is the price.
        beat = graded["line_clv"].where(graded["line_clv"].notna(), graded["price_clv"])
        metrics["pct_beat_close"] = clv_stats(beat)["pct_positive"]
    if not curve.empty:
        final = float(curve["bankroll"].iloc[-1])
        metrics["final_bankroll"] = final
        metrics["total_return"] = final / starting_bankroll - 1.0
        metrics["max_drawdown"] = max_drawdown(
            np.concatenate([[starting_bankroll], curve["bankroll"].to_numpy()])
        )
    return metrics
