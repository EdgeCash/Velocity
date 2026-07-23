"""Finals resolver (velocity.report.results) — offline, id-space bridge.

The slate is keyed by Odds-API ids; finals come from StatsAPI gamePks. These pin
that the join is by resolved team code + date: a matched, played game yields a
final; an unplayed game, an unmatched team, or a date mismatch is dropped.
"""

from __future__ import annotations

import pandas as pd
from velocity.report.results import finals_for_slate

# The slate side: Odds-API ids + provider names + kickoff.
GAMES_MAP = pd.DataFrame({
    "game_id": ["odds1", "odds2", "odds3"],
    "away_team": ["San Francisco Giants", "New York Yankees", "Chicago Cubs"],
    "home_team": ["Los Angeles Dodgers", "Boston Red Sox", "St. Louis Cardinals"],
    "kickoff": pd.to_datetime(["2026-07-23T02:10", "2026-07-23T23:05", "2026-07-24T18:00"]),
})

# The StatsAPI side: gamePks + names + scores. g1 played, g2 played, the Cubs game
# is on a different date (no match).
SCHEDULE = pd.DataFrame({
    "game_id": ["745804", "745820", "745999"],
    "away_team": ["San Francisco Giants", "New York Yankees", "Chicago Cubs"],
    "home_team": ["Los Angeles Dodgers", "Boston Red Sox", "St. Louis Cardinals"],
    "kickoff": pd.to_datetime(["2026-07-23T02:10", "2026-07-23T23:05", "2026-07-25T18:00"]),
    "home_score": [5.0, 4.0, 3.0],
    "away_score": [3.0, 6.0, 1.0],
})


def test_matches_by_team_code_and_date() -> None:
    finals = finals_for_slate(GAMES_MAP, SCHEDULE).set_index("game_id")
    # Odds ids get the StatsAPI scores, joined by team + date.
    assert (finals.loc["odds1", "home_score"], finals.loc["odds1", "away_score"]) == (5.0, 3.0)
    assert (finals.loc["odds2", "home_score"], finals.loc["odds2", "away_score"]) == (4.0, 6.0)
    # The Cubs game is a day off between the feeds → not matched.
    assert "odds3" not in finals.index


def test_unplayed_game_is_dropped() -> None:
    sched = SCHEDULE.copy()
    sched.loc[sched["game_id"] == "745804", ["home_score", "away_score"]] = float("nan")
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
