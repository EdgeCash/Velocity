"""The offseason gap in the recency key, and SP+ special teams in the prior.

Offline. Both are knobs of the recency round that followed the college
finding (docs/MODEL_LAB.md): the recency key steps a few empty week slots
between seasons unless told the offseason is longer, and the SP+
pseudo-games carried offense minus defense without the third component.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.features.team import recency_weights
from velocity.ingest.ncaaf import SP_PRIOR_ANCHOR, sp_pseudo_games


def test_offseason_weeks_age_last_season_and_nothing_else() -> None:
    plays = pd.DataFrame({
        "season": [2024, 2024, 2025, 2025], "week": [17, 18, 1, 2], "epa": 0.0,
    })
    flat = recency_weights(plays, 8.0)
    gapped = recency_weights(plays, 8.0, offseason_weeks=8.0)
    # Within the current season the two agree (newest = 1.0, one week older
    # halves at an 8-week half-life... a week is 2^(-1/8)).
    assert flat.iloc[3] == 1.0 and gapped.iloc[3] == 1.0
    assert flat.iloc[2] == pytest.approx(gapped.iloc[2]) == pytest.approx(2 ** (-1 / 8))
    # Last season's weeks are exactly 8 weeks older under the gap.
    for i in (0, 1):
        assert gapped.iloc[i] == pytest.approx(flat.iloc[i] * 2 ** (-8 / 8))
    # The default is the unchanged key.
    assert np.allclose(recency_weights(plays, 8.0, offseason_weeks=0.0), flat)
    with pytest.raises(ValueError):
        recency_weights(plays, 8.0, offseason_weeks=-1.0)


def test_special_teams_fold_into_the_pseudo_game_margin() -> None:
    sp = pd.DataFrame({
        "season": [2024, 2024], "team": ["A", "B"], "rating": [10.0, -3.0],
        "offense": [35.0, 25.0], "defense": [20.0, 30.0], "special_teams": [2.0, None],
    })
    cutoff = pd.Timestamp("2025-02-01")
    plain = sp_pseudo_games(sp, {"A", "B"}, cutoff=cutoff, k=1)
    with_st = sp_pseudo_games(sp, {"A", "B"}, cutoff=cutoff, k=1, special_teams=True)
    a_plain = plain[plain["home_team"] == "A"].iloc[0]
    a_st = with_st[with_st["home_team"] == "A"].iloc[0]
    assert a_plain["home_score"] - a_plain["away_score"] == pytest.approx(15.0)
    assert a_st["home_score"] == pytest.approx(36.0) and a_st["away_score"] == pytest.approx(19.0)
    assert a_st["away_team"] == SP_PRIOR_ANCHOR
    # A missing component adds nothing; without the column the call is unchanged.
    b_st = with_st[with_st["home_team"] == "B"].iloc[0]
    assert b_st["home_score"] == 25.0 and b_st["away_score"] == 30.0
    bare = sp_pseudo_games(sp.drop(columns=["special_teams"]), {"A"}, cutoff=cutoff, k=1,
                           special_teams=True)
    assert bare.iloc[0]["home_score"] == 35.0
