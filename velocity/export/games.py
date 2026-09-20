"""``games.csv`` — one row per game on the board, market against model.

The betting card's top half: what the market says, what the simulation says,
how far apart they are, and how confident the run is. Every number is read
off a frame the pipeline already banked.

Two of the columns are computed here rather than looked up, and both are
computed from the simulation's **own** distribution rather than a normal
standing in for it. ``distributions_{league}_{stamp}.parquet`` persists the
exact pmf of each game's total and margin over the full 100k draws
(``velocity.report.social.distributions_frame``), so

* ``cover_probability`` = P(margin + home_spread > 0), and
* ``over_probability``  = P(total > market_total)

are the same counts :meth:`~velocity.models.simulate.GameSim.prob_home_cover`
and ``prob_over`` take, on the same samples, with pushes excluded from the
numerator exactly as those methods and the grader do. Without the pmf the
cells are left empty; an approximation here would be a second, quieter
pricing model.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from velocity.export.meta import EXPORT_DIR, ExportMeta, round_columns, write_csv

GAMES_COLUMNS: tuple[str, ...] = (
    "game_id",
    "league",
    "away_team",
    "home_team",
    "market_spread",
    "market_total",
    "moneyline",
    "model_away_score",
    "model_home_score",
    "model_total",
    "spread_edge",
    "total_edge",
    "cover_probability",
    "over_probability",
    "confidence",
    "kelly_fraction",
    "weather",
    "generated_at",
    "season",
    "week",
)

# ``market_spread`` is always the HOME number, so a workbook can sort on it
# without asking which team it belongs to. A provider's away row carries the
# away number, which is the home number negated.
_HOME_SIDE = "home"
_AWAY_SIDE = "away"

# Conviction is scored in [0, 1]; the card shows it out of ten (docs say
# "Confidence: 8.2", not "0.82"). One multiplication, named, in one place.
_CONFIDENCE_SCALE = 10.0


def _pmf_by_game(distributions: pd.DataFrame | None, kind: str) -> dict[str, pd.DataFrame]:
    """``{game_id: frame(value, prob)}`` for one distribution kind."""
    if distributions is None or distributions.empty:
        return {}
    needed = {"game_id", "kind", "value", "prob"}
    if not needed <= set(distributions.columns):
        return {}
    rows = distributions[distributions["kind"] == kind]
    return {
        str(gid): group[["value", "prob"]]
        for gid, group in rows.groupby(rows["game_id"].astype(str))
    }


def prob_above(pmf: pd.DataFrame | None, point: float | None) -> float | None:
    """P(value > ``point``) on a pmf frame, pushes excluded from the numerator.

    The same comparison the sim makes (``np.count_nonzero(total > point)``),
    so a cell here and the engine's own number are the same number.
    """
    if pmf is None or pmf.empty or point is None or not np.isfinite(point):
        return None
    values = pd.to_numeric(pmf["value"], errors="coerce").to_numpy(dtype=float)
    probs = pd.to_numeric(pmf["prob"], errors="coerce").to_numpy(dtype=float)
    mass = float(np.nansum(probs))
    if mass <= 0:
        return None
    return float(np.nansum(probs[values > float(point)]))


def prob_home_cover(margin_pmf: pd.DataFrame | None, home_spread: float | None) -> float | None:
    """P(home covers ``home_spread``) — ``margin + home_spread > 0``."""
    if home_spread is None or not np.isfinite(home_spread):
        return None
    return prob_above(margin_pmf, -float(home_spread))


def market_numbers(board: pd.DataFrame | None) -> pd.DataFrame:
    """Per game: the consensus home spread, total and home moneyline price.

    ``board`` is any canonical-sided lines frame — an odds-archive snapshot,
    or the slate's own rows. Points are a plain median across books; prices
    go through :func:`velocity.wagering.odds.consensus_american`, because a
    median of American odds straddling ±100 lands in the invalid gap between
    -100 and +100.
    """
    cols = ["game_id", "market_spread", "market_total", "moneyline"]
    if board is None or board.empty:
        return pd.DataFrame(columns=cols)
    needed = {"game_id", "market", "side"}
    if not needed <= set(board.columns):
        return pd.DataFrame(columns=cols)

    from velocity.wagering.odds import consensus_american

    frame = board.copy()
    frame["game_id"] = frame["game_id"].astype(str)
    frame["market"] = frame["market"].astype(str).str.lower()
    frame["side"] = frame["side"].astype(str).str.lower()
    if "point" in frame.columns:
        frame["point"] = pd.to_numeric(frame["point"], errors="coerce")
    else:
        frame["point"] = np.nan
    if "price" in frame.columns:
        frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    else:
        frame["price"] = np.nan

    spreads = frame[frame["market"] == "spread"].copy()
    # Normalize every spread row to the home number before taking a median:
    # averaging a home -3 with an away +3 is averaging one number with its
    # own negation, and reads as a pick'em.
    spreads["home_point"] = np.where(
        spreads["side"] == _AWAY_SIDE, -spreads["point"], spreads["point"]
    )
    spreads = spreads[spreads["side"].isin({_HOME_SIDE, _AWAY_SIDE})]
    spread_by_game = spreads.groupby("game_id")["home_point"].median()

    totals = frame[(frame["market"] == "total") & frame["point"].notna()]
    total_by_game = totals.groupby("game_id")["point"].median()

    money = frame[(frame["market"] == "moneyline") & (frame["side"] == _HOME_SIDE)]
    money = money[money["price"].notna()]
    money_by_game = (
        money.groupby("game_id")["price"].agg(consensus_american)
        if not money.empty
        else pd.Series(dtype=float)
    )

    ids = sorted(set(spread_by_game.index) | set(total_by_game.index) | set(money_by_game.index))
    return pd.DataFrame(
        {
            "game_id": ids,
            "market_spread": [spread_by_game.get(g, np.nan) for g in ids],
            "market_total": [total_by_game.get(g, np.nan) for g in ids],
            "moneyline": [money_by_game.get(g, np.nan) for g in ids],
        },
        columns=cols,
    )


def weather_text(row: Mapping[Any, Any]) -> str:
    """The forecast as one cell: what it was, and what it did to the total.

    Empty when the forecast frame never covered the game — "not adjusted" and
    "adjusted by nothing" are different claims and only one of them is true
    (``scripts/run_live_slate.weather_frame`` makes the same distinction).
    """
    def num(key: str) -> float | None:
        value = pd.to_numeric(pd.Series([row.get(key)]), errors="coerce").iloc[0]
        return None if pd.isna(value) else float(value)

    wind, precip, temp, points = (num(k) for k in
                                  ("wind_mph", "precip_in", "temp_f", "total_points"))
    if wind is None and temp is None and precip is None:
        return ""
    parts = []
    if temp is not None:
        parts.append(f"{temp:.0f}°F")
    if wind is not None:
        parts.append(f"wind {wind:.0f} mph")
    if precip is not None and precip > 0:
        parts.append(f"precip {precip:.2f} in")
    if points is not None and abs(points) >= 0.05:
        parts.append(f"total {points:+.1f}")
    return ", ".join(parts)


def _confidence_by_game(intel: pd.DataFrame | None) -> dict[str, float]:
    """The strongest surviving conviction per game, on a 0-10 scale."""
    if intel is None or intel.empty or "conviction" not in intel.columns:
        return {}
    rows = intel
    if "recommended" in rows.columns:
        rows = rows[rows["recommended"].fillna(True).astype(bool)]
    if "player" in rows.columns:  # game markets only — props have their own file
        rows = rows[rows["player"].isna()]
    if rows.empty:
        return {}
    best = rows.groupby(rows["game_id"].astype(str))["conviction"].max()
    return {str(g): float(v) * _CONFIDENCE_SCALE for g, v in best.items() if pd.notna(v)}


def _kelly_by_game(slate: pd.DataFrame | None, bankroll: float) -> dict[str, float]:
    """The largest staked fraction of bankroll on each game.

    The slate banks a stake in money because that is what an operator places;
    the export carries the fraction because that is what survives a change of
    bankroll. A run with no bankroll to divide by yields nothing rather than
    an infinity.
    """
    if slate is None or slate.empty or "stake" not in slate.columns or bankroll <= 0:
        return {}
    stakes = pd.to_numeric(slate["stake"], errors="coerce")
    frame = pd.DataFrame({"game_id": slate["game_id"].astype(str), "stake": stakes})
    best = frame.dropna(subset=["stake"]).groupby("game_id")["stake"].max()
    return {str(g): float(v) / float(bankroll) for g, v in best.items()}


def build_games(  # noqa: PLR0913 - one row per game, assembled from the run's frames
    games: pd.DataFrame | None,
    projections: pd.DataFrame | None = None,
    *,
    board: pd.DataFrame | None = None,
    slate: pd.DataFrame | None = None,
    distributions: pd.DataFrame | None = None,
    weather: pd.DataFrame | None = None,
    intel: pd.DataFrame | None = None,
    bankroll: float = 100.0,
) -> pd.DataFrame:
    """The games table, before metadata is stamped on it.

    ``games`` is the run's ``game_id → teams`` map and sets the row set: a
    game the board never carried is not invented from a projection, and a
    projection with no game row has nowhere to put its team names.

    ``board`` is the market side. Pass an odds-archive snapshot for the whole
    board; pass nothing and the staked slate rows stand in, which covers only
    the games that earned a bet. ``slate`` supplies ``kelly_fraction`` and,
    when ``board`` is absent, the market numbers too.
    """
    if games is None or games.empty or "game_id" not in games.columns:
        return pd.DataFrame(columns=list(GAMES_COLUMNS))

    out = games.drop_duplicates(subset=["game_id"]).copy()
    out["game_id"] = out["game_id"].astype(str)
    for col in ("league", "home_team", "away_team"):
        if col not in out.columns:
            out[col] = None
    out = out[["game_id", "league", "away_team", "home_team"]]

    market = market_numbers(board if board is not None and not board.empty else slate)
    out = out.merge(market, on="game_id", how="left")

    if projections is not None and not projections.empty:
        proj = projections.drop_duplicates(subset=["game_id"]).copy()
        proj["game_id"] = proj["game_id"].astype(str)
        keep = [c for c in ("mu_away", "mu_home", "fair_spread", "fair_total")
                if c in proj.columns]
        out = out.merge(proj[["game_id", *keep]], on="game_id", how="left")
    for col in ("mu_away", "mu_home", "fair_spread", "fair_total"):
        if col not in out.columns:
            out[col] = np.nan

    out["model_away_score"] = pd.to_numeric(out["mu_away"], errors="coerce")
    out["model_home_score"] = pd.to_numeric(out["mu_home"], errors="coerce")
    out["model_total"] = out["model_away_score"] + out["model_home_score"]

    # Sign convention, stated once and the same in both columns: POSITIVE
    # means value on the home side / the over.
    #   spread_edge = market - fair: market -3 against a fair -6 is +3 points
    #   of value on the home side, because the market is selling home too cheap.
    #   total_edge  = fair - market: a fair 48 against a market 44 is +4 to the over.
    out["spread_edge"] = pd.to_numeric(out["market_spread"], errors="coerce") - pd.to_numeric(
        out["fair_spread"], errors="coerce"
    )
    out["total_edge"] = pd.to_numeric(out["fair_total"], errors="coerce") - pd.to_numeric(
        out["market_total"], errors="coerce"
    )

    margins = _pmf_by_game(distributions, "margin")
    totals = _pmf_by_game(distributions, "total")
    out["cover_probability"] = [
        prob_home_cover(margins.get(g), s)
        for g, s in zip(out["game_id"], out["market_spread"], strict=True)
    ]
    out["over_probability"] = [
        prob_above(totals.get(g), t)
        for g, t in zip(out["game_id"], out["market_total"], strict=True)
    ]

    confidence = _confidence_by_game(intel)
    out["confidence"] = [confidence.get(g) for g in out["game_id"]]
    kelly = _kelly_by_game(slate, bankroll)
    out["kelly_fraction"] = [kelly.get(g) for g in out["game_id"]]

    if weather is not None and not weather.empty and "game_id" in weather.columns:
        notes = {
            str(r["game_id"]): weather_text(r)
            for r in weather.drop_duplicates(subset=["game_id"]).to_dict("records")
        }
    else:
        notes = {}
    out["weather"] = [notes.get(g, "") for g in out["game_id"]]

    out = round_columns(out, ("model_away_score", "model_home_score", "model_total"), 2)
    out = round_columns(
        out, ("market_spread", "market_total", "spread_edge", "total_edge", "confidence"), 2
    )
    out = round_columns(out, ("cover_probability", "over_probability", "kelly_fraction"), 4)
    return out.reindex(columns=[c for c in GAMES_COLUMNS if c not in
                                ("generated_at", "season", "week")])


def export_games(  # noqa: PLR0913 - mirrors build_games, plus where to write
    meta: ExportMeta,
    games: pd.DataFrame | None,
    projections: pd.DataFrame | None = None,
    *,
    out_dir: str | Path = EXPORT_DIR,
    board: pd.DataFrame | None = None,
    slate: pd.DataFrame | None = None,
    distributions: pd.DataFrame | None = None,
    weather: pd.DataFrame | None = None,
    intel: pd.DataFrame | None = None,
    bankroll: float = 100.0,
) -> Path:
    """Build and write ``games.csv``. Returns the path."""
    frame = build_games(
        games, projections, board=board, slate=slate, distributions=distributions,
        weather=weather, intel=intel, bankroll=bankroll,
    )
    return write_csv(frame, Path(out_dir) / "games.csv", GAMES_COLUMNS, meta)
