"""DFS scoring + lineup optimizer — exactness where it's cheap, honesty elsewhere.

DK scoring is pinned by hand arithmetic. The optimizer is pinned two ways: on
a small pool it must match the brute-force optimum exactly, and on any pool
its output must be *legal* (slots, cap, no duplicate players).
"""

from __future__ import annotations

from collections import Counter
from itertools import combinations, product

import pandas as pd
import pytest
from velocity.dfs.optimizer import (
    CFB_CLASSIC,
    NFL_CLASSIC,
    SALARY_CAP,
    SLOTS,
    RosterSpec,
    build_lineup,
    lineup_pool,
)
from velocity.dfs.scoring import dk_expected_points


def test_dk_expected_points_hand_checked() -> None:
    fp = pd.DataFrame(
        [
            ("qb1", "Josh Allen", "BUF", "QB", "pass_yds", 285.0),
            ("qb1", "Josh Allen", "BUF", "QB", "pass_tds", 2.1),
            ("qb1", "Josh Allen", "BUF", "QB", "pass_int", 0.7),
            ("qb1", "Josh Allen", "BUF", "QB", "rush_yds", 32.0),
            ("qb1", "Josh Allen", "BUF", "QB", "rush_tds", 0.4),
            ("te1", "Travis Kelce", "KC", "TE", "rec", 6.5),
            ("te1", "Travis Kelce", "KC", "TE", "rec_yds", 72.0),
            ("te1", "Travis Kelce", "KC", "TE", "rec_tds", 0.55),
            ("te1", "Travis Kelce", "KC", "TE", "points", 19.9),  # unscored key ignored
        ],
        columns=["player_id", "player_name", "team", "position", "stat", "value"],
    )
    pts = dk_expected_points(fp).set_index("player_name")["points"]
    # Allen: 285×.04 + 2.1×4 − 0.7 + 32×.1 + 0.4×6 = 11.4+8.4−0.7+3.2+2.4 = 24.7
    assert pts["Josh Allen"] == pytest.approx(24.7)
    # Kelce: 6.5×1 + 72×.1 + 0.55×6 = 6.5+7.2+3.3 = 17.0
    assert pts["Travis Kelce"] == pytest.approx(17.0)


def _pool() -> pd.DataFrame:
    """A tiny board: exactly enough players that brute force is feasible."""
    rows = [
        # name, pos, team, salary, points
        ("QB A", "QB", "AAA", 8000, 24.0),
        ("QB B", "QB", "BBB", 6000, 19.0),
        ("RB A", "RB", "AAA", 9000, 22.0),
        ("RB B", "RB", "BBB", 7000, 17.0),
        ("RB C", "RB", "CCC", 5000, 13.0),
        ("RB D", "RB", "DDD", 4000, 9.0),
        ("WR A", "WR", "AAA", 9000, 21.0),
        ("WR B", "WR", "BBB", 7500, 17.5),
        ("WR C", "WR", "CCC", 6000, 14.0),
        ("WR D", "WR", "DDD", 4500, 10.0),
        ("WR E", "WR", "EEE", 3500, 7.0),
        ("TE A", "TE", "AAA", 6500, 14.5),
        ("TE B", "TE", "BBB", 3000, 6.5),
        ("DST A", "DST", "AAA", 3500, 9.0),
        ("DST B", "DST", "BBB", 2500, 6.0),
    ]
    return pd.DataFrame(rows, columns=["player_name", "position", "team", "salary", "points"])


def _legal_multiset(positions: list[str], spec: RosterSpec) -> bool:
    """Roster legality straight from the contest rules: the position counts
    must equal the dedicated slots plus one eligible position per flex slot,
    for some choice of flex fills. Independent of the optimizer's search."""
    have = Counter(positions)
    for fills in product(*[eligible for _, eligible in spec.flex]):
        need = Counter(dict(spec.base_counts))
        for position in fills:
            need[position] += 1
        if have == +need:  # +need drops zero-count entries
            return True
    return False


def _brute_force_best(
    pool: pd.DataFrame, cap: int, spec: RosterSpec = NFL_CLASSIC
) -> float:
    """The true optimum by exhaustive enumeration (small pools only)."""
    idx = list(pool.index)
    best = -1.0
    for combo in combinations(idx, len(spec.slots)):
        rows = pool.loc[list(combo)]
        if rows["salary"].sum() > cap:
            continue
        if _legal_multiset(list(rows["position"]), spec):
            best = max(best, float(rows["points"].sum()))
    return best


def test_optimizer_matches_brute_force_on_a_small_pool() -> None:
    pool = _pool()
    lineup = build_lineup(pool, cap=SALARY_CAP)
    assert lineup is not None
    assert lineup.total_points == pytest.approx(_brute_force_best(pool, SALARY_CAP))


def test_optimizer_respects_a_tight_cap() -> None:
    pool = _pool()
    lineup = build_lineup(pool, cap=42_000)
    assert lineup is not None
    assert lineup.total_salary <= 42_000
    assert lineup.total_points == pytest.approx(_brute_force_best(pool, 42_000))


def test_lineup_is_always_legal() -> None:
    lineup = build_lineup(_pool())
    assert lineup is not None
    assert tuple(s.slot for s in lineup.slots) == SLOTS
    names = [s.player_name for s in lineup.slots]
    assert len(names) == len(set(names))  # no player twice
    for s in lineup.slots:
        if s.slot == "FLEX":
            assert s.position in ("RB", "WR", "TE")
        else:
            assert s.position == s.slot
    assert lineup.total_salary <= SALARY_CAP


def test_infeasible_pool_returns_none() -> None:
    pool = _pool()[_pool()["position"] != "TE"]  # no TE anywhere → no legal lineup
    assert build_lineup(pool) is None


def test_stack_callout_names_qb_and_mates() -> None:
    lineup = build_lineup(_pool())
    assert lineup is not None
    # QB A (AAA) rosters with AAA teammates in the optimal lineup.
    if any(s.position == "QB" and s.team == "AAA" for s in lineup.slots):
        assert any(stack.startswith("AAA:") for stack in lineup.stacks())


def _cfb_pool() -> pd.DataFrame:
    """A small CFB board (no TE, no DST) with a strong second QB.

    QB B outscores every RB/WR flex option per dollar, so the true optimum
    plays two QBs — the Super-FLEX case a fixed-shape optimizer misses.
    """
    rows = [
        ("QB A", "QB", "UGA", 9000, 26.0),
        ("QB B", "QB", "ALA", 7500, 24.0),
        ("QB C", "QB", "TEX", 6000, 18.0),
        ("RB A", "RB", "UGA", 7800, 19.0),
        ("RB B", "RB", "ALA", 6200, 15.0),
        ("RB C", "RB", "OSU", 4800, 11.0),
        ("RB D", "RB", "TEX", 4000, 8.5),
        ("WR A", "WR", "UGA", 8200, 18.5),
        ("WR B", "WR", "ALA", 6600, 14.5),
        ("WR C", "WR", "OSU", 5200, 11.5),
        ("WR D", "WR", "TEX", 4200, 9.0),
        ("WR E", "WR", "LSU", 3400, 6.5),
    ]
    return pd.DataFrame(rows, columns=["player_name", "position", "team", "salary", "points"])


def test_cfb_classic_matches_brute_force_and_plays_two_qbs() -> None:
    pool = _cfb_pool()
    lineup = build_lineup(pool, cap=SALARY_CAP, spec=CFB_CLASSIC)
    assert lineup is not None
    assert lineup.total_points == pytest.approx(
        _brute_force_best(pool, SALARY_CAP, CFB_CLASSIC)
    )
    assert tuple(s.slot for s in lineup.slots) == CFB_CLASSIC.slots
    names = [s.player_name for s in lineup.slots]
    assert len(names) == len(set(names))
    assert lineup.total_salary <= SALARY_CAP
    for s in lineup.slots:
        if s.slot == "FLEX":
            assert s.position in ("RB", "WR")
        elif s.slot == "S-FLEX":
            assert s.position in ("QB", "RB", "WR")
        else:
            assert s.position == s.slot
    # The Super-FLEX earns its keep: two QBs in the optimal build.
    assert sum(1 for s in lineup.slots if s.position == "QB") == 2


def test_cfb_classic_never_rosters_a_te() -> None:
    pool = pd.concat([
        _cfb_pool(),
        pd.DataFrame([("TE X", "TE", "UGA", 100, 99.0)],
                     columns=["player_name", "position", "team", "salary", "points"]),
    ], ignore_index=True)
    lineup = build_lineup(pool, spec=CFB_CLASSIC)
    assert lineup is not None
    # DK college has no TE slot and no TE-eligible flex; the bargain TE is
    # ignored even at 99 projected points.
    assert all(s.position != "TE" for s in lineup.slots)


def test_lineup_pool_join_rules() -> None:
    salaries = pd.DataFrame([
        {"player_name": "Josh Allen", "position": "QB", "salary": 8200, "team": "BUF"},
        {"player_name": "Unknown Guy", "position": "WR", "salary": 3000, "team": "KC"},
        {"player_name": "Bills", "position": "DST", "salary": 3200, "team": "BUF"},
    ])
    points = pd.DataFrame([{"player_name": "josh  ALLEN", "points": 24.7}])
    pool = lineup_pool(salaries, points)
    by_name = pool.set_index("player_name")
    assert by_name.loc["Josh Allen", "points"] == pytest.approx(24.7)  # name-normalized
    assert "Unknown Guy" not in by_name.index  # unprojected skill player dropped
    assert by_name.loc["Bills", "points"] == 0.0  # projectionless DST is a legal punt
