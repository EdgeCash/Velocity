"""Dark because it is out of season, or dark because a feed broke?

The slate printed ``no games on the board (off-season or empty snapshot)`` and
stopped there, which is two very different situations wearing one sentence.
The WNBA made it concrete: its 2026 regular season ended 31 August and twelve
days later neither the committed schedule nor the live wehoop release carried
a postseason row, while in 2024 and 2025 the playoffs began two days after the
regular season ended. These pin the distinction the log now draws.
"""

from __future__ import annotations

import pandas as pd
from velocity.report.league_health import QUIET_DAYS, league_health

NOW = pd.Timestamp("2026-09-12")


def _games(rows: list[tuple[int, str]]) -> pd.DataFrame:
    """``(season, kickoff)`` pairs as a canonical games frame."""
    return pd.DataFrame([
        {"season": season, "kickoff": pd.Timestamp(when),
         "home_team": "A", "away_team": "B"}
        for season, when in rows
    ])


def test_a_league_playing_on_this_date_in_every_prior_year_is_not_off_season() -> None:
    """The WNBA case, and the whole reason this exists."""
    games = _games([
        (2024, "2024-09-10"), (2024, "2024-10-01"),   # playing in mid-September
        (2025, "2025-09-14"), (2025, "2025-10-05"),
        (2026, "2026-08-31"),                          # and then nothing
    ])
    health = league_health(games, NOW, "wnba")
    assert health.suspicious
    assert health.played_on_this_date_before == 2 and health.prior_seasons == 2
    assert "SCHEDULE FEED" in health.describe()


def test_a_genuine_off_season_is_not_flagged() -> None:
    # Every banked season was idle in mid-September too, so silence is normal.
    games = _games([
        (2024, "2024-06-01"), (2025, "2025-06-01"), (2026, "2026-06-15"),
    ])
    health = league_health(games, NOW, "somesport")
    assert not health.suspicious
    assert "off-season" in health.describe()


def test_a_league_that_played_yesterday_is_current() -> None:
    games = _games([(2025, "2025-09-11"), (2026, "2026-09-11")])
    health = league_health(games, NOW, "nfl")
    assert not health.suspicious
    assert "the schedule is current" in health.describe()


def test_a_short_gap_is_never_flagged_however_the_prior_seasons_look() -> None:
    """A few quiet days is a bye week, not a broken feed."""
    games = _games([
        (2024, "2024-09-12"), (2025, "2025-09-12"),
        (2026, f"2026-09-{12 - QUIET_DAYS + 1:02d}"),
    ])
    assert not league_health(games, NOW, "x").suspicious


def test_games_scheduled_ahead_point_at_the_odds_feed_instead() -> None:
    # When the schedule knows about upcoming games, an empty board is the
    # sportsbook snapshot's problem, not the league's.
    games = _games([(2026, "2026-09-11"), (2026, "2026-09-20")])
    health = league_health(games, NOW, "nfl")
    assert health.upcoming == 1
    assert not health.suspicious
    assert "odds feed" in health.describe()


def test_one_season_of_history_cannot_convict_anything() -> None:
    games = _games([(2026, "2026-08-01")])
    health = league_health(games, NOW, "newsport")
    assert health.prior_seasons == 0
    assert not health.suspicious
    assert "no earlier season" in health.describe()


def test_half_the_prior_seasons_playing_is_enough() -> None:
    games = _games([
        (2023, "2023-09-12"), (2024, "2024-06-01"), (2026, "2026-08-20"),
    ])
    health = league_health(games, NOW, "x")
    assert (health.played_on_this_date_before, health.prior_seasons) == (1, 2)
    assert health.suspicious


def test_an_empty_or_unusable_frame_says_so_rather_than_raising() -> None:
    assert league_health(pd.DataFrame(), NOW, "x").describe().endswith("is empty")
    assert not league_health(pd.DataFrame(), NOW, "x").suspicious
    junk = pd.DataFrame({"kickoff": [None], "season": [2026]})
    assert not league_health(junk, NOW, "x").suspicious
