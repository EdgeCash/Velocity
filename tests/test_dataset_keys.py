"""The committed college frames key onto each other.

The lab's blend cuts plays to ``game_id ∈ train_games``; a season whose games
carry ids the plays do not (the 2025 boxscore backfill did) drops out of the
EPA half without a warning. This reads the committed datasets when they are
present and refuses a season where the ids no longer line up — the check
docs/SYSTEM_REVIEW.md M0 asked for and the audit found missing.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

DATA = Path("datasets/ncaaf")


@pytest.mark.skipif(
    not (DATA / "games.parquet").exists() or not (DATA / "plays.parquet").exists(),
    reason="committed NCAAF datasets not present",
)
def test_ncaaf_play_games_key_onto_the_games_frame() -> None:
    games = pd.read_parquet(
        DATA / "games.parquet", columns=["game_id", "season", "week", "home_team", "away_team"])
    plays = pd.read_parquet(
        DATA / "plays.parquet", columns=["game_id", "season", "week", "posteam", "defteam"]
    ).drop_duplicates("game_id")
    assert not games["game_id"].duplicated().any()
    for season, sub in plays.groupby("season"):
        season_games = games[games["season"] == season]
        by_id = int(sub["game_id"].isin(set(season_games["game_id"])).sum())
        pairs = set(zip(season_games["week"], season_games["home_team"],
                        season_games["away_team"], strict=True))
        pairs |= {(w, a, h) for w, h, a in pairs}
        by_teams = int(sum(
            (w, p, d) in pairs
            for w, p, d in zip(sub["week"], sub["posteam"], sub["defteam"], strict=True)))
        assert by_id >= 0.9 * by_teams, (
            f"season {season}: {by_id} play-games keyed by id against {by_teams} by teams")


NFL = Path("datasets/nfl")


@pytest.mark.skipif(
    not (NFL / "games.parquet").exists() or not (NFL / "plays.parquet").exists(),
    reason="committed NFL datasets not present",
)
def test_every_played_nfl_game_has_its_plays() -> None:
    """The plays-coverage assertion the audit asked for (docs/PROJECTION_AUDIT.md §7.5).

    The NFL fit is the plays frame; a season whose games carry ids the plays
    do not would drop out of the ratings silently, exactly as the college
    backfill once did. Every played game on the committed frame has had at
    least 128 plays since 2011 (a full game is ~165), so a game under 100 is
    a truncated or mis-keyed one, not a short game.
    """
    games = pd.read_parquet(NFL / "games.parquet", columns=["game_id", "season", "home_score"])
    played = games.dropna(subset=["home_score"])
    plays = pd.read_parquet(NFL / "plays.parquet", columns=["game_id"])
    per_game = plays.groupby("game_id").size()
    counts = played["game_id"].map(per_game).fillna(0)
    missing = played.loc[counts < 100, ["game_id", "season"]]
    assert missing.empty, (
        f"{len(missing)} played NFL games with under 100 plays: "
        f"{missing['season'].value_counts().sort_index().to_dict()}")
    # And no plays keyed to a game the schedule does not know.
    assert per_game.index.isin(set(games["game_id"])).all()
