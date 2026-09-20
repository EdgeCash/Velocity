"""One finished workbook — the export layer for a device that cannot query.

``docs/EXCEL_SETUP.md`` builds the front end out of six CSVs and Power Query.
That design assumes desktop Excel, and it is the right design there: the
workbook is built once and every later refresh is a button.

**Excel for iPad, iPhone and Android has no Power Query at all** — no Get
Data, no query refresh, no connections pane. Neither does any of them run
Python, so the operator cannot produce the CSVs locally either. On those
devices the CSV design is not merely inconvenient; there is no sequence of
taps that assembles it.

So this module ships the other shape: a single ``.xlsx`` with every tab
already populated, built by the same pipeline run that makes the CSVs, from
the same frames, and delivered as a file. Nothing to connect, nothing to
refresh — a later refresh is a newer file, which on a tablet is fewer taps
than a query refresh would have been anyway.

It is a **view of the export contract, not a second source**. Every number
comes from the `build_*` functions in this package; this module chooses
headers, widths and number formats and nothing else.

Two deliberate differences from the CSVs:

* ``generated_at`` / ``season`` / ``week`` appear in each sheet's subtitle
  and on Read Me, not as three repeated columns on every row. Power Query
  needs them per-row to append runs into a history; a reader on a 10-inch
  screen needs the horizontal space more, and a constant repeated down a
  column is not information.
* Headers are title-case for reading (``Cover %``) rather than the
  Power-Query-safe ``cover_probability``. The CSVs keep the machine names;
  nothing queries this file.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from velocity.export.meta import ExportMeta

# The file the workbook is written as. Stable, like the CSV names: an
# operator's bookmark, an email attachment and a Files-app icon all key on it.
WORKBOOK_NAME = "velocity.xlsx"

# The visual language is velocity/report/slate_xlsx.py's, on purpose — the two
# workbooks are the same product and should not look like two products.
ARIAL = "Arial"
NAVY = "1F3864"
_HEADER_FILL = PatternFill("solid", fgColor=NAVY)
_BAND_FILL = PatternFill("solid", fgColor="F2F5FA")
_THIN = Side(style="thin", color="D9D9D9")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

# Tier colours for the curated card — the one place colour carries meaning.
_TIER_FILL = {
    "A+": PatternFill("solid", fgColor="C6EFCE"),
    "A": PatternFill("solid", fgColor="E2EFDA"),
    "B": PatternFill("solid", fgColor="FFF2CC"),
    "Watch": PatternFill("solid", fgColor="F2F2F2"),
}

# Export column → what the header says. Anything absent falls back to the
# column name with underscores opened out, so a new export column still reads
# sensibly before anyone gets round to naming it here.
DISPLAY_NAMES: Mapping[str, str] = {
    "game_id": "Game ID",
    "away_team": "Away",
    "home_team": "Home",
    "market_spread": "Mkt Spread",
    "market_total": "Mkt Total",
    "moneyline": "Home ML",
    "model_away_score": "Proj Away",
    "model_home_score": "Proj Home",
    "model_total": "Proj Total",
    "spread_edge": "Spread Edge (pts)",
    "total_edge": "Total Edge (pts)",
    "cover_probability": "Home Cover %",
    "over_probability": "Over %",
    "kelly_fraction": "Kelly %",
    "hit_probability": "Model %",
    "fair_price": "Fair Price",
    "market_price": "Price",
    "recommended_stake": "Stake $",
    "75th_percentile": "75th",
    "90th_percentile": "90th",
    "99th_percentile": "99th",
    "value_score": "Value",
    "leverage_score": "Leverage",
    "stack_rating": "Stack",
    "single_entry": "Single Entry",
    "gpp": "GPP",
    "bet_type": "Type",
    "stake": "Stake $",
}

# Number formats, by export column name.
_PERCENT = {
    "cover_probability", "over_probability", "hit_probability", "edge",
    "kelly_fraction", "ownership",
}
_MONEY = {"recommended_stake", "stake", "salary"}
_SIGNED_INT = {"moneyline", "fair_price", "market_price"}
_TWO_DP = {
    "model_away_score", "model_home_score", "model_total", "projection",
    "median", "ceiling", "cash", "single_entry", "gpp", "75th_percentile",
    "90th_percentile", "99th_percentile", "value_score",
}
_ONE_DP = {
    "market_spread", "market_total", "spread_edge", "total_edge", "line",
    "point", "confidence", "leverage_score", "stack_rating",
}
# Columns read as text, left-aligned. Everything else centres.
_LEFT = {
    "away_team", "home_team", "player", "team", "selection", "reason",
    "weather", "market", "position", "tier", "bet_type", "section",
    "metric", "detail", "game_id",
}
# Wider than the header needs, because the content is prose.
_WIDTHS = {"reason": 88, "selection": 30, "weather": 30, "player": 24,
           "detail": 96, "metric": 26, "market": 18, "away_team": 22,
           "home_team": 22, "game_id": 16, "section": 14}


def header_for(column: str) -> str:
    """The header this column wears in the workbook."""
    return DISPLAY_NAMES.get(column, column.replace("_", " ").title())


def _cell_value(value: Any) -> Any:
    """A pandas cell as something openpyxl will accept, or ``None``.

    An empty export cell has to reach the sheet as a genuinely blank cell:
    Excel's own functions treat blank and zero differently, and this export's
    whole posture is that "unknown" and "zero" are different claims.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return float(value)
    return str(value)


def _format_cell(cell: Any, column: str) -> None:
    cell.font = Font(name=ARIAL, size=10)
    cell.alignment = Alignment(horizontal="left" if column in _LEFT else "center")
    if column in _PERCENT:
        cell.number_format = "0.0%"
    elif column in _MONEY:
        cell.number_format = "$#,##0.00" if column != "salary" else "$#,##0"
    elif column in _SIGNED_INT:
        cell.number_format = "+#,##0;-#,##0"
    elif column in _TWO_DP:
        cell.number_format = "0.00"
    elif column in _ONE_DP:
        cell.number_format = "0.0"


def _title_block(ws: Worksheet, title: str, subtitle: str, width: int) -> None:
    ws.sheet_view.showGridLines = False
    span = max(width, 1)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=span)
    top = ws.cell(row=1, column=1, value=title)
    top.font = Font(name=ARIAL, bold=True, size=15, color=NAVY)
    ws.row_dimensions[1].height = 21
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=span)
    sub = ws.cell(row=2, column=1, value=subtitle)
    sub.font = Font(name=ARIAL, italic=True, size=10, color="808080")


def _table_sheet(
    wb: Workbook,
    tab: str,
    title: str,
    subtitle: str,
    frame: pd.DataFrame,
    *,
    drop: Sequence[str] = ("generated_at", "season", "week"),
    tier_column: str | None = None,
) -> Worksheet:
    """One export frame as a formatted, filterable, frozen-header sheet."""
    ws = wb.create_sheet(tab)
    body = frame.drop(columns=[c for c in drop if c in frame.columns], errors="ignore")
    columns = list(body.columns)
    _title_block(ws, title, subtitle, len(columns))

    head = 4
    for j, column in enumerate(columns, start=1):
        cell = ws.cell(row=head, column=j, value=header_for(column))
        cell.font = Font(name=ARIAL, bold=True, color="FFFFFF", size=11)
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = _BORDER

    for i, record in enumerate(body.to_dict("records")):
        row = head + 1 + i
        tier = str(record.get(tier_column, "")) if tier_column else ""
        for j, column in enumerate(columns, start=1):
            cell = ws.cell(row=row, column=j, value=_cell_value(record[column]))
            _format_cell(cell, column)
            cell.border = _BORDER
            if tier and tier in _TIER_FILL:
                cell.fill = _TIER_FILL[tier]
            elif i % 2 == 1:
                cell.fill = _BAND_FILL

    for j, column in enumerate(columns, start=1):
        width = _WIDTHS.get(column, max(len(header_for(column)) + 3, 11))
        ws.column_dimensions[get_column_letter(j)].width = width

    if columns:
        # Filter and freeze: on a tablet there is no mouse and no second
        # window, so tapping a header to sort and keeping it on screen while
        # scrolling is most of what "using" this file means.
        last = get_column_letter(len(columns))
        ws.auto_filter.ref = f"A{head}:{last}{head + max(len(body), 1)}"
        ws.freeze_panes = ws.cell(row=head + 1, column=1)
    return ws


def _dashboard_sheet(wb: Workbook, dashboard: pd.DataFrame, subtitle: str) -> Worksheet:
    """The summary tab, laid out as blocks rather than as the long frame.

    The CSV is long (``section``, ``metric``, ``value``, ``detail``) because
    Power Query pivots it into tiles. There is no pivot on a tablet, so the
    blocks are built here instead — otherwise the first thing the operator
    sees on opening the file is a hundred-row key/value dump.
    """
    ws = wb.create_sheet("Dashboard")
    _title_block(ws, "Velocity — Dashboard", subtitle, 4)

    def block(row: int, heading: str, rows: Sequence[Mapping[str, Any]]) -> int:
        cell = ws.cell(row=row, column=1, value=heading)
        cell.font = Font(name=ARIAL, bold=True, size=12, color=NAVY)
        row += 1
        if not rows:
            note = ws.cell(row=row, column=1, value="nothing to report yet")
            note.font = Font(name=ARIAL, italic=True, size=10, color="808080")
            return row + 2
        for record in rows:
            label = ws.cell(row=row, column=1, value=header_for(str(record["metric"])))
            label.font = Font(name=ARIAL, size=10, color="404040")
            value = _cell_value(record.get("value"))
            vcell = ws.cell(row=row, column=2, value=value)
            vcell.font = Font(name=ARIAL, bold=True, size=10)
            vcell.alignment = Alignment(horizontal="left")
            metric = str(record["metric"])
            if metric in ("win_rate", "pct_beat_close") or metric.startswith("roi"):
                vcell.number_format = "0.0%"
            elif value is not None:
                vcell.number_format = "0.000" if metric.startswith("mean_") else "0.##"
            detail = ws.cell(row=row, column=3, value=str(record.get("detail") or ""))
            detail.font = Font(name=ARIAL, size=10, color="606060")
            detail.alignment = Alignment(horizontal="left")
            row += 1
        return row + 1

    def section(name: str) -> list[Mapping[str, Any]]:
        if dashboard.empty or "section" not in dashboard.columns:
            return []
        rows = dashboard[dashboard["section"] == name].to_dict("records")
        return [{str(k): v for k, v in row.items()} for row in rows]

    row = 4
    row = block(row, "How the model is doing", section("performance"))
    row = block(row, "Return on investment", section("roi"))
    row = block(row, "Closing-line value — the durable signal", section("clv"))
    row = block(row, "Top plays", section("top_plays"))
    row = block(row, "Top props", section("top_props"))
    row = block(row, "Top DFS values", section("top_dfs"))

    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 110
    return ws


_READ_ME: tuple[tuple[str, str], ...] = (
    ("Dashboard", "Model performance, ROI, closing-line value, and the run's best calls."),
    ("Betting Card", "Every game on the board: the market's number, the model's, and the gap."),
    ("Props", "Staked player props, with the simulated distribution behind each line."),
    ("DFS Pool", "The DraftKings slate priced: salary, projection, ceiling, value, stack."),
    ("DFS Optimizer", "One column per contest type — cash 50th, single 75th, "
                      "GPP 90th, ceiling 99th."),
    ("Curated Plays", "A+ / A / B / Watch, each with the argument for it."),
    ("", ""),
    ("Signs", "Spread Edge positive = value on the HOME side."),
    ("", "Total Edge positive = value on the OVER."),
    ("Probabilities", "Counted off the simulation's own distribution over the full 100,000 draws,"),
    ("", "pushes excluded from the numerator exactly as the grader does."),
    ("Blank cells", "Unknown, never zero. Ownership has no source in this system, so it is blank;"),
    ("", "leverage, which is meaningless without it, is blank too."),
    ("Watch tier", "Seen and NOT bet — vetoed, papered, zero-staked, or no positive edge."),
    ("", ""),
    ("Refreshing", "This file is a snapshot. A newer run produces a newer file; there is nothing"),
    ("", "inside it to refresh. See docs/EXCEL_IPAD.md for how each run reaches your device."),
    ("", ""),
    ("Note", "A single slate is a sanity check, not proof of edge — the durable signal is"),
    ("", "closing-line value, validated by backtest. Research tool, not betting advice."),
)


def _read_me_sheet(wb: Workbook, meta: ExportMeta, counts: Mapping[str, int]) -> Worksheet:
    ws = wb.active
    ws.title = "Read Me"
    week = f"Week {meta.week}" if meta.week is not None else "week unknown"
    season = meta.season if meta.season is not None else "season unknown"
    _title_block(ws, "Velocity", f"{season} · {week} · generated {meta.generated_at}", 2)

    row = 4
    for label, text in _READ_ME:
        lcell = ws.cell(row=row, column=1, value=label)
        lcell.font = Font(name=ARIAL, bold=bool(label), size=10, color=NAVY)
        lcell.alignment = Alignment(horizontal="left", vertical="top")
        if text:
            tcell = ws.cell(row=row, column=2, value=text)
            tcell.font = Font(name=ARIAL, size=10, color="404040")
            tcell.alignment = Alignment(horizontal="left", vertical="top")
        row += 1

    row += 1
    heading = ws.cell(row=row, column=1, value="Rows in this file")
    heading.font = Font(name=ARIAL, bold=True, size=11, color=NAVY)
    row += 1
    for tab, count in counts.items():
        ws.cell(row=row, column=1, value=tab).font = Font(name=ARIAL, size=10, color="404040")
        cell = ws.cell(row=row, column=2, value=int(count))
        cell.font = Font(name=ARIAL, size=10)
        cell.alignment = Alignment(horizontal="left")
        row += 1

    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 100
    return ws


def build_workbook(  # noqa: PLR0913 - one sheet per export, plus where to write
    dest: str | Path,
    meta: ExportMeta,
    *,
    games: pd.DataFrame | None = None,
    props: pd.DataFrame | None = None,
    dfs: pd.DataFrame | None = None,
    dfs_optimizer: pd.DataFrame | None = None,
    plays: pd.DataFrame | None = None,
    dashboard: pd.DataFrame | None = None,
) -> Path:
    """Write every export frame into one formatted workbook. Returns the path.

    Each frame is the corresponding ``build_*`` output (or its CSV read back).
    A frame that is missing or empty still gets its tab, carrying its headers
    and no rows — the same rule the CSVs follow, and for the same reason: an
    absent tab reads as a broken file, an empty one reads as an empty board.
    """
    def frame(value: pd.DataFrame | None) -> pd.DataFrame:
        return pd.DataFrame() if value is None else value

    games, props = frame(games), frame(props)
    dfs, dfs_optimizer = frame(dfs), frame(dfs_optimizer)
    plays, dashboard = frame(plays), frame(dashboard)

    week = f"Week {meta.week}" if meta.week is not None else ""
    season = str(meta.season) if meta.season is not None else ""
    subtitle = " · ".join(x for x in (season, week, f"generated {meta.generated_at}") if x)

    wb = Workbook()
    counts = {
        "Betting Card": len(games), "Props": len(props), "DFS Pool": len(dfs),
        "DFS Optimizer": len(dfs_optimizer), "Curated Plays": len(plays),
    }
    _read_me_sheet(wb, meta, counts)
    _dashboard_sheet(wb, dashboard, subtitle)
    _table_sheet(wb, "Betting Card", "Betting Card", subtitle, games)
    _table_sheet(wb, "Props", "Player Props", subtitle, props)
    _table_sheet(wb, "DFS Pool", "DFS Pool", subtitle, dfs)
    _table_sheet(wb, "DFS Optimizer", "DFS Optimizer", subtitle, dfs_optimizer)
    _table_sheet(wb, "Curated Plays", "Curated Plays", subtitle, plays,
                 tier_column="tier")

    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return path
