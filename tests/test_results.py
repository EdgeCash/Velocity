"""Finals resolver (velocity.report.results) — offline, id-space bridge.

The slate is keyed by Odds-API ids; finals come from the league schedule feed
(nflverse codes). These pin that the join is by resolved team code + date: a
matched, played game yields a final; an unplayed game, an unmatched team, or a
date mismatch is dropped.
"""

from __future__ import annotations

import pandas as pd
from velocity.report.results import finals_for_slate

# The slate side: Odds-API ids + provider names + kickoff.
GAMES_MAP = pd.DataFrame({
    "game_id": ["odds1", "odds2", "odds3"],
    "away_team": ["Buffalo Bills", "Dallas Cowboys", "Chicago Bears"],
    "home_team": ["Kansas City Chiefs", "Philadelphia Eagles", "Green Bay Packers"],
    "kickoff": pd.to_datetime(["2026-09-10T00:20", "2026-09-13T17:00", "2026-09-13T20:25"]),
})

# The schedule-feed side: its own ids + nflverse team codes + scores. The first
# two are played; the Bears game is on a different date (no match).
SCHEDULE = pd.DataFrame({
    "game_id": ["2026_01_BUF_KC", "2026_01_DAL_PHI", "2026_02_CHI_GB"],
    "away_team": ["BUF", "DAL", "CHI"],
    "home_team": ["KC", "PHI", "GB"],
    "kickoff": pd.to_datetime(["2026-09-10T00:20", "2026-09-13T17:00", "2026-09-20T20:25"]),
    "home_score": [27.0, 24.0, 21.0],
    "away_score": [20.0, 28.0, 14.0],
})


def test_matches_by_team_code_and_date() -> None:
    finals = finals_for_slate(GAMES_MAP, SCHEDULE).set_index("game_id")
    # Odds ids get the schedule feed's scores, joined by team + date.
    assert (finals.loc["odds1", "home_score"], finals.loc["odds1", "away_score"]) == (27.0, 20.0)
    assert (finals.loc["odds2", "home_score"], finals.loc["odds2", "away_score"]) == (24.0, 28.0)
    # The Bears game is a week off between the feeds → not matched.
    assert "odds3" not in finals.index


def test_unplayed_game_is_dropped() -> None:
    sched = SCHEDULE.copy()
    sched.loc[sched["game_id"] == "2026_01_BUF_KC", ["home_score", "away_score"]] = float("nan")
    finals = finals_for_slate(GAMES_MAP, sched).set_index("game_id")
    assert "odds1" not in finals.index  # null score → ungraded
    assert "odds2" in finals.index


def test_unresolvable_team_is_dropped() -> None:
    games = GAMES_MAP.copy()
    games.loc[games["game_id"] == "odds1", "home_team"] = "Sioux Falls Canaries"
    finals = finals_for_slate(games, SCHEDULE)
    assert "odds1" not in set(finals["game_id"])


def test_empty_inputs_yield_empty_frame() -> None:
    out = finals_for_slate(GAMES_MAP.iloc[:0], SCHEDULE)
    assert out.empty and list(out.columns) == ["game_id", "home_score", "away_score"]


def test_a_prime_time_game_grades_across_the_utc_date_boundary() -> None:
    """The defect that left NFL with zero settled rows for a whole season.

    nflverse writes kickoff as a naive Eastern clock; the slate carries The
    Odds API's UTC instant. A 20:20 Eastern kickoff is 00:20 UTC the next day,
    so keying on the calendar date meant every Thursday, Sunday and Monday
    night game missed. These are the two games actually played in Week 1 2026.
    """
    slate = pd.DataFrame(
        {
            "game_id": ["evt-ne-sea", "evt-sf-la"],
            "home_team": ["Seattle Seahawks", "Los Angeles Rams"],
            "away_team": ["New England Patriots", "San Francisco 49ers"],
            # UTC, as The Odds API reports it.
            "kickoff": [
                pd.Timestamp("2026-09-10 00:20:00"),
                pd.Timestamp("2026-09-11 00:35:00"),
            ],
        }
    )
    schedule = pd.DataFrame(
        {
            "home_team": ["SEA", "LA"],
            "away_team": ["NE", "SF"],
            # Eastern, as nflverse reports it — the previous calendar day.
            "kickoff": [
                pd.Timestamp("2026-09-09 20:20:00"),
                pd.Timestamp("2026-09-10 20:35:00"),
            ],
            "home_score": [13.0, 7.0],
            "away_score": [10.0, 27.0],
        }
    )
    finals = finals_for_slate(
        slate, schedule, schedule_tz="America/New_York"
    ).set_index("game_id")
    assert len(finals) == 2
    assert finals.loc["evt-ne-sea", "home_score"] == 13.0
    assert finals.loc["evt-sf-la", "away_score"] == 27.0


def test_a_doubleheader_grades_as_two_games_not_one() -> None:
    """Baseball plays the same pair twice on one date; the old key collapsed them.

    Both entries shared ``(away, home, date)``, so the second overwrote the
    first and a bet on game one could be settled against game two's score.
    """
    slate = pd.DataFrame(
        {
            "game_id": ["evt-g1", "evt-g2"],
            "home_team": ["NYY", "NYY"],
            "away_team": ["BOS", "BOS"],
            "kickoff": [
                pd.Timestamp("2026-09-11 17:05:00"),
                pd.Timestamp("2026-09-11 23:05:00"),
            ],
        }
    )
    schedule = pd.DataFrame(
        {
            "home_team": ["NYY", "NYY"],
            "away_team": ["BOS", "BOS"],
            "kickoff": [
                pd.Timestamp("2026-09-11 17:05:00"),
                pd.Timestamp("2026-09-11 23:05:00"),
            ],
            "home_score": [2.0, 9.0],
            "away_score": [1.0, 4.0],
        }
    )
    finals = finals_for_slate(slate, schedule, aliases={"NYY": "NYY", "BOS": "BOS"})
    finals = finals.set_index("game_id")
    assert len(finals) == 2
    # Each half keeps its own score rather than inheriting the other's.
    assert (finals.loc["evt-g1", "home_score"], finals.loc["evt-g1", "away_score"]) == (2.0, 1.0)
    assert (finals.loc["evt-g2", "home_score"], finals.loc["evt-g2", "away_score"]) == (9.0, 4.0)


def test_one_played_game_is_claimed_by_only_one_slate_row() -> None:
    # Two slate rows for a pair but only one game played: the nearer kickoff
    # takes it and the other stays ungraded rather than both inheriting it.
    slate = pd.DataFrame(
        {
            "game_id": ["evt-near", "evt-far"],
            "home_team": ["NYY", "NYY"],
            "away_team": ["BOS", "BOS"],
            "kickoff": [
                pd.Timestamp("2026-09-11 17:10:00"),
                pd.Timestamp("2026-09-11 23:05:00"),
            ],
        }
    )
    schedule = pd.DataFrame(
        {
            "home_team": ["NYY"],
            "away_team": ["BOS"],
            "kickoff": [pd.Timestamp("2026-09-11 17:05:00")],
            "home_score": [2.0],
            "away_score": [1.0],
        }
    )
    finals = finals_for_slate(slate, schedule, aliases={"NYY": "NYY", "BOS": "BOS"})
    assert list(finals["game_id"]) == ["evt-near"]


def test_a_game_outside_the_tolerance_is_not_matched() -> None:
    # The same pair meeting again a week later is a different game.
    slate = pd.DataFrame(
        {
            "game_id": ["evt-week1"],
            "home_team": ["NYY"],
            "away_team": ["BOS"],
            "kickoff": [pd.Timestamp("2026-09-04 17:05:00")],
        }
    )
    schedule = pd.DataFrame(
        {
            "home_team": ["NYY"],
            "away_team": ["BOS"],
            "kickoff": [pd.Timestamp("2026-09-11 17:05:00")],
            "home_score": [2.0],
            "away_score": [1.0],
        }
    )
    assert finals_for_slate(
        slate, schedule, aliases={"NYY": "NYY", "BOS": "BOS"}
    ).empty
