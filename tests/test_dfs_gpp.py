"""GPP portfolio — stacks, overlap/exposure caps, tail scoring, determinism."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.dfs.gpp import (
    GppConfig,
    build_gpp_portfolio,
    opponent_map,
    portfolio_frame,
    stack_ok,
    tail_score,
)
from velocity.dfs.optimizer import Lineup, LineupSlot, build_lineup
from velocity.util.seed import make_rng


def _pool() -> pd.DataFrame:
    """Two games, enough depth at every position for many legal lineups."""
    rows = []

    def add(name: str, pos: str, team: str, comp: str, salary: int, pts: float) -> None:
        rows.append({"player_name": name, "position": pos, "team": team,
                     "competition": comp, "salary": salary, "points": pts})

    g1, g2 = "BUF @ KC", "DET @ CHI"
    add("QB A", "QB", "KC", g1, 8000, 22.0)
    add("QB B", "QB", "BUF", g1, 7800, 21.0)
    add("QB C", "QB", "DET", g2, 7000, 19.0)
    for i, (team, comp) in enumerate([("KC", g1), ("BUF", g1), ("DET", g2), ("CHI", g2)]):
        for j in range(3):
            add(f"RB {team}{j}", "RB", team, comp, 5200 + 100 * i + 50 * j, 12.0 + j)
            add(f"WR {team}{j}", "WR", team, comp, 5600 + 100 * i + 50 * j, 13.0 + j)
        add(f"TE {team}", "TE", team, comp, 3800 + 100 * i, 8.0 + i)
        add(f"DST {team}", "DST", team, comp, 2800 + 50 * i, 6.0)
    return pd.DataFrame(rows)


def _slot(name: str, pos: str, team: str | None, pts: float = 10.0) -> LineupSlot:
    return LineupSlot(slot=pos, player_name=name, position=pos, team=team,
                      salary=5000, points=pts)


def test_opponent_map_pairs_teams_by_competition() -> None:
    opponents = opponent_map(_pool())
    assert opponents["KC"] == "BUF" and opponents["BUF"] == "KC"
    assert opponents["DET"] == "CHI"


def test_stack_ok_requires_teammates_and_bring_back() -> None:
    config = GppConfig(stack_teammates=2, bring_backs=1)
    opponents = {"KC": "BUF", "BUF": "KC"}
    stacked = Lineup(slots=(
        _slot("QB A", "QB", "KC"), _slot("WR KC0", "WR", "KC"),
        _slot("TE KC", "TE", "KC"), _slot("WR BUF0", "WR", "BUF"),
    ), total_salary=20000, total_points=50.0)
    assert stack_ok(stacked, opponents, config)
    naked = Lineup(slots=(
        _slot("QB A", "QB", "KC"), _slot("WR BUF0", "WR", "BUF"),
        _slot("WR DET0", "WR", "DET"), _slot("WR CHI0", "WR", "CHI"),
    ), total_salary=20000, total_points=50.0)
    assert not stack_ok(naked, opponents, config)  # no KC teammates
    no_back = Lineup(slots=(
        _slot("QB A", "QB", "KC"), _slot("WR KC0", "WR", "KC"),
        _slot("TE KC", "TE", "KC"), _slot("WR DET0", "WR", "DET"),
    ), total_salary=20000, total_points=50.0)
    assert not stack_ok(no_back, opponents, config)  # no BUF bring-back
    # Unknown opponent: only the teammate half of the rule can apply.
    assert stack_ok(no_back, {}, config)


def test_tail_score_reads_the_correlated_tail() -> None:
    lineup = Lineup(slots=(
        _slot("A", "WR", "KC", pts=10.0), _slot("B", "WR", "KC", pts=10.0),
    ), total_salary=10000, total_points=20.0)
    # Perfectly correlated boom sims: totals are [10, 20, 60, 70].
    samples = {"A": np.array([5.0, 10.0, 30.0, 35.0]),
               "B": np.array([5.0, 10.0, 30.0, 35.0])}
    score = tail_score(lineup, samples, tail_q=0.75)
    assert score == pytest.approx(70.0)  # the top quartile is the 70 world
    # A player with no samples contributes his constant projection.
    partial = tail_score(lineup, {"A": samples["A"]}, tail_q=0.75)
    assert partial == pytest.approx(35.0 + 10.0)


def test_portfolio_respects_overlap_exposure_and_stack() -> None:
    config = GppConfig(n_lineups=4, candidate_factor=20, max_overlap=7,
                       max_exposure=0.75, salary_leave=0, jitter=0.10)
    portfolio = build_gpp_portfolio(_pool(), config=config, rng=make_rng())
    assert portfolio.lineups, "the pool supports many legal lineups"
    opponents = opponent_map(_pool())
    rosters = [frozenset(s.player_name for s in lu.slots) for lu in portfolio.lineups]
    for lineup in portfolio.lineups:
        assert stack_ok(lineup, opponents, config)
    for i, a in enumerate(rosters):
        for b in rosters[i + 1:]:
            assert len(a & b) <= config.max_overlap
    max_count = int(np.floor(config.max_exposure * config.n_lineups))
    counts: dict[str, int] = {}
    for roster in rosters:
        for name in roster:
            counts[name] = counts.get(name, 0) + 1
    assert max(counts.values()) <= max_count
    # True projections, not the jittered ones, are reported.
    by_name = dict(zip(_pool()["player_name"], _pool()["points"], strict=False))
    for lineup in portfolio.lineups:
        for slot in lineup.slots:
            assert slot.points == pytest.approx(by_name[slot.player_name])


def test_portfolio_is_deterministic_under_a_seed() -> None:
    config = GppConfig(n_lineups=3, candidate_factor=10)
    a = build_gpp_portfolio(_pool(), config=config, rng=make_rng())
    b = build_gpp_portfolio(_pool(), config=config, rng=make_rng())
    assert [lu.slots for lu in a.lineups] == [lu.slots for lu in b.lineups]
    assert a.scores == b.scores


def test_salary_leave_caps_the_effective_spend() -> None:
    config = GppConfig(n_lineups=2, candidate_factor=10, salary_leave=1000)
    portfolio = build_gpp_portfolio(_pool(), config=config, rng=make_rng())
    for lineup in portfolio.lineups:
        assert lineup.total_salary <= 49_000


def test_portfolio_frame_carries_the_card_columns() -> None:
    config = GppConfig(n_lineups=2, candidate_factor=10)
    portfolio = build_gpp_portfolio(_pool(), config=config, rng=make_rng())
    frame = portfolio_frame(portfolio)
    assert list(frame.columns) == [
        "rank", "players", "total_salary", "total_points", "score", "stacks",
    ]
    assert len(frame) == len(portfolio.lineups)
    if not frame.empty:
        assert frame.loc[0, "rank"] == 1
        assert "QB" in frame.loc[0, "players"]


def test_empty_pool_yields_empty_portfolio() -> None:
    empty = build_gpp_portfolio(
        pd.DataFrame(columns=["player_name", "position", "salary", "points"]),
        config=GppConfig(n_lineups=2), rng=make_rng(),
    )
    assert empty.lineups == ()
    assert portfolio_frame(empty).empty


def test_cash_lineup_is_reachable_when_stacking_disabled() -> None:
    # Sanity: with the stack rule off and zero jitter, the first candidate is
    # the cash-optimal lineup at the effective cap.
    config = GppConfig(n_lineups=1, candidate_factor=1, jitter=0.0,
                       require_stack=False, salary_leave=0)
    portfolio = build_gpp_portfolio(_pool(), config=config, rng=make_rng())
    cash = build_lineup(_pool())
    assert cash is not None and portfolio.lineups
    assert portfolio.lineups[0].total_points == pytest.approx(cash.total_points)


def _mlb_pool() -> pd.DataFrame:
    """Four clubs, enough depth at every DK classic slot for many lineups."""
    rows = []
    games = {"CIN": "CIN @ STL", "STL": "CIN @ STL",
             "KC": "KC @ CLE", "CLE": "KC @ CLE"}
    for team, comp in games.items():
        for j in range(2):
            rows.append({"player_name": f"P {team}{j}", "position": "P",
                         "team": team, "competition": comp,
                         "salary": 8000 + 200 * j, "points": 16.0 + j})
        for position in ("C", "1B", "2B", "3B", "SS"):
            for j in range(2):
                rows.append({"player_name": f"{position} {team}{j}",
                             "position": position, "team": team,
                             "competition": comp, "salary": 3600 + 100 * j,
                             "points": 8.0 + j})
        for j in range(4):
            rows.append({"player_name": f"OF {team}{j}", "position": "OF",
                         "team": team, "competition": comp,
                         "salary": 3500 + 100 * j, "points": 8.0 + j * 0.5})
    return pd.DataFrame(rows)


def test_mlb_stack_reads_the_batting_order_not_a_quarterback() -> None:
    """Baseball has no QB to anchor on: the block is a run of the order."""
    from velocity.dfs.gpp import mlb_stack_ok, team_hitter_counts

    stacked = Lineup((
        _slot("P1", "P", "CLE"), _slot("P2", "P", "KC"),
        _slot("C1", "C", "CIN"), _slot("1B", "1B", "CIN"),
        _slot("2B", "2B", "CIN"), _slot("SS", "SS", "CIN"),
        _slot("3B", "3B", "STL"), _slot("OF1", "OF", "STL"),
        _slot("OF2", "OF", "TOR"), _slot("OF3", "OF", "SF"),
    ), 50_000, 100.0)
    # Pitchers never count toward a stack — they are on the other side of it.
    assert team_hitter_counts(stacked) == [4, 2, 1, 1]
    assert mlb_stack_ok(stacked, GppConfig(mlb_stack=4, mlb_secondary=2))
    assert not mlb_stack_ok(stacked, GppConfig(mlb_stack=5, mlb_secondary=0))
    assert not mlb_stack_ok(stacked, GppConfig(mlb_stack=4, mlb_secondary=3))
    # And the sport is detected from the roster: this one has P slots, no QB.
    assert stack_ok(stacked, {}, GppConfig(mlb_stack=4, mlb_secondary=2))
    assert not stack_ok(stacked, {}, GppConfig(mlb_stack=5, mlb_secondary=0))


def test_mlb_portfolio_stacks_and_stays_legal() -> None:
    from velocity.dfs.optimizer import MLB_CLASSIC

    portfolio = build_gpp_portfolio(
        _mlb_pool(), spec=MLB_CLASSIC, rng=make_rng(),
        config=GppConfig(n_lineups=6, candidate_factor=8, mlb_stack=4,
                         mlb_secondary=2, max_overlap=8, max_exposure=1.0))
    assert portfolio.lineups
    for lineup in portfolio.lineups:
        counts: dict[str, int] = {}
        for slot in lineup.slots:
            if slot.position != "P" and slot.team:
                counts[slot.team] = counts.get(slot.team, 0) + 1
        ordered = sorted(counts.values(), reverse=True)
        assert ordered[0] >= 4  # the primary block
        assert ordered[1] >= 2  # the mini-stack
        # DK's own rule: never more than five hitters from one club.
        assert ordered[0] <= 5
        assert lineup.total_salary <= 50_000
        assert len({s.player_name for s in lineup.slots}) == 10


def test_batting_order_stacks_read_out_for_the_card() -> None:
    lineup = Lineup((
        _slot("P1", "P", "CLE"), _slot("P2", "P", "PIT"),
        _slot("C1", "C", "CIN"), _slot("1B", "1B", "CIN"),
        _slot("2B", "2B", "CIN"), _slot("SS", "SS", "CIN"),
        _slot("3B", "3B", "STL"), _slot("OF1", "OF", "STL"),
        _slot("OF2", "OF", "TOR"), _slot("OF3", "OF", "SF"),
    ), 50_000, 100.0)
    # A lone bat is a player, not a stack, so TOR and SF do not appear.
    assert lineup.stacks() == ["CIN x4 + STL x2"]
