"""Social card rendering — the MatchUp Labs graphic family (1600×900 PNG).

The broadcast-grade pass: layered surface panels on the dark ground, vendored
Barlow Condensed display type for headlines and hero numbers, real team logos
from the public CDN (cached, best-effort), team-brand-colored win bars, and a
one-line consensus market strip — SportsCenter grammar without the stat wall.
1600×900 is the X/Twitter card frame.

Three cards, one visual language:

* **Market vs Model card** (pregame) — team blocks with logos, the
  team-colored win split, then the heart of the card: the market's consensus
  numbers over the model's numbers in equal type (spread / total / win prob),
  a verdict strip that only wears color where disagreement clears the
  published thresholds, a top-props strip in the same micro-grammar, and the
  season receipt line. The market is the benchmark; the delta is the content.
* **Sim Check** (post-game) — the actual result pinned on the pregame
  distribution, percentile as the hero number.
* **Model record** — yesterday graded plus the season line.

Color rules: the model's distributions are always teal and the *actual result*
always amber (validated against the dark surface) — the reality-vs-model
contrast never changes. Team brand colors appear only where identity is the
message (the win split) and always beside a direct code label, never as the
only cue. Text wears ink tokens. Everything degrades: no asset cache → no
logos; missing fonts → system default; a card never fails to render because a
nicety was unavailable.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: the runner lives in CI
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import FancyBboxPatch

from velocity.report.assets import bar_colors, logo_path, register_fonts
from velocity.report.sim_check import SimCheckCard, ordinal, sim_check_caption
from velocity.report.social import _WATCH_MARKETS, MarketView, SocialCard

# Surfaces + ink (text) tokens.
BG = "#0b0f14"
SURFACE = "#131a23"
EDGE = "#232d3a"
INK = "#eef2f6"
INK_DIM = "#8b96a3"
BRAND = "#3ddad0"  # wordmark accent only — never a data mark
# Model-mark palette — validated (dark surface): the model is teal, reality is
# amber, on every card.
MODEL = "#0d9488"
ACTUAL = "#d97706"
TRACK = "#1d2430"

WIDTH, HEIGHT, DPI = 1600, 900, 100
_GOOD = "#22c55e"
_BAD = "#ef4444"

DISPLAY, BODY = register_fonts()
matplotlib.rcParams["font.family"] = BODY


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
    """A rounded surface panel — the layering that lifts the card off the ground."""
    ax = fig.add_axes((0, 0, 1, 1))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    x, y, w, h = rect
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0,rounding_size=0.012",
        mutation_aspect=9 / 16, facecolor=SURFACE, edgecolor=EDGE, linewidth=1.2,
    ))
    ax.set_zorder(0)


def _image(
    fig: plt.Figure, path: Path | None, x: float, y_center: float, height: float
) -> bool:
    """Place a cached PNG (logo) by figure coords; False if unavailable."""
    if path is None:
        return False
    try:
        img = plt.imread(str(path))
    except Exception:  # noqa: BLE001 - a corrupt cache entry is cosmetic
        return False
    width = height * HEIGHT / WIDTH  # square on screen despite the 16:9 figure
    ax = fig.add_axes((x, y_center - height / 2, width, height))
    ax.imshow(img)
    ax.axis("off")
    return True


def _rounded(ax: plt.Axes, x: float, w: float, color: str, *, y: float = 0.0,
             h: float = 1.0) -> None:
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0,rounding_size=0.012",
        mutation_aspect=0.08, facecolor=color, edgecolor="none",
    ))


def _brand_header(fig: plt.Figure, kind: str, when: str) -> None:
    _display(fig, 0.05, 0.938, "MATCHUP LABS", color=BRAND, fontsize=27)
    _display(fig, 0.185, 0.938, kind, color=INK_DIM, fontsize=27, fontweight="semibold")
    _text(fig, 0.95, 0.941, when, color=INK_DIM, fontsize=16, ha="right")


def _footer(fig: plt.Figure, note: str) -> None:
    _text(fig, 0.05, 0.042, note, color=INK_DIM, fontsize=12.5)
    _text(fig, 0.95, 0.042, "@MatchUpLabs", color=BRAND, fontsize=13, ha="right",
          fontweight="bold")


def _histogram_axes(
    fig: plt.Figure,
    rect: tuple[float, float, float, float],
    pmf: dict[int, float],
    fair: float,
    *,
    highlight: int | None = None,
) -> None:
    """The total-points distribution: teal bars, fair-total marker, optional
    amber highlight on the actual result (the Sim Check's reality mark)."""
    ax = fig.add_axes(rect)
    ax.set_facecolor("none")
    values = sorted(pmf)
    probs = [pmf[v] for v in values]
    colors = [ACTUAL if highlight is not None and v == highlight else MODEL
              for v in values]
    ax.bar(values, probs, width=0.92, color=colors, edgecolor="none")
    top = max(probs) if probs else 0.0
    if top > 0:  # selective direct labels: the modal total, and the actual result
        mode = values[probs.index(top)]
        ax.text(mode, top + top * 0.06, f"{mode}", color=INK, fontsize=14,
                ha="center", fontweight="bold")
    if highlight is not None and highlight in pmf:
        ax.text(highlight, pmf[highlight] + (top or 0.1) * 0.06, "actual",
                color=ACTUAL, fontsize=13, ha="center", fontweight="bold")
    ax.axvline(fair, color=INK_DIM, linewidth=1.6, linestyle=(0, (4, 3)))
    ax.text(fair + 0.6, (top or 0.1) * 1.18, f"fair {fair:.1f}",
            color=INK_DIM, fontsize=12.5, ha="left")
    ax.set_ylim(0, (top or 0.1) * 1.3)
    ax.set_yticks([])
    ticks = [v for v in values if v % 10 == 0]
    ax.set_xticks(ticks, [str(v) for v in ticks])
    ax.tick_params(colors=INK_DIM, labelsize=12, length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color(EDGE)


def _trim_pmf(
    pmf: dict[int, float], *, tail: float = 0.005, ensure: int | None = None
) -> dict[int, float]:
    """Trim the display tails past ``tail`` cumulative probability each side.

    Folding the tail into one bucket paints a phantom spike at the cap; a
    football total's tail is long and thin, so the honest display is to stop
    drawing where the probability runs out. ``ensure`` extends the support to
    include a value the sims never produced (a zero-height position), so an
    off-the-charts actual result still appears on the axis instead of silently
    vanishing — "the sims never got here" is part of the story.
    """
    if not pmf:
        return {}
    values = sorted(pmf)
    cum = 0.0
    lo = values[0]
    for v in values:
        cum += pmf[v]
        if cum > tail:
            lo = v
            break
    cum = 0.0
    hi = values[-1]
    for v in reversed(values):
        cum += pmf[v]
        if cum > tail:
            hi = v
            break
    if ensure is not None:
        lo, hi = min(lo, ensure), max(hi, ensure)
    return {v: pmf.get(v, 0.0) for v in range(lo, hi + 1)}


# --- the pregame MARKET vs MODEL card ------------------------------------------


def _label_size(label: str, base: float) -> float:
    """Auto-shrink for long display labels (school names have no fixed code)."""
    n = len(label)
    if n <= 4:
        return base
    if n <= 9:
        return base * 0.74
    return base * 0.55


def _team_blocks(fig: plt.Figure, card: SocialCard, asset_dir: Path | None,
                 league: str = "nfl") -> None:
    """Away block left, home block right — logo + code — time + projection center.

    Logos render only on NFL cards: every other league (NCAAF schools, but
    also MLB/WNBA, whose abbreviations COLLIDE with NFL codes — an MLB "ARI"
    must never draw the Cardinals) is deliberately label + colors.
    """
    logo_dir = asset_dir if league == "nfl" else None
    away_logo = _image(fig, logo_path(card.away_code, logo_dir), 0.065, 0.83, 0.115)
    home_logo = _image(fig, logo_path(card.home_code, logo_dir), 0.873, 0.83, 0.115)
    away_x = 0.145 if away_logo else 0.07
    _display(fig, away_x, 0.815, card.away_code, color=INK,
             fontsize=_label_size(card.away_code, 46))
    home_x = 0.855 if home_logo else 0.93
    _display(fig, home_x, 0.815, card.home_code, color=INK,
             fontsize=_label_size(card.home_code, 46), ha="right")

    when = ""
    if card.kickoff is not None:
        central = card.kickoff.tz_localize("UTC").tz_convert("America/Chicago")
        when = central.strftime("%-I:%M %p CT")
    _display(fig, 0.5, 0.845, "@", color=INK_DIM, fontsize=24, ha="center",
             fontweight="semibold")
    if when:
        _display(fig, 0.5, 0.805, when, color=INK, fontsize=24, ha="center",
                 fontweight="semibold")
    _text(fig, 0.5, 0.768, f"model projects {card.mu_away:.1f} – {card.mu_home:.1f}",
          color=INK_DIM, fontsize=14, ha="center")


def _win_bar(fig: plt.Figure, card: SocialCard) -> None:
    from velocity.report.assets import TEAM_META

    away_hex = card.away_color or (
        TEAM_META[card.away_code].color if card.away_code in TEAM_META else None
    )
    home_hex = card.home_color or (
        TEAM_META[card.home_code].color if card.home_code in TEAM_META else None
    )
    away_color, home_color = bar_colors(away_hex, home_hex)
    ax = fig.add_axes((0.065, 0.675, 0.87, 0.042))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    p_home = card.p_home_win
    p_away = 1.0 - p_home
    gap = 0.003  # the 2px spacer between adjacent fills
    _rounded(ax, 0.0, max(p_away - gap, 0.0), away_color)
    _rounded(ax, p_away + gap, max(p_home - gap, 0.0), home_color)
    _display(fig, 0.065, 0.726, f"{card.away_code} {p_away:.0%}", color=INK,
             fontsize=min(21.0, _label_size(card.away_code, 46) * 0.62),
             fontweight="semibold")
    _display(fig, 0.935, 0.726, f"{p_home:.0%} {card.home_code}", color=INK,
             fontsize=min(21.0, _label_size(card.home_code, 46) * 0.62),
             ha="right", fontweight="semibold")


# Matrix geometry: three market columns, centered; the row-label gutter left.
_COLS = (("spread", 0.335), ("total", 0.585), ("win", 0.825))


def _matrix(fig: plt.Figure, card: SocialCard) -> None:
    """The heart of the card: MARKET on top, MODEL under it in equal type, and
    a verdict strip that only wears the edge color past the thresholds."""
    view = card.market_view or MarketView()
    heads = {"spread": "SPREAD", "total": "TOTAL", "win": "WIN PROB"}
    for key, cx in _COLS:
        _text(fig, cx, 0.578, heads[key], color=INK_DIM, fontsize=13.5, ha="center")
    for label, y in (("MARKET", 0.495), ("MODEL", 0.385)):
        _text(fig, 0.068, y + 0.008, label, color=INK_DIM, fontsize=14.5)
    _text(fig, 0.068, 0.298, "EDGE", color=INK_DIM, fontsize=14.5)

    implied = view.implied_home_prob()
    market_cells = {
        "spread": "—" if view.spread_home is None
        else f"{card.home_code} {view.spread_home:+g}",
        "total": "—" if view.total is None else f"{view.total:g}",
        "win": "—" if implied is None else f"{card.home_code} {implied:.0%}",
    }
    model_cells = {
        "spread": card.spread_label(),
        "total": f"{card.fair_total:.1f}",
        "win": f"{card.home_code} {card.p_home_win:.0%}",
    }
    # Equal type size on both rows — the honesty signal: neither number
    # outranks. One shared size per column, shrunk only when a long school
    # label would overflow the column.
    for key, cx in _COLS:
        size = 36.0
        longest = max(len(market_cells[key]), len(model_cells[key]))
        if longest > 12:
            size = 36.0 * min(1.0, 12 / longest)
        color = INK if market_cells[key] != "—" else INK_DIM
        _display(fig, cx, 0.475, market_cells[key], color=color, fontsize=size,
                 ha="center")
        _display(fig, cx, 0.365, model_cells[key], color=INK, fontsize=size,
                 ha="center")
    if view.ml_away is not None and view.ml_home is not None:
        _text(fig, _COLS[2][1], 0.443,
              f"{card.away_code} {view.ml_away:+d} · {card.home_code} {view.ml_home:+d}",
              color=INK_DIM, fontsize=11.5, ha="center")

    edges = card.edges()
    ax = fig.add_axes((0, 0, 1, 1))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    for key, cx in _COLS:
        call = edges[key]
        if call.fired:
            ax.add_patch(FancyBboxPatch(
                (cx - 0.105, 0.272), 0.21, 0.062,
                boxstyle="round,pad=0,rounding_size=0.012", mutation_aspect=9 / 16,
                facecolor=TRACK, edgecolor=_GOOD, linewidth=2.0,
            ))
            lean = f"LEAN {call.label}"
            _display(fig, cx, 0.308, lean, color=_GOOD,
                     fontsize=19.0 * min(1.0, 15 / len(lean)),
                     ha="center", fontweight="semibold")
            _text(fig, cx, 0.280, call.detail, color=INK_DIM, fontsize=11.5,
                  ha="center")
        else:
            _text(fig, cx, 0.298, call.detail, color=INK_DIM, fontsize=14,
                  ha="center")


def _props_strip(fig: plt.Figure, card: SocialCard) -> None:
    """Up to three prop chips, each repeating the market-vs-model micro-grammar."""
    _text(fig, 0.065, 0.192, "TOP PROPS · MARKET LINE vs MODEL",
          color=INK_DIM, fontsize=13.5)
    if not card.watch:
        _text(fig, 0.065, 0.128, "no prop board posted yet", color=INK_DIM,
              fontsize=15)
        return
    for i, entry in enumerate(card.watch[:3]):
        x = 0.075 + i * 0.30
        # The teal accent bar marks a board-lined prop where the model's
        # probability sits well off 50% — the same "edge only" color logic.
        if entry.from_board and abs(entry.p_over - 0.5) >= 0.10:
            ax = fig.add_axes((x - 0.010, 0.108, 0.0035, 0.055))
            ax.axis("off")
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            ax.add_patch(FancyBboxPatch(
                (0, 0), 1, 1, boxstyle="round,pad=0,rounding_size=0.2",
                facecolor=MODEL, edgecolor="none"))
        _display(fig, x, 0.146, entry.player, color=INK, fontsize=18,
                 fontweight="semibold")
        unit = _WATCH_MARKETS.get(entry.market, (entry.market, entry.market))[1]
        avg = f"{entry.mean:.1f}" if entry.mean < 20 else f"{entry.mean:.0f}"
        line_kind = "line" if entry.from_board else "model line"
        _text(fig, x, 0.113,
              f"{unit} · {line_kind} {entry.line:g} · model {avg} · "
              f"{entry.p_over:.0%} over",
              color=INK_DIM, fontsize=12.5)


def _matchup_footer_note(card: SocialCard) -> str:
    view = card.market_view
    if view is None:
        return "no market board captured · model output, informational only"
    parts = []
    if view.books:
        noun = "book" if view.books == 1 else "books"
        parts.append(f"lines: consensus of {view.books} {noun}")
    if view.captured is not None:
        parts.append(f"captured {view.captured.strftime('%b %-d %H:%M')} UTC")
    parts.append("leans fire only past fixed thresholds · informational only")
    return " · ".join(parts)


def render_card(card: SocialCard, path: Path | str,
                asset_dir: Path | str | None = None,
                slide: tuple[int, int] | None = None,
                league: str = "nfl") -> Path:
    """Render one pregame MARKET vs MODEL card to ``path`` (1600×900 PNG).

    ``slide=(n, total)`` stamps a carousel badge ("3/9") beside the date so a
    slate posts as one ordered multi-image thread.
    """
    assets = None if asset_dir is None else Path(asset_dir)
    fig = _fig()
    when = ""
    if card.kickoff is not None:
        central = card.kickoff.tz_localize("UTC").tz_convert("America/Chicago")
        when = central.strftime("%A, %b %-d").upper()
    if slide is not None:
        n, total = slide
        when = f"{when} · {n}/{total}" if when else f"{n}/{total}"
    _panel(fig, (0.04, 0.655, 0.92, 0.245))
    _panel(fig, (0.04, 0.245, 0.92, 0.375))
    _panel(fig, (0.04, 0.085, 0.92, 0.135))
    _brand_header(fig, "MARKET vs MODEL", when)
    _team_blocks(fig, card, assets, league)
    _win_bar(fig, card)
    _matrix(fig, card)
    _props_strip(fig, card)
    if card.record_line:
        _text(fig, 0.95, 0.898, f"{card.record_line} · GRADED DAILY, LOSSES INCLUDED",
              color=INK, fontsize=13, ha="right")
    _footer(fig, _matchup_footer_note(card))
    out = Path(path)
    fig.savefig(out, dpi=DPI, facecolor=BG)
    plt.close(fig)
    return out


def card_filename(card: SocialCard, stamp: str, league: str = "nfl") -> str:
    """A stable, filesystem-safe name: ``social_<league>_<stamp>_AWY_at_HOM.png``."""
    away = "".join(c for c in card.away_code if c.isalnum())
    home = "".join(c for c in card.home_code if c.isalnum())
    return f"social_{league}_{stamp}_{away}_at_{home}.png"


def render_cards(
    cards: list[SocialCard], out_dir: Path | str, stamp: str,
    asset_dir: Path | str | None = None, league: str = "nfl",
    number_slides: bool = False,
) -> list[Path]:
    """Render every card plus a ``social_<league>_<stamp>_captions.md`` copy file.

    ``number_slides`` stamps each card with its "n/total" carousel badge —
    the multi-image thread format that outperforms scattered single posts.
    """
    from velocity.report.social import caption

    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    total = len(cards)
    paths = [
        render_card(
            card, folder / card_filename(card, stamp, league), asset_dir,
            slide=(i + 1, total) if number_slides and total > 1 else None,
            league=league,
        )
        for i, card in enumerate(cards)
    ]
    if cards:
        copy = "\n\n---\n\n".join(caption(card) for card in cards)
        (folder / f"social_{league}_{stamp}_captions.md").write_text(copy + "\n")
    return paths


# --- the post-game Sim Check --------------------------------------------------


def render_sim_check(card: SimCheckCard, path: Path | str,
                     asset_dir: Path | str | None = None,
                     league: str = "nfl") -> Path:
    """Render one Sim Check to ``path``: the actual result on the pregame pmf.

    The hero number is the percentile — the one-glance verdict on how normal
    or wild the result was against the model's pregame distribution. The
    actual total is the amber bar in the model's teal histogram.
    """
    assets = None if asset_dir is None else Path(asset_dir)
    fig = _fig()
    when = "" if card.game_date is None else card.game_date.strftime("%b %-d").upper()
    _panel(fig, (0.04, 0.72, 0.92, 0.175))
    _panel(fig, (0.04, 0.09, 0.38, 0.575))
    _panel(fig, (0.44, 0.09, 0.52, 0.575))
    _brand_header(fig, "SIM CHECK", when)

    if league != "nfl":
        assets = None  # non-NFL codes collide with the NFL logo table
    away_logo = _image(fig, logo_path(card.away_code, assets), 0.062, 0.805, 0.10)
    x = 0.132 if away_logo else 0.065
    _display(fig, x, 0.79, f"{card.away_code} {card.away_score}", color=INK,
             fontsize=44)
    home_logo = _image(fig, logo_path(card.home_code, assets), 0.878, 0.805, 0.10)
    hx = 0.868 if home_logo else 0.935
    _display(fig, hx, 0.79, f"{card.home_score} {card.home_code}", color=INK,
             fontsize=44, ha="right")
    _display(fig, 0.5, 0.795, "FINAL", color=INK_DIM, fontsize=22, ha="center",
             fontweight="semibold")

    # Hero: the percentile of the actual total against the pregame simulation.
    _display(fig, 0.075, 0.50, ordinal(card.total_percentile).upper(), color=INK,
             fontsize=96)
    _text(fig, 0.075, 0.435, "PERCENTILE TOTAL", color=INK_DIM, fontsize=15)
    _text(fig, 0.075, 0.395,
          f"{card.actual_total} combined points vs the",
          color=INK_DIM, fontsize=13.5)
    _text(fig, 0.075, 0.368, "pregame distribution", color=INK_DIM, fontsize=13.5)

    facts = [
        ("PREGAME WINNER PROB", f"{card.winner_code} {card.p_winner_pregame:.0%}"),
        ("WINNER MARGIN PCTILE", ordinal(card.winner_percentile)),
        ("FAIR TOTAL (PREGAME)", f"{card.fair_total:.1f}"),
    ]
    for i, (label, value) in enumerate(facts):
        y = 0.30 - i * 0.055
        _text(fig, 0.075, y, label, color=INK_DIM, fontsize=12.5)
        _display(fig, 0.40 - 0.005, y - 0.004, value, color=INK, fontsize=19,
                 ha="right", fontweight="semibold")

    _text(fig, 0.465, 0.617,
          f"PREGAME SIMULATION · {card.n_sims:,} GAMES" if card.n_sims
          else "PREGAME SIMULATION", color=INK_DIM, fontsize=13.5)
    display_pmf = _trim_pmf(card.total_pmf, ensure=card.actual_total)
    _histogram_axes(fig, (0.475, 0.125, 0.46, 0.44), display_pmf,
                    card.fair_total, highlight=card.actual_total)

    date = "" if card.game_date is None else card.game_date.strftime("%Y-%m-%d · ")
    _footer(fig, f"{date}result graded against the pregame model · informational only")
    out = Path(path)
    fig.savefig(out, dpi=DPI, facecolor=BG)
    plt.close(fig)
    return out


def sim_check_filename(card: SimCheckCard, stamp: str, league: str = "nfl") -> str:
    away = "".join(c for c in card.away_code if c.isalnum())
    home = "".join(c for c in card.home_code if c.isalnum())
    return f"simcheck_{league}_{stamp}_{away}_at_{home}.png"


def render_sim_checks(
    cards: list[SimCheckCard], out_dir: Path | str, stamp: str,
    asset_dir: Path | str | None = None, league: str = "nfl",
) -> list[Path]:
    """Render every Sim Check plus a captions file of post copy."""
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    paths = [
        render_sim_check(card, folder / sim_check_filename(card, stamp, league),
                         asset_dir, league=league)
        for card in cards
    ]
    if cards:
        copy = "\n\n---\n\n".join(sim_check_caption(card) for card in cards)
        (folder / f"simcheck_{league}_{stamp}_captions.md").write_text(copy + "\n")
    return paths


# --- the model record card ----------------------------------------------------


def render_record_card(
    record: pd.DataFrame,
    cumulative: pd.DataFrame | None,
    path: Path | str,
    *,
    date_label: str = "",
) -> Path:
    """Yesterday's graded record + the season line, in the card language.

    Rendered only when something settled — a day of all-pending plays isn't a
    record. Win/loss rows use the reserved status colors with counts, never
    color alone.
    """
    from velocity.report.daily_record import record_summary, season_record_line

    day = record_summary(record[record["result"] != "pending"] if not record.empty
                         else record)
    fig = _fig()
    _panel(fig, (0.04, 0.09, 0.55, 0.78))
    _panel(fig, (0.61, 0.09, 0.35, 0.78))
    _brand_header(fig, "MODEL RECORD", date_label.upper())

    line = f"{day['wins']}-{day['losses']}"
    if day["pushes"]:
        line += f"-{day['pushes']}"
    _display(fig, 0.075, 0.60, line, color=INK, fontsize=110)
    _text(fig, 0.075, 0.525, f"YESTERDAY · {day['units']:+.2f} UNITS",
          color=INK_DIM, fontsize=18)

    for i, section in enumerate(("games", "props", "parlays")):
        rows = record[record["section"] == section]
        if rows.empty:
            continue
        wins = int((rows["result"] == "win").sum())
        losses = int((rows["result"] == "loss").sum())
        units = float(rows["profit"].dropna().sum())
        y = 0.41 - i * 0.085
        _text(fig, 0.075, y, section.upper(), color=INK_DIM, fontsize=14.5)
        _display(fig, 0.24, y - 0.004, f"{wins}-{losses}", color=INK, fontsize=22,
                 fontweight="semibold")
        color = _GOOD if units > 0 else _BAD if units < 0 else INK_DIM
        _display(fig, 0.33, y - 0.004, f"{units:+.2f}u", color=color, fontsize=22,
                 fontweight="semibold")

    season = season_record_line(cumulative)
    if season:
        _text(fig, 0.645, 0.72, "SEASON TO DATE", color=INK_DIM, fontsize=13.5)
        parts = season.replace("SEASON ", "").split(" · ")
        _display(fig, 0.645, 0.63, parts[0], color=INK, fontsize=54)
        if len(parts) > 1:
            units_color = _GOOD if parts[1].startswith("+") else _BAD
            _display(fig, 0.645, 0.545, parts[1], color=units_color, fontsize=40)
        _text(fig, 0.645, 0.48, "every play graded", color=INK_DIM, fontsize=14.5)
        _text(fig, 0.645, 0.45, "losses included", color=INK_DIM, fontsize=14.5)

    _footer(fig, "graded against final scores and box scores · informational only")
    out = Path(path)
    fig.savefig(out, dpi=DPI, facecolor=BG)
    plt.close(fig)
    return out
