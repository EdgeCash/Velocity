"""Scorecard — grade a persisted slate and score it on CLV + calibration.

The measurement half of the loop: take a slate the runner archived, attach the
closing lines, grade every bet against the final score, and report the two things
that tell us whether the *model* is any good —

* **CLV by market** — did we beat the closing price/number, and in which markets?
  Consistent positive closing-line value is the durable edge signal (a bankroll
  can run hot or cold on variance; CLV can't).
* **Calibration** — when the model says 55%, does it hit ~55%? A reliability table
  per probability bucket plus a single expected-calibration-error (ECE) number.

Everything here is a **pure** function of frames (slate, closing lines, finals), so
it is offline-testable and — crucially — **league-agnostic**: it reads the same
``game_id / market / side / point / price / p_model`` columns the football slates
emit, so the identical harness scores NFL/NCAAF once their archives fill. Grading
and CLV themselves are reused from :mod:`velocity.wagering.bet_log`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from velocity.wagering.bet_log import Bet, BetLog

_CLOSE_KEYS = ["game_id", "market", "side"]


def _closing_index(closing: pd.DataFrame | None) -> dict[tuple[str, str, str], tuple]:
    """Map ``(game_id, market, side)`` → ``(closing_price, closing_point)``."""
    if closing is None or closing.empty:
        return {}
    index: dict[tuple[str, str, str], tuple] = {}
    for row in closing.to_dict("records"):
        key = (str(row["game_id"]), str(row["market"]), str(row["side"]))
        point = row.get("point")
        index[key] = (row.get("price"), None if point is None or pd.isna(point) else float(point))
    return index


def bets_from_slate(slate: pd.DataFrame, closing: pd.DataFrame | None = None) -> list[Bet]:
    """Reconstruct :class:`Bet` tickets from a persisted slate frame.

    ``closing`` (a canonical closing-lines frame with the same key columns) supplies
    each bet's closing price/point for CLV; a bet with no matching close simply has
    ``None`` there and contributes to grading but not to CLV.
    """
    close = _closing_index(closing)
    bets: list[Bet] = []
    for row in slate.to_dict("records"):
        point = row.get("point")
        point_f = None if point is None or pd.isna(point) else float(point)
        c_price, c_point = close.get(
            (str(row["game_id"]), str(row["market"]), str(row["side"])), (None, None)
        )
        bets.append(
            Bet(
                game_id=str(row["game_id"]),
                market=str(row["market"]),
                side=str(row["side"]),
                book=str(row.get("book", "")),
                price=float(row["price"]),
                stake=float(row.get("stake", 0.0) or 0.0),
                p_model=float(row["p_model"]),
                point=point_f,
                closing_price=None if c_price is None or pd.isna(c_price) else float(c_price),
                closing_point=c_point,
                player=row.get("player"),
                p_fair=None if pd.isna(row.get("p_fair")) else row.get("p_fair"),
            )
        )
    return bets


def grade_slate(
    slate: pd.DataFrame,
    finals: pd.DataFrame,
    closing: pd.DataFrame | None = None,
    *,
    starting_bankroll: float = 100.0,
) -> pd.DataFrame:
    """Grade a persisted slate against final scores; return the settled bet rows.

    ``finals`` is a ``Games``-shaped frame (``game_id`` + ``home_score`` /
    ``away_score``; null scores mark a game not yet played → ``pending``). The
    result carries ``result / profit / bankroll`` and both CLV measures per bet.
    """
    log = BetLog()
    for bet in bets_from_slate(slate, closing):
        log.add(bet)
    return log.settle(finals, starting_bankroll)


def _decided(graded: pd.DataFrame) -> pd.DataFrame:
    """Rows with a settled win/loss (drop push + pending — no calibration signal)."""
    return graded[graded["result"].isin(["win", "loss"])]


def calibration_table(graded: pd.DataFrame, n_bins: int = 10) -> pd.DataFrame:
    """Reliability table: realized win rate vs mean model probability, per bucket.

    Buckets the decided bets by ``p_model`` into ``n_bins`` equal-width bins over
    [0, 1]. A well-calibrated model has ``realized ≈ mean_p`` in every populated
    bin. Empty bins are dropped.
    """
    decided = _decided(graded)
    if decided.empty:
        return pd.DataFrame(columns=["bin", "n", "mean_p", "realized", "gap"])
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(decided["p_model"], edges[1:-1]), 0, n_bins - 1)
    rows: list[dict[str, object]] = []
    won = (decided["result"] == "win").to_numpy()
    p = decided["p_model"].to_numpy()
    for b in range(n_bins):
        mask = idx == b
        n = int(mask.sum())
        if n == 0:
            continue
        mean_p = float(p[mask].mean())
        realized = float(won[mask].mean())
        rows.append({
            "bin": f"{edges[b]:.1f}-{edges[b + 1]:.1f}",
            "n": n,
            "mean_p": round(mean_p, 3),
            "realized": round(realized, 3),
            "gap": round(realized - mean_p, 3),
        })
    return pd.DataFrame(rows)


def expected_calibration_error(graded: pd.DataFrame, n_bins: int = 10) -> float:
    """Bucket-count-weighted mean ``|realized − mean_p|`` (0 = perfectly calibrated)."""
    table = calibration_table(graded, n_bins)
    if table.empty:
        return float("nan")
    weights = table["n"] / table["n"].sum()
    return float((weights * table["gap"].abs()).sum())


def clv_by_market(graded: pd.DataFrame) -> pd.DataFrame:
    """Per-market CLV: mean price CLV, mean line CLV, % positive, and bet count."""
    cols = ["market", "n", "mean_price_clv", "mean_line_clv", "pct_positive"]
    if graded.empty:
        return pd.DataFrame(columns=cols)
    rows: list[dict[str, object]] = []
    for market, grp in graded.groupby("market"):
        price = grp["price_clv"].dropna()
        line = grp["line_clv"].dropna()
        beat = price.reindex(grp.index)
        beat = beat.where(beat.notna(), grp["line_clv"])
        beat = beat.dropna()
        rows.append({
            "market": market,
            "n": int(len(grp)),
            "mean_price_clv": round(float(price.mean()), 4) if not price.empty else float("nan"),
            "mean_line_clv": round(float(line.mean()), 3) if not line.empty else float("nan"),
            "pct_positive": round(float((beat > 0).mean()), 3) if not beat.empty else float("nan"),
        })
    return pd.DataFrame(rows).sort_values("market").reset_index(drop=True)


def summarize(graded: pd.DataFrame, n_bins: int = 10) -> dict[str, float]:
    """One-line scorecard: record, ROI, CLV, and calibration error."""
    decided = _decided(graded)
    staked = float(graded.loc[graded["result"] != "pending", "stake"].sum())
    profit = float(graded.loc[graded["result"] != "pending", "profit"].sum())
    price_clv = graded["price_clv"].dropna()
    beat = graded["price_clv"].where(graded["price_clv"].notna(), graded["line_clv"]).dropna()
    return {
        "n_bets": int(len(graded)),
        "n_decided": int(len(decided)),
        "wins": int((graded["result"] == "win").sum()),
        "losses": int((graded["result"] == "loss").sum()),
        "pushes": int((graded["result"] == "push").sum()),
        "pending": int((graded["result"] == "pending").sum()),
        "roi": round(profit / staked, 4) if staked else float("nan"),
        "mean_price_clv": (
            round(float(price_clv.mean()), 4) if not price_clv.empty else float("nan")
        ),
        "pct_positive_clv": round(float((beat > 0).mean()), 3) if not beat.empty else float("nan"),
        "ece": round(expected_calibration_error(graded, n_bins), 4),
    }
