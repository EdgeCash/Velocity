"""Deep Dive card rendering — the analytical 1600×900 companion frame.

Same visual language as the card family (dark ground, surface panels, Barlow
type, teal model marks). Left: the mirrored comparison table — away column,
league-rank pills, the ADV chip in team color where a unit edge clears the
threshold, home column. Right: the margin distribution with the market's
spread drawn on it (the "why the model differs" picture) plus cover/over
percentages, then the extended props list. Rank pills wear the green/amber/
red convention by league tercile — color plus the rank number, never color
alone.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: the runner lives in CI
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from velocity.report.assets import TEAM_META, lighten_for_dark, logo_path
from velocity.report.deepdive import DeepDive, deep_dive_caption
from velocity.report.social import _WATCH_MARKETS
from velocity.report.social_png import (
    ACTUAL,
    BG,
    DPI,
    EDGE,
    INK,
    INK_DIM,
    MODEL,
    TRACK,
    _brand_header,
    _display,
    _fig,
    _footer,
    _image,
    _label_size,
    _panel,
    _text,
    _trim_pmf,
)

_GOOD, _MID, _BAD = "#22c55e", "#fbbf24", "#ef4444"


def _rank_color(rank: int, n_teams: int) -> str:
    """Green / amber / red by league tercile — the reference-genre convention."""
    if n_teams <= 0:
        return INK_DIM
    tercile = rank / n_teams
    if tercile <= 1 / 3:
        return _GOOD
    if tercile <= 2 / 3:
        return _MID
    return _BAD


def _team_hex(dive: DeepDive, side: str) -> str:
    card = dive.card
    code = card.away_code if side == "away" else card.home_code
    explicit = card.away_color if side == "away" else card.home_color
    hex_ = explicit or (TEAM_META[code].color if code in TEAM_META else None)
    return lighten_for_dark(hex_) if hex_ else MODEL


def _stat_table(fig: plt.Figure, dive: DeepDive) -> None:
    card = dive.card
    x_away_v, x_away_r = 0.205, 0.245
    x_adv = 0.285
    x_home_r, x_home_v = 0.325, 0.365
    _text(fig, 0.065, 0.60, f"{dive.stat_season} FORM · MODEL INPUTS",
          color=INK_DIM, fontsize=13.5)
    for label, x in ((card.away_code, x_away_v), (card.home_code, x_home_v)):
        _display(fig, x, 0.565, label[:4] if len(label) > 6 else label,
                 color=INK, fontsize=14, ha="center", fontweight="semibold")
    _text(fig, x_adv, 0.567, "ADV", color=INK_DIM, fontsize=11, ha="center")

    ax = fig.add_axes((0, 0, 1, 1))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    # Rows span the panel exactly: first at 0.53, last no lower than ~0.215.
    step = 0.315 / max(len(dive.rows) - 1, 1)
    for i, row in enumerate(dive.rows):
        y = 0.53 - i * step
        _text(fig, 0.065, y, row.label, color=INK_DIM, fontsize=11.5)
        _display(fig, x_away_v, y - 0.003, row.away_text(), color=INK,
                 fontsize=16, ha="center", fontweight="semibold")
        _text(fig, x_away_r, y, str(row.away_rank),
              color=_rank_color(row.away_rank, row.n_teams), fontsize=12,
              ha="center", fontweight="bold")
        _text(fig, x_home_r, y, str(row.home_rank),
              color=_rank_color(row.home_rank, row.n_teams), fontsize=12,
              ha="center", fontweight="bold")
        _display(fig, x_home_v, y - 0.003, row.home_text(), color=INK,
                 fontsize=16, ha="center", fontweight="semibold")
        if row.advantage:
            code = card.away_code if row.advantage == "away" else card.home_code
            color = _team_hex(dive, row.advantage)
            ax.add_patch(FancyBboxPatch(
                (x_adv - 0.026, y - 0.009), 0.052, 0.030,
                boxstyle="round,pad=0,rounding_size=0.006", mutation_aspect=9 / 16,
                facecolor=TRACK, edgecolor=color, linewidth=1.6,
            ))
            _display(fig, x_adv, y - 0.001, code[:4], color=color, fontsize=11,
                     ha="center", fontweight="bold")
        else:
            _text(fig, x_adv, y, "—", color=INK_DIM, fontsize=11, ha="center")


def _margin_axes(fig: plt.Figure, dive: DeepDive) -> None:
    """The margin distribution with the market spread drawn where it falls."""
    card = dive.card
    view = card.market_view
    _text(fig, 0.475, 0.60, f"SIMULATED MARGIN ({card.home_code} − "
          f"{card.away_code}) · {card.n_sims:,} GAMES",
          color=INK_DIM, fontsize=13.5)
    pmf = _trim_pmf(dict(dive.margin_pmf))
    ax = fig.add_axes((0.475, 0.335, 0.46, 0.24))
    ax.set_facecolor("none")
    values = sorted(pmf)
    probs = [pmf[v] for v in values]
    ax.bar(values, probs, width=0.92, color=MODEL, edgecolor="none")
    top = max(probs) if probs else 0.1
    fair = -card.fair_spread  # fair home margin
    ax.axvline(fair, color=INK_DIM, linewidth=1.6, linestyle=(0, (4, 3)))
    ax.text(fair, top * 1.16, f"model {fair:+.1f}", color=INK_DIM,
            fontsize=11.5, ha="center")
    if view is not None and view.spread_home is not None:
        market = -view.spread_home  # the margin the market line implies
        ax.axvline(market, color=ACTUAL, linewidth=1.8, linestyle=(0, (4, 3)))
        ax.text(market, top * 1.02, f"market {market:+.1f}", color=ACTUAL,
                fontsize=11.5, ha="center")
    ax.set_ylim(0, top * 1.3)
    ax.set_yticks([])
    ticks = [v for v in values if v % 10 == 0]
    ax.set_xticks(ticks, [str(v) for v in ticks])
    ax.tick_params(colors=INK_DIM, labelsize=11, length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color(EDGE)

    facts: list[tuple[str, str]] = []
    if view is not None and view.spread_home is not None and dive.p_home_cover is not None:
        facts.append((f"{card.home_code} {view.spread_home:+g} COVERS",
                      f"{dive.p_home_cover:.0%} of sims"))
    if view is not None and view.total is not None and dive.p_over is not None:
        facts.append((f"OVER {view.total:g} HITS", f"{dive.p_over:.0%} of sims"))
    for i, (label, value) in enumerate(facts):
        x = 0.475 + i * 0.24
        _text(fig, x, 0.285, label, color=INK_DIM, fontsize=12)
        _display(fig, x, 0.240, value, color=INK, fontsize=23,
                 fontweight="semibold")


def _props_panel(fig: plt.Figure, dive: DeepDive) -> None:
    _text(fig, 0.065, 0.152, "TOP PROPS · MARKET LINE vs MODEL",
          color=INK_DIM, fontsize=13.5)
    if not dive.watch:
        _text(fig, 0.065, 0.115, "no prop board posted yet", color=INK_DIM,
              fontsize=14)
        return
    for i, entry in enumerate(list(dive.watch)[:6]):
        col, row_i = i % 3, i // 3
        x = 0.065 + col * 0.30
        y = 0.118 - row_i * 0.052
        unit = _WATCH_MARKETS.get(entry.market, (entry.market, entry.market))[1]
        avg = f"{entry.mean:.1f}" if entry.mean < 20 else f"{entry.mean:.0f}"
        _display(fig, x, y, entry.player, color=INK, fontsize=14,
                 fontweight="semibold")
        _text(fig, x, y - 0.026,
              f"{unit} {entry.line:g} · model {avg} · {entry.p_over:.0%} over",
              color=INK_DIM, fontsize=11)


def render_deep_dive(dive: DeepDive, path: Path | str,
                     asset_dir: Path | str | None = None) -> Path:
    """Render one deep-dive card to ``path`` (1600×900 PNG)."""
    assets = None if asset_dir is None else Path(asset_dir)
    card = dive.card
    fig = _fig()
    when = ""
    if card.kickoff is not None:
        central = card.kickoff.tz_localize("UTC").tz_convert("America/Chicago")
        when = central.strftime("%A, %b %-d · %-I:%M %p CT").upper()
    _panel(fig, (0.04, 0.655, 0.92, 0.245))
    _panel(fig, (0.04, 0.185, 0.40, 0.44))
    _panel(fig, (0.455, 0.185, 0.505, 0.44))
    _panel(fig, (0.04, 0.05, 0.92, 0.115))
    _brand_header(fig, "DEEP DIVE", when)

    away_logo = _image(fig, logo_path(card.away_code, assets), 0.065, 0.80, 0.10)
    ax_x = 0.135 if away_logo else 0.07
    _display(fig, ax_x, 0.795, card.away_code, color=INK,
             fontsize=_label_size(card.away_code, 40))
    _text(fig, ax_x, 0.755, f"{dive.away_record} · {dive.stat_season}",
          color=INK_DIM, fontsize=13)
    home_logo = _image(fig, logo_path(card.home_code, assets), 0.878, 0.80, 0.10)
    hx = 0.862 if home_logo else 0.93
    _display(fig, hx, 0.795, card.home_code, color=INK,
             fontsize=_label_size(card.home_code, 40), ha="right")
    _text(fig, hx, 0.755, f"{dive.stat_season} · {dive.home_record}",
          color=INK_DIM, fontsize=13, ha="right")
    _display(fig, 0.5, 0.80, "@", color=INK_DIM, fontsize=22, ha="center",
             fontweight="semibold")
    if card.market:
        _text(fig, 0.5, 0.757, card.market, color=INK_DIM, fontsize=13.5,
              ha="center")
    _text(fig, 0.5, 0.712,
          f"model: {card.away_code} {1 - card.p_home_win:.0%} · "
          f"projected {card.mu_away:.1f}–{card.mu_home:.1f} · "
          f"{card.home_code} {card.p_home_win:.0%}",
          color=INK, fontsize=15, ha="center")
    _text(fig, 0.5, 0.678,
          f"fair line {card.spread_label()} · fair total {card.fair_total:.1f}",
          color=INK_DIM, fontsize=13, ha="center")

    _stat_table(fig, dive)
    _margin_axes(fig, dive)
    _props_panel(fig, dive)
    if card.record_line:
        _text(fig, 0.95, 0.898, f"{card.record_line} · GRADED DAILY",
              color=INK, fontsize=13, ha="right")
    _footer(fig, "form + EPA from play-by-play · simulation vs the market's "
                 "posted numbers · informational only")
    out = Path(path)
    fig.savefig(out, dpi=DPI, facecolor=BG)
    plt.close(fig)
    return out


def deep_dive_filename(dive: DeepDive, stamp: str, league: str = "nfl") -> str:
    away = "".join(c for c in dive.card.away_code if c.isalnum())
    home = "".join(c for c in dive.card.home_code if c.isalnum())
    return f"deepdive_{league}_{stamp}_{away}_at_{home}.png"


def render_deep_dives(
    dives: list[DeepDive], out_dir: Path | str, stamp: str,
    asset_dir: Path | str | None = None, league: str = "nfl",
) -> list[Path]:
    """Render every deep dive plus a captions file of post copy."""
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    paths = [
        render_deep_dive(d, folder / deep_dive_filename(d, stamp, league), asset_dir)
        for d in dives
    ]
    if dives:
        copy = "\n\n---\n\n".join(deep_dive_caption(d) for d in dives)
        (folder / f"deepdive_{league}_{stamp}_captions.md").write_text(copy + "\n")
    return paths
