"""The broadcast grid — a whole Saturday or Sunday on one shareable frame.

The TV-guide grammar: rows are broadcast networks (NCAAF, from CFBD's media
listing) or kickoff windows (the NFL fallback until a network source lands),
columns are Eastern-time half hours, and each game is a block spanning its
broadcast window — the away school's brand color over the home school's,
each beside its code label, with one small strip underneath carrying the
kickoff time and the consensus market numbers.

**Facts, not advice**: the strip reads "3:30 · UGA -13.5 · 51.5" — the
posted favorite and total as the market states them. No verdict colors, no
edges, no model numbers. The graphic is the day's map.

Identity follows the licensing posture (report/assets.py): school
abbreviations + official colors for college, club codes for the NFL,
network names as plain text — never a network or school mark.

Pure pieces (`pack_lanes`, `window_label`, `consensus_line_text`,
`normalize_media`) are offline-testable; `render_grid` draws with the same
matplotlib/Agg token palette as the social cards.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: the runner lives in CI
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle

from velocity.report.assets import lighten_for_dark, register_fonts

BG = "#0b0f14"
SURFACE = "#131a23"
EDGE = "#232d3a"
INK = "#eef2f6"
INK_DIM = "#8b96a3"
BRAND = "#3ddad0"

EASTERN = "America/New_York"

DEFAULT_FOOTER = ("Consensus lines at render time · schedule and lines move · "
                  "not betting advice")


@dataclass(frozen=True)
class GridGame:
    """One block on the grid — display codes, colors, marks, the fact strip."""

    row: str  # network name, or a kickoff-window label
    away: str
    home: str
    kickoff_et: pd.Timestamp  # tz-naive, already Eastern
    away_color: str | None = None
    home_color: str | None = None
    line_text: str = ""  # "UGA -13.5 · 51.5"; "" renders nothing
    # Cached logo PNGs (ESPN CDN via report/assets.py); None degrades to the
    # abbreviation chip alone — a mark is a nicety, never a requirement.
    away_logo: Path | str | None = None
    home_logo: Path | str | None = None


def eastern(kickoff_utc: pd.Timestamp | str) -> pd.Timestamp:
    """A UTC-naive kickoff (the datasets' convention) → Eastern, tz-naive."""
    ts = pd.Timestamp(kickoff_utc)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(EASTERN).tz_localize(None)


def window_label(kickoff_et: pd.Timestamp) -> str:
    """The NFL fallback rows: the Sunday windows everyone already thinks in."""
    hour = kickoff_et.hour + kickoff_et.minute / 60.0
    if hour < 12.0:
        return "MORNING"
    if hour < 15.0:
        return "EARLY (1:00)"
    if hour < 18.5:
        return "LATE (4:05 / 4:25)"
    return "PRIME TIME"


def consensus_line_text(
    away_code: str, home_code: str,
    spread_home: float | None, total: float | None,
) -> str:
    """The fact strip: posted favorite + number, and the total. No verdicts.

    ``spread_home`` is the home side's handicap in market convention
    (negative = home favored). A pick'em renders as ``PK``.
    """
    parts: list[str] = []
    if spread_home is not None and spread_home == spread_home:  # NaN-safe
        if spread_home == 0:
            parts.append("PK")
        elif spread_home < 0:
            parts.append(f"{home_code} {spread_home:g}")
        else:
            parts.append(f"{away_code} {-spread_home:g}")
    if total is not None and total == total:
        parts.append(f"O/U {total:g}")
    return " · ".join(parts)


# The reference grammar puts the broadcast networks up top; cable and
# conference channels follow in first-kickoff order. Text ranks, no marks.
NETWORK_PRIORITY = ("ABC", "CBS", "FOX", "NBC", "ESPN", "ESPN2", "ESPNU",
                    "FS1", "CBSSN", "TNT", "USA")


def pack_lanes(
    games: list[GridGame],
    duration_hours: float,
    priority: tuple[str, ...] = NETWORK_PRIORITY,
) -> list[tuple[str, list[list[GridGame]]]]:
    """Rows by network prominence then first kickoff, packed into lanes.

    A network airing regional splits (three 3:30 games on one row) gets one
    lane per concurrent game, greedily: a game joins the first lane whose
    last block has ended by its kickoff.
    """
    by_row: dict[str, list[GridGame]] = {}
    for game in sorted(games, key=lambda g: (g.kickoff_et, g.away, g.home)):
        by_row.setdefault(game.row, []).append(game)
    rank = {name: i for i, name in enumerate(priority)}
    ordered = sorted(
        by_row.items(),
        key=lambda kv: (rank.get(kv[0].upper(), len(priority)), kv[1][0].kickoff_et),
    )
    packed: list[tuple[str, list[list[GridGame]]]] = []
    for row, row_games in ordered:
        lanes: list[list[GridGame]] = []
        for game in row_games:
            for lane in lanes:
                if lane[-1].kickoff_et + pd.Timedelta(hours=duration_hours) \
                        <= game.kickoff_et:
                    lane.append(game)
                    break
            else:
                lanes.append([game])
        packed.append((row, lanes))
    return packed


def normalize_media(payload: list[dict] | None) -> dict[tuple[str, str], str]:
    """CFBD ``/games/media`` rows → ``{(home school, away school): outlet}``.

    TV outlets win over streaming when a game carries both; rows missing a
    team or outlet contribute nothing.
    """
    tv: dict[tuple[str, str], str] = {}
    web: dict[tuple[str, str], str] = {}
    for row in payload or []:
        if not isinstance(row, Mapping):
            continue
        home = row.get("homeTeam") or row.get("home_team")
        away = row.get("awayTeam") or row.get("away_team")
        outlet = row.get("outlet")
        if not home or not away or not outlet:
            continue
        kind = str(row.get("mediaType") or row.get("media_type") or "").lower()
        target = tv if kind == "tv" else web
        target.setdefault((str(home), str(away)), str(outlet))
    return {**web, **tv}  # tv overwrites a web-only entry


def _ink_for(color: str) -> str:
    """Black or white text, by the fill's luminance."""
    r, g, b = (int(color.lstrip("#")[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return "#0b0f14" if (0.299 * r + 0.587 * g + 0.114 * b) > 0.6 else "#f7fafc"


def _chip_color(color: str | None) -> str:
    """A team band color legible on the dark ground (neutral when unknown)."""
    if not color:
        return "#3a4656"
    return lighten_for_dark(color, floor=0.30)


def _mark(fig: plt.Figure, ax: plt.Axes, path: Path | str | None,
          x: float, y: float, height_y: float) -> float:
    """Draw a cached mark on a light holding chip at data coords.

    ``(x, y)`` is the mark's left edge and vertical center; ``height_y`` its
    height in y-data units. Returns the width consumed in x-data units —
    0.0 when the file is absent or corrupt, so the code chip alone carries
    identity and the caller's text never shifts for a mark that isn't there.
    """
    if not path:
        return 0.0
    try:
        img = plt.imread(str(path))
    except Exception:  # noqa: BLE001 - a corrupt cache entry is cosmetic
        return 0.0
    origin = ax.transData.transform((x, y))
    h_px = abs(ax.transData.transform((0.0, 0.0))[1]
               - ax.transData.transform((0.0, height_y))[1])
    box = fig.add_axes((origin[0] / fig.bbox.width,
                        origin[1] / fig.bbox.height - h_px / fig.bbox.height / 2,
                        h_px / fig.bbox.width, h_px / fig.bbox.height))
    box.imshow(img)
    # The chip: a light inset ground, so a mark the same color as its brand
    # band never sinks into it.
    box.set_xlim(-img.shape[1] * 0.12, img.shape[1] * 1.12)
    box.set_ylim(img.shape[0] * 1.12, -img.shape[0] * 0.12)
    box.patch.set_facecolor("#eef1f5")
    box.patch.set_alpha(0.92)
    box.set_xticks([])
    box.set_yticks([])
    for spine in box.spines.values():
        spine.set_visible(False)
    box.set_zorder(6)
    return float(ax.transData.inverted().transform(
        (origin[0] + h_px, origin[1]))[0] - x)


def _chrome(fig: plt.Figure, fig_h: float, *, title: str, subtitle: str,
            footer: str, league_logo: Path | str | None,
            display: str, body: str) -> None:
    """Header (league mark, title, wordmark) and footer, shared by layouts."""
    title_x = 0.006
    if league_logo is not None:
        try:
            mark = plt.imread(str(league_logo))
            box = fig.add_axes((0.006, 1 - 0.86 / fig_h, 0.68 / 16.0,
                                0.68 / fig_h))
            box.imshow(mark)
            box.axis("off")
            title_x = 0.006 + 0.68 / 16.0 + 0.007
        except Exception:  # noqa: BLE001 - the league mark is a nicety
            pass
    fig.text(title_x, 1 - 0.28 / fig_h, title, color=INK, fontsize=21,
             family=display, weight="bold", ha="left", va="top")
    if subtitle:
        fig.text(title_x, 1 - 0.62 / fig_h, subtitle, color=INK_DIM,
                 fontsize=11, family=body, ha="left", va="top")
    fig.text(0.994, 1 - 0.28 / fig_h, "MATCHUP LABS", color=BRAND, fontsize=14,
             family=display, weight="bold", ha="right", va="top")
    fig.text(0.006, 0.12 / fig_h, footer, color=INK_DIM, fontsize=9,
             family=body, ha="left", va="bottom")


def window_sections(games: list[GridGame]) -> list[tuple[str, list[GridGame]]]:
    """Rows grouped and ordered by first kickoff — the board's sections."""
    by_row: dict[str, list[GridGame]] = {}
    for game in sorted(games, key=lambda g: (g.kickoff_et, g.away, g.home)):
        by_row.setdefault(game.row, []).append(game)
    return sorted(by_row.items(), key=lambda kv: kv[1][0].kickoff_et)


def render_grid(  # noqa: PLR0915 - one deliberate drawing pass
    games: list[GridGame],
    dest: Path | str,
    *,
    title: str,
    subtitle: str = "",
    footer: str = DEFAULT_FOOTER,
    duration_hours: float = 3.5,
    league_logo: Path | str | None = None,
) -> Path:
    """Draw the grid to ``dest`` (PNG, 1600px wide, height fits the slate)."""
    display, body = register_fonts()
    packed = pack_lanes(games, duration_hours)
    n_lanes = sum(len(lanes) for _row, lanes in packed) or 1

    start = min(g.kickoff_et for g in games).floor("h")
    end = (max(g.kickoff_et for g in games) + pd.Timedelta(hours=duration_hours)).ceil("h")
    hours = (end - start).total_seconds() / 3600.0

    lane_h, header_h, footer_h, gutter = 0.70, 1.05, 0.42, 2.1
    fig_h = header_h + n_lanes * lane_h + footer_h
    fig, ax = plt.subplots(figsize=(16, fig_h), dpi=100)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(-gutter, hours)
    ax.set_ylim(0, n_lanes)
    ax.invert_yaxis()
    ax.axis("off")
    fig.subplots_adjust(left=0.005, right=0.995,
                        top=1 - header_h / fig_h, bottom=footer_h / fig_h)

    def x_of(ts: pd.Timestamp) -> float:
        return (ts - start).total_seconds() / 3600.0

    # Hour grid + labels.
    for hour_i in range(int(hours) + 1):
        x = float(hour_i)
        ax.plot([x, x], [0, n_lanes], color=EDGE, lw=0.8, zorder=1)
        label = (start + pd.Timedelta(hours=hour_i)).strftime("%I:%M %p").lstrip("0")
        ax.text(x + 0.04, -0.12, label, color=INK_DIM, fontsize=9,
                family=body, ha="left", va="bottom", clip_on=False)

    y = 0.0
    for row, lanes in packed:
        row_top = y
        for lane in lanes:
            for game in lane:
                x0 = x_of(game.kickoff_et)
                width = duration_hours
                away_c = _chip_color(game.away_color)
                home_c = _chip_color(game.home_color)
                kick = game.kickoff_et.strftime("%I:%M").lstrip("0")
                # With a fact strip the color bands make room for it; the
                # strip gets its own dark surface so spread/total read in
                # full-contrast ink, never against a brand color.
                band = 0.65 if game.line_text else 0.92
                ax.add_patch(Rectangle((x0, y + 0.04), width, band * 0.5,
                                       fc=away_c, ec=EDGE, lw=0.6, zorder=3))
                ax.add_patch(Rectangle((x0, y + 0.04 + band * 0.5), width,
                                       band * 0.5, fc=home_c, ec=EDGE, lw=0.6,
                                       zorder=3))
                logo_h = band * 0.46
                away_w = _mark(fig, ax, game.away_logo, x0 + 0.05,
                               y + 0.04 + band * 0.25, logo_h)
                home_w = _mark(fig, ax, game.home_logo, x0 + 0.05,
                               y + 0.04 + band * 0.75, logo_h)
                pad = max(away_w, home_w)
                text_x = x0 + 0.09 + (pad + 0.06 if pad else 0.0)
                ax.text(text_x, y + 0.04 + band * 0.25, game.away,
                        color=_ink_for(away_c), fontsize=11, family=display,
                        weight="bold", ha="left", va="center", zorder=4)
                ax.text(text_x, y + 0.04 + band * 0.75, f"@ {game.home}",
                        color=_ink_for(home_c), fontsize=11, family=display,
                        weight="bold", ha="left", va="center", zorder=4)
                if game.line_text:
                    strip_h = 0.23
                    ax.add_patch(Rectangle((x0, y + 0.04 + band), width,
                                           strip_h, fc=SURFACE, ec=EDGE,
                                           lw=0.6, zorder=3))
                    ax.text(x0 + 0.09, y + 0.04 + band + strip_h / 2, kick,
                            color=INK_DIM, fontsize=9.5, family=body,
                            ha="left", va="center", zorder=4)
                    ax.text(x0 + width - 0.09, y + 0.04 + band + strip_h / 2,
                            game.line_text, color=INK, fontsize=10.5,
                            family=display, weight="bold", ha="right",
                            va="center", zorder=4)
                else:
                    ax.text(x0 + width - 0.09, y + 0.04 + band * 0.75, kick,
                            color=_ink_for(home_c), fontsize=9, family=body,
                            ha="right", va="center", zorder=4, alpha=0.95)
            y += 1.0
        # Row label in the gutter + separator.
        ax.text(-gutter + 0.08, (row_top + y) / 2.0, row, color=INK,
                fontsize=15, family=display, weight="bold",
                ha="left", va="center")
        ax.plot([-gutter, hours], [y, y], color=EDGE, lw=1.1, zorder=2)

    _chrome(fig, fig_h, title=title, subtitle=subtitle, footer=footer,
            league_logo=league_logo, display=display, body=body)

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, facecolor=BG)
    plt.close(fig)
    return dest


def _card(fig: plt.Figure, ax: plt.Axes, game: GridGame, x: float, y: float,
          display: str, body: str) -> None:
    """One compact card in a window section — the timeline block, off the clock."""
    pad = 0.05
    x0, w = x + pad, 1.0 - 2 * pad
    top, band_h, strip_h = 0.10, 0.26, 0.24
    away_c = _chip_color(game.away_color)
    home_c = _chip_color(game.home_color)
    ax.add_patch(Rectangle((x0, y + top), w, band_h, fc=away_c, ec=EDGE,
                           lw=0.6, zorder=3))
    ax.add_patch(Rectangle((x0, y + top + band_h), w, band_h, fc=home_c,
                           ec=EDGE, lw=0.6, zorder=3))
    ax.add_patch(Rectangle((x0, y + top + 2 * band_h), w, strip_h, fc=SURFACE,
                           ec=EDGE, lw=0.6, zorder=3))
    away_w = _mark(fig, ax, game.away_logo, x0 + 0.02,
                   y + top + band_h * 0.5, band_h * 0.80)
    home_w = _mark(fig, ax, game.home_logo, x0 + 0.02,
                   y + top + band_h * 1.5, band_h * 0.80)
    pad_x = max(away_w, home_w)
    text_x = x0 + 0.045 + (pad_x + 0.02 if pad_x else 0.0)
    ax.text(text_x, y + top + band_h * 0.5, game.away, color=_ink_for(away_c),
            fontsize=12, family=display, weight="bold", ha="left",
            va="center", zorder=4)
    ax.text(text_x, y + top + band_h * 1.5, f"@ {game.home}",
            color=_ink_for(home_c), fontsize=12, family=display,
            weight="bold", ha="left", va="center", zorder=4)
    strip_y = y + top + 2 * band_h + strip_h / 2
    ax.text(x0 + 0.04, strip_y, game.kickoff_et.strftime("%I:%M").lstrip("0"),
            color=INK_DIM, fontsize=9.5, family=body, ha="left", va="center",
            zorder=4)
    if game.line_text:
        ax.text(x0 + w - 0.04, strip_y, game.line_text, color=INK,
                fontsize=10.5, family=display, weight="bold", ha="right",
                va="center", zorder=4)


def render_window_board(
    games: list[GridGame],
    dest: Path | str,
    *,
    title: str,
    subtitle: str = "",
    footer: str = DEFAULT_FOOTER,
    columns: int = 4,
    league_logo: Path | str | None = None,
) -> Path:
    """The NFL Sunday layout: kickoff-window sections of compact cards.

    A timeline earns its width when kickoffs spread across twelve hours; an
    NFL Sunday is four discrete windows, so the axis would be mostly dead
    air. Here each window is a labeled section and its games flow side by
    side — same identity bands, same fact strips, no axis pretending to be
    information it isn't.
    """
    display, body = register_fonts()
    sections = window_sections(games)
    header_u = 0.42
    total_u = sum(header_u + math.ceil(len(sec) / columns)
                  for _label, sec in sections) or 1.0
    unit_in, header_h, footer_h = 1.16, 1.05, 0.42
    fig_h = header_h + total_u * unit_in + footer_h
    fig, ax = plt.subplots(figsize=(16, fig_h), dpi=100)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.set_xlim(0, columns)
    ax.set_ylim(0, total_u)
    ax.invert_yaxis()
    ax.axis("off")
    fig.subplots_adjust(left=0.012, right=0.988,
                        top=1 - header_h / fig_h, bottom=footer_h / fig_h)

    y = 0.0
    for label, sec in sections:
        ax.text(0.05, y + header_u * 0.48, label, color=INK, fontsize=15,
                family=display, weight="bold", ha="left", va="center")
        ax.plot([0.05, columns - 0.05], [y + header_u - 0.08] * 2, color=EDGE,
                lw=1.1, zorder=1)
        y += header_u
        for i, game in enumerate(sec):
            _card(fig, ax, game, float(i % columns), y + i // columns,
                  display, body)
        y += math.ceil(len(sec) / columns)

    _chrome(fig, fig_h, title=title, subtitle=subtitle, footer=footer,
            league_logo=league_logo, display=display, body=body)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, facecolor=BG)
    plt.close(fig)
    return dest
