"""Terminal model card — the football graphic, rendered as a phosphor readout.

A deliberate counter-aesthetic. Betting X is saturated with glossy, over-designed
cards; a monospace terminal readout is both instantly distinct in a feed *and*
honest signalling — this account posts computed output, and the card looks like
computed output.

Two rules keep it from being a costume:

* **Poster scale, not emulation.** The texture is a terminal (monospace grid, box
  rules, bracket headers, phosphor palette) but the type is 2–3× true terminal
  density, because an authentic 80×25 screenshot is unreadable in a phone
  timeline. Legibility wins over authenticity everywhere they conflict.
* **Two phosphors carry meaning.** Green is the model, amber is the market — the
  same model-vs-reality encoding the other cards use, in period-correct colors.
  Nothing is encoded by color alone; every value is labelled.

The grid is built as pure data (:func:`card_lines` → positioned, colored spans),
so the exact character output is unit-testable; :func:`render_terminal_card`
only paints it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

# Phosphor palette. Green = model, amber = market; dim green is chrome (frame,
# labels) and never carries a value.
BG = "#050a06"
GREEN = "#3dff77"
GREEN_DIM = "#1f7a3d"
AMBER = "#ffb000"
INK = "#c8ffd8"

COLS, ROWS = 80, 23
WIDTH, HEIGHT, DPI = 1600, 900, 100

_BLOCKS = " ▁▂▃▄▅▆▇█"  # 0/8 … 8/8 of a cell
_HIST_ROWS = 4

_FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "fonts"
_MONO_CACHE: tuple[str, float] | None = None


def _advance_ratio(path: Path) -> float:
    """Advance width per em for a monospace face (0.6 for most)."""
    try:
        from fontTools.ttLib import TTFont

        font = TTFont(str(path), fontNumber=0)
        return float(font["hmtx"]["A"][0]) / float(font["head"].unitsPerEm)
    except Exception:  # noqa: BLE001 - fall back to the common monospace ratio
        return 0.6


def mono_family() -> tuple[str, float]:
    """Register and name the vendored monospace face (DejaVu Sans Mono fallback).

    Both faces carry the full box-drawing and block-element ranges the card is
    built from; a missing font degrades to matplotlib's bundled mono rather than
    dropping glyphs.
    """
    global _MONO_CACHE
    if _MONO_CACHE is not None:
        return _MONO_CACHE
    family, ratio = "DejaVu Sans Mono", 0.6
    try:
        for path in sorted(_FONT_DIR.glob("*Mono*.ttf")):
            font_manager.fontManager.addfont(str(path))
            family = font_manager.FontProperties(fname=str(path)).get_name()
            ratio = _advance_ratio(path)
    except Exception:  # noqa: BLE001 - typography is a nicety, never fatal
        pass
    _MONO_CACHE = (family, ratio)
    return _MONO_CACHE


@dataclass(frozen=True)
class Span:
    """One colored run of text at a character position."""

    row: int
    col: int
    text: str
    color: str = GREEN


@dataclass(frozen=True)
class TerminalCard:
    """Everything the football terminal card prints. Market fields are optional."""

    league: str  # "NCAAF" / "NFL"
    away_code: str
    home_code: str
    away_name: str
    home_name: str
    kickoff: str  # already formatted, e.g. "SAT NOV 30 · 7:30 PM ET"
    week: str  # "WK 14 · 2024"
    p_home_win: float
    mu_away: float
    mu_home: float
    fair_spread: float  # negative = home favored (repo convention)
    fair_total: float
    total_pmf: dict[int, float]
    n_sims: int
    away_record: str | None = None
    home_record: str | None = None
    market_spread: float | None = None  # positive = home favored (nflverse)
    market_total: float | None = None
    record_line: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def _fmt_spread(home_code: str, away_code: str, home_line: float) -> str:
    """A spread as the favored side and its number ("UGA -23.0")."""
    if home_line == 0:
        return "PK"
    code = home_code if home_line < 0 else away_code
    return f"{code} {-abs(home_line):.1f}"


def probability_bar(fraction: float, width: int) -> tuple[str, str]:
    """A filled/empty block bar — the terminal's progress-meter idiom.

    Returned as ``(filled, empty)`` so the two runs can be painted in the
    phosphor and its dim shade rather than one flat colour.
    """
    filled = int(round(max(0.0, min(1.0, fraction)) * width))
    return "█" * filled, "░" * (width - filled)


def histogram_rows(
    pmf: dict[int, float], lo: int, hi: int, width: int, rows: int = _HIST_ROWS
) -> list[str]:
    """Render a pmf as ``rows`` lines of block characters, tallest bar full height.

    Buckets the support ``[lo, hi]`` into ``width`` columns, then draws each
    column top-down in eighths — the classic terminal bar chart, and the one
    element of this card no other account is posting.
    """
    if not pmf or width <= 0 or hi <= lo:
        return [" " * width for _ in range(rows)]
    # Integer points per column: a fractional bucket width leaves some columns
    # empty and others double-counted, which reads as a comb rather than a curve.
    step = max(1, round((hi - lo) / width))
    n_cols = max(1, min(width, math.ceil((hi - lo) / step)))
    buckets = [0.0] * n_cols
    for value, prob in pmf.items():
        idx = int((value - lo) // step)
        # Values outside the drawn range are dropped, not folded into the end
        # column — a fold would print a spike the distribution doesn't have.
        if 0 <= idx < n_cols:
            buckets[idx] += prob
    peak = max(buckets) or 1.0
    lines: list[str] = []
    for row in range(rows):
        # Row 0 is the top; a column fills from the bottom row upward.
        ceiling = (rows - row) / rows
        floor = (rows - row - 1) / rows
        chars = []
        for weight in buckets:
            share = weight / peak
            if share >= ceiling:
                chars.append("█")
            elif share <= floor:
                chars.append(" ")
            else:
                eighths = int(round((share - floor) * rows * 8))
                chars.append(_BLOCKS[max(0, min(8, eighths))])
        lines.append("".join(chars).ljust(width))
    return lines


# --- block numerals -----------------------------------------------------------
# A 5×3 cell figure set drawn from full blocks — the terminal's own answer to
# "make it bigger". Rendered at grid scale these stand ~200px tall on the 900px
# canvas, so the headline numbers survive a timeline thumbnail; the compact
# layout's uniform type does not.
_GLYPH_ROWS = 5
_BLOCK_FONT: dict[str, tuple[str, ...]] = {
    "0": ("███", "█ █", "█ █", "█ █", "███"),
    "1": (" █ ", "██ ", " █ ", " █ ", "███"),
    "2": ("███", "  █", "███", "█  ", "███"),
    "3": ("███", "  █", "███", "  █", "███"),
    "4": ("█ █", "█ █", "███", "  █", "  █"),
    "5": ("███", "█  ", "███", "  █", "███"),
    "6": ("███", "█  ", "███", "█ █", "███"),
    "7": ("███", "  █", "  █", "  █", "  █"),
    "8": ("███", "█ █", "███", "█ █", "███"),
    "9": ("███", "█ █", "███", "  █", "███"),
    "%": ("█   █", "   █ ", "  █  ", " █   ", "█   █"),
    ".": (" ", " ", " ", " ", "█"),
    "-": ("   ", "   ", "███", "   ", "   "),
    "+": ("   ", " █ ", "███", " █ ", "   "),
    " ": ("   ", "   ", "   ", "   ", "   "),
}


def block_text(text: str) -> list[str]:
    """Render ``text`` as :data:`_GLYPH_ROWS` lines of block characters.

    Unknown characters fall back to a blank cell, so a stray glyph thins the
    headline rather than raising mid-render.
    """
    rows = []
    for row in range(_GLYPH_ROWS):
        parts = [_BLOCK_FONT.get(ch, _BLOCK_FONT[" "])[row] for ch in text]
        rows.append(" ".join(parts))
    return rows


def block_width(text: str) -> int:
    """Grid columns a :func:`block_text` headline occupies (glyphs vary in width)."""
    if not text:
        return 0
    widths = [len(_BLOCK_FONT.get(ch, _BLOCK_FONT[" "])[0]) for ch in text]
    return sum(widths) + len(widths) - 1


def _axis_labels(lo: int, hi: int, width: int, ticks: int = 6) -> tuple[str, list[int]]:
    """A monospace axis line for the histogram plus the tick values used."""
    line = [" "] * width
    values = [int(round(lo + (hi - lo) * i / (ticks - 1))) for i in range(ticks)]
    for value in values:
        col = int((value - lo) / (hi - lo) * (width - 1))
        label = str(value)
        start = max(0, min(width - len(label), col - len(label) // 2))
        for i, ch in enumerate(label):
            line[start + i] = ch
    return "".join(line), values


def card_lines(card: TerminalCard) -> list[Span]:
    """The full character grid as positioned, colored spans (pure — testable)."""
    spans: list[Span] = []
    inner = COLS - 2

    def put(row: int, col: int, text: str, color: str = GREEN) -> None:
        spans.append(Span(row, col, text, color))

    # --- frame -------------------------------------------------------------
    title = f" MATCHUP LABS TERMINAL · {card.league} "
    top = "┌" + title + "─" * (inner - len(title)) + "┐"
    put(0, 0, top, GREEN_DIM)
    put(0, 1, title, GREEN)
    for row in range(1, ROWS - 1):
        put(row, 0, "│", GREEN_DIM)
        put(row, COLS - 1, "│", GREEN_DIM)
    footer = f" >_ @MatchUpLabs · {card.league} MODEL · informational only "
    bottom = "└" + footer + "─" * (inner - len(footer)) + "┘"
    put(ROWS - 1, 0, bottom, GREEN_DIM)
    put(ROWS - 1, 1, footer, GREEN)

    # --- header ------------------------------------------------------------
    put(1, 3, card.week, GREEN_DIM)
    put(1, COLS - 3 - len(card.kickoff), card.kickoff, GREEN_DIM)

    away_txt, home_txt = card.away_name.upper(), card.home_name.upper()
    put(2, 3, away_txt, INK)
    put(2, (COLS - 2) // 2, "AT", GREEN_DIM)
    put(2, COLS - 3 - len(home_txt), home_txt, INK)
    if card.away_record:
        put(3, 3, card.away_record, GREEN_DIM)
    if card.home_record:
        put(3, COLS - 3 - len(card.home_record), card.home_record, GREEN_DIM)

    # --- win probability ---------------------------------------------------
    put(5, 3, "[ WIN PROBABILITY ]", GREEN_DIM)
    bar_w = 44
    for i, (code, prob) in enumerate(
        ((card.away_code, 1.0 - card.p_home_win), (card.home_code, card.p_home_win))
    ):
        row = 6 + i
        filled, empty = probability_bar(prob, bar_w)
        put(row, 3, f"{code:<5}", INK)
        put(row, 9, filled, GREEN)
        put(row, 9 + len(filled), empty, GREEN_DIM)
        put(row, 9 + bar_w + 2, f"{prob:.0%}", INK)

    # --- model / market ----------------------------------------------------
    put(9, 3, "[ MODEL ]", GREEN)
    model_rows = [
        ("PROJ SCORE", f"{card.mu_away:.1f} - {card.mu_home:.1f}"),
        ("FAIR SPREAD", _fmt_spread(card.home_code, card.away_code, card.fair_spread)),
        ("FAIR TOTAL", f"{card.fair_total:.1f}"),
    ]
    for i, (label, value) in enumerate(model_rows):
        put(10 + i, 3, f"{label:<12}", GREEN_DIM)
        put(10 + i, 16, value, INK)

    market_rows: list[tuple[str, str]] = []
    if card.market_spread is not None:
        market_rows.append(
            ("SPREAD", _fmt_spread(card.home_code, card.away_code, -card.market_spread))
        )
    if card.market_total is not None:
        market_rows.append(("TOTAL", f"O/U {card.market_total:.1f}"))
        market_rows.append(
            ("MODEL-MKT", f"{card.fair_total - card.market_total:+.1f} PTS")
        )
    # The market header only prints when a board exists — an empty amber block
    # would imply a number the card doesn't actually have.
    if market_rows:
        put(9, 42, "[ MARKET ]", AMBER)
    for i, (label, value) in enumerate(market_rows):
        put(10 + i, 42, f"{label:<12}", GREEN_DIM)
        put(10 + i, 55, value, AMBER)

    # --- distribution ------------------------------------------------------
    put(14, 3, f"[ SIMULATED TOTAL POINTS · {card.n_sims:,} GAMES ]", GREEN_DIM)
    hist_w, hist_col = 62, 9
    values = sorted(card.total_pmf)
    if values:
        # Trim the thin tails so the body of the curve fills the width.
        ordered = sorted(card.total_pmf.items())
        lo, hi = min(values), max(values)
        cumulative = 0.0
        for total, prob in ordered:
            cumulative += prob
            if cumulative >= 0.02:
                lo = int(math.floor(total / 5.0) * 5)
                break
        cumulative = 0.0
        for total, prob in reversed(ordered):
            cumulative += prob
            if cumulative >= 0.02:
                hi = int(math.ceil(total / 5.0) * 5)
                break
        for i, line in enumerate(histogram_rows(card.total_pmf, lo, hi, hist_w)):
            put(15 + i, hist_col, line, GREEN)
        marker_row = 15 + _HIST_ROWS
        axis, _ = _axis_labels(lo, hi, hist_w)
        put(marker_row + 1, hist_col, axis, GREEN_DIM)
        # The market's number gets its own caret row in amber, directly under
        # the bars — on the axis line it disappears among the tick labels.
        if card.market_total is not None and lo <= card.market_total <= hi:
            col = hist_col + int((card.market_total - lo) / (hi - lo) * (hist_w - 1))
            label = f"^ MKT {card.market_total:.1f}"
            put(marker_row, max(3, col), label, AMBER)

    if card.record_line:
        put(21, 3, card.record_line, AMBER)
    note = card.notes[0] if card.notes else ""
    if note:
        put(21, COLS - 3 - len(note), note, GREEN_DIM)
    return spans



def hero_card_lines(card: TerminalCard) -> list[Span]:
    """The hybrid layout: terminal frame and grammar, headline numbers at poster
    scale.

    The compact layout prints every value at one size, which reads as a wall of
    green at timeline thumbnail size. Here the three numbers that carry the card
    — the favourite's win probability, the model's total, and how far that sits
    from the market — are drawn as block numerals, and the win-probability bars
    are dropped as redundant with the headline. Everything else is unchanged.
    """
    spans: list[Span] = []
    inner = COLS - 2

    def put(row: int, col: int, text: str, color: str = GREEN) -> None:
        spans.append(Span(row, col, text, color))

    # --- frame -------------------------------------------------------------
    title = f" MATCHUP LABS TERMINAL · {card.league} "
    put(0, 0, "┌" + title + "─" * (inner - len(title)) + "┐", GREEN_DIM)
    put(0, 1, title, GREEN)
    for row in range(1, ROWS - 1):
        put(row, 0, "│", GREEN_DIM)
        put(row, COLS - 1, "│", GREEN_DIM)
    footer = f" >_ @MatchUpLabs · {card.league} MODEL · informational only "
    put(ROWS - 1, 0, "└" + footer + "─" * (inner - len(footer)) + "┘", GREEN_DIM)
    put(ROWS - 1, 1, footer, GREEN)

    # --- header ------------------------------------------------------------
    put(1, 3, card.week, GREEN_DIM)
    put(1, COLS - 3 - len(card.kickoff), card.kickoff, GREEN_DIM)
    away_txt = f"{card.away_name.upper()}" + (f"  {card.away_record}" if card.away_record else "")
    home_txt = f"{card.home_name.upper()}" + (f"  {card.home_record}" if card.home_record else "")
    put(2, 3, away_txt, INK)
    put(2, (COLS - 2) // 2, "AT", GREEN_DIM)
    put(2, COLS - 3 - len(home_txt), home_txt, INK)

    # --- headline numbers --------------------------------------------------
    fav_code = card.home_code if card.p_home_win >= 0.5 else card.away_code
    dog_code = card.away_code if card.p_home_win >= 0.5 else card.home_code
    p_fav = max(card.p_home_win, 1.0 - card.p_home_win)
    heroes: list[tuple[str, str, str]] = [
        (f"{p_fav:.0%}", f"{fav_code} WIN · {dog_code} {1 - p_fav:.0%}", GREEN),
        (f"{card.fair_total:.1f}", "MODEL TOTAL", GREEN),
    ]
    if card.market_total is not None:
        gap = card.fair_total - card.market_total
        heroes.append((f"{gap:+.1f}", f"VS MARKET {card.market_total:.1f}", AMBER))
    lane = (COLS - 6) // len(heroes)
    for i, (value, label, color) in enumerate(heroes):
        col = 3 + i * lane
        for row, line in enumerate(block_text(value)):
            put(4 + row, col, line, color)
        put(4 + _GLYPH_ROWS, col, label[: lane - 1], GREEN_DIM)

    # --- the detail line ---------------------------------------------------
    put(11, 3, "MODEL", GREEN)
    put(11, 10, f"{card.mu_away:.1f}-{card.mu_home:.1f}", INK)
    put(11, 22, _fmt_spread(card.home_code, card.away_code, card.fair_spread), INK)
    if card.market_spread is not None or card.market_total is not None:
        put(11, 42, "MARKET", AMBER)
        detail = []
        if card.market_spread is not None:
            detail.append(_fmt_spread(card.home_code, card.away_code, -card.market_spread))
        if card.market_total is not None:
            detail.append(f"O/U {card.market_total:.1f}")
        put(11, 50, "  ".join(detail), AMBER)

    # --- distribution ------------------------------------------------------
    put(13, 3, f"[ SIMULATED TOTAL POINTS · {card.n_sims:,} GAMES ]", GREEN_DIM)
    hist_w, hist_col, hist_rows = 62, 9, 5
    values = sorted(card.total_pmf)
    if values:
        ordered = sorted(card.total_pmf.items())
        lo, hi = min(values), max(values)
        cumulative = 0.0
        for total, prob in ordered:
            cumulative += prob
            if cumulative >= 0.02:
                lo = int(math.floor(total / 5.0) * 5)
                break
        cumulative = 0.0
        for total, prob in reversed(ordered):
            cumulative += prob
            if cumulative >= 0.02:
                hi = int(math.ceil(total / 5.0) * 5)
                break
        for i, line in enumerate(histogram_rows(card.total_pmf, lo, hi, hist_w, hist_rows)):
            put(14 + i, hist_col, line, GREEN)
        marker_row = 14 + hist_rows
        axis, _ = _axis_labels(lo, hi, hist_w)
        put(marker_row + 1, hist_col, axis, GREEN_DIM)
        if card.market_total is not None and lo <= card.market_total <= hi:
            col = hist_col + int((card.market_total - lo) / (hi - lo) * (hist_w - 1))
            put(marker_row, max(3, col), f"^ MKT {card.market_total:.1f}", AMBER)

    if card.record_line:
        put(21, 3, card.record_line, AMBER)
    note = card.notes[0] if card.notes else ""
    if note:
        put(21, COLS - 3 - len(note), note, GREEN_DIM)
    return spans


def render_terminal_card(
    card: TerminalCard, path: Path | str, *, style: str = "hero"
) -> Path:
    """Paint the character grid to a 1600×900 PNG.

    ``style`` picks the layout: ``"hero"`` (headline numbers at poster scale,
    the default) or ``"compact"`` (uniform grid, more rows of detail).
    """
    family, ratio = mono_family()
    fig = plt.figure(figsize=(WIDTH / DPI, HEIGHT / DPI), dpi=DPI)
    fig.patch.set_facecolor(BG)
    ax = fig.add_axes((0, 0, 1, 1))
    # A hair of margin: at exactly COLS the right-hand box rule sits on the
    # canvas edge and gets shaved.
    ax.set_xlim(-0.6, COLS + 0.6)
    ax.set_ylim(ROWS + 0.35, -0.35)
    ax.axis("off")
    ax.set_facecolor(BG)
    # Lock the type size so one glyph advance == one grid column: the box rules
    # only close if the font's advance width matches the cell exactly.
    size = (WIDTH / COLS) / ratio * (72.0 / DPI)
    builder = hero_card_lines if style == "hero" else card_lines
    for span in builder(card):
        ax.text(
            span.col, span.row + 0.72, span.text,
            color=span.color, fontfamily=family, fontsize=size,
            ha="left", va="baseline", linespacing=1.0,
        )
    out = Path(path)
    fig.savefig(out, dpi=DPI, facecolor=BG)
    plt.close(fig)
    return out
