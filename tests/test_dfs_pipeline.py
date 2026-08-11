"""DFS pipeline + lineup card — slate picking, frame glue, and a render smoke."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from velocity.dfs.pipeline import lineup_frame, main_slate_group, solve_slate

# A two-group board: group 9 is a single-game showdown, group 1 the main slate.
_POOL_ROWS = [
    # name, pos, team, salary, points (points only used to build the FP frame)
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


def _salaries() -> pd.DataFrame:
    games = {"AAA": "AAA @ BBB", "BBB": "AAA @ BBB", "CCC": "CCC @ DDD",
             "DDD": "CCC @ DDD", "EEE": "EEE @ FFF"}
    rows = [
        {"draft_group_id": "1", "player_id": f"p{i}", "player_name": name,
         "position": pos, "salary": salary, "team": team,
         "competition": games[team]}
        for i, (name, pos, team, salary, _) in enumerate(_POOL_ROWS)
    ]
    rows.append({  # a showdown group spanning one game — never the main slate
        "draft_group_id": "9", "player_id": "p0", "player_name": "QB A",
        "position": "QB", "salary": 11000, "team": "AAA",
        "competition": "AAA @ BBB",
    })
    return pd.DataFrame(rows)


def _fp() -> pd.DataFrame:
    """Every skill player's points expressed as a single rec_yds stat (0.1/yd)."""
    rows = [
        {"player_id": f"p{i}", "player_name": name, "team": team, "position": pos,
         "stat": "rec_yds", "value": points * 10.0}
        for i, (name, pos, team, salary, points) in enumerate(_POOL_ROWS)
        if pos != "DST"  # defenses ride the projectionless-punt path
    ]
    return pd.DataFrame(rows)


def test_main_slate_group_prefers_the_most_games() -> None:
    assert main_slate_group(_salaries()) == "1"
    assert main_slate_group(_salaries().iloc[0:0]) is None


def test_solve_slate_builds_a_legal_lineup_from_frames() -> None:
    run = solve_slate(_salaries(), _fp())
    assert run.draft_group_id == "1"
    assert run.n_games == 3
    assert run.lineup is not None
    assert run.lineup.total_salary <= 50_000
    assert len(run.lineup.slots) == 9
    # DSTs joined at 0.0 (the legal punt), skill players at their FP points.
    dst = next(s for s in run.lineup.slots if s.slot == "DST")
    assert dst.points == 0.0

    frame = lineup_frame(run)
    assert len(frame) == 9
    assert list(frame["slot"]) == ["QB", "RB", "RB", "WR", "WR", "WR", "TE",
                                   "FLEX", "DST"]
    assert (frame["draft_group_id"] == "1").all()


def test_solve_slate_handles_an_unsolvable_board() -> None:
    salaries = _salaries()
    run = solve_slate(salaries[salaries["position"] != "TE"], _fp())
    assert run.lineup is None
    assert lineup_frame(run).empty


def test_render_dfs_card_writes_a_png(tmp_path: Path) -> None:
    from velocity.report.dfs_png import dfs_caption, render_dfs_card

    run = solve_slate(_salaries(), _fp())
    assert run.lineup is not None
    dest = tmp_path / "dfs_nfl_test.png"
    render_dfs_card(run.lineup, dest, when="THURSDAY, SEP 10",
                    slate_label="DK CLASSIC · 3 GAMES")
    assert dest.stat().st_size > 20_000  # a real rendered frame, not a stub

    copy = dfs_caption(run.lineup)
    assert f"${run.lineup.total_salary:,}" in copy
    assert "QB:" in copy
