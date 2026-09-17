"""Matchup card rendering — the 1200×1650 portrait graphic (4:5 for X).

The wide :mod:`velocity.report.social_png` family answers "where do the market
and the model disagree" in one 16:9 frame. This one answers "who are these
teams, what does the model think, and why", which needs vertical room — so it
is a sibling renderer with its own frame rather than a squeeze into 16:9.
Everything else is shared: the same ink and surface tokens, the same vendored
Barlow faces, the same cached logo CDN, the same rule that the model's marks
are teal and team brand colors appear only as identity beside a direct label.

Layout, top to bottom, in the order a reader needs it:

1. wordmark, league/week, venue, and the generation stamp
2. both teams — logo, record, and the last three results as W/L chips
3. the board's number, then the model's projected final and win probability
4. **the two distributions** — margin and total, each with the market's number
   marked on it. This is the card's whole argument: a gap is only meaningful
   against the width of the belief it sits in.
5. EPA-per-play ranks as 1→N dot tracks, which explain the projection
6. four projected player lines per side (QB, RB, two pass-catchers)
7. the notes, and the footer disclaimer

Everything degrades: no logo cache → team codes in brand color; no fonts →
matplotlib's default; an empty pmf → the panel renders its numbers without a
curve. A card never fails to render because a nicety was unavailable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch

from velocity.report.assets import logo_path, ncaaf_logo_path, register_fonts
from velocity.report.matchup import MatchupCard, TeamSide
from velocity.report.social_png import (
    _BAD,
    _GOOD,
    BG,
    EDGE,
    INK,
    INK_DIM,
    MODEL,
    SURFACE,
    TRACK,
)

WIDTH, HEIGHT, DPI = 1200, 1650, 100
ASPECT = HEIGHT / WIDTH
BRAND = "#3ddad0"
# The two entity hues for the rank tracks. Validated against this ground, and
# always printed with the team's code beside the dot, so identity never rests
# on color alone.
AWAY_MARK, HOME_MARK = "#d97706", "#0d9488"
CAUTION = "#fab219"

DISPLAY, BODY = register_fonts()

_RANK_ROWS = (
    ("OFFENSE", "off", "EPA / play"),
    ("DEFENSE", "def", "EPA allowed"),
    ("PASS OFFENSE", "off_pass", "EPA / dropback"),
    ("RUSH DEFENSE", "def_rush", "EPA allowed"),
)


def _fig() -> plt.Figure:
    fig = plt.figure(figsize=(WIDTH / DPI, HEIGHT / DPI), dpi=DPI)
    fig.patch.set_facecolor(BG)
    return fig


def _text(fig: plt.Figure, x: float, y: float, s: str, **kw: object) -> None:
    fig.text(x, y, s, **kw)  # type: ignore[arg-type]


def _display(fig: plt.Figure, x: float, y: float, s: str, **kw: object) -> None:
    kw.setdefault("fontfamily", DISPLAY)
    kw.setdefault("fontweight", "bold")
    fig.text(x, y, s, **kw)  # type: ignore[arg-type]


def _panel(fig: plt.Figure, rect: tuple[float, float, float, float]) -> None:
    ax = fig.add_axes((0, 0, 1, 1))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    x, y, w, h = rect
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0,rounding_size=0.010",
        mutation_aspect=ASPECT, facecolor=SURFACE, edgecolor=EDGE, linewidth=1.2))
    ax.set_zorder(0)


def _logo(fig: plt.Figure, side: TeamSide, asset_dir: Path | None,
          league: str, x: float, y_center: float, height: float) -> None:
    """The club mark, or its code in brand color when the cache has nothing.

    The pro leagues address the CDN by slug (keyed off the club code); college
    addresses it by ESPN's numeric team id, so the league picks the lookup.
    """
    path = None
    if asset_dir is not None:
        path = (ncaaf_logo_path(side.espn_id, asset_dir) if league == "ncaaf"
                else logo_path(side.code, asset_dir))
    if path is not None:
        try:
            img = plt.imread(str(path))
            width = height / ASPECT
            ax = fig.add_axes((x, y_center - height / 2, width, height))
            ax.imshow(img)
            ax.axis("off")
            return
        except Exception:  # noqa: BLE001 - a corrupt cache entry is cosmetic
            pass
    # The code stands in for a missing mark, so it has to fit the mark's box.
    # A pro code is 2-3 characters and sets the size; college codes are usually
    # abbreviations, but with no CFBD identity table (no key, no cache) they
    # fall back to the full school name -- and "Georgia" at the pro size runs
    # straight through the nickname beside it.
    code = side.code
    size = 34.0 if len(code) <= 3 else max(12.0, 34.0 * 3.0 / len(code))  # noqa: PLR2004
    _display(fig, x + height / ASPECT / 2, y_center - 0.010, code,
             color=side.color, fontsize=size, ha="center")


def _form_chips(fig: plt.Figure, side: TeamSide, x: float, y: float,
                align: str = "left") -> None:
    """The last three, as colored W/L squares with the score beside each."""
    ax = fig.add_axes((0, 0, 1, 1))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_zorder(3)
    step = 0.112
    start = x if align == "left" else x - step * len(side.last3)
    for i, game in enumerate(side.last3):
        gx = start + i * step
        color = _GOOD if game.result == "W" else _BAD if game.result == "L" else INK_DIM
        ax.add_patch(FancyBboxPatch(
            (gx, y), 0.016, 0.011, boxstyle="round,pad=0,rounding_size=0.004",
            mutation_aspect=ASPECT, facecolor=color, edgecolor="none"))
        _text(fig, gx + 0.008, y + 0.0028, game.result, color=BG, fontsize=9.5,
              ha="center", fontweight="bold")
        _text(fig, gx + 0.021, y + 0.0028, game.label(), color=INK_DIM, fontsize=10.5)


def _curve(fig: plt.Figure, rect: tuple[float, float, float, float],
           pmf: Mapping[int, float], market: float | None, model: float) -> None:
    """One distribution: the filled belief, the market's number, the model's."""
    ax = fig.add_axes(rect)
    ax.set_facecolor("none")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_yticks([])
    if not pmf:
        ax.set_xticks([])
        return
    values = np.array(sorted(pmf), dtype=float)
    probs = np.array([pmf[int(v)] for v in values], dtype=float)
    if len(values) >= 5:  # a light smooth; the pmf is tens of thousands of draws
        kernel = np.ones(5) / 5.0
        probs = np.convolve(probs, kernel, mode="same")
    ax.fill_between(values, probs, color=MODEL, alpha=0.30, linewidth=0)
    ax.plot(values, probs, color=MODEL, linewidth=2.0)
    top = float(probs.max()) if len(probs) else 1.0
    # The two markers are often only a point or two apart — that is the whole
    # subject of the card — so their labels splay away from each other rather
    # than stacking, which overlapped illegibly whenever the gap was small.
    span = float(values.max() - values.min()) or 1.0
    nudge = span * 0.012
    model_right = market is None or model >= market
    if market is not None:
        ax.axvline(market, color=INK, linewidth=1.8, linestyle=(0, (4, 3)))
        ax.text(market + (-nudge if model_right else nudge), top * 1.09, "market",
                color=INK, fontsize=11, fontweight="bold",
                ha="right" if model_right else "left")
    ax.axvline(model, color=MODEL, linewidth=2.0)
    ax.text(model + (nudge if model_right else -nudge), top * 1.24, "model",
            color=MODEL, fontsize=11, fontweight="bold",
            ha="left" if model_right else "right")
    ax.set_ylim(0, top * 1.42)
    ax.set_xlim(values.min(), values.max())
    step = 10 if values.max() - values.min() > 40 else 5
    ticks = [v for v in values if v % step == 0]
    ax.set_xticks(ticks, [f"{int(v)}" for v in ticks])
    ax.tick_params(colors=INK_DIM, labelsize=11, length=0)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color(EDGE)


def _rank_track(fig: plt.Figure, card: MatchupCard, key: str,
                x: float, y: float, w: float) -> None:
    """One measure as a 1→N track with both clubs on it, each dot labelled."""
    ax = fig.add_axes((0, 0, 1, 1))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_zorder(3)
    ax.add_patch(FancyBboxPatch(
        (x, y), w, 0.0030, boxstyle="round,pad=0,rounding_size=0.0015",
        mutation_aspect=ASPECT, facecolor=TRACK, edgecolor="none"))
    for side, color, above in ((card.away, AWAY_MARK, True),
                               (card.home, HOME_MARK, False)):
        entry = side.ranks.get(key)
        if entry is None:
            continue
        cx = x + entry.position() * w
        ax.add_patch(plt.Circle((cx, y + 0.0015), 0.0052, transform=ax.transData,
                                facecolor=color, edgecolor=BG, linewidth=1.4))
        _text(fig, cx, y + (0.0085 if above else -0.0088),
              f"{side.code} {entry.rank}", color=color, fontsize=10.5,
              ha="center", fontweight="bold")


def _player_rows(fig: plt.Figure, side: TeamSide, x: float, top: float,
                 w: float) -> None:
    """Four projected lines — our numbers, and the card says so in the footer."""
    # Two fixed stat sub-columns, each a small unit label LEFT of a
    # right-aligned number. Stacking the label over the value collided with it
    # at this row height, and the two stats collided with each other.
    row_h = 0.0250
    col1_label, col1_value = x + w - 0.208, x + w - 0.120
    col2_label, col2_value = x + w - 0.092, x + w - 0.004
    for i, line in enumerate(side.projections[:4]):
        y = top - i * row_h
        _text(fig, x, y, line.position, color=INK_DIM, fontsize=10.5,
              fontweight="bold")
        name = line.name if len(line.name) <= 19 else line.name[:18] + "\u2026"
        _text(fig, x + 0.028, y, name, color=INK, fontsize=12.5)
        _text(fig, col1_label, y + 0.0012, line.stat, color=INK_DIM, fontsize=8.5)
        _display(fig, col1_value, y - 0.0014, line.value, color=INK, fontsize=15.5,
                 ha="right")
        if line.stat2 and line.value2:
            _text(fig, col2_label, y + 0.0012, line.stat2, color=INK_DIM,
                  fontsize=8.5)
            _display(fig, col2_value, y - 0.0014, line.value2, color=INK,
                     fontsize=15.5, ha="right")
        ax = fig.add_axes((0, 0, 1, 1))
        ax.axis("off")
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_zorder(2)
        ax.plot([x, x + w], [y - 0.0072, y - 0.0072], color=EDGE, linewidth=0.9)


def _market_panel(fig: plt.Figure, card: MatchupCard, key: str, label: str,
                  rect: tuple[float, float, float, float],
                  pmf: Mapping[int, float], market: float | None, model: float,
                  market_text: str, model_text: str) -> None:
    """One market's block: its curve, its three numbers, its lean.

    The curve takes the room a confidence meter used to, which is the right
    trade even before the measurement that removed the meter: the distribution
    is the argument, and a taller one shows the width of the belief the gap
    sits in.
    """
    x, y, w, h = rect
    _panel(fig, rect)
    _text(fig, x + 0.020, y + h - 0.020, label, color=INK, fontsize=13,
          fontweight="bold")
    _text(fig, x + w - 0.020, y + h - 0.020, f"{card.n_sims:,} sims",
          color=INK_DIM, fontsize=10.5, ha="right")
    _curve(fig, (x + 0.020, y + 0.077, w - 0.040, 0.113), pmf, market, model)

    lean = card.spread_lean() if key == "spread" else card.total_lean()
    diff = "—" if market is None else f"{abs(model - market):.1f} pts"
    for i, (head, body) in enumerate((("MARKET", market_text),
                                      ("MODEL", model_text), ("DIFF", diff))):
        cx = x + 0.020 + i * ((w - 0.040) / 3.0)
        _text(fig, cx, y + 0.052, head,
              color=MODEL if head == "MODEL" else INK_DIM, fontsize=9)
        _display(fig, cx, y + 0.028, body, color=INK, fontsize=19)

    tone = CAUTION if lean.wide else (INK if lean.fired else INK_DIM)
    text = f"Lean {lean.label} · {lean.detail}" if lean.fired else lean.detail.capitalize()
    if lean.wide:
        text += " · unusually wide"
    _text(fig, x + 0.020, y + 0.008, text, color=tone, fontsize=11.5)


def render_matchup_card(card: MatchupCard, path: Path | str,
                        asset_dir: Path | None = None) -> Path:
    """Render one matchup card to ``path`` and return it."""
    fig = _fig()
    out = Path(path)

    # 1 — masthead
    _display(fig, 0.033, 0.968, "MATCHUP LABS", color=BRAND, fontsize=23)
    # No week label (a league without weeks, or a schedule that cannot say
    # which one) prints the league alone rather than a dangling separator.
    heading = card.league.upper()
    if card.week_label:
        heading += f" · {card.week_label.upper()}"
    _text(fig, 0.967, 0.972, heading,
          color=INK, fontsize=13, ha="right", fontweight="bold")
    where = " · ".join(p for p in (card.venue,
                                   card.kickoff.strftime("%a %b %-d · %-I:%M%p")
                                   if card.kickoff is not None else None) if p)
    if where:
        _text(fig, 0.967, 0.958, where, color=INK_DIM, fontsize=11, ha="right")
    if card.stamp():
        _text(fig, 0.967, 0.946, card.stamp(), color=INK_DIM, fontsize=9.5, ha="right")
    ax = fig.add_axes((0, 0, 1, 1))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.plot([0.033, 0.967], [0.938, 0.938], color=EDGE, linewidth=1.0)

    # 2 — the two clubs
    mark_h = 0.062
    mark_w = mark_h / ASPECT
    for side, lx, align in ((card.away, 0.033, "left"), (card.home, 0.967, "right")):
        tx = lx + mark_w + 0.018 if align == "left" else lx - mark_w - 0.018
        _logo(fig, side, asset_dir, card.league,
              lx if align == "left" else lx - mark_w, 0.892, mark_h)
        ha = "left" if align == "left" else "right"
        _text(fig, tx, 0.912, side.city.upper(), color=INK_DIM, fontsize=11, ha=ha)
        _display(fig, tx, 0.888, side.nickname.upper(), color=INK, fontsize=27, ha=ha)
        _display(fig, tx, 0.868, side.record, color=INK, fontsize=15, ha=ha)
        _form_chips(fig, side, tx if align == "left" else tx, 0.848, align)

    _text(fig, 0.5, 0.912, "THE NUMBER", color=INK_DIM, fontsize=10, ha="center")
    _display(fig, 0.5, 0.888, card.market_spread_label(), color=INK, fontsize=24,
             ha="center")
    if card.market.total is not None:
        _display(fig, 0.5, 0.866, f"O/U {card.market.total:g}", color=INK_DIM,
                 fontsize=18, ha="center")

    # 3 — the projection
    _panel(fig, (0.033, 0.760, 0.934, 0.072))
    _text(fig, 0.055, 0.812, "PROJECTED FINAL", color=INK_DIM, fontsize=10)
    # Away first, matching the club row above it and the way a slate is read.
    # Leading with the home side here put the two halves of the card in
    # different orders, which is a misread waiting to happen on a score line.
    _display(fig, 0.055, 0.777,
             f"{card.away.code} {card.mu_away:.1f}   {card.home.code} {card.mu_home:.1f}",
             color=INK, fontsize=38)
    _text(fig, 0.945, 0.812, "WIN PROBABILITY", color=INK_DIM, fontsize=10, ha="right")
    leader, prob = ((card.home.code, card.p_home_win) if card.p_home_win >= 0.5
                    else (card.away.code, 1.0 - card.p_home_win))
    _display(fig, 0.945, 0.779, f"{leader} {prob:.1%}", color=INK, fontsize=26,
             ha="right")

    # 4 — the distributions, the card's argument
    _market_panel(fig, card, "spread", "MARGIN", (0.033, 0.520, 0.456, 0.224),
                  card.margin_pmf, card.market.spread_home, card.fair_spread_home,
                  card.market_spread_label(), card.spread_label())
    _market_panel(fig, card, "total", "TOTAL", (0.511, 0.520, 0.456, 0.224),
                  card.total_pmf, card.market.total, card.fair_total,
                  f"{card.market.total:g}" if card.market.total is not None else "—",
                  f"{card.fair_total:.1f}")

    # 5 — the ranks that explain it
    _panel(fig, (0.033, 0.330, 0.934, 0.174))
    _text(fig, 0.055, 0.487, "TEAM RANKS · EPA PER PLAY", color=INK_DIM, fontsize=10)
    any_rank = next((r for s in (card.away, card.home) for r in s.ranks.values()), None)
    if any_rank is not None:
        _text(fig, 0.945, 0.487, card.ranks_caption(any_rank.of),
              color=INK_DIM, fontsize=9.5, ha="right")
    for i, (label, key, note) in enumerate(_RANK_ROWS):
        y = 0.458 - i * 0.032
        _text(fig, 0.055, y - 0.0016, label, color=INK, fontsize=10.5,
              fontweight="bold")
        _rank_track(fig, card, key, 0.200, y, 0.640)
        _text(fig, 0.945, y - 0.0016, note, color=INK_DIM, fontsize=9, ha="right")

    # 6 — the projected player lines
    for side, x in ((card.away, 0.033), (card.home, 0.511)):
        _panel(fig, (x, 0.170, 0.456, 0.146))
        _text(fig, x + 0.022, 0.298, f"{side.city.upper()} · PROJECTIONS",
              color=INK, fontsize=10.5, fontweight="bold")
        _player_rows(fig, side, x + 0.022, 0.272, 0.412)

    # 7 — notes and the footer
    if card.notes:
        _panel(fig, (0.033, 0.086, 0.934, 0.070))
        _text(fig, 0.055, 0.140, "WHAT MOVES THIS GAME", color=INK_DIM, fontsize=10)
        for i, note in enumerate(card.notes[:3]):
            _text(fig, 0.055, 0.118 - i * 0.017, f"·  {note}", color=INK, fontsize=11.5)
    _text(fig, 0.033, 0.040,
          "Model output, not a bet recommendation · player numbers are our "
          "projections, not betting lines", color=INK_DIM, fontsize=10)
    _text(fig, 0.967, 0.040, "@MatchUpLabs", color=BRAND, fontsize=11.5, ha="right",
          fontweight="bold")

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=DPI, facecolor=BG)
    plt.close(fig)
    return out


def matchup_filename(card: MatchupCard, stamp: str, league: str = "nfl") -> str:
    return f"matchup_{league}_{card.away.code}_{card.home.code}_{stamp}.png"


def render_matchup_cards(cards: Sequence[MatchupCard], out_dir: Path | str,
                         stamp: str, *, asset_dir: Path | None = None,
                         league: str = "nfl") -> list[Path]:
    """Render a slate's worth; a card that fails never sinks the others."""
    out_dir = Path(out_dir)
    done: list[Path] = []
    for card in cards:
        try:
            done.append(render_matchup_card(
                card, out_dir / matchup_filename(card, stamp, league), asset_dir))
        except Exception as exc:  # noqa: BLE001 - one bad card is cosmetic
            print(f"matchup card skipped for {card.game_id} ({exc})")
    return done
