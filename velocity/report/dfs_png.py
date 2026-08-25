"""DFS lineup card — the optimizer's best DK classic roster as a graphic.

Same visual language as the matchup cards (1600×900 X frame, dark ground,
surface panels, Barlow display type). Left panel: one row per roster slot
(NFL classic 9, CFB classic 8) — slot chip, player, team, salary, projected
DK points — with the QB-stack players carrying the teal accent. Right panel:
the context a DFS reader needs — salary used vs the cap (with the bar),
projected total,
the stack callout, and the projection source. Footer states what this is
(a projection-maximizing cash lineup) and what it is not (advice).

Ownership/leverage columns arrive only when an ownership feed exists — a
column the model has no data for never renders (docs/FOOTBALL_CUTOVER.md §5b).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: the runner lives in CI
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from velocity.dfs.optimizer import SALARY_CAP, Lineup
from velocity.dfs.pipeline import game_time_ct
from velocity.dfs.tiered import TierEntry
from velocity.report.social_png import (
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
    _panel,
    _text,
)

# Roster column x-positions inside the left panel.
_X_CHIP, _X_NAME, _X_TEAM = 0.068, 0.135, 0.335
_X_TIME, _X_SALARY, _X_POINTS = 0.395, 0.495, 0.575


def _stacked_names(lineup: Lineup) -> set[str]:
    """Players in a QB stack (the QB and their rostered pass-catchers/backs)."""
    out: set[str] = set()
    for qb in (s for s in lineup.slots if s.position == "QB"):
        if not qb.team:
            continue
        mates = [s.player_name for s in lineup.slots
                 if s.team == qb.team and s.player_name != qb.player_name]
        if mates:
            out.update([qb.player_name, *mates])
    return out


def _roster_rows(fig: plt.Figure, lineup: Lineup) -> None:
    stacked = _stacked_names(lineup)
    for label, x, ha in (("POS", _X_CHIP, "left"), ("PLAYER", _X_NAME, "left"),
                         ("TEAM", _X_TEAM, "left"), ("GAME", _X_TIME, "left"),
                         ("SALARY", _X_SALARY, "right"),
                         ("PROJ", _X_POINTS, "right")):
        _text(fig, x, 0.795, label, color=INK_DIM, fontsize=12.5, ha=ha)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    # Row pitch adapts to the roster size (NFL classic 9 rows, CFB classic 8,
    # showdown 6) so every format fills the same panel envelope.
    step = 0.648 / max(len(lineup.slots), 6)
    for i, slot in enumerate(lineup.slots):
        y = 0.735 - i * step
        in_stack = slot.player_name in stacked
        chip_edge = MODEL if in_stack else EDGE
        ax.add_patch(FancyBboxPatch(
            (_X_CHIP, y - 0.017), 0.048, 0.048,
            boxstyle="round,pad=0,rounding_size=0.008", mutation_aspect=9 / 16,
            facecolor=TRACK, edgecolor=chip_edge, linewidth=1.6,
        ))
        _display(fig, _X_CHIP + 0.024, y - 0.001, slot.slot, color=INK,
                 fontsize=14 if len(slot.slot) <= 4 else 9.5, ha="center",
                 fontweight="semibold")
        _display(fig, _X_NAME, y - 0.004, slot.player_name, color=INK,
                 fontsize=20, fontweight="semibold")
        _text(fig, _X_TEAM, y, slot.team or "—", color=INK_DIM, fontsize=14)
        _text(fig, _X_TIME, y, game_time_ct(slot.kickoff), color=INK_DIM,
              fontsize=13.5)
        _text(fig, _X_SALARY, y, f"${slot.salary:,}", color=INK, fontsize=15,
              ha="right")
        _display(fig, _X_POINTS, y - 0.004, f"{slot.points:.1f}", color=INK,
                 fontsize=18, ha="right", fontweight="semibold")


def _team_split(lineup: Lineup) -> str:
    """"LAD 4 · ATL 2" — rostered players per team, biggest side first."""
    counts: dict[str, int] = {}
    for slot in lineup.slots:
        if slot.team:
            counts[slot.team] = counts.get(slot.team, 0) + 1
    if not counts:
        return "—"
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return " · ".join(f"{team} {n}" for team, n in ranked[:4])


def _context_panel(fig: plt.Figure, lineup: Lineup, *, cap: int,
                   source_note: str) -> None:
    x = 0.655
    _text(fig, x, 0.795, "SALARY USED", color=INK_DIM, fontsize=13.5)
    _display(fig, x, 0.735, f"${lineup.total_salary:,}", color=INK, fontsize=42)
    # Two "$" in one string trips matplotlib's mathtext parser; escape both.
    _text(fig, x, 0.700,
          rf"of \${cap:,} cap · \${cap - lineup.total_salary:,} left",
          color=INK_DIM, fontsize=13.5)
    ax = fig.add_axes((x, 0.660, 0.28, 0.020))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.add_patch(FancyBboxPatch(
        (0, 0), 1, 1, boxstyle="round,pad=0,rounding_size=0.05",
        mutation_aspect=0.04, facecolor=TRACK, edgecolor="none"))
    used = min(lineup.total_salary / cap, 1.0)
    ax.add_patch(FancyBboxPatch(
        (0, 0), max(used, 0.02), 1, boxstyle="round,pad=0,rounding_size=0.05",
        mutation_aspect=0.04, facecolor=MODEL, edgecolor="none"))

    _text(fig, x, 0.585, "PROJECTED", color=INK_DIM, fontsize=13.5)
    _display(fig, x, 0.520, f"{lineup.total_points:.1f}", color=INK, fontsize=48)
    _text(fig, x, 0.487, "DK points (expected, no milestone bonuses)",
          color=INK_DIM, fontsize=12.5)

    stacks = lineup.stacks()
    # QB-stack grammar is football's; a roster with no QB slot (MLB classic)
    # simply has no stack section.
    has_qb = any(s.position == "QB" for s in lineup.slots)
    if has_qb:
        _text(fig, x, 0.408, "STACK", color=INK_DIM, fontsize=13.5)
        if stacks:
            for i, stack in enumerate(stacks[:2]):
                _display(fig, x, 0.360 - i * 0.052, stack, color=MODEL,
                         fontsize=19, fontweight="semibold")
            _text(fig, x, 0.360 - len(stacks[:2]) * 0.052,
                  "QB with own pass-catchers: correlated scoring",
                  color=INK_DIM, fontsize=12)
        else:
            _text(fig, x, 0.360, "no QB stack in the optimal build",
                  color=INK_DIM, fontsize=14)
    else:
        # No QB grammar to state (MLB, showdown) — the panel instead carries
        # what those formats actually turn on: who is rostered from which
        # side, and which player is carrying the captain multiplier.
        _text(fig, x, 0.408, "ROSTER SPLIT", color=INK_DIM, fontsize=13.5)
        _display(fig, x, 0.360, _team_split(lineup), color=MODEL, fontsize=19,
                 fontweight="semibold")
        captain = next((s for s in lineup.slots if s.slot == "CPT"), None)
        if captain is not None:
            _text(fig, x, 0.322,
                  f"captain {captain.player_name} · 1.5x points and salary",
                  color=INK_DIM, fontsize=12.5)

    _text(fig, x, 0.205, "PROJECTIONS", color=INK_DIM, fontsize=13.5)
    _text(fig, x, 0.170, source_note, color=INK_DIM, fontsize=13)
    _text(fig, x, 0.140, "cash-game objective: maximize expected points",
          color=INK_DIM, fontsize=13)


def render_dfs_card(
    lineup: Lineup,
    path: Path | str,
    *,
    when: str = "",
    slate_label: str = "DK CLASSIC · MAIN SLATE",
    source_note: str = "FantasyPros consensus scored as DK points",
    cap: int = SALARY_CAP,
) -> Path:
    """Render the optimal-lineup card to ``path`` (1600×900 PNG)."""
    fig = _fig()
    _panel(fig, (0.04, 0.09, 0.575, 0.775))
    _panel(fig, (0.63, 0.09, 0.33, 0.775))
    _brand_header(fig, "DFS LINEUP", when)
    _text(fig, 0.95, 0.898, slate_label, color=INK, fontsize=13, ha="right")
    _roster_rows(fig, lineup)
    _context_panel(fig, lineup, cap=cap, source_note=source_note)
    _footer(fig, "projection-maximizing lineup · informational only, not advice · 21+")
    out = Path(path)
    fig.savefig(out, dpi=DPI, facecolor=BG)
    plt.close(fig)
    return out


def dfs_caption(lineup: Lineup, *, slate_label: str = "DK classic main slate") -> str:
    """Post copy: the build stated as fact — salary, projection, stack."""
    lines = [
        f"Optimal {slate_label} build — ${lineup.total_salary:,} of ${SALARY_CAP:,}, "
        f"projected {lineup.total_points:.1f} DK points.",
    ]
    lines.extend(
        f"{s.slot}: {s.player_name} ({s.team or '—'}) ${s.salary:,} · {s.points:.1f}"
        for s in lineup.slots
    )
    if lineup.stacks():
        lines.append("Stack: " + "; ".join(lineup.stacks()) + ".")
    return "\n".join(lines)


# --- salary-free formats (Tiers, Single Stat) ---------------------------------
# Same panel envelope as the classic card, minus the column that does not
# exist on these boards. With no cap to spend, the right panel states what
# the contest actually turns on: the projected total in the contest's OWN
# unit (home runs, touchdowns, DK points) and how the picks spread.

_X_TIER_NAME, _X_TIER_TEAM = 0.135, 0.360
_X_TIER_TIME, _X_TIER_POINTS = 0.430, 0.575


def _tier_rows(fig: plt.Figure, entry: TierEntry, unit: str) -> None:
    for label, x, ha in (("SLOT", _X_CHIP, "left"), ("PLAYER", _X_TIER_NAME, "left"),
                         ("TEAM", _X_TIER_TEAM, "left"),
                         ("GAME", _X_TIER_TIME, "left"),
                         (unit.upper(), _X_TIER_POINTS, "right")):
        _text(fig, x, 0.795, label, color=INK_DIM, fontsize=12.5, ha=ha)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    step = 0.648 / max(len(entry.picks), 6)
    for i, pick in enumerate(entry.picks):
        y = 0.735 - i * step
        ax.add_patch(FancyBboxPatch(
            (_X_CHIP, y - 0.017), 0.048, 0.048,
            boxstyle="round,pad=0,rounding_size=0.008", mutation_aspect=9 / 16,
            facecolor=TRACK, edgecolor=EDGE, linewidth=1.6,
        ))
        _display(fig, _X_CHIP + 0.024, y - 0.001, pick.slot, color=INK,
                 fontsize=14 if len(pick.slot) <= 4 else 9.5, ha="center",
                 fontweight="semibold")
        _display(fig, _X_TIER_NAME, y - 0.004, pick.player_name, color=INK,
                 fontsize=20, fontweight="semibold")
        _text(fig, _X_TIER_TEAM, y, pick.team or "—", color=INK_DIM, fontsize=14)
        _text(fig, _X_TIER_TIME, y, game_time_ct(pick.kickoff), color=INK_DIM,
              fontsize=13.5)
        _display(fig, _X_TIER_POINTS, y - 0.004, _tier_number(pick.points),
                 color=INK, fontsize=18, ha="right", fontweight="semibold")


def _tier_number(value: float) -> str:
    """A projection in the contest's own unit: 0.248 HR reads nothing at 0.2."""
    return f"{value:.3f}" if abs(value) < 5 else f"{value:.1f}"


def render_tier_card(
    entry: TierEntry,
    path: Path | str,
    *,
    when: str = "",
    slate_label: str = "DK TIERS",
    unit: str = "DK PTS",
    source_note: str = "contextual model scored as DK points",
) -> Path:
    """Render a Tiers / Single Stat entry card to ``path`` (1600×900 PNG)."""
    fig = _fig()
    _panel(fig, (0.04, 0.09, 0.575, 0.775))
    _panel(fig, (0.63, 0.09, 0.33, 0.775))
    _brand_header(fig, "DFS ENTRY", when)
    _text(fig, 0.95, 0.898, slate_label, color=INK, fontsize=13, ha="right")
    _tier_rows(fig, entry, unit)

    x = 0.655
    _text(fig, x, 0.795, "PROJECTED", color=INK_DIM, fontsize=13.5)
    _display(fig, x, 0.720, _tier_number(entry.total_points), color=INK, fontsize=48)
    _text(fig, x, 0.688, f"expected {unit.lower()} across the entry",
          color=INK_DIM, fontsize=12.5)

    _text(fig, x, 0.600, "SPREAD", color=INK_DIM, fontsize=13.5)
    teams = len({p.team for p in entry.picks if p.team})
    games = len({game_time_ct(p.kickoff) for p in entry.picks})
    noun = "team" if teams == 1 else "teams"
    _display(fig, x, 0.552, f"{teams} {noun} · {games} start times",
             color=MODEL, fontsize=19, fontweight="semibold")
    _text(fig, x, 0.514, "no salary cap on this format — the projection is "
                         "the whole contest", color=INK_DIM, fontsize=12.5)

    _text(fig, x, 0.205, "PROJECTIONS", color=INK_DIM, fontsize=13.5)
    _text(fig, x, 0.170, source_note, color=INK_DIM, fontsize=13)
    _text(fig, x, 0.140, "objective: maximize the contest's own stat",
          color=INK_DIM, fontsize=13)

    _footer(fig, "projection-maximizing entry · informational only, not advice · 21+")
    out = Path(path)
    fig.savefig(out, dpi=DPI, facecolor=BG)
    plt.close(fig)
    return out


def tier_caption(entry: TierEntry, *, slate_label: str, unit: str) -> str:
    """Post copy for a salary-free entry."""
    lines = [f"{slate_label} entry — projected {_tier_number(entry.total_points)} "
             f"{unit}."]
    lines.extend(
        f"{p.slot}: {p.player_name} ({p.team or '—'}) · {_tier_number(p.points)}"
        for p in entry.picks
    )
    return "\n".join(lines)
