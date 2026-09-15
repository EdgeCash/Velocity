"""The NHL goalie lookup lab — point-in-time correctness above all.

The whole experiment turns on one thing: the "depth chart" predictor must not
be able to see the game it is predicting. A trailing-window count that includes
game *i* would quietly become a partial oracle, and the measured gap between a
guessed goalie and a known one — the entire question — would be an artifact.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "nhl_goalie_lab", Path(__file__).parent.parent / "scripts" / "nhl_goalie_lab.py"
)
lab = importlib.util.module_from_spec(_SPEC)  # type: ignore[arg-type]
_SPEC.loader.exec_module(lab)  # type: ignore[union-attr]


def _games(n: int = 6) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "game_id": f"g{i}", "season": 2024, "week": i,
            "kickoff": pd.Timestamp("2024-10-01") + pd.Timedelta(days=i),
            "home_team": "AAA", "away_team": "BBB",
            "home_score": 3.0, "away_score": 2.0,
        }
        for i in range(n)
    ])


def _starters(home_ids: list[str], away_ids: list[str]) -> pd.DataFrame:
    rows = []
    for i, (home, away) in enumerate(zip(home_ids, away_ids, strict=True)):
        rows.append({"game_id": f"g{i}", "side": "home", "starter_id": home})
        rows.append({"game_id": f"g{i}", "side": "away", "starter_id": away})
    return pd.DataFrame(rows)


def test_the_first_game_has_no_nominal_starter() -> None:
    """Nothing prior means no depth chart — it must price neutral, not guess."""
    long = lab.team_game_log(_games(3), _starters(["A"] * 3, ["X"] * 3))
    scoped = lab.nominal_starters(long, window=10)
    first = scoped.sort_values("kickoff").groupby("team").head(1)
    assert first["nominal_id"].isna().all()


def test_the_predictor_cannot_see_its_own_game() -> None:
    """The leak that would turn this experiment into a partial oracle.

    Team AAA starts A four times and then B. If game 5's own starter counted,
    the window would still say A — so that is not the discriminating case. The
    discriminating case is a team whose ONLY start is the game being predicted:
    including it would name that goalie; excluding it names nobody.
    """
    long = lab.team_game_log(_games(1), _starters(["A"], ["X"]))
    scoped = lab.nominal_starters(long, window=10)
    assert scoped["nominal_id"].isna().all()


def test_the_nominal_starter_is_the_most_frequent_prior_goalie() -> None:
    long = lab.team_game_log(
        _games(5), _starters(["A", "A", "B", "A", "B"], ["X"] * 5)
    )
    scoped = lab.nominal_starters(long, window=10).sort_values("kickoff")
    home = scoped[scoped["team"] == "AAA"]
    # Priors per game: [], [A], [A,A], [A,A,B], [A,A,B,A] → none, A, A, A, A
    assert home["nominal_id"].isna().tolist() == [True, False, False, False, False]
    assert home["nominal_id"].dropna().tolist() == ["A", "A", "A", "A"]


def test_the_window_forgets_older_starts() -> None:
    """A short window must actually drop what falls out of it."""
    long = lab.team_game_log(
        _games(5), _starters(["A", "A", "B", "B", "B"], ["X"] * 5)
    )
    scoped = lab.nominal_starters(long, window=2).sort_values("kickoff")
    home = scoped[scoped["team"] == "AAA"]
    # Last two priors per game: [], [A], [A,A], [A,B]→A (tie, first wins), [B,B]
    assert list(home["nominal_id"])[-1] == "B"


def test_hit_rate_scores_only_where_both_are_known() -> None:
    long = lab.team_game_log(_games(4), _starters(["A", "A", "A", "B"], ["X"] * 4))
    scoped = lab.nominal_starters(long, window=10)
    rate, n = lab.hit_rate(scoped)
    # Every team-game after the first is scored: AAA 2 hits of 3, BBB 3 of 3.
    assert n == 6
    assert rate == pytest.approx(5 / 6)


def test_the_lookup_is_keyed_the_way_the_model_reads_it() -> None:
    """StarterAwareModel looks up (home, away, kickoff) — a mismatch prices neutral."""
    games = _games(2)
    long = lab.team_game_log(games, _starters(["A", "A"], ["X", "X"]))
    lookup = lab.build_lookup(long, games, "starter_id")
    key = ("AAA", "BBB", pd.Timestamp(games.iloc[0]["kickoff"]))
    assert lookup[key] == ("A", "X")


def test_an_absent_nominal_starter_becomes_a_neutral_lookup_entry() -> None:
    """A missing depth chart must price neutral, never as some other goalie."""
    games = _games(2)
    long = lab.team_game_log(games, _starters(["A", "A"], ["X", "X"]))
    scoped = lab.nominal_starters(long, window=10)
    lookup = lab.build_lookup(scoped, games, "nominal_id")
    first = ("AAA", "BBB", pd.Timestamp(games.iloc[0]["kickoff"]))
    assert lookup[first] == (None, None)
