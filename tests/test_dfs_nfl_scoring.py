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


def test_kickers_score_by_distance_band() -> None:
    """Chris Boswell's 2024 opener: 1 from 0-39, 2 from 40-49, 3 from 50+."""
    row = pd.DataFrame([{"fg_made_0_39": 1, "fg_made_40_49": 2,
                         "fg_made_50_plus": 3, "pat_made": 0}])
    assert nfl_dk_points(row).iloc[0] == pytest.approx(3 + 8 + 15)
    # DK's NFL rules charge nothing for a miss.
    with_misses = row.assign(fg_missed=4)
    assert nfl_dk_points(with_misses).iloc[0] == pytest.approx(26.0)


def test_normalizer_collapses_nflverse_bands_into_dks_three() -> None:
    """nflverse buckets more finely than DK pays."""
    raw = pd.DataFrame([{
        "season": 2024, "week": 1, "player_id": "00-003",
        "player_name": "A Kicker", "team": "PIT", "position": "K",
        "fg_made_0_19": 1, "fg_made_20_29": 1, "fg_made_30_39": 1,
        "fg_made_40_49": 2, "fg_made_50_59": 1, "fg_made_60_": 1,
        "pat_made": 3,
    }])
    weeks = normalize_dfs_weeks(raw)
    row = weeks.iloc[0]
    assert row["fg_made_0_39"] == 3.0  # three nflverse bands, one DK tier
    assert row["fg_made_40_49"] == 2.0
    assert row["fg_made_50_plus"] == 2.0  # 50-59 and 60+ are one DK tier
    assert nfl_dk_points(weeks).iloc[0] == pytest.approx(9 + 8 + 10 + 3)


def test_per_sim_scoring_prices_the_bonus_as_a_probability() -> None:
    import numpy as np
    from velocity.dfs.scoring import NFL_BONUS, nfl_dk_points_from_samples

    n = 4
    samples = {
        ("wr1", "receptions"): np.array([5.0, 6.0, 7.0, 4.0]),
        ("wr1", "receiving_yards"): np.array([80.0, 120.0, 99.0, 100.0]),
        ("wr1", "anytime_td"): np.array([0.0, 1.0, 0.0, 2.0]),
    }
    pts = nfl_dk_points_from_samples(samples, "wr1", fumbles=0.1)
    assert pts is not None and len(pts) == n
    expected = (np.array([5.0, 6.0, 7.0, 4.0]) + 0.1 * np.array([80.0, 120.0, 99.0, 100.0])
                + NFL_BONUS * np.array([0.0, 1.0, 0.0, 1.0]) + 6.0 * np.array([0.0, 1.0, 0.0, 2.0])
                - 0.1)
    assert pts.tolist() == expected.tolist()
    assert nfl_dk_points_from_samples(samples, "nobody") is None


def test_sim_points_are_bonus_inclusive_and_carry_samples() -> None:
    import numpy as np
    from velocity.dfs.scoring import dk_expected_points, nfl_sim_points
    from velocity.models.props_football import FootballPropConfig

    fp = pd.DataFrame([
        {"player_id": "qb", "player_name": "Star QB", "team": "KC", "position": "QB",
         "stat": "pass_yds", "value": 290.0},
        {"player_id": "qb", "player_name": "Star QB", "team": "KC", "position": "QB",
         "stat": "pass_tds", "value": 2.1},
        {"player_id": "qb", "player_name": "Star QB", "team": "KC", "position": "QB",
         "stat": "pass_int", "value": 0.7},
        {"player_id": "wr", "player_name": "Star WR", "team": "KC", "position": "WR",
         "stat": "rec", "value": 6.5},
        {"player_id": "wr", "player_name": "Star WR", "team": "KC", "position": "WR",
         "stat": "rec_yds", "value": 92.0},
        {"player_id": "wr", "player_name": "Star WR", "team": "KC", "position": "WR",
         "stat": "rec_tds", "value": 0.6},
        {"player_id": "rb", "player_name": "Away RB", "team": "BUF", "position": "RB",
         "stat": "rush_yds", "value": 85.0},
        {"player_id": "rb", "player_name": "Away RB", "team": "BUF", "position": "RB",
         "stat": "rush_tds", "value": 0.5},
    ])
    frame, arrays = nfl_sim_points(fp, "KC", "BUF", np.random.default_rng(4),
                                   FootballPropConfig(n_sims=20_000, rush_pool={}))
    assert set(frame["player_name"]) == {"Star QB", "Star WR", "Away RB"}
    assert set(arrays) == {"Star QB", "Star WR", "Away RB"}
    linear = dk_expected_points(fp).set_index("player_name")["points"]
    sim = frame.set_index("player_name")["points"]
    # A 92-yard receiver crosses 100 often: the bonus lifts him above the
    # linear pass; the 290-yard passer crosses 300 too.
    assert sim["Star WR"] > linear["Star WR"] + 0.5
    assert sim["Star QB"] > linear["Star QB"] + 0.5
    # The mean of the samples IS the projection.
    assert arrays["Star WR"].mean() == pytest.approx(sim["Star WR"], abs=0.01)


def test_the_live_feeds_receptions_key_is_scored() -> None:
    # FantasyPros spells receptions ``rec_rec`` on the live NFL feed. Every
    # receiver's PPR points hung on that key being known.
    from velocity.dfs.scoring import dk_expected_points
    from velocity.models.props_football import FP_STAT_TO_MARKET, team_player_means

    fp = pd.DataFrame([
        {"player_id": "wr", "player_name": "Live WR", "team": "CIN", "position": "WR",
         "stat": "rec_rec", "value": 7.2},
        {"player_id": "wr", "player_name": "Live WR", "team": "CIN", "position": "WR",
         "stat": "rec_yds", "value": 88.6},
    ])
    scored = dk_expected_points(fp).set_index("player_name").loc["Live WR", "points"]
    assert scored == pytest.approx(7.2 + 8.86)
    assert FP_STAT_TO_MARKET["rec_rec"] == "receptions"
    means = team_player_means(fp, "CIN")[0].means
    assert means["receptions"] == 7.2 and means["receiving_yards"] == 88.6
