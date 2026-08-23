"""DFS lineup optimizer — the exact best DK classic roster under the cap.

Builds the highest-projected legal lineup for a roster spec — NFL classic
(QB / 2 RB / 3 WR / TE / FLEX / DST) or CFB classic (QB / 2 RB / 3 WR /
FLEX / S-FLEX; DK college has no TE slot or DST, and the Super-FLEX admits a
second QB) — under the $50,000 cap. The search is **exact**, not heuristic:
DK salaries are multiples of $100, so the cap discretizes into 500 buckets
and the problem solves as a choose-k knapsack per position group, convolved
across groups, once per flex shape (every way the flex slots can distribute
across positions). The test suite pins the result against brute force.

Cash-game objective only (maximize projected points). GPP construction —
distribution targets, stacking constraints, ownership leverage — arrives with
the sim-based scoring (docs/FOOTBALL_CUTOVER.md §5b).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import permutations, product

import pandas as pd

SALARY_CAP = 50_000
_BUCKET = 100  # DK salaries are $100 multiples; non-multiples round UP (never over cap)


@dataclass(frozen=True)
class RosterSpec:
    """One contest format: display slots, fixed position counts, flex slots."""

    name: str
    slots: tuple[str, ...]  # display order, flex slots by label
    base_counts: Mapping[str, int]  # position → dedicated slot count
    flex: tuple[tuple[str, tuple[str, ...]], ...]  # (slot label, eligible positions)


NFL_CLASSIC = RosterSpec(
    "nfl_classic",
    ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"),
    {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 1},
    (("FLEX", ("RB", "WR", "TE")),),
)
# DK college classic: no TE slot (DK lists college TEs as WR), no DST, and the
# Super-FLEX admits a second QB.
CFB_CLASSIC = RosterSpec(
    "cfb_classic",
    ("QB", "RB", "RB", "WR", "WR", "WR", "FLEX", "S-FLEX"),
    {"QB": 1, "RB": 2, "WR": 3},
    (("FLEX", ("RB", "WR")), ("S-FLEX", ("QB", "RB", "WR"))),
)
# DK MLB classic: 2 P + the eight field slots, no flex/UTIL. SP/RP both fill
# P; multi-eligible fielders ("2B/SS") price at their first listed position
# (pipeline normalization) — always legal on DK, occasionally sub-optimal.
MLB_CLASSIC = RosterSpec(
    "mlb_classic",
    ("P", "P", "C", "1B", "2B", "3B", "SS", "OF", "OF", "OF"),
    {"P": 2, "C": 1, "1B": 1, "2B": 1, "3B": 1, "SS": 1, "OF": 3},
    (),
)

# NFL-classic aliases, kept for callers/tests that predate roster specs.
SLOTS: tuple[str, ...] = NFL_CLASSIC.slots
FLEX_POSITIONS = ("RB", "WR", "TE")

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


def _flex_shapes(spec: RosterSpec) -> set[tuple[tuple[str, int], ...]]:
    """Every distinct position-count multiset the flex slots can produce."""
    shapes: set[tuple[tuple[str, int], ...]] = set()
    for combo in product(*[eligible for _, eligible in spec.flex]):
        counts = dict(spec.base_counts)
        for position in combo:
            counts[position] = counts.get(position, 0) + 1
        shapes.add(tuple(sorted(counts.items())))
    return shapes


def _assign_slots(rows: pd.DataFrame, spec: RosterSpec) -> list[LineupSlot] | None:
    """Chosen rows → display slots: base slots by position, extras to flex.

    Per position, the top-point players take the dedicated slots (display
    convention); the leftovers must fill the flex slots in some eligible
    order — with at most a couple of flex slots, trying permutations is exact
    and cheap.
    """
    by_position: dict[str, list] = {}
    for _idx, row in rows.sort_values("points", ascending=False).iterrows():
        by_position.setdefault(str(row.position), []).append(row)
    base_queues: dict[str, list] = {}
    extras: list = []
    for position, group in by_position.items():
        base = int(spec.base_counts.get(position, 0))
        base_queues[position] = group[:base]
        extras.extend(group[base:])
    assignment = None
    for perm in permutations(extras):
        if all(
            str(row.position) in eligible
            for row, (_label, eligible) in zip(perm, spec.flex, strict=True)
        ):
            assignment = {label: row for row, (label, _e) in
                          zip(perm, spec.flex, strict=True)}
            break
    if assignment is None:
        return None
    slots: list[LineupSlot] = []
    for slot in spec.slots:
        row = assignment[slot] if slot in assignment else base_queues[slot].pop(0)
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
    return slots


def build_lineup(
    pool: pd.DataFrame, *, cap: int = SALARY_CAP, spec: RosterSpec = NFL_CLASSIC
) -> Lineup | None:
    """The exact best legal lineup from a player pool, or ``None`` if infeasible.

    ``pool`` needs ``player_name``/``position``/``salary``/``points`` (``team``
    optional, used for stack callouts). Rows with missing salary or points are
    dropped; duplicate player names keep their highest-point row. ``spec``
    selects the contest format (NFL classic by default).
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
    for shape in sorted(_flex_shapes(spec)):
        frontiers = []
        feasible = True
        for position, k in shape:
            if k == 0:
                continue
            frontier = _group_frontier(pool, position, k, buckets)
            if frontier is None:
                feasible = False
                break
            frontiers.append(frontier)
        if not feasible or not frontiers:
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

    if best_total is None:
        return None

    _, chosen_ids = best_total
    rows = pool.loc[list(chosen_ids)]
    slots = _assign_slots(rows, spec)
    if slots is None:  # cannot happen for counts produced by _flex_shapes
        return None
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
    import unicodedata

    def norm(name: object) -> str:
        # Fold accents BEFORE stripping: statsapi spells "Sánchez", DK
        # "Sanchez" — deleting the accented letter instead of folding it
        # would silently drop the player from the pool.
        folded = unicodedata.normalize("NFKD", str(name)).encode(
            "ascii", "ignore").decode("ascii")
        return re.sub(r"[^a-z0-9]+", "", folded.lower())

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
