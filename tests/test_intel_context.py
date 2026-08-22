"""Context assembly: stats, form, injuries, and point-in-time discipline."""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.intel.context import ContextLibrary, recent_scoring


def _games() -> pd.DataFrame:
    return pd.DataFrame(
        [
            # week 1
            {"game_id": "g1", "season": 2025, "week": 1,
             "kickoff": pd.Timestamp("2025-09-07 17:00"),
             "home_team": "BBB", "away_team": "AAA",
             "home_score": 27.0, "away_score": 20.0},
            {"game_id": "g2", "season": 2025, "week": 1,
             "kickoff": pd.Timestamp("2025-09-07 20:00"),
             "home_team": "DDD", "away_team": "CCC",
             "home_score": 14.0, "away_score": 21.0},
            # week 2
            {"game_id": "g3", "season": 2025, "week": 2,
             "kickoff": pd.Timestamp("2025-09-14 17:00"),
             "home_team": "AAA", "away_team": "CCC",
             "home_score": 30.0, "away_score": 10.0},
            {"game_id": "g4", "season": 2025, "week": 2,
             "kickoff": pd.Timestamp("2025-09-14 20:00"),
             "home_team": "BBB", "away_team": "DDD",
             "home_score": 24.0, "away_score": 17.0},
            # upcoming (no final): never enters any number
            {"game_id": "g5", "season": 2025, "week": 3,
             "kickoff": pd.Timestamp("2025-09-21 17:00"),
             "home_team": "AAA", "away_team": "BBB",
             "home_score": None, "away_score": None},
        ]
    )


def _plays() -> pd.DataFrame:
    rows = [
        # g1: AAA on offense vs BBB
        ("g1", "AAA", "BBB", "pass", 0.2),
        ("g1", "AAA", "BBB", "run", 0.0),
        # g1: BBB on offense vs AAA
        ("g1", "BBB", "AAA", "pass", -0.1),
        ("g1", "BBB", "AAA", "run", 0.1),
        # g3: AAA on offense vs CCC
        ("g3", "AAA", "CCC", "pass", 0.4),
    ]
    return pd.DataFrame(
        [
            {"game_id": gid, "season": 2025, "posteam": pos, "defteam": deft,
             "play_type": kind, "epa": epa}
            for gid, pos, deft, kind, epa in rows
        ]
    )


def _injuries() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"player_id": "1", "player_name": "Al Smith", "team": "AAA",
             "position": "QB", "status": "Out", "is_out": True},
            {"player_id": "2", "player_name": "Bob Jones", "team": "AAA",
             "position": "WR", "status": "Questionable", "is_out": False},
            {"player_id": "3", "player_name": "Cy West", "team": "BBB",
             "position": "CB", "status": "IR", "is_out": True},
        ]
    )


def test_season_scoring_and_epa_units() -> None:
    lib = ContextLibrary.build(_games(), _plays())
    assert lib.season == 2025
    ctx = lib.team_context("AAA")
    # AAA: 20 & 30 for, 27 & 10 against.
    assert ctx.ppg == pytest.approx(25.0)
    assert ctx.papg == pytest.approx(18.5)
    # AAA offense: epa mean of (0.2, 0.0, 0.4); defense allowed: (-0.1, 0.1).
    assert ctx.off_epa == pytest.approx(0.2)
    assert ctx.def_epa == pytest.approx(0.0)
    assert ctx.pass_off == pytest.approx(0.3)
    assert ctx.rush_off == pytest.approx(0.0)


def test_recent_scoring_takes_each_teams_last_n() -> None:
    completed = _games().dropna(subset=["home_score"])
    recent = recent_scoring(completed[completed["season"] == 2025], n=1)
    # AAA's last completed game is g3: 30 for, 10 against.
    assert recent.loc["AAA", "recent_ppg"] == pytest.approx(30.0)
    assert recent.loc["AAA", "recent_papg"] == pytest.approx(10.0)
    # DDD's last is g4: 17 for, 24 against.
    assert recent.loc["DDD", "recent_ppg"] == pytest.approx(17.0)


def test_as_of_excludes_later_games_and_their_plays() -> None:
    lib = ContextLibrary.build(
        _games(), _plays(), as_of=pd.Timestamp("2025-09-10")
    )
    ctx = lib.team_context("AAA")
    # Only week 1 exists: 20 for, 27 against; the g3 pass play (0.4) is unseen.
    assert ctx.ppg == pytest.approx(20.0)
    assert ctx.papg == pytest.approx(27.0)
    assert ctx.off_epa == pytest.approx(0.1)
    assert ctx.pass_off == pytest.approx(0.2)


def test_injury_outs_keep_only_genuine_outs() -> None:
    lib = ContextLibrary.build(_games(), injuries=_injuries())
    outs = lib.outs_for("AAA")
    assert [o.player_name for o in outs] == ["Al Smith"]  # questionable dropped
    assert outs[0].position == "QB"
    assert lib.outs_for("BBB")[0].status == "IR"
    assert lib.outs_for("CCC") == ()


def test_context_for_assembles_both_teams_with_rest() -> None:
    lib = ContextLibrary.build(_games(), _plays(), injuries=_injuries())
    kickoff = pd.Timestamp("2025-09-21 17:00")
    ctx = lib.context_for("g5", "AAA", "BBB", kickoff)
    assert ctx.game_id == "g5"
    assert ctx.away.team == "AAA" and ctx.home.team == "BBB"
    # Both teams last played 2025-09-14; BBB's game kicked at 20:00, so its
    # 17:00 kickoff a week later is 6 *full* days — exactly team_form's count.
    assert ctx.away.rest_days == 7
    assert ctx.home.rest_days == 6
    assert ctx.away.last5 == ("L", "W")
    assert ctx.home.streak == "W2"
    assert [o.player_name for o in ctx.away.outs] == ["Al Smith"]


def test_dispersion_matches_cross_team_std() -> None:
    lib = ContextLibrary.build(_games(), _plays())
    expected = float(lib.scoring["ppg"].std())
    assert lib.dispersion["ppg"] == pytest.approx(expected)
    assert lib.baseline["ppg"] == pytest.approx(float(lib.scoring["ppg"].mean()))
    # z-scoring round-trips: one std above the mean reads exactly +1.
    ctx = lib.context_for("g5", "AAA", "BBB")
    assert ctx.zscore("ppg", lib.baseline["ppg"] + expected) == pytest.approx(1.0)


def test_missing_data_yields_none_not_zero() -> None:
    lib = ContextLibrary.build(_games())  # no plays, no injuries
    ctx = lib.team_context("AAA")
    assert ctx.off_epa is None and ctx.pass_def is None
    assert ctx.outs == ()
    unknown = lib.team_context("ZZZ")
    assert unknown.ppg is None and unknown.recent_ppg is None


def test_injuries_for_week_slices_history_and_passes_snapshots_through() -> None:
    from velocity.intel.context import injuries_for_week

    history = pd.DataFrame([
        {"season": 2024, "week": 1, "player_name": "A", "team": "KC",
         "position": "WR", "status": "Out", "is_out": True},
        {"season": 2024, "week": 2, "player_name": "B", "team": "KC",
         "position": "QB", "status": "Doubtful", "is_out": True},
    ])
    week2 = injuries_for_week(history, 2024, 2)
    assert list(week2["player_name"]) == ["B"]
    assert injuries_for_week(history, 2024, 9).empty
    # A snapshot without season/week is already current: passes through.
    snapshot = history.drop(columns=["season", "week"])
    assert injuries_for_week(snapshot, 2024, 2) is snapshot
    assert injuries_for_week(None, 2024, 1) is None
