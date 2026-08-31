"""The SP+ previous-season prior — pseudo-games for the NCAAF scores fit.

Pins the leak gate (a rating season enters only after Feb 1 following it,
and only the latest eligible season forms the prior), the component scores
(offense vs anchor-scoring-defense, so the fit recovers SP+'s scale), the
fallback split when components are missing, and the teams filter.
"""

from __future__ import annotations

import pandas as pd
from velocity.ingest.ncaaf import SP_PRIOR_ANCHOR, sp_pseudo_games

SP = pd.DataFrame([
    {"season": 2024, "team": "Georgia", "rating": 27.0, "offense": 34.0, "defense": 12.0},
    {"season": 2024, "team": "Kent State", "rating": -20.0, "offense": None, "defense": None},
    {"season": 2024, "team": "Not A Team", "rating": 5.0, "offense": 25.0, "defense": 25.0},
    {"season": 2025, "team": "Georgia", "rating": 22.0, "offense": 31.0, "defense": 13.0},
])
TEAMS = {"Georgia", "Kent State"}


def test_latest_finished_season_forms_the_prior() -> None:
    # August 2026: the 2025 season is finished → 2025 ratings, stamped 2026.
    pseudo = sp_pseudo_games(SP, TEAMS, cutoff=pd.Timestamp("2026-08-30"), k=2)
    assert set(pseudo["season"]) == {2026}
    georgia = pseudo[pseudo["home_team"] == "Georgia"]
    assert len(georgia) == 2  # k copies
    assert (georgia["home_score"] == 31.0).all()  # SP+ offense
    assert (georgia["away_score"] == 13.0).all()  # SP+ defense, scored by anchor
    assert (pseudo["away_team"] == SP_PRIOR_ANCHOR).all()
    assert pseudo["neutral_site"].all()  # never pollutes the home edge


def test_leak_gate_holds_mid_season() -> None:
    # November 2025: the 2025 season is NOT finished — only 2024 may inform.
    pseudo = sp_pseudo_games(SP, TEAMS, cutoff=pd.Timestamp("2025-11-15"), k=1)
    assert set(pseudo["season"]) == {2025}
    georgia = pseudo[pseudo["home_team"] == "Georgia"].iloc[0]
    assert georgia["home_score"] == 34.0  # the 2024 rating, not 2025's
    # And before any season is finished, no prior at all.
    assert sp_pseudo_games(SP, TEAMS, cutoff=pd.Timestamp("2024-11-01"), k=1).empty


def test_rating_table_shares_the_gate_and_fallback() -> None:
    from velocity.ingest.ncaaf import sp_rating_table

    table, season = sp_rating_table(SP, pd.Timestamp("2026-08-30"))
    assert season == 2025
    assert table["Georgia"] == (31.0, 13.0)  # the 2025 finals, not 2024's
    empty, none_season = sp_rating_table(SP, pd.Timestamp("2024-11-01"))
    assert empty == {} and none_season is None  # nothing finished yet


def test_missing_components_fall_back_and_unknown_teams_are_skipped() -> None:
    pseudo = sp_pseudo_games(SP, TEAMS, cutoff=pd.Timestamp("2025-08-01"), k=1)
    kent = pseudo[pseudo["home_team"] == "Kent State"].iloc[0]
    # base 26.5 ± rating/2 → a −20 team scores 16.5 and allows 36.5.
    assert kent["home_score"] == 16.5
    assert kent["away_score"] == 36.5
    assert "Not A Team" not in set(pseudo["home_team"])  # outside the fit universe
