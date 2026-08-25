"""DK's salary-free formats — tier ordering, and the two shape rules exactly.

Tiers and Single Stat have no cap to search, so the tests are about the
things that are easy to get subtly wrong: reading DK's tier order off its
roster-slot ids, and applying the "at least two games" / "at least two
teams" rules as the exact optimum rather than as a nudge.
"""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.dfs.tiered import (
    CFB_SINGLE_STAT_TD,
    MLB_SINGLE_STAT_HR,
    MLB_TIERS,
    build_tier_entry,
    normalize_tiered,
    tier_frame,
)


def _payload(slots: list[int]) -> dict:
    return {
        "draftables": [
            {
                "playerDkId": i, "displayName": f"P{i}", "position": "OF",
                "teamAbbreviation": "AAA", "salary": None, "rosterSlotId": slot,
                "competition": {"name": "AAA @ BBB",
                                "startTime": "2026-08-25T23:15:00.0000000Z"},
                "draftStatAttributes": [{"id": 408, "value": "10.5"}],
                "playerGameAttributes": [],
            }
            for i, slot in enumerate(slots)
        ]
    }


def test_normalize_tiered_reads_the_tier_off_dks_slot_order() -> None:
    board = normalize_tiered(_payload([283, 278, 281, 278]), "999")
    assert len(board) == 4
    # Slot ids ascend with the tier: 278 is T1, 281 T2, 283 T3 on this board.
    assert dict(zip(board["player_name"], board["tier"], strict=True)) == {
        "P0": 3, "P1": 1, "P2": 2, "P3": 1}
    assert board["dk_stat"].tolist() == [10.5] * 4
    assert board["kickoff"].iloc[0] == pd.Timestamp("2026-08-25 23:15:00")


def test_normalize_tiered_single_slot_board_is_one_pool() -> None:
    board = normalize_tiered(_payload([671] * 5), "999")
    assert set(board["tier"]) == {1}


def test_normalize_tiered_empty_payload() -> None:
    assert normalize_tiered({}, "999").empty


def _pool(rows: list[tuple[str, int, str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["player_name", "tier", "team", "competition", "points"]
    ).assign(position="OF", kickoff=pd.NaT)


def test_tiers_takes_the_best_of_each_tier() -> None:
    pool = _pool([
        ("A1", 1, "AAA", "G1", 12.0), ("A2", 1, "BBB", "G2", 9.0),
        ("B1", 2, "CCC", "G3", 11.0), ("B2", 2, "AAA", "G1", 10.0),
        ("C1", 3, "DDD", "G4", 8.0), ("C2", 3, "BBB", "G2", 7.0),
        ("D1", 4, "EEE", "G5", 6.0), ("E1", 5, "FFF", "G6", 5.0),
        ("F1", 6, "GGG", "G7", 20.0), ("F2", 6, "HHH", "G8", 19.0),
    ])
    entry = build_tier_entry(pool, spec=MLB_TIERS)
    assert entry is not None
    assert [p.player_name for p in entry.picks] == ["A1", "B1", "C1", "D1", "E1", "F1"]
    assert [p.slot for p in entry.picks] == ["T1", "T2", "T3", "T4", "T5", "T6"]
    assert entry.total_points == pytest.approx(62.0)


def test_tiers_two_game_rule_moves_exactly_one_tier_at_the_smallest_loss() -> None:
    # Every tier's best sits in G1. The cheapest legal move is tier 3, where
    # the alternative costs 0.5 instead of 4.0 or 2.0.
    pool = _pool([
        ("A1", 1, "AAA", "G1", 12.0), ("A2", 1, "CCC", "G2", 8.0),
        ("B1", 2, "AAA", "G1", 11.0), ("B2", 2, "CCC", "G2", 9.0),
        ("C1", 3, "AAA", "G1", 10.0), ("C2", 3, "CCC", "G2", 9.5),
        ("D1", 4, "BBB", "G1", 9.0), ("E1", 5, "BBB", "G1", 8.0),
        ("F1", 6, "BBB", "G1", 20.0),
    ])
    entry = build_tier_entry(pool, spec=MLB_TIERS)
    assert entry is not None
    assert [p.player_name for p in entry.picks] == [
        "A1", "B1", "C2", "D1", "E1", "F1"]
    assert len({p.player_name for p in entry.picks}) == 6
    assert entry.total_points == pytest.approx(69.5)


def test_tiers_infeasible_when_a_tier_is_missing() -> None:
    pool = _pool([("A1", 1, "AAA", "G1", 5.0), ("B1", 2, "BBB", "G2", 4.0)])
    assert build_tier_entry(pool, spec=MLB_TIERS) is None


def test_single_stat_takes_the_top_three() -> None:
    pool = _pool([
        ("A", 1, "AAA", "G1", 0.30), ("B", 1, "BBB", "G2", 0.28),
        ("C", 1, "CCC", "G3", 0.26), ("D", 1, "DDD", "G4", 0.24),
    ])
    entry = build_tier_entry(pool, spec=MLB_SINGLE_STAT_HR)
    assert entry is not None
    assert [p.player_name for p in entry.picks] == ["A", "B", "C"]
    assert [p.slot for p in entry.picks] == ["UTIL"] * 3


def test_single_stat_two_team_rule_keeps_the_top_two() -> None:
    # The top three all play for AAA, so every other player ranks below all
    # three: the exact optimum is the top two plus the best outsider.
    pool = _pool([
        ("A", 1, "AAA", "G1", 0.30), ("B", 1, "AAA", "G1", 0.28),
        ("C", 1, "AAA", "G1", 0.26), ("D", 1, "BBB", "G1", 0.20),
        ("E", 1, "CCC", "G2", 0.19),
    ])
    entry = build_tier_entry(pool, spec=MLB_SINGLE_STAT_HR)
    assert entry is not None
    assert [p.player_name for p in entry.picks] == ["A", "B", "D"]
    assert len({p.team for p in entry.picks}) == 2
    assert entry.total_points == pytest.approx(0.78)


def test_single_stat_one_team_pool_is_infeasible_not_illegal() -> None:
    pool = _pool([(n, 1, "AAA", "G1", 0.2) for n in ("A", "B", "C", "D")])
    assert build_tier_entry(pool, spec=CFB_SINGLE_STAT_TD) is None


def test_duplicate_names_keep_the_better_row() -> None:
    pool = _pool([
        ("A", 1, "AAA", "G1", 0.10), ("A", 1, "AAA", "G1", 0.30),
        ("B", 1, "BBB", "G2", 0.20), ("C", 1, "CCC", "G3", 0.15),
    ])
    entry = build_tier_entry(pool, spec=MLB_SINGLE_STAT_HR)
    assert entry is not None
    assert [p.player_name for p in entry.picks] == ["A", "B", "C"]
    assert entry.picks[0].points == pytest.approx(0.30)


def test_tier_frame_is_persistable() -> None:
    pool = _pool([
        ("A", 1, "AAA", "G1", 0.30), ("B", 1, "BBB", "G2", 0.28),
        ("C", 1, "CCC", "G3", 0.26),
    ])
    entry = build_tier_entry(pool, spec=MLB_SINGLE_STAT_HR)
    assert entry is not None
    frame = tier_frame(entry, "999")
    assert list(frame["player_name"]) == ["A", "B", "C"]
    assert set(frame["draft_group_id"]) == {"999"}
    assert set(frame["format"]) == {"mlb_single_stat_hr"}


def test_football_touchdowns_counts_only_scores() -> None:
    """A thrown touchdown is scored by the receiver, not the quarterback."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from build_dfs_tiered import _football_touchdowns

    fp = pd.DataFrame(
        [
            ("qb1", "Passer", "AAA", "QB", "pass_tds", 2.4),
            ("qb1", "Passer", "AAA", "QB", "rush_tds", 0.3),
            ("wr1", "Catcher", "AAA", "WR", "rec_tds", 0.7),
            ("rb1", "Runner", "BBB", "RB", "rush_tds", 0.6),
            ("rb1", "Runner", "BBB", "RB", "rec_yds", 22.0),
        ],
        columns=["player_id", "player_name", "team", "position", "stat", "value"],
    )
    out = _football_touchdowns(fp).set_index("player_name")["points"]
    assert out["Passer"] == pytest.approx(0.3)  # his own rushing scores only
    assert out["Catcher"] == pytest.approx(0.7)
    assert out["Runner"] == pytest.approx(0.6)  # receiving YARDS score nothing
