"""Backtest joins — the places a DFS backtest quietly lies to itself.

Every test here pins a rule that, broken, produces a *better-looking* number:
a name that folds instead of matching, a box score borrowed from the wrong
game of a series, a lineup slot read out of the game being scored, or a
random field that is not actually legal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.dfs.backtest import (
    board_games,
    board_probables,
    norm,
    player_day_index,
    prepare_banks,
    random_rosters,
    realized,
    recent_slots,
)
from velocity.dfs.optimizer import LineupSlot


def _games() -> pd.DataFrame:
    return pd.DataFrame([
        {"game_id": "1", "kickoff": pd.Timestamp("2026-06-01 23:00"),
         "home_team": "Braves", "away_team": "Dodgers", "season": 2026,
         "home_score": 4, "away_score": 3},
        {"game_id": "2", "kickoff": pd.Timestamp("2026-06-02 23:00"),
         "home_team": "Braves", "away_team": "Dodgers", "season": 2026,
         "home_score": 1, "away_score": 7},
        {"game_id": "3", "kickoff": pd.Timestamp("2026-06-03 23:00"),
         "home_team": "Braves", "away_team": "Dodgers", "season": 2026,
         "home_score": 2, "away_score": 2, "home_score_final": None},
    ])


def _batters() -> pd.DataFrame:
    rows = []
    for game_id, hr, slot in (("1", 0, 2), ("2", 1, 4), ("3", 0, 4)):
        rows.append({"game_id": game_id, "team": "Dodgers", "side": "away",
                     "batter_id": "660271", "batter_name": "Julio Sánchez",
                     "lineup_slot": slot, "started": True, "pa": 4, "ab": 4,
                     "h": 1, "double": 0, "triple": 0, "hr": hr, "rbi": hr,
                     "r": hr, "bb": 0, "hbp": 0, "sb": 0})
    return pd.DataFrame(rows)


def _starters() -> pd.DataFrame:
    return pd.DataFrame([
        {"game_id": g, "team": "Braves", "side": "home", "starter_id": f"s{g}",
         "starter_name": f"Arm {g}", "outs": 18, "batters_faced": 24, "k": 6,
         "bb": 1, "hbp": 0, "hr": 0, "er": 2, "hits_allowed": 5, "win": 0}
        for g in ("1", "2", "3")
    ])


def test_norm_folds_accents_rather_than_deleting_them() -> None:
    # DK spells "Sanchez", statsapi spells "Sánchez": deleting the accented
    # letter would make the two names disagree and drop the player.
    assert norm("Julio Sánchez") == norm("Julio Sanchez") == "juliosanchez"
    assert norm("Ronald Acuña Jr.") == norm("Ronald Acuna Jr")


def test_prepare_banks_scores_dk_points_and_drops_unplayed_games() -> None:
    bat, sp, played = prepare_banks(_batters(), _starters(), _games())
    assert set(played["game_id"]) == {"1", "2", "3"}
    homer = bat[bat["game_id"] == "2"].iloc[0]
    # single 3 + hr 7 more + rbi 2 + run 2 = 14 (h scores at the single's rate,
    # the hr column tops up the difference).
    assert homer["actual"] == pytest.approx(14.0)
    assert sp.iloc[0]["actual"] == pytest.approx(6 * 2.25 + 6 * 2 - 2 * 2
                                                - 5 * 0.6 - 1 * 0.6)


def test_player_day_index_keys_on_the_date_not_just_the_name() -> None:
    # The trap: two clubs play a series with the same names every night, so a
    # name-only index hands back an arbitrary game of the series.
    bat, sp, _played = prepare_banks(_batters(), _starters(), _games())
    index = player_day_index(bat, sp)
    from datetime import date
    assert index[("juliosanchez", date(2026, 6, 1))]["game_id"] == "1"
    assert index[("juliosanchez", date(2026, 6, 2))]["game_id"] == "2"
    assert index[("juliosanchez", date(2026, 6, 2))]["actual"] == pytest.approx(14.0)
    assert index[("arm2", date(2026, 6, 2))]["is_pitcher"] is True


def test_recent_slots_never_reads_the_game_being_scored() -> None:
    bat, _sp, _played = prepare_banks(_batters(), _starters(), _games())
    # Before the 6/2 game he had hit second; his 6/2 slot (fourth) is exactly
    # the number a backtest must not know.
    assert recent_slots(bat, pd.Timestamp("2026-06-02 12:00")) == {"660271": 2}
    assert recent_slots(bat, pd.Timestamp("2026-06-03 12:00")) == {"660271": 4}
    assert recent_slots(bat, pd.Timestamp("2026-05-01")) == {}


def _board() -> pd.DataFrame:
    return pd.DataFrame([
        {"player_name": "Julio Sanchez", "team": "LAD", "position": "OF",
         "competition": "LAD @ ATL", "kickoff": pd.Timestamp("2026-06-02 23:00"),
         "probable": False},
        {"player_name": "Arm 2", "team": "ATL", "position": "SP",
         "competition": "LAD @ ATL", "kickoff": pd.Timestamp("2026-06-02 23:00"),
         "probable": True},
        {"player_name": "Someone Else", "team": "LAD", "position": "SP",
         "competition": "LAD @ ATL", "kickoff": pd.Timestamp("2026-06-02 23:00"),
         "probable": True},
    ])


def test_board_games_resolves_dks_matchup_string_to_the_right_game() -> None:
    bat, sp, _played = prepare_banks(_batters(), _starters(), _games())
    index = player_day_index(bat, sp)
    # DK says "LAD @ ATL" three nights running; the date decides which one.
    assert board_games(_board(), index) == {"LAD @ ATL": "2"}


def test_board_probables_reads_dks_own_flag() -> None:
    assert board_probables(_board()) == {"LAD @ ATL": ["Arm 2", "Someone Else"]}


def test_realized_applies_the_captain_multiplier() -> None:
    slots = [
        LineupSlot("CPT", "Star", "OF", "AAA", 15_000, 30.0),
        LineupSlot("UTIL", "Other", "OF", "BBB", 5_000, 10.0),
        LineupSlot("UTIL", "Ghost", "OF", "BBB", 3_000, 8.0),
    ]
    # "Ghost" never appeared, so he is absent from the actuals and scores 0.0
    # — exactly what DK pays a player who does not take the field.
    total = realized(slots, {"Star": 20.0, "Other": 6.0})
    assert total == pytest.approx(1.5 * 20.0 + 6.0)


def _pool() -> pd.DataFrame:
    rows = []
    for position, k in (("P", 4), ("C", 3), ("1B", 3), ("2B", 3), ("3B", 3),
                        ("SS", 3), ("OF", 6)):
        for i in range(k):
            rows.append({"player_name": f"{position}{i}", "position": position,
                         "team": f"T{i % 3}", "salary": 4000 + i * 100,
                         "actual": float(i)})
    return pd.DataFrame(rows).assign(captain_salary=lambda f: f["salary"] * 1.5)


def test_random_rosters_respects_the_position_quota_and_the_cap() -> None:
    rng = np.random.default_rng(3)
    scores = random_rosters(_pool(), rng, size=10, n=50,
                            groups=(("P", 2), ("C", 1), ("1B", 1), ("2B", 1),
                                    ("3B", 1), ("SS", 1), ("OF", 3)))
    assert len(scores) == 50
    # Ten players at $4,000-$4,200 always fit; the scores are real sums.
    assert scores.min() >= 0
    assert scores.max() <= _pool()["actual"].nlargest(10).sum()


def test_random_rosters_returns_empty_when_a_slot_cannot_be_filled() -> None:
    rng = np.random.default_rng(3)
    thin = _pool()[_pool()["position"] != "C"]
    scores = random_rosters(thin, rng, size=10, n=10,
                            groups=(("P", 2), ("C", 1), ("OF", 3)))
    assert len(scores) == 0


def test_random_rosters_enforces_the_two_team_rule_for_showdown() -> None:
    rng = np.random.default_rng(5)
    one_team = _pool().assign(team="AAA")
    assert len(random_rosters(one_team, rng, size=6, n=5, captain=True,
                              min_teams=2)) == 0


def _nfl_weeks() -> tuple[pd.DataFrame, pd.DataFrame]:
    games = pd.DataFrame([
        {"game_id": "2025_01_KC_NYJ", "season": 2025, "week": 1,
         "kickoff": pd.Timestamp("2025-09-07")},
        {"game_id": "2025_02_KC_BUF", "season": 2025, "week": 2,
         "kickoff": pd.Timestamp("2025-09-14")},
    ])
    weeks = pd.DataFrame([
        {"season": 2025, "week": 1, "game_id": "2025_01_KC_NYJ",
         "player_id": "00-01", "player_name": "Ronald Acuña Jr.", "team": "KC",
         "opponent": "NYJ", "position": "WR", "carries": 0, "targets": 9,
         "attempts": 0, "dk_points": 21.5},
        {"season": 2025, "week": 2, "game_id": "2025_02_KC_BUF",
         "player_id": "00-01", "player_name": "Ronald Acuña Jr.", "team": "KC",
         "opponent": "BUF", "position": "WR", "carries": 0, "targets": 0,
         "attempts": 0, "dk_points": 0.0},
    ])
    return weeks, games


def test_prepare_nfl_weeks_joins_the_kickoff_and_marks_who_played() -> None:
    from velocity.dfs.backtest import prepare_nfl_weeks

    weeks, games = _nfl_weeks()
    out = prepare_nfl_weeks(weeks, games)
    assert list(out["kickoff"]) == [pd.Timestamp("2025-09-07"),
                                    pd.Timestamp("2025-09-14")]
    # Touches, targets or attempts mark a real appearance — the football
    # analogue of a posted lineup card.
    assert list(out["played"]) == [True, False]
    # The name key folds the accent, like every other join in the harness.
    assert out["key"].iloc[0] == "ronaldacunajr"


def test_nfl_player_day_index_keys_on_the_date() -> None:
    from datetime import date

    from velocity.dfs.backtest import nfl_player_day_index, prepare_nfl_weeks

    weeks, games = _nfl_weeks()
    index = nfl_player_day_index(prepare_nfl_weeks(weeks, games))
    assert index[("ronaldacunajr", date(2025, 9, 7))]["actual"] == 21.5
    assert index[("ronaldacunajr", date(2025, 9, 14))]["started"] is False
