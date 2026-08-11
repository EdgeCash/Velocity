"""DFS pipeline — salary snapshot + FantasyPros projections → the best lineup.

The pure glue between the collectors and the optimizer: pick the main slate
out of a DK salary snapshot, score the FantasyPros long frame into expected
DK points, join, and solve. Offline-testable end to end; the CLI wrapper
(``scripts/build_dfs_lineup.py``) only adds file IO and the card render.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from velocity.dfs.optimizer import (
    NFL_CLASSIC,
    Lineup,
    RosterSpec,
    build_lineup,
    lineup_pool,
)
from velocity.dfs.scoring import dk_expected_points


@dataclass(frozen=True)
class LineupRun:
    """One solved slate: the lineup plus the context the card and logs state."""

    lineup: Lineup | None
    draft_group_id: str
    n_games: int
    n_salaried: int  # players on the DK board for the group
    n_pool: int  # players surviving the projection join


def is_season_long(fp: pd.DataFrame) -> bool:
    """True when a FantasyPros frame carries season-long (week 0) projections.

    Season totals collapse to absurd weekly numbers (a 372-point QB), so
    weekly consumers — the DFS lineup and the prop sim — must refuse them
    rather than price nonsense. Proven live: the first demo run built a
    1,761-point "lineup" from a week-0 snapshot.
    """
    if fp.empty or "week" not in fp.columns:
        return False
    weeks = pd.to_numeric(fp["week"], errors="coerce").fillna(0)
    return bool((weeks == 0).all())


def main_slate_group(salaries: pd.DataFrame) -> str | None:
    """The draft group that looks like the classic main slate.

    Largest distinct-game count wins (the main slate spans the most games);
    ties break to the most salaried players, then lowest id for determinism.
    """
    if salaries.empty:
        return None
    by_group = salaries.groupby(salaries["draft_group_id"].astype(str)).agg(
        games=("competition", "nunique"), players=("player_id", "size")
    )
    top = by_group.sort_values(["games", "players"], ascending=False).iloc[0]
    winners = by_group[
        (by_group["games"] == top["games"]) & (by_group["players"] == top["players"])
    ]
    return str(sorted(winners.index)[0])


def solve_slate(
    salaries: pd.DataFrame,
    fp: pd.DataFrame,
    *,
    draft_group: str | None = None,
    spec: RosterSpec = NFL_CLASSIC,
) -> LineupRun:
    """Solve one draft group's optimal lineup from raw frames.

    ``salaries`` is the collector's normalized frame (any number of draft
    groups — the main slate is auto-picked unless ``draft_group`` pins one);
    ``fp`` is the FantasyPros long frame; ``spec`` the contest format. An
    unsolvable slate (no group, or an infeasible pool) returns a run with
    ``lineup=None`` rather than raising — an empty board is a state, not an
    error.
    """
    group = draft_group or main_slate_group(salaries)
    if group is None:
        return LineupRun(None, "", 0, 0, 0)
    board = salaries[salaries["draft_group_id"].astype(str) == str(group)]
    points = dk_expected_points(fp)
    pool = lineup_pool(board, points)
    lineup = None if pool.empty else build_lineup(pool, spec=spec)
    return LineupRun(
        lineup=lineup,
        draft_group_id=str(group),
        n_games=int(board["competition"].nunique()),
        n_salaried=len(board),
        n_pool=len(pool),
    )


def lineup_frame(run: LineupRun) -> pd.DataFrame:
    """The solved lineup as a persistable frame (empty when unsolved)."""
    if run.lineup is None:
        return pd.DataFrame(
            columns=["slot", "player_name", "position", "team", "salary", "points"]
        )
    return pd.DataFrame([
        {"slot": s.slot, "player_name": s.player_name, "position": s.position,
         "team": s.team, "salary": s.salary, "points": s.points}
        for s in run.lineup.slots
    ]).assign(draft_group_id=run.draft_group_id)
