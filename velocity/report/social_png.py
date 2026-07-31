"""Social card rendering — the MatchUp Labs graphic family (1600×900 PNG).

Three cards, one visual language, so the format itself is the brand:

* **Model card** (pregame) — the daily ritual: win split, hero tiles, the
  simulated total-runs distribution, players to watch, and the running graded
  record so every card doubles as the receipt.
* **Sim Check** (post-game) — the argument-settler: the actual result pinned
  onto the pregame distribution with its percentile rendered as the hero
  number. The model grading itself, in public, nightly.
* **Model record** — yesterday's graded plays and the season line.

Palette is validator-checked against the dark surface (teal ``#0d9488`` /
amber ``#d97706`` pass the lightness band, CVD separation, and contrast
checks). Fixed assignment: home/model marks are teal, the away side and the
*actual result* are amber — the "reality vs model" contrast reads the same on
every card. Text always wears ink tokens, never a mark color. 16:9 at
1600×900 renders uncropped in the X timeline.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: the collector runs in CI
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import FancyBboxPatch

from velocity.report.sim_check import SimCheckCard, ordinal, sim_check_caption
from velocity.report.social import _WATCH_MARKETS, SocialCard

# Surfaces + ink (text) tokens.
BG = "#0b0f14"
SURFACE = "#10151c"
EDGE = "#1d2430"
INK = "#e8edf2"
INK_DIM = "#7d8894"
BRAND = "#3ddad0"  # wordmark accent only — never a data mark
# Mark palette — validated (dark surface #10151c): fixed assignment, every card.
HOME = "#0d9488"  # teal: home side, and the model's distributions
AWAY = "#d97706"  # amber: away side, and the ACTUAL result on a Sim Check
TRACK = "#1d2430"

WIDTH, HEIGHT, DPI = 1600, 900, 100

_GOOD = "#22c55e"
_BAD = "#ef4444"


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


def _brand_header(fig: plt.Figure, kind: str, when: str) -> None:
    _text(fig, 0.055, 0.935, "MATCHUP LABS", color=BRAND, fontsize=23, fontweight="bold")
    _text(fig, 0.232, 0.935, kind, color=INK_DIM, fontsize=23)
    _text(fig, 0.945, 0.935, when, color=INK_DIM, fontsize=17, ha="right")


def _footer(fig: plt.Figure, note: str) -> None:
    _text(fig, 0.055, 0.05, note, color=INK_DIM, fontsize=13)
    _text(fig, 0.945, 0.05, "@MatchUpLabs", color=BRAND, fontsize=13, ha="right",
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
    ax.set_facecolor(BG)
    values = sorted(pmf)
    probs = [pmf[v] for v in values]
    colors = [AWAY if highlight is not None and v == highlight else HOME for v in values]
    ax.bar(values, probs, width=0.82, color=colors, edgecolor="none")
    top = max(probs) if probs else 0.0
    if top > 0:  # selective direct labels: the modal total, and the actual result
        mode = values[probs.index(top)]
        ax.text(mode, top + top * 0.06, f"{mode}", color=INK, fontsize=15,
                ha="center", fontweight="bold")
    if highlight is not None and highlight in pmf:
        ax.text(highlight, pmf[highlight] + (top or 0.1) * 0.06, "actual",
                color=AWAY, fontsize=13, ha="center", fontweight="bold")
    ax.axvline(fair, color=INK_DIM, linewidth=1.6, linestyle=(0, (4, 3)))
    ax.text(fair + 0.35, (top or 0.1) * 1.18, f"fair {fair:.1f}",
            color=INK_DIM, fontsize=13, ha="left")
    ax.set_ylim(0, (top or 0.1) * 1.3)
    ax.set_yticks([])
    last = values[-1] if values else 0
    ticks = [v for v in values if v % 2 == 0 and v != last - 1] + ([last] if last else [])
    ax.set_xticks(ticks, [str(v) for v in ticks])
    ax.tick_params(colors=INK_DIM, labelsize=13, length=0)
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
          fontsize=19, fontweight="bold")
    _text(fig, 0.945, 0.812, f"{p_home:.0%}  {card.home_code}", color=INK,
          fontsize=19, fontweight="bold", ha="right")


def _tiles(fig: plt.Figure, card: SocialCard) -> None:
    tiles = [
        ("PROJECTED SCORE", f"{card.mu_away:.1f} – {card.mu_home:.1f}"),
        ("FAIR TOTAL", f"{card.fair_total:.1f}"),
        ("F5 TOTAL", f"{card.f5_fair_total:.1f}"),
        ("FIRST-INNING RUN", f"{card.p_yrfi:.0%}"),
    ]
    for i, (label, value) in enumerate(tiles):
        x = 0.055 + i * 0.2275
        _text(fig, x, 0.685, label, color=INK_DIM, fontsize=15)
        _text(fig, x, 0.625, value, color=INK, fontsize=32, fontweight="bold")


def _watch(fig: plt.Figure, card: SocialCard) -> None:
    _text(fig, 0.63, 0.545, "PLAYERS TO WATCH", color=INK_DIM, fontsize=15)
    if not card.watch:
        _text(fig, 0.63, 0.48, "lineups not posted yet", color=INK_DIM, fontsize=16)
        return
    for i, entry in enumerate(card.watch[:3]):
        y = 0.487 - i * 0.128
        stat_label = _WATCH_MARKETS.get(entry.market, (entry.market, ""))[0]
        _text(fig, 0.63, y, f"{entry.player}", color=INK, fontsize=19,
              fontweight="bold")
        _text(fig, 0.945, y, stat_label, color=INK_DIM, fontsize=15, ha="right")
        _text(fig, 0.63, y - 0.042, entry.fact(), color=INK_DIM, fontsize=15)
        # The probability at the stated line, as a thin filled track.
        ax = fig.add_axes((0.63, y - 0.075, 0.315, 0.014))
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        _rounded(ax, 0.0, 1.0, TRACK)
        _rounded(ax, 0.0, max(entry.p_over, 0.012), HOME)


def render_card(card: SocialCard, path: Path | str) -> Path:
    """Render one pregame model card to ``path`` (1600×900 PNG)."""
    fig = _fig()
    when = ""
    if card.kickoff is not None:
        central = card.kickoff.tz_localize("UTC").tz_convert("America/Chicago")
        when = central.strftime("%b %-d · %-I:%M %p CT")
    _brand_header(fig, "MODEL CARD", when)
    _text(
        fig, 0.055, 0.862,
        f"{card.away_name.upper()}  @  {card.home_name.upper()}",
        color=INK, fontsize=28, fontweight="bold",
    )
    _win_bar(fig, card)
    _tiles(fig, card)
    _text(fig, 0.055, 0.545, f"SIMULATED TOTAL RUNS · {card.n_sims:,} GAMES",
          color=INK_DIM, fontsize=15)
    _histogram_axes(fig, (0.055, 0.14, 0.52, 0.375), dict(card.total_runs_pmf),
                    card.fair_total)
    _watch(fig, card)
    if card.record_line:
        _text(fig, 0.945, 0.095, f"{card.record_line} · all plays graded, losses included",
              color=INK, fontsize=14, ha="right")
    date = "" if card.kickoff is None else card.kickoff.strftime("%Y-%m-%d · ")
    _footer(fig, f"{date}Monte Carlo simulation · model output, informational only")
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


# --- the post-game Sim Check --------------------------------------------------


def render_sim_check(card: SimCheckCard, path: Path | str) -> Path:
    """Render one Sim Check to ``path``: the actual result on the pregame pmf.

    The hero number is the percentile — the one-glance verdict on how normal
    or wild the result was against the model's pregame distribution. The
    actual total is the amber bar in the model's teal histogram.
    """
    fig = _fig()
    when = "" if card.game_date is None else card.game_date.strftime("%b %-d")
    _brand_header(fig, "SIM CHECK", when)
    _text(
        fig, 0.055, 0.855,
        f"{card.away_name.upper()} {card.away_score}  @  "
        f"{card.home_name.upper()} {card.home_score}   ·   FINAL",
        color=INK, fontsize=26, fontweight="bold",
    )

    # Hero: the percentile of the actual total against the pregame simulation.
    _text(fig, 0.055, 0.60, ordinal(card.total_percentile).upper(), color=INK,
          fontsize=76, fontweight="bold")
    _text(fig, 0.055, 0.545, "PERCENTILE TOTAL", color=INK_DIM, fontsize=17)
    _text(fig, 0.055, 0.50,
          f"{card.actual_total} combined runs vs the pregame distribution",
          color=INK_DIM, fontsize=15)

    facts = [
        ("PREGAME WINNER PROB",
         f"{card.winner_code} {card.p_winner_pregame:.0%}"),
        ("WINNER MARGIN PCTILE", ordinal(card.winner_percentile)),
        ("FAIR TOTAL (PREGAME)", f"{card.fair_total:.1f}"),
    ]
    if card.p_yrfi is not None and card.yrfi_actual is not None:
        outcome = "YES" if card.yrfi_actual else "NO"
        facts.append(("1ST-INNING RUN", f"{card.p_yrfi:.0%} → {outcome}"))
    for i, (label, value) in enumerate(facts):
        y = 0.40 - i * 0.082
        _text(fig, 0.055, y, label, color=INK_DIM, fontsize=14)
        _text(fig, 0.30, y, value, color=INK, fontsize=19, fontweight="bold")

    _text(fig, 0.46, 0.545,
          f"PREGAME SIMULATION · {card.n_sims:,} GAMES" if card.n_sims
          else "PREGAME SIMULATION", color=INK_DIM, fontsize=15)
    display_pmf = _fold_pmf(
        card.total_pmf, cap=max(17, card.actual_total), ensure=card.actual_total
    )
    _histogram_axes(fig, (0.46, 0.14, 0.485, 0.375), display_pmf,
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
    cards: list[SimCheckCard], out_dir: Path | str, stamp: str
) -> list[Path]:
    """Render every Sim Check plus a captions file of post copy."""
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    paths = [
        render_sim_check(card, folder / sim_check_filename(card, stamp))
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
    _brand_header(fig, "MODEL RECORD", date_label)

    line = f"{day['wins']}-{day['losses']}"
    if day["pushes"]:
        line += f"-{day['pushes']}"
    _text(fig, 0.055, 0.62, line, color=INK, fontsize=88, fontweight="bold")
    _text(fig, 0.055, 0.545, f"YESTERDAY · {day['units']:+.2f} UNITS",
          color=INK_DIM, fontsize=19)

    for i, section in enumerate(("games", "props", "parlays")):
        rows = record[record["section"] == section]
        if rows.empty:
            continue
        wins = int((rows["result"] == "win").sum())
        losses = int((rows["result"] == "loss").sum())
        units = float(rows["profit"].dropna().sum())
        y = 0.42 - i * 0.082
        _text(fig, 0.055, y, section.upper(), color=INK_DIM, fontsize=15)
        _text(fig, 0.21, y, f"{wins}-{losses}", color=INK, fontsize=19,
              fontweight="bold")
        color = _GOOD if units > 0 else _BAD if units < 0 else INK_DIM
        _text(fig, 0.30, y, f"{units:+.2f}u", color=color, fontsize=19,
              fontweight="bold")

    season = season_record_line(cumulative)
    if season:
        _text(fig, 0.945, 0.62, season, color=INK, fontsize=26, ha="right",
              fontweight="bold")
        _text(fig, 0.945, 0.575, "every play graded · losses included",
              color=INK_DIM, fontsize=15, ha="right")

    _footer(fig, "graded against final scores, linescores, and box scores · "
                 "informational only")
    out = Path(path)
    fig.savefig(out, dpi=DPI, facecolor=BG)
    plt.close(fig)
    return out
