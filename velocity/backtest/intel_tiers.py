"""Tier backtest — does context-confirmed +EV actually outperform?

The intelligence layer's honest gap (docs/INTEL.md §5) is that its tiers
organize *evidence* without *measured* edge. This module measures it: replay
the walk-forward backtest, and for every week take the model's pick against
the closing number carried in the games frame (``spread_line`` /
``total_line`` — the same market-beating test as ``_ats_vs_close`` and the
totals sweep), gate it exactly as the live slate would (``evaluate()`` at a
standard two-way price), judge it through the intelligence layer with a
**point-in-time** context library (``as_of`` = the week's first kickoff), and
grade it against the realized score.

The output slices the record three ways:

* **by tier** — the layer's own A/B/C verdicts (no line archive → the fair
  probability is the de-vigged standard price, so the edge component is
  driven by the model's probability, exactly as the tiers would see live);
* **by context bucket** — confirming / neutral / contradicting on the
  context score *alone*, the cleaner scientific cut (tiers blend edge in);
* **by tier × season** — the robustness view (the 7-of-10 discipline).

No injuries history exists yet, so the injury/availability signals abstain
throughout — historically the layer runs on matchup, form, and rest. Flat
one-unit stakes: this is an evidence backtest in the BACKTEST_*.md genre,
not a bankroll simulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from velocity.backtest.engine import ModelFactory
from velocity.intel.context import ContextLibrary
from velocity.intel.score import IntelConfig, assess
from velocity.intel.signals import default_game_signals
from velocity.util.seed import DEFAULT_SEED, make_rng
from velocity.wagering.bet_log import Bet
from velocity.wagering.edge import evaluate
from velocity.wagering.slate import model_probability

# Context-score cuts for the confirming / neutral / contradicting buckets.
CONTEXT_CONFIRM = 0.15
CONTEXT_CONTRA = -0.15


@dataclass(frozen=True)
class TierBacktestConfig:
    """Knobs for the tier replay."""

    markets: tuple[str, ...] = ("spread", "total")
    # The synthetic two-way price for every pick. −110/−110 de-vigs to 0.5,
    # so the EV gate reads: p_model must clear ~52.4% plus the edge floor —
    # the same bar the docs' break-even lines use.
    price: float = -110.0
    min_edge: float = 0.02
    min_train_games: int = 20
    seed: int = DEFAULT_SEED
    form_games: int = 5
    intel: IntelConfig = field(default_factory=IntelConfig)


@dataclass(frozen=True)
class TierBacktestResult:
    """The per-pick record plus the three summary slices."""

    picks: pd.DataFrame
    by_tier: pd.DataFrame
    by_context: pd.DataFrame
    by_tier_season: pd.DataFrame


def _pick_for(
    proj: object, market: str, spread_line: float | None, total_line: float | None
) -> tuple[str, float] | None:
    """The model's side and side-perspective point vs the close (None: no pick).

    Conventions match ``_ats_vs_close``: ``spread_line`` is the expected home
    margin (positive = home favored), so the home side's handicap is its
    negation; ``fair_spread`` is negative when home is favored.
    """
    if market == "spread":
        if spread_line is None or pd.isna(spread_line):
            return None
        model_margin = -float(proj.fair_spread())  # type: ignore[attr-defined]
        if model_margin == spread_line:
            return None
        side = "home" if model_margin > spread_line else "away"
        point = -float(spread_line) if side == "home" else float(spread_line)
        return side, point
    if market == "total":
        if total_line is None or pd.isna(total_line):
            return None
        fair = float(proj.fair_total())  # type: ignore[attr-defined]
        if fair == total_line:
            return None
        return ("over" if fair > total_line else "under"), float(total_line)
    raise ValueError(f"unknown market {market!r}")


def tier_backtest(
    games: pd.DataFrame,
    train_frame: pd.DataFrame,
    model_factory: ModelFactory,
    config: TierBacktestConfig | None = None,
    *,
    context_plays: pd.DataFrame | None = None,
) -> TierBacktestResult:
    """Walk forward, pick vs the close, judge each pick, grade it.

    ``train_frame`` is whatever the factory fits on (plays for the EPA
    ratings, games for the scores fit) — sliced strictly before each
    prediction week, exactly as :func:`velocity.backtest.engine.walk_forward`
    slices it. ``context_plays`` feeds the context library's EPA units (pass
    the plays frame when one exists; the library falls back to scoring form
    without it).
    """
    config = config or TierBacktestConfig()
    played = games.dropna(subset=["home_score", "away_score"])
    points = played[["season", "week"]].drop_duplicates().sort_values(["season", "week"])
    signals = default_game_signals()
    # The fair probability of a −110/−110 pair, computed once: both sides
    # de-vig to one half, and the EV gate does the rest.
    p_fair = 0.5

    rows: list[dict[str, object]] = []
    for season, week in points.itertuples(index=False):
        before_week = (train_frame["season"] == season) & (train_frame["week"] < week)
        train = train_frame[(train_frame["season"] < season) | before_week]
        if train["game_id"].nunique() < config.min_train_games:
            continue

        model = model_factory(train)
        week_games = played[(played["season"] == season) & (played["week"] == week)]
        rng = make_rng(config.seed + int(week))

        kickoffs = pd.to_datetime(week_games.get("kickoff"), errors="coerce")
        as_of = kickoffs.min() if kickoffs.notna().any() else None
        library = ContextLibrary.build(
            games, context_plays, as_of=as_of, form_games=config.form_games
        )

        for g in week_games.itertuples(index=False):
            proj = model.project(
                g.home_team, g.away_team,
                neutral_site=bool(getattr(g, "neutral_site", False)), rng=rng,
            )
            ctx = library.context_for(
                str(g.game_id), g.away_team, g.home_team,
                getattr(g, "kickoff", None),
            )
            for market in config.markets:
                pick = _pick_for(
                    proj, market,
                    getattr(g, "spread_line", None), getattr(g, "total_line", None),
                )
                if pick is None:
                    continue
                side, point = pick
                p_model = model_probability(proj, market, side, point)
                if p_model is None:  # projection can't price this market
                    continue
                gate = evaluate(p_model, config.price, p_fair, min_edge=config.min_edge)
                if not gate.qualifies:
                    continue
                bet = Bet(
                    game_id=str(g.game_id), market=market, side=side, book="close",
                    price=config.price, stake=1.0, p_model=p_model, point=point,
                    p_fair=p_fair,
                )
                verdict = assess(bet, ctx, signals, config.intel)
                result, profit = bet.grade(float(g.home_score), float(g.away_score))
                rows.append(
                    {
                        "season": int(season),
                        "week": int(week),
                        "game_id": str(g.game_id),
                        "market": market,
                        "side": side,
                        "point": point,
                        "p_model": round(p_model, 4),
                        "edge_score": round(verdict.edge_score, 4),
                        "context_score": round(verdict.context_score, 4),
                        "conviction": round(verdict.score, 4),
                        "tier": verdict.tier,
                        "result": result,
                        "profit": profit,
                        "rationale": verdict.rationale(),
                    }
                )

    picks = pd.DataFrame(rows, columns=[
        "season", "week", "game_id", "market", "side", "point", "p_model",
        "edge_score", "context_score", "conviction", "tier", "result", "profit",
        "rationale",
    ])
    return TierBacktestResult(
        picks=picks,
        by_tier=summarize(picks, "tier"),
        by_context=summarize(picks.assign(context=_context_bucket(picks)), "context"),
        by_tier_season=tier_by_season(picks),
    )


def _context_bucket(picks: pd.DataFrame) -> pd.Series:
    """Confirming / neutral / contradicting, on the context score alone."""
    if picks.empty:
        return pd.Series(dtype=object)
    score = picks["context_score"]
    out = pd.Series("neutral", index=picks.index)
    out[score >= CONTEXT_CONFIRM] = "confirming"
    out[score <= CONTEXT_CONTRA] = "contradicting"
    return out


def summarize(picks: pd.DataFrame, key: str) -> pd.DataFrame:
    """Per-group record: bets, win rate (pushes excluded), flat ROI, mean context.

    Break-even at −110 is 52.38%; ``roi`` is profit per unit staked at flat
    one-unit stakes.
    """
    columns = ["bets", "win_rate", "roi", "mean_context", "mean_conviction"]
    if picks.empty:
        return pd.DataFrame(columns=columns)
    grouped = []
    for value, part in picks.groupby(key, sort=True):
        decided = part[part["result"].isin(["win", "loss"])]
        wins = int((decided["result"] == "win").sum())
        grouped.append({
            key: value,
            "bets": len(part),
            "win_rate": wins / len(decided) if len(decided) else float("nan"),
            "roi": float(part["profit"].sum()) / len(part),
            "mean_context": float(part["context_score"].mean()),
            "mean_conviction": float(part["conviction"].mean()),
        })
    return pd.DataFrame(grouped, columns=[key, *columns])


def tier_by_season(picks: pd.DataFrame) -> pd.DataFrame:
    """Win rate per (season, tier) — the robustness view, pushes excluded."""
    if picks.empty:
        return pd.DataFrame(columns=["season", "tier", "bets", "win_rate"])
    decided = picks[picks["result"].isin(["win", "loss"])]
    out = (
        decided.assign(win=(decided["result"] == "win"))
        .groupby(["season", "tier"])["win"].agg(["size", "mean"])
        .rename(columns={"size": "bets", "mean": "win_rate"})
        .reset_index()
    )
    return out
