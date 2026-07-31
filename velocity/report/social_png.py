"""Social card rendering — the MatchUp Labs graphic family (1600×900 PNG).

The broadcast-grade pass: layered surface panels on the dark ground, vendored
Barlow Condensed display type for headlines and hero numbers, real team spot
logos and player headshots from the MLB static CDN (cached, best-effort),
team-brand-colored win bars, W-L records, and a one-line consensus market
strip — SportsCenter grammar without the stat wall.

Three cards, one visual language:

* **Model card** (pregame) — team blocks with logos and records, the
  team-colored win split, four hero tiles, the simulated total-runs
  distribution, players to watch with headshots, and the season receipt line.
* **Sim Check** (post-game) — the actual result pinned on the pregame
  distribution, percentile as the hero number.
* **Model record** — yesterday graded plus the season line.

Color rules: the model's distributions are always teal and the *actual result*
always amber (validated against the dark surface) — the reality-vs-model
contrast never changes. Team brand colors appear only where identity is the
message (the win split, score accents) and always beside a direct code label,
never as the only cue. Text wears ink tokens. Everything degrades: no asset
cache → no images; missing fonts → system default; a card never fails to
render because a nicety was unavailable.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: the collector runs in CI
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import FancyBboxPatch

from velocity.report.assets import (
    headshot_path,
    logo_path,
    register_fonts,
    team_bar_colors,
)
from velocity.report.sim_check import SimCheckCard, ordinal, sim_check_caption
from velocity.report.social import _WATCH_MARKETS, SocialCard

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
    """Place a cached PNG (logo/headshot) by figure coords; False if unavailable."""
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
    """The total-runs distribution: teal bars, fair-total marker, optional
    amber highlight on the actual result (the Sim Check's reality mark)."""
    ax = fig.add_axes(rect)
    ax.set_facecolor("none")
    values = sorted(pmf)
    probs = [pmf[v] for v in values]
    colors = [ACTUAL if highlight is not None and v == highlight else MODEL
              for v in values]
    ax.bar(values, probs, width=0.82, color=colors, edgecolor="none")
    top = max(probs) if probs else 0.0
    if top > 0:  # selective direct labels: the modal total, and the actual result
        mode = values[probs.index(top)]
        ax.text(mode, top + top * 0.06, f"{mode}", color=INK, fontsize=14,
                ha="center", fontweight="bold")
    if highlight is not None and highlight in pmf:
        ax.text(highlight, pmf[highlight] + (top or 0.1) * 0.06, "actual",
                color=ACTUAL, fontsize=13, ha="center", fontweight="bold")
    ax.axvline(fair, color=INK_DIM, linewidth=1.6, linestyle=(0, (4, 3)))
    ax.text(fair + 0.35, (top or 0.1) * 1.18, f"fair {fair:.1f}",
            color=INK_DIM, fontsize=12.5, ha="left")
    ax.set_ylim(0, (top or 0.1) * 1.3)
    ax.set_yticks([])
    last = values[-1] if values else 0
    ticks = [v for v in values if v % 2 == 0 and v != last - 1] + ([last] if last else [])
    ax.set_xticks(ticks, [str(v) for v in ticks])
    ax.tick_params(colors=INK_DIM, labelsize=12, length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color(EDGE)


def _fold_pmf(pmf: dict[int, float], cap: int, *, ensure: int | None = None) -> dict[int, float]:
    """Right tail folded into ``cap`` for display; probabilities preserved.

    ``ensure`` extends the support to include a value the sims never produced
    (a zero-height position), so an off-the-charts actual result still appears
    on the axis instead of silently vanishing — "the sims never got here" is
    part of the story.
    """
    out: dict[int, float] = {}
    for value, prob in pmf.items():
        out[min(value, cap)] = out.get(min(value, cap), 0.0) + prob
    last = max(out) if out else 0
    if ensure is not None:
        last = max(last, min(ensure, cap))
    return {v: out.get(v, 0.0) for v in range(0, last + 1)}


# --- the pregame model card ---------------------------------------------------


def _team_blocks(fig: plt.Figure, card: SocialCard, asset_dir: Path | None) -> None:
    """Away block left, home block right — logo, code, record — time + market center."""
    away_logo = _image(fig, logo_path(card.away_code, asset_dir), 0.065, 0.83, 0.115)
    home_logo = _image(fig, logo_path(card.home_code, asset_dir), 0.873, 0.83, 0.115)
    away_x = 0.145 if away_logo else 0.07
    _display(fig, away_x, 0.815, card.away_code, color=INK, fontsize=46)
    if card.away_record:
        _text(fig, away_x + 0.002, 0.775, card.away_record, color=INK_DIM, fontsize=15)
    home_x = 0.855 if home_logo else 0.93
    _display(fig, home_x, 0.815, card.home_code, color=INK, fontsize=46, ha="right")
    if card.home_record:
        _text(fig, home_x - 0.002, 0.775, card.home_record, color=INK_DIM,
              fontsize=15, ha="right")

    when = ""
    if card.kickoff is not None:
        central = card.kickoff.tz_localize("UTC").tz_convert("America/Chicago")
        when = central.strftime("%-I:%M %p CT")
    _display(fig, 0.5, 0.845, "@", color=INK_DIM, fontsize=24, ha="center",
             fontweight="semibold")
    if when:
        _display(fig, 0.5, 0.805, when, color=INK, fontsize=24, ha="center",
                 fontweight="semibold")
    if card.market:
        _text(fig, 0.5, 0.768, card.market, color=INK_DIM, fontsize=14, ha="center")


def _win_bar(fig: plt.Figure, card: SocialCard) -> None:
    away_color, home_color = team_bar_colors(card.away_code, card.home_code)
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
             fontsize=21, fontweight="semibold")
    _display(fig, 0.935, 0.726, f"{p_home:.0%} {card.home_code}", color=INK,
             fontsize=21, ha="right", fontweight="semibold")


def _tiles(fig: plt.Figure, card: SocialCard) -> None:
    tiles = [
        ("PROJECTED SCORE", f"{card.mu_away:.1f} – {card.mu_home:.1f}"),
        ("FAIR TOTAL", f"{card.fair_total:.1f}"),
        ("F5 TOTAL", f"{card.f5_fair_total:.1f}"),
        ("FIRST-INNING RUN", f"{card.p_yrfi:.0%}"),
    ]
    for i, (label, value) in enumerate(tiles):
        x = 0.065 + i * 0.2225
        _text(fig, x, 0.595, label, color=INK_DIM, fontsize=13.5)
        _display(fig, x, 0.532, value, color=INK, fontsize=40)


def _watch(fig: plt.Figure, card: SocialCard, asset_dir: Path | None) -> None:
    _text(fig, 0.635, 0.452, "PLAYERS TO WATCH", color=INK_DIM, fontsize=13.5)
    if not card.watch:
        _text(fig, 0.635, 0.39, "lineups not posted yet", color=INK_DIM, fontsize=15)
        return
    for i, entry in enumerate(card.watch[:3]):
        y = 0.398 - i * 0.108
        shot = _image(
            fig,
            headshot_path(entry.player_id or "", asset_dir),
            0.633, y - 0.012, 0.078,
        )
        x = 0.678 if shot else 0.635
        _display(fig, x, y, entry.player, color=INK, fontsize=21,
                 fontweight="semibold")
        _text(fig, 0.933, y, _WATCH_MARKETS.get(entry.market, (entry.market, ""))[0],
              color=INK_DIM, fontsize=13, ha="right")
        _text(fig, x, y - 0.035, entry.fact(), color=INK_DIM, fontsize=13.5)
        ax = fig.add_axes((x, y - 0.062, 0.933 - x, 0.011))
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        _rounded(ax, 0.0, 1.0, TRACK)
        _rounded(ax, 0.0, max(entry.p_over, 0.012), MODEL)


def render_card(card: SocialCard, path: Path | str,
                asset_dir: Path | str | None = None) -> Path:
    """Render one pregame model card to ``path`` (1600×900 PNG)."""
    assets = None if asset_dir is None else Path(asset_dir)
    fig = _fig()
    when = ""
    if card.kickoff is not None:
        central = card.kickoff.tz_localize("UTC").tz_convert("America/Chicago")
        when = central.strftime("%A, %b %-d").upper()
    _panel(fig, (0.04, 0.655, 0.92, 0.245))
    _panel(fig, (0.04, 0.09, 0.565, 0.40))
    _panel(fig, (0.62, 0.09, 0.34, 0.40))
    _brand_header(fig, "MODEL CARD", when)
    _team_blocks(fig, card, assets)
    _win_bar(fig, card)
    _tiles(fig, card)
    _text(fig, 0.065, 0.452, f"SIMULATED TOTAL RUNS · {card.n_sims:,} GAMES",
          color=INK_DIM, fontsize=13.5)
    _histogram_axes(fig, (0.075, 0.125, 0.5, 0.30), dict(card.total_runs_pmf),
                    card.fair_total)
    _watch(fig, card, assets)
    if card.record_line:
        _text(fig, 0.95, 0.898, f"{card.record_line} · GRADED DAILY",
              color=INK, fontsize=13, ha="right")
    _footer(fig, "Monte Carlo simulation · model output, informational only")
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
    cards: list[SocialCard], out_dir: Path | str, stamp: str,
    asset_dir: Path | str | None = None,
) -> list[Path]:
    """Render every card plus a ``social_mlb_<stamp>_captions.md`` post-copy file."""
    from velocity.report.social import caption

    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    paths = [
        render_card(card, folder / card_filename(card, stamp), asset_dir)
        for card in cards
    ]
    if cards:
        copy = "\n\n---\n\n".join(caption(card) for card in cards)
        (folder / f"social_mlb_{stamp}_captions.md").write_text(copy + "\n")
    return paths


# --- the post-game Sim Check --------------------------------------------------


def render_sim_check(card: SimCheckCard, path: Path | str,
                     asset_dir: Path | str | None = None) -> Path:
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
          f"{card.actual_total} combined runs vs the",
          color=INK_DIM, fontsize=13.5)
    _text(fig, 0.075, 0.368, "pregame distribution", color=INK_DIM, fontsize=13.5)

    facts = [
        ("PREGAME WINNER PROB", f"{card.winner_code} {card.p_winner_pregame:.0%}"),
        ("WINNER MARGIN PCTILE", ordinal(card.winner_percentile)),
        ("FAIR TOTAL (PREGAME)", f"{card.fair_total:.1f}"),
    ]
    if card.p_yrfi is not None and card.yrfi_actual is not None:
        outcome = "YES" if card.yrfi_actual else "NO"
        facts.append(("1ST-INNING RUN", f"{card.p_yrfi:.0%} · {outcome}"))
    for i, (label, value) in enumerate(facts):
        y = 0.30 - i * 0.055
        _text(fig, 0.075, y, label, color=INK_DIM, fontsize=12.5)
        _display(fig, 0.40 - 0.005, y - 0.004, value, color=INK, fontsize=19,
                 ha="right", fontweight="semibold")

    _text(fig, 0.465, 0.617,
          f"PREGAME SIMULATION · {card.n_sims:,} GAMES" if card.n_sims
          else "PREGAME SIMULATION", color=INK_DIM, fontsize=13.5)
    display_pmf = _fold_pmf(
        card.total_pmf, cap=max(17, card.actual_total), ensure=card.actual_total
    )
    _histogram_axes(fig, (0.475, 0.125, 0.46, 0.44), display_pmf,
                    card.fair_total, highlight=card.actual_total)

    date = "" if card.game_date is None else card.game_date.strftime("%Y-%m-%d · ")
    _footer(fig, f"{date}result graded against the pregame model · informational only")
    out = Path(path)
    fig.savefig(out, dpi=DPI, facecolor=BG)
    plt.close(fig)
    return out


def sim_check_filename(card: SimCheckCard, stamp: str) -> str:
    away = "".join(c for c in card.away_code if c.isalnum())
    home = "".join(c for c in card.home_code if c.isalnum())
    return f"simcheck_mlb_{stamp}_{away}_at_{home}.png"


def render_sim_checks(
    cards: list[SimCheckCard], out_dir: Path | str, stamp: str,
    asset_dir: Path | str | None = None,
) -> list[Path]:
    """Render every Sim Check plus a captions file of post copy."""
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    paths = [
        render_sim_check(card, folder / sim_check_filename(card, stamp), asset_dir)
        for card in cards
    ]
    if cards:
        copy = "\n\n---\n\n".join(sim_check_caption(card) for card in cards)
        (folder / f"simcheck_mlb_{stamp}_captions.md").write_text(copy + "\n")
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

    _footer(fig, "graded against final scores, linescores, and box scores · "
                 "informational only")
    out = Path(path)
    fig.savefig(out, dpi=DPI, facecolor=BG)
    plt.close(fig)
    return out
