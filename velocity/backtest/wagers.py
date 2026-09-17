"""The wager lab: score selection rules on walk-forward projections, against the close.

The model lab (:mod:`velocity.backtest.lab`) gates what goes *into* the
projection. This is the same discipline for what comes *out* of it — the
curated list of plays — and it scores a rule the only way a rule can be
scored: every game the promoted chain projected out of sample, the closing
line it would have faced, the price it would have paid, and what happened.

A :class:`WagerRule` names a market, a side and a threshold in points of
disagreement with the close (the unit the college totals record is kept in,
docs/BACKTEST_NCAAF.md), optionally an edge on the market-anchored
probability instead. :func:`score_rule` returns the record — bets, win
rate, ROI at the real juice where the games frame carries it (−110
otherwise), and the per-season table with how many seasons cleared the
break-even rate — because a rule that pays in 5 of 11 seasons is a coin
whatever its aggregate says.

:func:`anchoring_weight` is the least-squares weight the closing line would
put on the model's number (docs/PROJECTION_AUDIT.md §6's information weight);
:func:`calibration_table` shows what a picked side's probability is worth
against the close, raw and anchored. Both are what the live slate's
``model_weight`` and ``prob_shrink`` ought to be fitted from.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from velocity.wagering.odds import american_to_prob

BREAK_EVEN_110 = 110.0 / 210.0  # the win rate that returns the stake at −110 (52.4%)
STANDARD_PRICE = -110.0

# The price columns the NFL games frame carries (velocity.ingest.nfl
# SCHEDULE_EXTRA_COLUMNS) and the side each belongs to; a frame without them
# (college) is priced at −110 throughout.
_PRICE_COLUMNS = {
    ("spread", "home"): "home_spread_odds", ("spread", "away"): "away_spread_odds",
    ("total", "over"): "over_odds", ("total", "under"): "under_odds",
    ("moneyline", "home"): "home_moneyline", ("moneyline", "away"): "away_moneyline",
}


@dataclass(frozen=True)
class WagerRule:
    """A selection rule: which market, which side, how much disagreement.

    ``market`` is ``spread``, ``total`` or ``moneyline``; ``side`` is
    ``either`` (the side the model leans to), ``over``/``under``,
    ``home``/``away``. ``min_points`` is the model's disagreement with the
    close in points, in the side's direction (the totals record's unit);
    ``min_edge`` is instead a floor on the anchored probability's edge over
    the market (``weight`` anchoring the model's probability to the
    close's, 1.0 = the raw model). Moneylines are edge rules only.
    """

    market: str
    side: str = "either"
    min_points: float = 0.0
    min_edge: float | None = None
    weight: float = 1.0

    @property
    def name(self) -> str:
        parts = [self.market, self.side]
        if self.min_points > 0:
            parts.append(f"{self.min_points:g}pt")
        if self.min_edge is not None:
            parts.append(f"edge{self.min_edge:g}@w{self.weight:.2f}")
        return "-".join(parts)


def _normal_sf(z: np.ndarray | pd.Series) -> np.ndarray:
    erf = np.vectorize(math.erf)
    values = np.asarray(z, dtype=float)
    return np.asarray(0.5 * (1.0 - erf(values / math.sqrt(2.0))), dtype=float)


def _profit(price: np.ndarray) -> np.ndarray:
    """Profit per unit staked on a winning bet at an American ``price``."""
    price = np.asarray(price, dtype=float)
    return np.where(price < 0, 100.0 / np.abs(price), price / 100.0)


def grade_frame(projections: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """The projections joined to their games: lines, prices, outcomes, model numbers.

    Keeps the games with both a spread and a total close and a final score.
    Adds ``mu_margin``/``mu_total``, ``margin``/``total``, ``home_cover`` and
    ``over_hit`` (1/0, null on a push), the six price columns (−110 where the
    frame has none) and, where the moneylines exist, the market's de-vigged
    home probability ``q_home``.
    """
    cols = ["game_id", "home_score", "away_score", "spread_line", "total_line"]
    cols += [c for c in set(_PRICE_COLUMNS.values()) | {"neutral_site"} if c in games.columns]
    frame = projections.merge(games[cols], on="game_id", how="inner")
    frame = frame.dropna(subset=["home_score", "away_score", "spread_line", "total_line"]).copy()
    frame["mu_margin"] = frame["mu_home"] - frame["mu_away"]
    frame["mu_total"] = frame["mu_home"] + frame["mu_away"]
    frame["margin"] = frame["home_score"] - frame["away_score"]
    frame["total"] = frame["home_score"] + frame["away_score"]
    frame["home_cover"] = np.where(frame["margin"] > frame["spread_line"], 1.0,
                                   np.where(frame["margin"] < frame["spread_line"], 0.0, np.nan))
    frame["over_hit"] = np.where(frame["total"] > frame["total_line"], 1.0,
                                 np.where(frame["total"] < frame["total_line"], 0.0, np.nan))
    for col in _PRICE_COLUMNS.values():
        if col not in frame.columns:
            frame[col] = np.nan
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
        if col not in ("home_moneyline", "away_moneyline"):
            frame[col] = frame[col].fillna(STANDARD_PRICE)
    priced = frame["home_moneyline"].notna() & frame["away_moneyline"].notna()
    q_home = np.full(len(frame), np.nan)
    if priced.any():
        ph = frame.loc[priced, "home_moneyline"].map(american_to_prob).to_numpy(dtype=float)
        pa = frame.loc[priced, "away_moneyline"].map(american_to_prob).to_numpy(dtype=float)
        q_home[priced.to_numpy()] = ph / (ph + pa)
    frame["q_home"] = q_home
    return frame.reset_index(drop=True)


def anchoring_weight(frame: pd.DataFrame, market: str) -> float:
    """The least-squares weight on (model − close) that best predicts (actual − close).

    0 means the close already knows everything the model does; 1 means the
    model's number should replace the close. The margin's is fitted on the
    spread, the total's on the total; the moneyline's on the de-vigged win
    probability, when the frame carries it (else nan).
    """
    if market == "spread":
        actual, close, model = frame["margin"], frame["spread_line"], frame["mu_margin"]
    elif market == "total":
        actual, close, model = frame["total"], frame["total_line"], frame["mu_total"]
    elif market == "moneyline":
        ok = frame["q_home"].notna()
        if not ok.any():
            return float("nan")
        actual = (frame.loc[ok, "home_win"].astype(float)
                  if "home_win" in frame.columns
                  else (frame.loc[ok, "margin"] > 0).astype(float))
        close, model = frame.loc[ok, "q_home"], frame.loc[ok, "p_home_win"]
    else:
        raise ValueError(f"unknown market {market!r}")
    gap = (model - close).to_numpy(dtype=float)
    err = (actual - close).to_numpy(dtype=float)
    keep = np.isfinite(gap) & np.isfinite(err)
    denom = float(np.sum(gap[keep] ** 2))
    return float(np.sum(gap[keep] * err[keep]) / denom) if denom > 0 else 0.0


def side_probabilities(frame: pd.DataFrame, market: str, weight: float = 1.0) -> pd.Series:
    """The model's probability of the *home*/*over* side against the close, anchored.

    Raw: the sim's normal at the projection's own dispersion, evaluated at
    the closing number. Anchored: the market's 0.5 (a spread or total is
    priced at even) or its de-vigged moneyline probability, plus ``weight``
    of the model's departure from it.
    """
    if market == "spread":
        raw = _normal_sf((frame["spread_line"] - frame["mu_margin"]) / frame["sd_margin"])
        base = np.full(len(frame), 0.5)
    elif market == "total":
        raw = _normal_sf((frame["total_line"] - frame["mu_total"]) / frame["sd_total"])
        base = np.full(len(frame), 0.5)
    elif market == "moneyline":
        raw = frame["p_home_win"].to_numpy(dtype=float)
        base = frame["q_home"].to_numpy(dtype=float)
    else:
        raise ValueError(f"unknown market {market!r}")
    return pd.Series(base + weight * (raw - base), index=frame.index)


def _outcome(frame: pd.DataFrame, market: str) -> pd.Series:
    if market == "spread":
        return frame["home_cover"]
    if market == "total":
        return frame["over_hit"]
    if "home_win" in frame.columns:
        return frame["home_win"].astype(float)
    return (frame["margin"] > 0).astype(float)


def _first_side(market: str) -> tuple[str, str]:
    return ("over", "under") if market == "total" else ("home", "away")


def select(frame: pd.DataFrame, rule: WagerRule) -> pd.DataFrame:
    """The bets ``rule`` makes on ``frame``: one row per bet with ``won``, ``price``, ``profit``.

    ``won`` is 1/0, null on a push (excluded from the record). ``profit`` is
    per unit staked at the side's price.
    """
    market = rule.market
    first, second = _first_side(market)
    if market == "spread":
        disagreement = frame["mu_margin"] - frame["spread_line"]
    elif market == "total":
        disagreement = frame["mu_total"] - frame["total_line"]
    else:
        disagreement = pd.Series(0.0, index=frame.index)
    p_first = side_probabilities(frame, market, rule.weight)
    edge_first = (p_first - (frame["q_home"] if market == "moneyline" else 0.5))

    if rule.side == "either":
        lean_first = (edge_first >= 0) if rule.min_edge is not None else (disagreement >= 0)
    else:
        lean_first = pd.Series(rule.side == first, index=frame.index)
    signed_points = np.where(lean_first, disagreement, -disagreement)
    signed_edge = np.where(lean_first, edge_first, -edge_first)
    keep = pd.Series(True, index=frame.index)
    if rule.min_points > 0:
        keep &= signed_points >= rule.min_points
    if rule.min_edge is not None:
        keep &= signed_edge >= rule.min_edge
    if market == "moneyline":
        keep &= frame["q_home"].notna()
    outcome = _outcome(frame, market)
    won = np.where(lean_first, outcome, 1.0 - outcome)
    price = np.where(lean_first, frame[_PRICE_COLUMNS[(market, first)]],
                     frame[_PRICE_COLUMNS[(market, second)]])
    bets = frame.loc[keep, ["game_id", "season", "week"]].copy()
    bets["side"] = np.where(lean_first, first, second)[keep.to_numpy()]
    bets["points"] = signed_points[keep.to_numpy()]
    bets["edge"] = signed_edge[keep.to_numpy()]
    bets["price"] = price[keep.to_numpy()]
    bets["won"] = won[keep.to_numpy()]
    bets = bets[bets["won"].notna()].copy()
    bets["profit"] = np.where(bets["won"] == 1.0, _profit(bets["price"].to_numpy()), -1.0)
    return bets.reset_index(drop=True)


@dataclass(frozen=True)
class RuleRecord:
    rule: WagerRule
    n: int
    win_rate: float
    roi: float
    seasons: int
    seasons_above_break_even: int
    per_season: pd.DataFrame

    def as_row(self) -> dict[str, object]:
        return {
            "rule": self.rule.name, "n": self.n, "win_rate": self.win_rate, "roi": self.roi,
            "seasons": self.seasons, "seasons_above": self.seasons_above_break_even,
            "bets_per_season": self.n / self.seasons if self.seasons else 0.0,
            "worst_season": (float(self.per_season["win_rate"].min())
                             if self.seasons else float("nan")),
        }


def score_rule(frame: pd.DataFrame, rule: WagerRule, *, min_season_bets: int = 5) -> RuleRecord:
    """The rule's record on ``frame``: aggregate and per season.

    A season counts toward robustness only with ``min_season_bets`` or more
    bets; it clears break-even when its win rate beats the −110 rate.
    """
    bets = select(frame, rule)
    per_season = (bets.groupby("season")
                  .agg(n=("won", "size"), win_rate=("won", "mean"), roi=("profit", "mean"))
                  .reset_index())
    counted = per_season[per_season["n"] >= min_season_bets]
    return RuleRecord(
        rule=rule, n=int(len(bets)),
        win_rate=float(bets["won"].mean()) if len(bets) else float("nan"),
        roi=float(bets["profit"].mean()) if len(bets) else float("nan"),
        seasons=int(len(counted)),
        seasons_above_break_even=int((counted["win_rate"] > BREAK_EVEN_110).sum()),
        per_season=per_season,
    )


def calibration_table(
    frame: pd.DataFrame, market: str, weight: float = 1.0,
    bins: Sequence[float] = (0.5, 0.53, 0.56, 0.6, 0.65, 0.75, 1.0),
) -> pd.DataFrame:
    """What a picked side's probability is worth: predicted vs realized by bin.

    The side is the one the (anchored) probability favours; ``predicted`` is
    its mean probability in the bin, ``actual`` how often it won.
    """
    p_first = side_probabilities(frame, market, weight)
    outcome = _outcome(frame, market)
    ok = outcome.notna() & p_first.notna()
    if market == "moneyline":
        ok &= frame["q_home"].notna()
    lean_first = p_first >= (frame["q_home"] if market == "moneyline" else 0.5)
    picked = np.where(lean_first, p_first, 1.0 - p_first)
    won = np.where(lean_first, outcome, 1.0 - outcome)
    table = pd.DataFrame({"predicted": picked[ok.to_numpy()], "won": won[ok.to_numpy()]})
    table["bin"] = pd.cut(table["predicted"], list(bins))
    out = (table.groupby("bin", observed=True)
           .agg(n=("won", "size"), predicted=("predicted", "mean"), actual=("won", "mean"))
           .reset_index())
    out["bin"] = out["bin"].astype(str)
    return out


def standard_rules(league: str, weights: dict[str, float] | None = None) -> list[WagerRule]:
    """The rules every league is scored on: the points cuts by side, and the
    anchored-edge cuts at the league's fitted weights (``weights`` by market).
    """
    rules: list[WagerRule] = []
    for market in ("spread", "total"):
        sides = ("either", *_first_side(market))
        for side in sides:
            for pts in (0.0, 2.0, 4.0, 6.0, 8.0):
                rules.append(WagerRule(market, side, min_points=pts))
    weights = weights or {}
    for market in ("spread", "total", "moneyline"):
        w = weights.get(market)
        if w is None or not np.isfinite(w) or w <= 0:
            continue
        for edge in (0.01, 0.02, 0.03, 0.05):
            rules.append(WagerRule(market, "either", min_edge=edge, weight=w))
    return rules


def records_frame(records: Iterable[RuleRecord]) -> pd.DataFrame:
    return pd.DataFrame([r.as_row() for r in records])
