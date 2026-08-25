"""Showdown Captain Mode — the exact best six-man single-game DK roster.

DK's Showdown format (MLB game type 114, NFL 96, CFB 95) is one game, six
players, a $50,000 cap: a **captain** who scores 1.5x and costs 1.5x, plus
five UTIL/FLEX. DK ships the board with every player listed **twice** — once
at the captain roster slot at the inflated salary, once at flex — so the
first job here is collapsing that doubled board back to one row per player
carrying both prices (:func:`showdown_board`).

Two DK rules bind beyond the cap, both read off the game-type rules payload
(``uniquePlayers``, ``teamCount.minValue``):

* the captain may not also fill a flex slot;
* a roster must span **at least two teams** — all six from one side is
  illegal, which matters because a lopsided projection set will happily
  build it.

The search is exact, like the classic optimizer: salaries are $100
multiples, so the cap discretizes and the five flex slots solve as a
choose-5 knapsack whose states also carry a per-team count (the two-team
rule) . Captains are then enumerated against that frontier; a captain who
turns up inside his own best-five triggers one rebuild without him. The
pool is dominance-pruned first — a player beaten on both price and points
by six same-team players can never appear in a six-man roster — which keeps
the whole solve well under a second on a real 95-player board.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import reduce

import pandas as pd

from velocity.dfs.optimizer import Lineup, LineupSlot

SALARY_CAP = 50_000
CAPTAIN_MULTIPLIER = 1.5


@dataclass(frozen=True)
class ShowdownSpec:
    """One showdown format: what DK calls the slots and how many flex."""

    name: str
    captain_slot: str
    flex_slot: str
    n_flex: int = 5
    multiplier: float = CAPTAIN_MULTIPLIER
    min_teams: int = 2


# DK game type 114 / 96 / 95 — identical shapes, different slot vocabulary
# (MLB and CFB call the non-captain slots UTIL, NFL calls them FLEX).
MLB_SHOWDOWN = ShowdownSpec("mlb_showdown", "CPT", "UTIL")
NFL_SHOWDOWN = ShowdownSpec("nfl_showdown", "CPT", "FLEX")
CFB_SHOWDOWN = ShowdownSpec("cfb_showdown", "CPT", "UTIL")

SHOWDOWN_SPECS = {
    "mlb": MLB_SHOWDOWN,
    "nfl": NFL_SHOWDOWN,
    "ncaaf": CFB_SHOWDOWN,
}
# DK's own name for the format, as the lobby's contests spell it. Exact
# match matters: DK also runs "Madden Showdown Captain Mode" boards of
# simulated games, which share the roster shape and nothing else.
SHOWDOWN_GAME_TYPE = "Showdown Captain Mode"
# The matching contest_type_id per league, for snapshots banked before the
# game-type name was captured.
SHOWDOWN_CONTEST_TYPES = {"mlb": 114, "nfl": 96, "ncaaf": 95}


def showdown_board(salaries: pd.DataFrame) -> pd.DataFrame:
    """Collapse DK's doubled showdown board to one row per player.

    Returns the flex-price row per player with a ``captain_salary`` column
    taken from that player's captain entry (DK's own number — 1.5x rounded
    DK's way, never re-derived here). A board that lists a player once (an
    old snapshot taken before the roster-slot id was captured, or a format
    without a captain price) keeps its single row and prices the captain at
    the multiplier.
    """
    if salaries.empty:
        return salaries.assign(captain_salary=pd.Series(dtype="int64"))
    df = salaries.copy()
    df["salary"] = pd.to_numeric(df["salary"], errors="coerce")
    df = df.dropna(subset=["salary"])
    if df.empty:
        return df.assign(captain_salary=pd.Series(dtype="int64"))
    df["_key"] = df["player_id"].astype(str)
    # Within a player, the captain entry is simply the dearer of the two.
    df["_flex"] = df.groupby("_key")["salary"].transform("min")
    df["_cpt"] = df.groupby("_key")["salary"].transform("max")
    return (
        df[df["salary"] == df["_flex"]]
        .drop_duplicates(subset=["_key"])
        .assign(captain_salary=lambda f: f["_cpt"].astype(int),
                salary=lambda f: f["_flex"].astype(int))
        .drop(columns=["_key", "_flex", "_cpt"])
        .reset_index(drop=True)
    )


def _bucket_size(values: list[int]) -> int:
    """The salary grid: DK's own increment, so ceil() costs stay exact."""
    grid = reduce(math.gcd, values, 0)
    return grid if grid > 0 else 100


def _prune_dominated(pool: pd.DataFrame, roster_size: int) -> pd.DataFrame:
    """Drop players beaten on price AND points by ``roster_size`` teammates.

    Exact, not a heuristic: a roster holds ``roster_size`` players, so if a
    player has that many same-team dominators (no dearer at either price, no
    fewer points) then any roster using him can swap in a dominator that the
    roster is not already using — same team counts, no more salary, no fewer
    points. Same-team only, because the two-team rule makes a cross-team
    swap unsafe.
    """
    keep: list[int] = []
    for _team, group in pool.groupby(pool["team"].astype(str), dropna=False):
        salary = group["salary"].to_numpy()
        captain = group["captain_salary"].to_numpy()
        points = group["points"].to_numpy()
        order = group.index.to_numpy()
        for i in range(len(group)):
            dominators = (
                (salary <= salary[i])
                & (captain <= captain[i])
                & (points >= points[i])
            )
            dominators[i] = False
            # Ties would dominate each other symmetrically; break by index so
            # a block of identical players never deletes itself entirely.
            equal = (salary == salary[i]) & (captain == captain[i]) & (points == points[i])
            dominators &= ~equal | (order < order[i])
            if int(dominators.sum()) < roster_size:
                keep.append(int(order[i]))
    return pool.loc[sorted(keep)]


_State = dict[tuple[int, int], tuple[float, tuple[int, ...]]]


def _flex_frontier(
    players: list[tuple[int, float, int, int]], k: int, buckets: int
) -> _State:
    """best[(home_count, budget)] = (points, ids) for exactly-k flex picks.

    ``players`` rows are ``(bucket_cost, points, row_id, is_home)``. The
    home count rides along so the two-team rule can be answered exactly
    rather than repaired after the fact.
    """
    best: list[_State] = [{} for _ in range(k + 1)]
    best[0][(0, 0)] = (0.0, ())
    for cost, points, rid, is_home in players:
        for depth in range(k, 0, -1):
            source, target = best[depth - 1], best[depth]
            for (home, budget), (pts, ids) in list(source.items()):
                nb = budget + cost
                if nb > buckets:
                    continue
                state = (home + is_home, nb)
                candidate = (pts + points, tuple(sorted((*ids, rid))))
                incumbent = target.get(state)
                if incumbent is None or candidate > incumbent:
                    target[state] = candidate
    return best[k]


def _best_under(
    frontier: _State, budget: int, *, min_home: int, max_home: int
) -> tuple[float, tuple[int, ...]] | None:
    """The best flex five inside a budget and a home-count window."""
    best: tuple[float, tuple[int, ...]] | None = None
    for (home, cost), value in frontier.items():
        if cost > budget or home < min_home or home > max_home:
            continue
        if best is None or value > best:
            best = value
    return best


def build_showdown(
    pool: pd.DataFrame, *, cap: int = SALARY_CAP, spec: ShowdownSpec = MLB_SHOWDOWN
) -> Lineup | None:
    """The exact best legal showdown roster, or ``None`` when infeasible.

    ``pool`` needs ``player_name``/``salary``/``points`` plus, ideally,
    ``team`` (the two-team rule is enforced only when the pool actually
    spans two teams) and ``captain_salary`` (defaults to the multiplier).
    Captain points are the multiplier times the flex projection; captain
    salary is DK's number, never re-derived.
    """
    for col in ("player_name", "salary", "points"):
        if col not in pool.columns:
            raise ValueError(f"pool needs a {col!r} column")
    pool = pool.dropna(subset=["salary", "points"]).copy()
    if "team" not in pool.columns:
        pool["team"] = None
    if "position" not in pool.columns:
        pool["position"] = None
    pool["salary"] = pool["salary"].astype(int)
    pool["points"] = pool["points"].astype(float)
    if "captain_salary" not in pool.columns:
        pool["captain_salary"] = (pool["salary"] * spec.multiplier).round().astype(int)
    pool["captain_salary"] = pool["captain_salary"].fillna(
        pool["salary"] * spec.multiplier).astype(int)
    pool = (
        pool.sort_values("points", ascending=False)
        .drop_duplicates(subset=["player_name"])
        .reset_index(drop=True)
    )
    roster = spec.n_flex + 1
    if len(pool) < roster:
        return None
    pool = _prune_dominated(pool, roster).reset_index(drop=True)
    if len(pool) < roster:
        return None

    teams = [t for t in pool["team"].dropna().astype(str).unique() if t]
    # The rule only bites on a real two-sided board; a pool with one team (or
    # no team labels at all) can't satisfy it and must not be blocked by it.
    enforce_teams = spec.min_teams >= 2 and len(teams) == 2 and pool["team"].notna().all()
    home = sorted(teams)[0] if enforce_teams else None

    bucket = _bucket_size(
        [*pool["salary"].astype(int), *pool["captain_salary"].astype(int)])
    buckets = cap // bucket
    records = pool.to_dict("records")
    rows = [
        (math.ceil(int(r["salary"]) / bucket), float(r["points"]), i,
         1 if enforce_teams and str(r["team"]) == home else 0)
        for i, r in enumerate(records)
    ]
    frontier = _flex_frontier(rows, spec.n_flex, buckets)
    if not frontier:
        return None

    excluded: dict[int, _State] = {}
    best: tuple[float, int, tuple[int, ...]] | None = None
    for i, entry in enumerate(records):
        budget = buckets - math.ceil(int(entry["captain_salary"]) / bucket)
        if budget < 0:
            continue
        captain_home = enforce_teams and str(entry["team"]) == home
        # At least one flex from the other side whenever the captain would
        # otherwise make it a one-team roster.
        min_home = 1 if enforce_teams and not captain_home else 0
        max_home = spec.n_flex - 1 if captain_home else spec.n_flex
        table = frontier
        picked = _best_under(table, budget, min_home=min_home, max_home=max_home)
        if picked is not None and i in picked[1]:
            # The captain turned up in his own best five: re-solve without him.
            if i not in excluded:
                excluded[i] = _flex_frontier(
                    [r for r in rows if r[2] != i], spec.n_flex, buckets)
            picked = _best_under(excluded[i], budget,
                                 min_home=min_home, max_home=max_home)
        if picked is None:
            continue
        total = spec.multiplier * float(entry["points"]) + picked[0]
        candidate = (total, -i, picked[1])
        if best is None or candidate > best:
            best = candidate

    if best is None:
        return None
    _total, neg_idx, flex_ids = best
    captain = pool.iloc[-neg_idx]
    flex = pool.iloc[list(flex_ids)].sort_values("points", ascending=False)

    def slot_of(row: pd.Series, label: str, factor: float, salary: int) -> LineupSlot:
        kickoff = row.get("kickoff")
        return LineupSlot(
            slot=label,
            player_name=str(row["player_name"]),
            position=str(row["position"]) if pd.notna(row["position"]) else "",
            team=None if pd.isna(row["team"]) else str(row["team"]),
            salary=int(salary),
            points=round(float(row["points"]) * factor, 2),
            kickoff=None if kickoff is None or pd.isna(kickoff)
            else pd.Timestamp(kickoff),
        )

    slots = [slot_of(captain, spec.captain_slot, spec.multiplier,
                     int(captain["captain_salary"]))]
    slots += [slot_of(row, spec.flex_slot, 1.0, int(row["salary"]))
              for _i, row in flex.iterrows()]
    return Lineup(
        slots=tuple(slots),
        total_salary=sum(s.salary for s in slots),
        total_points=round(sum(s.points for s in slots), 2),
    )
