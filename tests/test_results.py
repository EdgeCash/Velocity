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
