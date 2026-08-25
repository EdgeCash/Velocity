"""NFL DK scoring — the arithmetic, pinned by hand against real games.

Every number here is a real stat line whose DK score is known, because the
whole point of an actuals scorer is that it agrees with what DraftKings
actually paid. The bonus boundaries get their own test: a scorer that
forgets +3 at 100 yards under-scores exactly the ceiling games tournaments
are won on.
"""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.ingest.nfl import normalize_dfs_weeks
from velocity.models.dfs_nfl import nfl_dk_points


def test_matches_a_real_ceiling_game_by_hand() -> None:
    # Tyreek Hill, 2020 week 12 at TB: 13 catches, 269 yards, 3 TDs.
    # 13 receptions + 26.9 yards + 18 TDs + 3 (100-yard bonus) = 60.9
    row = pd.DataFrame([{"receptions": 13, "receiving_yards": 269,
                         "receiving_tds": 3}])
    assert nfl_dk_points(row).iloc[0] == pytest.approx(60.9)


def test_matches_a_real_quarterback_line_by_hand() -> None:
    # 333 passing yards, 3 passing TDs, 1 interception, 22 rushing yards:
    # 13.32 + 12 - 1 + 2.2 + 3 (300-yard bonus) = 29.52
    row = pd.DataFrame([{"pass_yards": 333, "pass_tds": 3, "interceptions": 1,
                         "rush_yards": 22}])
    assert nfl_dk_points(row).iloc[0] == pytest.approx(29.52)


def test_bonuses_are_thresholds_not_slopes() -> None:
    just_under = pd.DataFrame([{"rush_yards": 99, "receiving_yards": 99,
                                "pass_yards": 299}])
    just_over = pd.DataFrame([{"rush_yards": 100, "receiving_yards": 100,
                               "pass_yards": 300}])
    under = nfl_dk_points(just_under).iloc[0]
    over = nfl_dk_points(just_over).iloc[0]
    # A yard in each category is worth 0.1 + 0.1 + 0.04 = 0.24; the three
    # bonuses add 9 on top, and not a point before the threshold.
    assert over - under == pytest.approx(0.24 + 9.0)
    # A player who clears 100 rushing twice over still gets one +3.
    twice = pd.DataFrame([{"rush_yards": 200}])
    assert nfl_dk_points(twice).iloc[0] == pytest.approx(20.0 + 3.0)


def test_projections_must_be_scored_without_bonuses() -> None:
    """A bonus is a tail event; at the mean it overstates every player."""
    projection = pd.DataFrame([{"pass_yards": 305, "pass_tds": 1.8}])
    assert nfl_dk_points(projection, bonuses=False).iloc[0] == pytest.approx(
        305 * 0.04 + 1.8 * 4.0)
    assert nfl_dk_points(projection).iloc[0] == pytest.approx(
        305 * 0.04 + 1.8 * 4.0 + 3.0)


def test_negatives_and_returns_count() -> None:
    row = pd.DataFrame([{"interceptions": 2, "fumbles_lost": 1,
                         "return_tds": 1, "two_point_conversions": 1}])
    assert nfl_dk_points(row).iloc[0] == pytest.approx(-2 - 1 + 6 + 2)


def test_missing_columns_contribute_nothing_rather_than_raising() -> None:
    assert nfl_dk_points(pd.DataFrame([{"receptions": 4}])).iloc[0] == 4.0


def _raw() -> pd.DataFrame:
    return pd.DataFrame([{
        "season": 2025, "week": 3, "season_type": "REG",
        "game_id": "2025_03_KC_NYJ", "player_id": "00-001",
        "player_display_name": "A Receiver", "team": "KC",
        "opponent_team": "NYJ", "position": "WR",
        "receptions": 8, "receiving_yards": 121, "receiving_tds": 1,
        "rushing_yards": 6, "rushing_fumbles_lost": 1,
        "receiving_fumbles_lost": 0, "sack_fumbles_lost": 0,
        "special_teams_tds": 0, "pt_return_tds": 1,
        "passing_2pt_conversions": 0, "rushing_2pt_conversions": 0,
        "receiving_2pt_conversions": 1,
    }])


def test_normalizer_sums_the_columns_nflverse_splits() -> None:
    weeks = normalize_dfs_weeks(_raw())
    row = weeks.iloc[0]
    assert row["player_name"] == "A Receiver"
    assert row["opponent"] == "NYJ"
    # Fumbles are split across three nflverse columns; DK scores the total.
    assert row["fumbles_lost"] == 1.0
    assert row["return_tds"] == 1.0
    assert row["two_point_conversions"] == 1.0
    # 8 + 12.1 + 6 + 0.6 - 1 + 6 + 2 + 3 (100-yard bonus) = 36.7
    assert nfl_dk_points(weeks).iloc[0] == pytest.approx(36.7)


def test_normalizer_tolerates_a_thinner_old_schema() -> None:
    thin = pd.DataFrame([{"season": 2015, "week": 1, "player_id": "00-002",
                          "player_name": "Old Timer", "recent_team": "SD",
                          "receiving_yards": 40}])
    weeks = normalize_dfs_weeks(thin)
    assert weeks.iloc[0]["team"] == "SD"
    assert weeks.iloc[0]["fumbles_lost"] == 0.0
    assert nfl_dk_points(weeks).iloc[0] == pytest.approx(4.0)


def test_empty_frame_keeps_the_schema() -> None:
    weeks = normalize_dfs_weeks(pd.DataFrame())
    assert weeks.empty
    assert {"player_id", "fumbles_lost", "opponent"} <= set(weeks.columns)
