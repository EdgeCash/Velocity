"""``dashboard.csv`` — the summary tab: how the model is doing, and today's best.

A dashboard is not one table. Model performance is a handful of scalars, ROI
and CLV are per-market rollups, and "top plays" is a short ranked list — so
the file is **long**, one metric per row:

    section, metric, value, detail, generated_at, season, week

``value`` is the number (empty for a row that is only prose) and ``detail``
the words. A single long table is the one shape Power Query can pivot into
any of the tiles a workbook wants without a transformation step, which is the
whole contract (docs/EXCEL_SETUP.md); six separate tables would each need
their own query and would drift apart on the first schema change.

Everything here is computed from the settled record via
:mod:`velocity.eval.metrics` — the same functions the site and the monitor
read — so a number on the dashboard and the same number on the site come from
one implementation.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from velocity.export.meta import EXPORT_DIR, ExportMeta, numeric_column, write_csv

DASHBOARD_COLUMNS: tuple[str, ...] = (
    "section",
    "metric",
    "value",
    "detail",
    "generated_at",
    "season",
    "week",
)

SECTION_PERFORMANCE = "performance"
SECTION_ROI = "roi"
SECTION_CLV = "clv"
SECTION_TOP_PLAYS = "top_plays"
SECTION_TOP_PROPS = "top_props"
SECTION_TOP_DFS = "top_dfs"

# How many rows each "top" section contributes. Short on purpose: a dashboard
# that lists forty plays is the board again, not a summary of it.
TOP_N = 5


def _row(section: str, metric: str, value: object = None, detail: str = "") -> dict:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return {
        "section": section,
        "metric": metric,
        "value": None if pd.isna(number) else float(number),
        "detail": detail,
    }


def performance_rows(record: pd.DataFrame | None) -> list[dict]:
    """Bets, record, win rate and ROI on the settled ledger."""
    if record is None or record.empty:
        return [_row(SECTION_PERFORMANCE, "graded_bets", 0,
                     "no settled bets in the record chain yet")]
    from velocity.eval.metrics import roi as roi_metric

    result = (record["result"].astype(str) if "result" in record.columns
              else pd.Series("", index=record.index))
    wins = int((result == "win").sum())
    losses = int((result == "loss").sum())
    pushes = int((result == "push").sum())
    decided = wins + losses
    profit = numeric_column(record, "profit").fillna(0.0)
    stake = numeric_column(record, "stake").fillna(0.0)
    rows = [
        _row(SECTION_PERFORMANCE, "graded_bets", int(len(record))),
        _row(SECTION_PERFORMANCE, "wins", wins),
        _row(SECTION_PERFORMANCE, "losses", losses),
        _row(SECTION_PERFORMANCE, "pushes", pushes),
        _row(SECTION_PERFORMANCE, "record", None, f"{wins}-{losses}-{pushes}"),
        _row(SECTION_PERFORMANCE, "win_rate",
             (wins / decided) if decided else None,
             "decided bets only; a push is neither" if decided else "no decided bets"),
        _row(SECTION_PERFORMANCE, "profit", float(profit.sum())),
        _row(SECTION_PERFORMANCE, "staked", float(stake.sum())),
    ]
    rows.append(_row(SECTION_ROI, "roi_overall", roi_metric(profit, stake),
                     "profit per unit staked, all markets"))
    if "market" in record.columns:
        from velocity.eval.metrics import clv_by_market

        for part in clv_by_market(record).to_dict("records"):
            market = str(part.get("market", ""))
            rows.append(_row(SECTION_CLV, f"mean_price_clv_{market}",
                             part.get("mean_price_clv"),
                             "trusted market" if part.get("clv_trusted")
                             else "CLV not the yardstick for this market"))
        for market, group in record.groupby(record["market"].astype(str)):
            rows.append(_row(
                SECTION_ROI, f"roi_{market}",
                roi_metric(numeric_column(group, "profit").fillna(0.0),
                           numeric_column(group, "stake").fillna(0.0)),
                f"{len(group)} bets",
            ))
    return rows


def clv_rows(record: pd.DataFrame | None) -> list[dict]:
    """Mean CLV and the share of bets that beat the close, overall and by tier."""
    if record is None or record.empty:
        return [_row(SECTION_CLV, "mean_price_clv", None, "no settled bets yet")]
    from velocity.eval.metrics import clv_by_tier, clv_stats

    price = numeric_column(record, "price_clv")
    line = numeric_column(record, "line_clv")
    stats = clv_stats(price.where(price.notna(), line))
    rows = [
        _row(SECTION_CLV, "mean_price_clv", stats["mean_clv"],
             "the durable signal: the close is the sharpest public forecast"),
        _row(SECTION_CLV, "pct_beat_close", stats["pct_positive"]),
    ]
    for part in clv_by_tier(record).to_dict("records"):
        tier = str(part.get("rule_tier", "none"))
        rows.append(_row(SECTION_CLV, f"mean_price_clv_tier_{tier}",
                         part.get("mean_price_clv"),
                         f"{int(part.get('n_bets') or 0)} bets, "
                         f"win rate {part.get('win_rate')}"))
    return rows


def top_play_rows(plays: pd.DataFrame | None, limit: int = TOP_N) -> list[dict]:
    """The card's leading calls, already ranked by the curated engine."""
    if plays is None or plays.empty:
        return [_row(SECTION_TOP_PLAYS, "none", None, "no plays on this board")]
    rows = []
    for i, play in enumerate(plays.head(limit).to_dict("records"), start=1):
        rows.append(_row(
            SECTION_TOP_PLAYS, f"play_{i}", play.get("edge"),
            f"[{play.get('tier')}] {play.get('selection')} — {play.get('reason')}",
        ))
    return rows


def top_prop_rows(props: pd.DataFrame | None, limit: int = TOP_N) -> list[dict]:
    """The biggest prop edges on the board."""
    if props is None or props.empty:
        return [_row(SECTION_TOP_PROPS, "none", None, "no props on this board")]
    frame = props.copy()
    frame["_edge"] = numeric_column(frame, "edge")
    frame = frame.sort_values("_edge", ascending=False, na_position="last")
    rows = []
    for i, prop in enumerate(frame.head(limit).to_dict("records"), start=1):
        line = prop.get("line")
        rows.append(_row(
            SECTION_TOP_PROPS, f"prop_{i}", prop.get("edge"),
            f"{prop.get('player')} {prop.get('market')} "
            f"{'' if line is None or pd.isna(line) else line} "
            f"(model {prop.get('hit_probability')}, "
            f"proj {prop.get('projection')})".strip(),
        ))
    return rows


def top_dfs_rows(dfs: pd.DataFrame | None, limit: int = TOP_N) -> list[dict]:
    """The best points-per-dollar on the DFS board."""
    if dfs is None or dfs.empty:
        return [_row(SECTION_TOP_DFS, "none", None, "no DFS pool on this board")]
    frame = dfs.copy()
    frame["_value"] = numeric_column(frame, "value_score")
    frame = frame.sort_values("_value", ascending=False, na_position="last")
    rows = []
    for i, player in enumerate(frame.head(limit).to_dict("records"), start=1):
        rows.append(_row(
            SECTION_TOP_DFS, f"value_{i}", player.get("value_score"),
            f"{player.get('player')} ({player.get('position')}, {player.get('team')}) "
            f"${player.get('salary')} — proj {player.get('projection')}, "
            f"ceiling {player.get('ceiling')}",
        ))
    return rows


def build_dashboard(
    record: pd.DataFrame | None = None,
    *,
    plays: pd.DataFrame | None = None,
    props: pd.DataFrame | None = None,
    dfs: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """The dashboard table, before metadata is stamped on it."""
    base = [c for c in DASHBOARD_COLUMNS if c not in ("generated_at", "season", "week")]
    rows = [
        *performance_rows(record),
        *clv_rows(record),
        *top_play_rows(plays),
        *top_prop_rows(props),
        *top_dfs_rows(dfs),
    ]
    frame = pd.DataFrame(rows, columns=base)
    if "value" in frame.columns:
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce").round(4)
        frame["value"] = frame["value"].replace([np.inf, -np.inf], np.nan)
    return frame


def export_dashboard(
    meta: ExportMeta,
    record: pd.DataFrame | None = None,
    *,
    plays: pd.DataFrame | None = None,
    props: pd.DataFrame | None = None,
    dfs: pd.DataFrame | None = None,
    out_dir: str | Path = EXPORT_DIR,
) -> Path:
    """Build and write ``dashboard.csv``. Returns the path."""
    frame = build_dashboard(record, plays=plays, props=props, dfs=dfs)
    return write_csv(frame, Path(out_dir) / "dashboard.csv", DASHBOARD_COLUMNS, meta)
