"""Football prop grading + confidence-shrink sweep — the calibration loop.

The MLB prop program's decisive finding was that the raw model is
over-confident and that over-confidence **varies sharply by market** — one
market wanted shrink 0.5, another was unprofitable at every shrink and had to
be excluded. This module ports that machinery to football:

* :func:`grade_prop_ledger` settles prop rows against the nflverse weekly
  actuals (``velocity.ingest.nfl.load_weekly_stats``) by normalized player
  name. A player with no stat line that week stays ``pending`` — the record
  never guesses (inactive ≠ under).
* :func:`sweep_shrink` re-prices a graded ledger at each candidate shrink and
  reports flat-stake ROI and hit rate, per market and overall. What it selects
  becomes ``SlateConfig.prop_shrink_by_market`` / ``exclude_markets`` — tuned
  by evidence, not carried over from MLB.

Everything here is a pure function of frames; the network fetch lives in the
CLI (``scripts/backtest_props_football.py``).
"""

from __future__ import annotations

import re

import pandas as pd

from velocity.wagering.edge import evaluate
from velocity.wagering.odds import net_payout

# Graded-ledger columns appended by grade_prop_ledger.
_RESULTS = ("win", "loss", "push", "pending")


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def actuals_index(weekly: pd.DataFrame) -> dict[str, dict[str, float]]:
    """``normalized player name → {market: actual}`` for one week's stat lines.

    Pass a frame already filtered to the slate's (season, week) — grading
    across the whole season would match the wrong game.
    """
    out: dict[str, dict[str, float]] = {}
    market_cols = [c for c in weekly.columns
                   if c not in ("season", "week", "player_id", "player_name",
                                "team", "position")]
    for r in weekly.to_dict("records"):
        key = _normalize_name(str(r["player_name"]))
        out[key] = {m: float(r[m]) for m in market_cols if pd.notna(r[m])}
    return out


def grade_prop_ledger(props: pd.DataFrame, weekly: pd.DataFrame) -> pd.DataFrame:
    """Settle prop rows against one week's actuals → ``actual``/``result``/``profit``.

    ``props`` rows need ``player``/``market``/``side``/``point``/``price``;
    ``profit`` is flat one-unit staking (``net_payout`` on a win, −1 on a
    loss, 0 on a push) so ROI reads per bet. A player missing from the weekly
    actuals (inactive, or a name-resolution miss) stays ``pending``.
    """
    index = actuals_index(weekly)
    actuals: list[float | None] = []
    results: list[str] = []
    profits: list[float] = []
    for r in props.to_dict("records"):
        stats = index.get(_normalize_name(str(r["player"])))
        actual = None if stats is None else stats.get(str(r["market"]))
        point = r.get("point")
        if actual is None or point is None or pd.isna(point):
            actuals.append(None)
            results.append("pending")
            profits.append(float("nan"))
            continue
        point = float(point)
        actuals.append(actual)
        if actual == point:
            results.append("push")
            profits.append(0.0)
            continue
        won = (actual > point) == (str(r["side"]) == "over")
        results.append("win" if won else "loss")
        profits.append(net_payout(float(r["price"])) if won else -1.0)
    graded = props.copy()
    graded["actual"] = actuals
    graded["result"] = results
    graded["profit"] = profits
    return attach_prop_clv(graded)


def attach_prop_clv(graded: pd.DataFrame) -> pd.DataFrame:
    """``price_clv`` / ``line_clv`` from ``closing_price`` / ``closing_point``.

    The same two yardsticks the game ledger carries (``Bet.price_clv`` /
    ``Bet.line_clv``): the decimal-odds edge over the closing price, and the
    signed points beaten on the closing number — over wants a lower close,
    under a higher one. Rows without a close get nulls; the columns are
    always present so the record chain's schema is stable. Prop CLV is not
    the doctrine's yardstick (``clv_trusted`` is False for every prop
    market), but it is what lets the shrink sweep grade against something.
    """
    from velocity.wagering.odds import american_to_decimal

    out = graded.copy()
    if "closing_price" not in out.columns:
        out["closing_price"] = float("nan")
    if "closing_point" not in out.columns:
        out["closing_point"] = float("nan")
    price = pd.to_numeric(out["price"], errors="coerce")
    close_price = pd.to_numeric(out["closing_price"], errors="coerce")
    point = pd.to_numeric(out["point"], errors="coerce")
    close_point = pd.to_numeric(out["closing_point"], errors="coerce")
    price_clv = [
        (american_to_decimal(float(a)) / american_to_decimal(float(b)) - 1.0)
        if pd.notna(a) and pd.notna(b) and not (-100.0 < float(b) < 100.0) else float("nan")
        for a, b in zip(price, close_price, strict=True)
    ]
    sign = out["side"].astype(str).map({"over": 1.0, "under": -1.0})
    out["price_clv"] = price_clv
    out["line_clv"] = (sign * (close_point - point)).where(point.notna() & close_point.notna())
    return out


def sweep_shrink(
    graded: pd.DataFrame,
    shrinks: list[float],
    *,
    min_edge: float = 0.02,
) -> pd.DataFrame:
    """Re-price a graded ledger at each shrink → per-market ROI/hit-rate table.

    ``graded`` rows need the entry pricing (``p_model`` — the RAW model
    probability, ``p_fair``, ``price``) plus ``market`` and the settled
    ``result``/``profit`` from :func:`grade_prop_ledger`. For each candidate
    shrink, a row qualifies if the *shrunk* probability still clears the edge
    gate — exactly the live slate's bet rule — and the settled outcomes of the
    qualifying rows give that shrink's flat-stake ROI. ``market="ALL"`` rows
    aggregate across markets. Pending rows never count.
    """
    rows: list[dict[str, object]] = []
    settled = graded[graded["result"].isin(["win", "loss", "push"])]
    for shrink in shrinks:
        picked = []
        for r in settled.to_dict("records"):
            p_fair = r.get("p_fair")
            if p_fair is None or pd.isna(p_fair):
                continue
            p = 0.5 + shrink * (float(r["p_model"]) - 0.5)
            signal = evaluate(p, float(r["price"]), float(p_fair), min_edge=min_edge)
            if signal.qualifies:
                picked.append(r)
        frame = pd.DataFrame(picked)
        markets = ["ALL"] if frame.empty else ["ALL", *sorted(frame["market"].unique())]
        for market in markets:
            sub = frame if market == "ALL" else frame[frame["market"] == market]
            n = len(sub)
            wins = int((sub["result"] == "win").sum()) if n else 0
            losses = int((sub["result"] == "loss").sum()) if n else 0
            decided = wins + losses
            rows.append(
                {
                    "shrink": shrink,
                    "market": market,
                    "n_bets": n,
                    "hit_rate": (wins / decided) if decided else float("nan"),
                    "roi": (float(sub["profit"].sum()) / n) if n else float("nan"),
                }
            )
    return pd.DataFrame(rows, columns=["shrink", "market", "n_bets", "hit_rate", "roi"])
