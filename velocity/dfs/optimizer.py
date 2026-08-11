"""DFS lineup optimizer — the exact best DK classic roster under the cap.

Builds the highest-projected legal lineup: QB / 2 RB / 3 WR / TE / FLEX
(RB-WR-TE) / DST under the $50,000 cap. The search is **exact**, not
heuristic: DK salaries are multiples of $100, so the cap discretizes into 500
buckets and the problem solves as a choose-k knapsack per position group,
convolved across groups, once per FLEX shape (extra RB, WR, or TE). The test
suite pins the result against brute force.

Cash-game objective only (maximize projected points). GPP construction —
distribution targets, stacking constraints, ownership leverage — arrives with
the sim-based scoring (docs/FOOTBALL_CUTOVER.md §5b).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

SALARY_CAP = 50_000
_BUCKET = 100  # DK salaries are $100 multiples; non-multiples round UP (never over cap)

# DK classic slots, in display order. FLEX accepts any of the flex positions.
SLOTS: tuple[str, ...] = ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST")
FLEX_POSITIONS = ("RB", "WR", "TE")
_BASE_COUNTS = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 1}

_REQUIRED_COLUMNS = ("player_name", "position", "salary", "points")


@dataclass(frozen=True)
class LineupSlot:
    """One filled roster slot."""

    slot: str
    player_name: str
    position: str
    team: str | None
    salary: int
    points: float


@dataclass(frozen=True)
class Lineup:
    """A legal DK classic lineup with its totals."""

    slots: tuple[LineupSlot, ...]
    total_salary: int
    total_points: float

    def stacks(self) -> list[str]:
        """QB-stack callouts: teammates of a rostered QB ("BUF: Allen + Shakir")."""
        out: list[str] = []
        for qb in (s for s in self.slots if s.position == "QB"):
            if not qb.team:
                continue
            mates = [s for s in self.slots
                     if s.team == qb.team and s.position in ("WR", "TE", "RB")
                     and s.player_name != qb.player_name]
            if mates:
                names = " + ".join(_surname(m.player_name) for m in mates)
                out.append(f"{qb.team}: {_surname(qb.player_name)} + {names}")
        return out


def _surname(name: str) -> str:
    tokens = str(name).split()
    return tokens[-1] if tokens else str(name)


def _choose_k_tables(
    players: list[tuple[int, float, int]], k_max: int, buckets: int
) -> list[list[dict[int, tuple[float, tuple[int, ...]]]]]:
    """Knapsack tables: best[k][b] = (points, chosen row ids) for exactly-k picks.

    ``players`` rows are ``(bucket_cost, points, row_id)``. States above the
    budget are simply absent. Deterministic tie-break: higher points, then the
    lexicographically smaller chosen-id tuple.
    """
    best: list[dict[int, tuple[float, tuple[int, ...]]]] = [
        {} for _ in range(k_max + 1)
    ]
    best[0][0] = (0.0, ())
    for cost, points, rid in players:
        for k in range(min(k_max, len(best) - 1), 0, -1):
            source = best[k - 1]
            target = best[k]
            for b, (pts, ids) in list(source.items()):
                nb = b + cost
                if nb > buckets:
                    continue
                candidate = (pts + points, tuple(sorted((*ids, rid))))
                incumbent = target.get(nb)
                if incumbent is None or (candidate[0], [-i for i in candidate[1]]) > (
                    incumbent[0], [-i for i in incumbent[1]]
                ):
                    target[nb] = candidate
    return [best]


def _group_frontier(
    pool: pd.DataFrame, position: str, k: int, buckets: int
) -> dict[int, tuple[float, tuple[int, ...]]] | None:
    """Best exactly-k selection from one position at every affordable budget."""
    rows = pool[pool["position"] == position]
    players = [
        (math.ceil(int(salary) / _BUCKET), float(points), int(rid))
        for rid, salary, points in zip(
            rows.index.to_list(), rows["salary"], rows["points"], strict=True
        )
    ]
    if len(players) < k:
        return None
    (tables,) = _choose_k_tables(players, k, buckets)
    return tables[k] if tables[k] else None


def _convolve(
    a: dict[int, tuple[float, tuple[int, ...]]],
    b: dict[int, tuple[float, tuple[int, ...]]],
    buckets: int,
) -> dict[int, tuple[float, tuple[int, ...]]]:
    out: dict[int, tuple[float, tuple[int, ...]]] = {}
    for ba, (pa, ids_a) in a.items():
        for bb, (pb, ids_b) in b.items():
            nb = ba + bb
            if nb > buckets:
                continue
            candidate = (pa + pb, tuple(sorted((*ids_a, *ids_b))))
            incumbent = out.get(nb)
            if incumbent is None or candidate > incumbent:
                out[nb] = candidate
    return out


def build_lineup(pool: pd.DataFrame, *, cap: int = SALARY_CAP) -> Lineup | None:
    """The exact best legal lineup from a player pool, or ``None`` if infeasible.

    ``pool`` needs ``player_name``/``position``/``salary``/``points`` (``team``
    optional, used for stack callouts). Rows with missing salary or points are
    dropped; duplicate player names keep their highest-point row.
    """
    pool = pool.dropna(subset=["salary", "points"]).copy()
    for col in _REQUIRED_COLUMNS:
        if col not in pool.columns:
            raise ValueError(f"pool needs a {col!r} column")
    pool["salary"] = pool["salary"].astype(int)
    pool["points"] = pool["points"].astype(float)
    if "team" not in pool.columns:
        pool["team"] = None
    pool = (
        pool.sort_values("points", ascending=False)
        .drop_duplicates(subset=["player_name"])
        .reset_index(drop=True)
    )
    buckets = cap // _BUCKET

    best_total: tuple[float, tuple[int, ...]] | None = None
    best_flex: str | None = None
    for flex in FLEX_POSITIONS:
        counts = dict(_BASE_COUNTS)
        counts[flex] += 1
        frontiers = []
        feasible = True
        for position, k in counts.items():
            frontier = _group_frontier(pool, position, k, buckets)
            if frontier is None:
                feasible = False
                break
            frontiers.append(frontier)
        if not feasible:
            continue
        combined = frontiers[0]
        for frontier in frontiers[1:]:
            combined = _convolve(combined, frontier, buckets)
            if not combined:
                break
        if not combined:
            continue
        candidate = max(combined.values())
        if best_total is None or candidate > best_total:
            best_total = candidate
            best_flex = flex

    if best_total is None or best_flex is None:
        return None

    _, chosen_ids = best_total
    rows = pool.loc[list(chosen_ids)]
    # Assign display slots: base slots by position; the extra flex-position
    # player (the lowest-point one of their position group) takes FLEX.
    slots: list[LineupSlot] = []
    by_position: dict[str, list] = {}
    for _idx, row in rows.sort_values("points", ascending=False).iterrows():
        by_position.setdefault(str(row.position), []).append(row)
    flex_row = by_position[best_flex].pop()  # lowest-point of the flexed group
    for slot in SLOTS:
        row = flex_row if slot == "FLEX" else by_position[slot].pop(0)
        slots.append(
            LineupSlot(
                slot=slot,
                player_name=str(row.player_name),
                position=str(row.position),
                team=None if pd.isna(row.team) else str(row.team),
                salary=int(row.salary),
                points=float(row.points),
            )
        )
    return Lineup(
        slots=tuple(slots),
        total_salary=int(rows["salary"].sum()),
        total_points=round(float(rows["points"].sum()), 2),
    )


def lineup_pool(salaries: pd.DataFrame, points: pd.DataFrame) -> pd.DataFrame:
    """Join a DK salaries frame to projected points by normalized player name.

    ``salaries`` is the collector's canonical frame (player_name/position/
    salary/team); ``points`` carries ``player_name``/``points``. Unprojected
    players join at 0.0 points **only for DST** (a projectionless defense is a
    legal punt); skill players without a projection are dropped — the optimizer
    must never roster a player the model knows nothing about.
    """
    import re

    def norm(name: object) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(name).lower())

    s = salaries.copy()
    s["_key"] = s["player_name"].map(norm)
    p = points.copy()
    p["_key"] = p["player_name"].map(norm)
    merged = s.merge(
        p[["_key", "points"]], on="_key", how="left", suffixes=("", "_proj")
    )
    is_dst = merged["position"].astype(str).str.upper().isin(["DST", "D", "DEF"])
    merged.loc[is_dst, "points"] = merged.loc[is_dst, "points"].fillna(0.0)
    merged.loc[is_dst, "position"] = "DST"
    merged = merged.dropna(subset=["points"])
    return merged.drop(columns=["_key"]).reset_index(drop=True)
