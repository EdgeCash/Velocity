"""``team_totals.csv`` — each side's own number, market against model.

Team totals are a market this system simulates, prices and stakes, and one
the operator actively bets (docs/DECISIONS.md D2). Until now they reached
only ``plays.csv``, and only when a play was staked — so the market existed
on the card exactly when it had already been bet, and nowhere when it had
not. This is the board for it.

One row per **side**, not per game: a team total is a claim about one team,
and pairing them onto a game row would put four numbers in two columns and
make the natural sort (by edge, across every side on the slate) impossible.

``over_probability`` is counted off the per-team score pmf the run banks
(``distributions_*.parquet``, kinds ``home_score``/``away_score``), which is
the same comparison :func:`velocity.wagering.slate.model_probability` makes
on the same samples: ``scores > point``. ``model_team_total`` is the median
of those samples — the engine's own fair number
(:func:`velocity.wagering.slate.team_total_disagreement`), not the mean,
because the median is the 50/50 over point the slate prices against.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from velocity.export.games import prob_above
from velocity.export.meta import EXPORT_DIR, ExportMeta, round_columns, write_csv

TEAM_TOTALS_COLUMNS: tuple[str, ...] = (
    "game_id",
    "league",
    "team",
    "opponent",
    "side",
    "market_team_total",
    "model_team_total",
    "team_total_edge",
    "over_probability",
    "generated_at",
    "season",
    "week",
)

# The provider's market name for each side, and the distribution kind that
# prices it. One table, so the two can never drift apart.
SIDES: tuple[tuple[str, str, str], ...] = (
    # (side, market name on the board, distribution kind)
    ("home", "team_total_home", "home_score"),
    ("away", "team_total_away", "away_score"),
)


def _pmf(distributions: pd.DataFrame | None, kind: str) -> dict[str, pd.DataFrame]:
    if distributions is None or distributions.empty:
        return {}
    if not {"game_id", "kind", "value", "prob"} <= set(distributions.columns):
        return {}
    rows = distributions[distributions["kind"] == kind]
    return {
        str(gid): group[["value", "prob"]]
        for gid, group in rows.groupby(rows["game_id"].astype(str))
    }


def _median(pmf: pd.DataFrame | None) -> float | None:
    """The pmf's median — the point with at least half the mass at or below.

    Read off the distribution rather than recomputed from samples nobody
    kept, and deliberately the same statistic the slate's fair team total
    uses. A mean here would disagree with what the engine priced against on
    every skewed board, which is most of them.
    """
    if pmf is None or pmf.empty:
        return None
    frame = pmf.copy()
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame["prob"] = pd.to_numeric(frame["prob"], errors="coerce")
    frame = frame.dropna().sort_values("value")
    if frame.empty:
        return None
    mass = float(frame["prob"].sum())
    if mass <= 0:
        return None
    running = frame["prob"].cumsum() / mass
    hit = frame.loc[running >= 0.5, "value"]
    return None if hit.empty else float(hit.iloc[0])


def market_team_totals(board: pd.DataFrame | None) -> dict[tuple[str, str], float]:
    """``(game_id, market) → consensus point`` for the two team-total markets.

    A plain median across books, as the game markets take. Only the ``over``
    rows are read: over and under share a number, and taking both would
    weight a book that posts both sides twice.
    """
    if board is None or board.empty:
        return {}
    if not {"game_id", "market", "side"} <= set(board.columns):
        return {}
    frame = board.copy()
    frame["market"] = frame["market"].astype(str).str.lower()
    frame["side"] = frame["side"].astype(str).str.lower()
    wanted = {m for _, m, _ in SIDES}
    frame = frame[frame["market"].isin(wanted) & (frame["side"] == "over")]
    if frame.empty or "point" not in frame.columns:
        return {}
    frame["point"] = pd.to_numeric(frame["point"], errors="coerce")
    rows = frame.dropna(subset=["point"]).copy()
    rows["game_id"] = rows["game_id"].astype(str)
    grouped = rows.groupby(["game_id", "market"], as_index=False)["point"].median()
    out: dict[tuple[str, str], float] = {}
    for gid, market, point in zip(
        grouped["game_id"], grouped["market"], grouped["point"], strict=True
    ):
        out[(str(gid), str(market))] = float(point)
    return out


def build_team_totals(
    games: pd.DataFrame | None,
    *,
    board: pd.DataFrame | None = None,
    distributions: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """The team-totals table, before metadata is stamped on it.

    Two rows per game — the home side and the away side. A game the board
    never posted a team total for still exports both rows, carrying the
    model's number with an empty market cell: "the model makes Carolina 21.4
    and nobody is quoting it" is a usable statement, and dropping the row
    would make an unquoted game indistinguishable from one that was never
    simulated.
    """
    base = [c for c in TEAM_TOTALS_COLUMNS
            if c not in ("generated_at", "season", "week")]
    if games is None or games.empty or "game_id" not in games.columns:
        return pd.DataFrame(columns=base)

    quotes = market_team_totals(board)
    pmfs = {kind: _pmf(distributions, kind) for _, _, kind in SIDES}

    rows: list[dict[str, object]] = []
    for record in games.drop_duplicates(subset=["game_id"]).to_dict("records"):
        gid = str(record.get("game_id"))
        home = str(record.get("home_team") or "")
        away = str(record.get("away_team") or "")
        for side, market, kind in SIDES:
            pmf = pmfs.get(kind, {}).get(gid)
            model = _median(pmf)
            point = quotes.get((gid, market))
            rows.append({
                "game_id": gid,
                "league": record.get("league"),
                "team": home if side == "home" else away,
                "opponent": away if side == "home" else home,
                "side": side,
                "market_team_total": point,
                "model_team_total": model,
                # Positive = value on the OVER, the same direction
                # games.csv's total_edge points.
                "team_total_edge": (None if model is None or point is None
                                    else model - point),
                "over_probability": prob_above(pmf, point),
            })

    frame = pd.DataFrame(rows, columns=base)
    frame = round_columns(
        frame, ("market_team_total", "model_team_total", "team_total_edge"), 2)
    frame = round_columns(frame, ("over_probability",), 4)
    return frame.replace({np.nan: None})


def export_team_totals(
    meta: ExportMeta,
    games: pd.DataFrame | None,
    *,
    board: pd.DataFrame | None = None,
    distributions: pd.DataFrame | None = None,
    out_dir: str | Path = EXPORT_DIR,
) -> Path:
    """Build and write ``team_totals.csv``. Returns the path."""
    frame = build_team_totals(games, board=board, distributions=distributions)
    return write_csv(
        frame, Path(out_dir) / "team_totals.csv", TEAM_TOTALS_COLUMNS, meta)
