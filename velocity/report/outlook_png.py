"""Season-outlook card rendering — one team's full season, portrait (1080×1350).

Same visual language as the social family (dark ground, surface panels,
Barlow Condensed display, teal model marks) in the 4:5 portrait frame the
list format wants: a header, the team block, the projected-record hero, then
one row per game — finished games as W/L results, remaining games as a
teal win-probability bar. Everything degrades like every other card: no
logo → code block, missing fonts → system default.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: the runner lives in CI
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from velocity.report.assets import logo_path, register_fonts
from velocity.report.outlook import SeasonOutlook
from velocity.report.social_png import (
    _BAD,
    _GOOD,
    ACTUAL,
    BG,
    BRAND,
    EDGE,
    INK,
    INK_DIM,
    MODEL,
    SURFACE,
    TRACK,
)

WIDTH, HEIGHT, DPI = 1080, 1350, 100
DISPLAY, BODY = register_fonts()

_MAX_ROWS = 18  # 17-game NFL season + one spare; longer slates get trimmed


def _text(fig: plt.Figure, x: float, y: float, s: str, **kw: object) -> None:
    fig.text(x, y, s, fontfamily=BODY, **kw)  # type: ignore[arg-type]


def _display(fig: plt.Figure, x: float, y: float, s: str, **kw: object) -> None:
    fig.text(x, y, s, fontfamily=DISPLAY, **kw)  # type: ignore[arg-type]


def render_outlook(
    outlook: SeasonOutlook,
    path: Path | str,
    *,
    team_name: str | None = None,
    team_color: str | None = None,
    league: str = "nfl",
    asset_dir: Path | str | None = None,
    note: str = "model output, informational only",
) -> Path:
    """Render the season-outlook card to ``path`` and return it."""
    fig = plt.figure(figsize=(WIDTH / DPI, HEIGHT / DPI), dpi=DPI)
    fig.patch.set_facecolor(BG)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # Header + hero.
    _display(fig, 0.06, 0.955, "MATCHUP LABS", color=BRAND, fontsize=24)
    _display(fig, 0.06, 0.922, "SEASON OUTLOOK", color=INK_DIM, fontsize=24,
             fontweight="semibold")
    _text(fig, 0.94, 0.955, f"{outlook.season} · {league.upper()}", color=INK_DIM,
          fontsize=15, ha="right")

    logo = None if asset_dir is None else logo_path(outlook.team, Path(asset_dir))
    if logo is not None and Path(logo).exists():
        img_ax = fig.add_axes((0.055, 0.83, 0.075, 0.06))
        img_ax.imshow(plt.imread(str(logo)))
        img_ax.set_axis_off()
        name_x = 0.15
    else:
        ax.add_patch(Rectangle((0.055, 0.832), 0.075, 0.055,
                               color=team_color or MODEL))
        _display(fig, 0.0925, 0.848, outlook.team[:4], color=INK, fontsize=20,
                 ha="center")
        name_x = 0.15
    _display(fig, name_x, 0.862, team_name or outlook.team, color=INK, fontsize=34,
             fontweight="bold")
    banked = f"{outlook.wins}-{outlook.losses}" + (
        f"-{outlook.ties}" if outlook.ties else ""
    )
    _text(fig, name_x, 0.834, f"so far {banked}", color=INK_DIM, fontsize=15)
    _display(fig, 0.94, 0.845, outlook.projected_line(), color=ACTUAL, fontsize=30,
             fontweight="bold", ha="right")

    # Rows.
    rows = outlook.rows[:_MAX_ROWS]
    top, bottom = 0.79, 0.085
    step = (top - bottom) / max(len(rows), 1)
    bar_x, bar_w = 0.40, 0.42
    for i, row in enumerate(rows):
        y = top - i * step
        ax.add_patch(Rectangle((0.05, y - step * 0.42), 0.90, step * 0.84,
                               color=SURFACE, ec=EDGE, lw=0.8))
        _text(fig, 0.075, y - 0.006, f"WK {row.week}", color=INK_DIM, fontsize=14)
        prefix = "vs" if row.at_home else "at"
        _display(fig, 0.155, y - 0.008, f"{prefix} {row.opponent}", color=INK,
                 fontsize=18)
        if row.played:
            color = {"W": _GOOD, "L": _BAD}.get(row.result or "", INK_DIM)
            _display(fig, bar_x + 0.02, y - 0.008, row.result or "", color=color,
                     fontsize=19, fontweight="bold")
            _text(fig, bar_x + 0.07, y - 0.006, row.score or "", color=INK_DIM,
                  fontsize=15)
        else:
            p = float(row.p_win or 0.0)
            ax.add_patch(Rectangle((bar_x, y - 0.009), bar_w, 0.014, color=TRACK))
            ax.add_patch(Rectangle((bar_x, y - 0.009), bar_w * p, 0.014,
                                   color=MODEL))
            _display(fig, 0.90, y - 0.008, f"{p:.0%}", color=INK, fontsize=18,
                     ha="right")

    _text(fig, 0.06, 0.038, note, color=INK_DIM, fontsize=12)
    _text(fig, 0.94, 0.038, "@MatchUpLabs", color=BRAND, fontsize=13, ha="right",
          fontweight="bold")

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=DPI, facecolor=BG)
    plt.close(fig)
    return out
