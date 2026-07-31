"""Social model cards — PNG rendering (1200×675, the X/Twitter card frame).

One fixed layout so the format itself is the brand: wordmark top-left, matchup
title, the away/amber vs home/teal win split, four hero tiles, the simulated
total-runs distribution (the card's signature — nobody else posts their model's
actual distribution), and the players-to-watch strip. Palette is
validator-checked against the dark surface (teal ``#0d9488`` / amber
``#d97706`` pass the lightness band, CVD separation, and contrast checks);
text always wears ink tokens, never a mark color.

Static image → no hover layer; identity is direct-labeled (team codes on the
split bar), the single-series histogram needs no legend, and the only value
labels are selective (the modal total, the fair-total marker).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: the collector runs in CI
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from velocity.report.social import _WATCH_MARKETS, SocialCard

# Surfaces + ink (text) tokens.
BG = "#0b0f14"
SURFACE = "#10151c"
EDGE = "#1d2430"
INK = "#e8edf2"
INK_DIM = "#7d8894"
BRAND = "#3ddad0"  # wordmark accent only — never a data mark
# Mark palette — validated (dark surface #10151c): fixed assignment, every card.
HOME = "#0d9488"  # teal
AWAY = "#d97706"  # amber
TRACK = "#1d2430"

WIDTH, HEIGHT, DPI = 1200, 675, 100


def _fig() -> plt.Figure:
    fig = plt.figure(figsize=(WIDTH / DPI, HEIGHT / DPI), dpi=DPI)
    fig.patch.set_facecolor(BG)
    return fig


def _text(fig: plt.Figure, x: float, y: float, s: str, **kw: object) -> None:
    fig.text(x, y, s, **kw)  # type: ignore[arg-type]


def _rounded(ax: plt.Axes, x: float, w: float, color: str, *, y: float = 0.0,
             h: float = 1.0) -> None:
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0,rounding_size=0.012",
        mutation_aspect=0.08, facecolor=color, edgecolor="none",
    ))


def _header(fig: plt.Figure, card: SocialCard) -> None:
    _text(fig, 0.055, 0.935, "MATCHUP LABS", color=BRAND, fontsize=17, fontweight="bold")
    _text(fig, 0.232, 0.935, "MODEL CARD", color=INK_DIM, fontsize=17)
    when = ""
    if card.kickoff is not None:
        central = card.kickoff.tz_localize("UTC").tz_convert("America/Chicago")
        when = central.strftime("%b %-d · %-I:%M %p CT")
    _text(fig, 0.945, 0.935, when, color=INK_DIM, fontsize=13, ha="right")
    _text(
        fig, 0.055, 0.862,
        f"{card.away_name.upper()}  @  {card.home_name.upper()}",
        color=INK, fontsize=21, fontweight="bold",
    )


def _win_bar(fig: plt.Figure, card: SocialCard) -> None:
    ax = fig.add_axes((0.055, 0.755, 0.89, 0.048))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    p_home = card.p_home_win
    p_away = 1.0 - p_home
    gap = 0.003  # the 2px spacer between adjacent fills
    _rounded(ax, 0.0, max(p_away - gap, 0.0), AWAY)
    _rounded(ax, p_away + gap, max(p_home - gap, 0.0), HOME)
    _text(fig, 0.055, 0.812, f"{card.away_code}  {p_away:.0%}", color=INK,
          fontsize=14, fontweight="bold")
    _text(fig, 0.945, 0.812, f"{p_home:.0%}  {card.home_code}", color=INK,
          fontsize=14, fontweight="bold", ha="right")


def _tiles(fig: plt.Figure, card: SocialCard) -> None:
    tiles = [
        ("PROJECTED SCORE", f"{card.mu_away:.1f} – {card.mu_home:.1f}"),
        ("FAIR TOTAL", f"{card.fair_total:.1f}"),
        ("F5 TOTAL", f"{card.f5_fair_total:.1f}"),
        ("FIRST-INNING RUN", f"{card.p_yrfi:.0%}"),
    ]
    for i, (label, value) in enumerate(tiles):
        x = 0.055 + i * 0.2275
        _text(fig, x, 0.685, label, color=INK_DIM, fontsize=11)
        _text(fig, x, 0.625, value, color=INK, fontsize=24, fontweight="bold")


def _histogram(fig: plt.Figure, card: SocialCard) -> None:
    _text(fig, 0.055, 0.545, f"SIMULATED TOTAL RUNS · {card.n_sims:,} GAMES",
          color=INK_DIM, fontsize=11)
    ax = fig.add_axes((0.055, 0.14, 0.52, 0.375))
    ax.set_facecolor(BG)
    values = sorted(card.total_runs_pmf)
    probs = [card.total_runs_pmf[v] for v in values]
    ax.bar(values, probs, width=0.82, color=HOME, edgecolor="none")
    top = max(probs) if probs else 0.0
    if top > 0:  # selective direct label: the modal total only
        mode = values[probs.index(top)]
        ax.text(mode, top + top * 0.06, f"{mode}", color=INK, fontsize=11,
                ha="center", fontweight="bold")
    ax.axvline(card.fair_total, color=INK_DIM, linewidth=1.2, linestyle=(0, (4, 3)))
    ax.text(card.fair_total + 0.35, (top or 0.1) * 1.18, f"fair {card.fair_total:.1f}",
            color=INK_DIM, fontsize=10, ha="left")
    ax.set_ylim(0, (top or 0.1) * 1.3)
    ax.set_yticks([])
    last = values[-1] if values else 0
    ticks = [v for v in values if v % 2 == 0 and v != last - 1] + ([last] if last else [])
    ax.set_xticks(ticks, [f"{v}+" if v == last else str(v) for v in ticks])
    ax.tick_params(colors=INK_DIM, labelsize=10, length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color(EDGE)


def _watch(fig: plt.Figure, card: SocialCard) -> None:
    _text(fig, 0.63, 0.545, "PLAYERS TO WATCH", color=INK_DIM, fontsize=11)
    if not card.watch:
        _text(fig, 0.63, 0.48, "lineups not posted yet", color=INK_DIM, fontsize=12)
        return
    for i, entry in enumerate(card.watch[:3]):
        y = 0.487 - i * 0.128
        stat_label = _WATCH_MARKETS.get(entry.market, (entry.market, ""))[0]
        _text(fig, 0.63, y, f"{entry.player}", color=INK, fontsize=14,
              fontweight="bold")
        _text(fig, 0.945, y, stat_label, color=INK_DIM, fontsize=11, ha="right")
        _text(fig, 0.63, y - 0.042, entry.fact(), color=INK_DIM, fontsize=11.5)
        # The probability at the stated line, as a thin filled track.
        ax = fig.add_axes((0.63, y - 0.075, 0.315, 0.014))
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        _rounded(ax, 0.0, 1.0, TRACK)
        _rounded(ax, 0.0, max(entry.p_over, 0.012), HOME)


def _footer(fig: plt.Figure, card: SocialCard) -> None:
    date = "" if card.kickoff is None else card.kickoff.strftime("%Y-%m-%d · ")
    _text(fig, 0.055, 0.055,
          f"{date}Monte Carlo simulation · model output, informational only",
          color=INK_DIM, fontsize=10)
    _text(fig, 0.945, 0.055, "@MatchUpLabs", color=BRAND, fontsize=10, ha="right",
          fontweight="bold")


def render_card(card: SocialCard, path: Path | str) -> Path:
    """Render one card to ``path`` (1200×675 PNG); returns the path."""
    fig = _fig()
    _header(fig, card)
    _win_bar(fig, card)
    _tiles(fig, card)
    _histogram(fig, card)
    _watch(fig, card)
    _footer(fig, card)
    out = Path(path)
    fig.savefig(out, dpi=DPI, facecolor=BG)
    plt.close(fig)
    return out


def card_filename(card: SocialCard, stamp: str) -> str:
    """A stable, filesystem-safe name: ``social_mlb_<stamp>_AWY_at_HOM.png``."""
    away = "".join(c for c in card.away_code if c.isalnum())
    home = "".join(c for c in card.home_code if c.isalnum())
    return f"social_mlb_{stamp}_{away}_at_{home}.png"


def render_cards(
    cards: list[SocialCard], out_dir: Path | str, stamp: str
) -> list[Path]:
    """Render every card plus a ``social_mlb_<stamp>_captions.md`` post-copy file."""
    from velocity.report.social import caption

    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    paths = [render_card(card, folder / card_filename(card, stamp)) for card in cards]
    if cards:
        copy = "\n\n---\n\n".join(caption(card) for card in cards)
        (folder / f"social_mlb_{stamp}_captions.md").write_text(copy + "\n")
    return paths
