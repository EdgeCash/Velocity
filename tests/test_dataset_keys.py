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
