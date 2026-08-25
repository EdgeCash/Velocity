"""Showdown Captain Mode — exactness against brute force, and DK's two rules.

The optimizer is pinned the same way the classic one is: on small pools it
must match exhaustive enumeration exactly, including the captain choice. DK's
non-cap rules (a captain may not also fill a flex slot; a roster must span at
least two teams) are tested against pools deliberately built to tempt the
solver into breaking them.
"""

from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd
import pytest
from velocity.dfs.pipeline import showdown_slates, solve_showdown
from velocity.dfs.salaries import normalize_draftables
from velocity.dfs.showdown import (
    MLB_SHOWDOWN,
    NFL_SHOWDOWN,
    SALARY_CAP,
    build_showdown,
    showdown_board,
)


def _brute_force(pool: pd.DataFrame, cap: int = SALARY_CAP,
                 *, multiplier: float = 1.5) -> float | None:
    """The true optimum by exhaustive enumeration (small pools only)."""
    rows = pool.to_dict("records")
    teams = {r["team"] for r in rows if r["team"]}
    best: float | None = None
    for combo in combinations(range(len(rows)), 6):
        for captain in combo:
            flex = [i for i in combo if i != captain]
            salary = rows[captain]["captain_salary"] + sum(
                rows[i]["salary"] for i in flex)
            if salary > cap:
                continue
            if len(teams) >= 2 and len({rows[i]["team"] for i in combo}) < 2:
                continue
            points = multiplier * rows[captain]["points"] + sum(
                rows[i]["points"] for i in flex)
            best = points if best is None or points > best else best
    return best


def _pool(rows: list[tuple[str, str, str, int, float]]) -> pd.DataFrame:
    frame = pd.DataFrame(
        rows, columns=["player_name", "position", "team", "salary", "points"])
    return frame.assign(
        captain_salary=(frame["salary"] * 1.5).round().astype(int))


def test_matches_brute_force_across_random_pools() -> None:
    rng = np.random.default_rng(11)
    for _trial in range(25):
        n = int(rng.integers(8, 14))
        salary = (rng.integers(30, 120, n) * 100).astype(int)
        pool = pd.DataFrame({
            "player_name": [f"P{i}" for i in range(n)],
            "position": rng.choice(["SP", "OF", "1B"], n),
            "team": rng.choice(["AAA", "BBB"], n),
            "salary": salary,
            "captain_salary": (salary * 1.5).astype(int),
            "points": (rng.random(n) * 20).round(2),
        })
        lineup = build_showdown(pool, spec=MLB_SHOWDOWN)
        expected = _brute_force(pool)
        if expected is None:
            assert lineup is None
            continue
        assert lineup is not None
        # abs=0.02: slot points are rounded to cents for display, and the
        # captain's 1.5x lands on half-cents — a presentation gap, not a
        # different roster.
        assert lineup.total_points == pytest.approx(expected, abs=0.02)


def test_roster_is_always_legal() -> None:
    rng = np.random.default_rng(4)
    for _trial in range(20):
        n = int(rng.integers(12, 30))
        salary = (rng.integers(30, 150, n) * 100).astype(int)
        pool = pd.DataFrame({
            "player_name": [f"P{i}" for i in range(n)],
            "position": rng.choice(["QB", "RB", "WR", "TE", "K", "DST"], n),
            "team": rng.choice(["AAA", "BBB"], n),
            "salary": salary,
            "captain_salary": (salary * 1.5).astype(int),
            "points": (rng.random(n) * 25).round(2),
        })
        lineup = build_showdown(pool, spec=NFL_SHOWDOWN)
        assert lineup is not None
        assert [s.slot for s in lineup.slots] == ["CPT"] + ["FLEX"] * 5
        assert len({s.player_name for s in lineup.slots}) == 6
        assert lineup.total_salary <= SALARY_CAP
        assert len({s.team for s in lineup.slots}) >= 2


def test_captain_never_also_fills_a_flex_slot() -> None:
    # The best five are cheap and excellent, so the greedy read of the
    # frontier would hand the captaincy to a player already inside it.
    pool = _pool([
        ("Star", "OF", "AAA", 6000, 30.0),
        ("Two", "OF", "AAA", 5000, 20.0),
        ("Three", "OF", "AAA", 5000, 19.0),
        ("Four", "OF", "BBB", 5000, 18.0),
        ("Five", "OF", "BBB", 5000, 17.0),
        ("Six", "OF", "BBB", 5000, 16.0),
        ("Seven", "OF", "BBB", 4000, 12.0),
    ])
    lineup = build_showdown(pool, spec=MLB_SHOWDOWN)
    assert lineup is not None
    assert len({s.player_name for s in lineup.slots}) == 6
    assert lineup.total_points == pytest.approx(_brute_force(pool), abs=0.02)


def test_two_team_rule_is_enforced_even_when_one_side_is_better() -> None:
    # Six AAA players dominate every BBB player: the unconstrained optimum is
    # an illegal one-team roster, so the solver must give up points instead.
    rows = [(f"A{i}", "OF", "AAA", 5000, 20.0 - i) for i in range(7)]
    rows += [(f"B{i}", "OF", "BBB", 3000, 5.0 - i) for i in range(3)]
    pool = _pool(rows)
    lineup = build_showdown(pool, spec=MLB_SHOWDOWN)
    assert lineup is not None
    assert {s.team for s in lineup.slots} == {"AAA", "BBB"}
    assert lineup.total_points == pytest.approx(_brute_force(pool), abs=0.02)


def test_one_team_pool_still_solves() -> None:
    # A pool DK would never serve, but the rule must not deadlock the solver.
    pool = _pool([(f"A{i}", "OF", "AAA", 4000, 10.0 - i) for i in range(8)])
    lineup = build_showdown(pool, spec=MLB_SHOWDOWN)
    assert lineup is not None
    assert len(lineup.slots) == 6


def test_captain_carries_the_multiplier_on_points_and_dk_salary() -> None:
    pool = _pool([
        ("Ace", "SP", "AAA", 10000, 24.0),
        ("Two", "OF", "AAA", 4000, 10.0),
        ("Three", "OF", "AAA", 4000, 9.0),
        ("Four", "OF", "BBB", 4000, 8.0),
        ("Five", "OF", "BBB", 4000, 7.0),
        ("Six", "OF", "BBB", 4000, 6.0),
    ])
    lineup = build_showdown(pool, spec=MLB_SHOWDOWN)
    assert lineup is not None
    captain = lineup.slots[0]
    assert captain.slot == "CPT"
    assert captain.player_name == "Ace"
    assert captain.salary == 15_000  # DK's own captain price, 1.5x
    assert captain.points == pytest.approx(36.0)  # 1.5 x 24.0
    assert lineup.total_salary == 15_000 + 4_000 * 5


def _draftables() -> dict:
    """A DK-shaped showdown payload: every player priced twice."""
    players = [
        ("1", "Ace", "SP", "AAA", 12000),
        ("2", "Bat", "OF", "AAA", 6000),
        ("3", "Cat", "OF", "AAA", 5000),
        ("4", "Dog", "OF", "BBB", 5000),
        ("5", "Eel", "OF", "BBB", 4000),
        ("6", "Fox", "OF", "BBB", 3000),
    ]
    rows = []
    for pid, name, position, team, salary in players:
        for slot_id, price in (("573", int(salary * 1.5)), ("574", salary)):
            rows.append({
                "playerDkId": pid, "displayName": name, "position": position,
                "teamAbbreviation": team, "salary": price,
                "rosterSlotId": slot_id,
                "competition": {"name": "AAA @ BBB",
                                "startTime": "2026-08-25T23:15:00.0000000Z"},
                "playerGameAttributes": [{"id": 1, "value": "true"}]
                if position == "SP" else [],
            })
    return {"draftables": rows}


def test_showdown_board_collapses_the_doubled_dk_listing() -> None:
    salaries = normalize_draftables(_draftables(), "999")
    assert len(salaries) == 12  # DK's doubled board survives normalization
    board = showdown_board(salaries)
    assert len(board) == 6
    ace = board[board["player_name"] == "Ace"].iloc[0]
    assert ace["salary"] == 12_000
    assert ace["captain_salary"] == 18_000


def test_showdown_slates_reads_dks_own_format_label() -> None:
    salaries = normalize_draftables(_draftables(), "999").assign(
        game_type="Showdown Captain Mode", contest_type_id=114,
        slate_start=pd.Timestamp("2026-08-25 23:15:00"))
    other = normalize_draftables(_draftables(), "1000").assign(
        game_type="Madden Showdown Captain Mode", contest_type_id=159,
        slate_start=pd.Timestamp("2026-08-25 23:15:00"))
    slates = showdown_slates(pd.concat([salaries, other], ignore_index=True), "mlb")
    # The simulated look-alike is not a real showdown board.
    assert [s.draft_group_id for s in slates] == ["999"]
    assert slates[0].suffix == "AAA @ BBB"


def test_solve_showdown_end_to_end() -> None:
    salaries = normalize_draftables(_draftables(), "999")
    points = pd.DataFrame({
        "player_id": list("123456"),
        "player_name": ["Ace", "Bat", "Cat", "Dog", "Eel", "Fox"],
        "team": ["AAA"] * 3 + ["BBB"] * 3,
        "position": ["SP"] + ["OF"] * 5,
        "points": [24.0, 12.0, 10.0, 9.0, 8.0, 7.0],
    })
    run = solve_showdown(salaries, pd.DataFrame(), draft_group="999",
                         league="mlb", points=points)
    assert run.lineup is not None
    assert run.n_games == 1
    assert [s.slot for s in run.lineup.slots] == ["CPT"] + ["UTIL"] * 5
    assert run.lineup.total_salary <= SALARY_CAP
