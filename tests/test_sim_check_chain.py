"""The accuracy chain: a Sim Check as a row, and the season of them merged.

A Sim Check card is a post; the frame is the record the site's Accuracy view
reads (docs/FOOTBALL_PAL.md). Every grading run writes the day's rows and the
whole chain carried forward, so the union of every copy on hand is the season,
with the newest grade of a game winning — the record chain's own rule.
"""

from __future__ import annotations

import pandas as pd
from velocity.report.sim_check import (
    SIM_CHECK_COLUMNS,
    SimCheckCard,
    merge_sim_check_chain,
    sim_check_frame,
)

PROJECTIONS = pd.DataFrame([{
    "game_id": "g1", "away": "BUF", "home": "KC", "n_sims": 4000,
    "mu_away": 21.0, "mu_home": 25.0, "p_home_win": 0.62,
    "fair_spread": -3.5, "fair_total": 46.0,
}])


def _card(gid: str, date: str, home: int = 27, away: int = 20) -> SimCheckCard:
    return SimCheckCard(
        game_id=gid, away_name="Buffalo Bills", home_name="Kansas City Chiefs",
        away_code="BUF", home_code="KC", game_date=pd.Timestamp(date),
        away_score=away, home_score=home, actual_total=home + away,
        total_percentile=0.55, winner_code="KC" if home > away else "BUF",
        winner_percentile=0.6, p_winner_pregame=0.62, fair_total=46.0,
        total_pmf={46: 1.0}, n_sims=4000,
    )


def test_the_frame_carries_the_projection_beside_the_final() -> None:
    frame = sim_check_frame([_card("g1", "2026-09-13")], PROJECTIONS, "nfl", "20260914T120000Z")
    assert list(frame.columns) == SIM_CHECK_COLUMNS
    row = frame.iloc[0]
    assert row["mu_home"] == 25.0 and row["mu_away"] == 21.0 and row["fair_spread"] == -3.5
    assert row["home_score"] == 27 and row["away_score"] == 20 and row["actual_total"] == 47
    assert row["league"] == "nfl" and row["graded_stamp"] == "20260914T120000Z"
    assert row["game_date"] == pd.Timestamp("2026-09-13")


def test_a_card_without_a_projection_row_keeps_its_own_numbers() -> None:
    frame = sim_check_frame([_card("g9", "2026-09-13")], PROJECTIONS, "nfl", "s")
    row = frame.iloc[0]
    assert pd.isna(row["mu_home"]) and pd.isna(row["p_home_win"])
    assert row["fair_total"] == 46.0 and row["actual_total"] == 47


def test_empty_inputs_give_typed_empty_frames() -> None:
    assert list(sim_check_frame([], PROJECTIONS, "nfl", "s").columns) == SIM_CHECK_COLUMNS
    merged = merge_sim_check_chain(None, pd.DataFrame())
    assert merged.empty and list(merged.columns) == SIM_CHECK_COLUMNS


def test_merge_keeps_every_game_once_and_the_newest_grade_wins() -> None:
    older = sim_check_frame(
        [_card("g1", "2026-09-13", home=27), _card("g2", "2026-09-06")],
        PROJECTIONS, "nfl", "20260914T120000Z")
    newer = sim_check_frame([_card("g1", "2026-09-13", home=30)],
                            PROJECTIONS, "nfl", "20260915T120000Z")
    # Order of the copies must not matter: the union is the chain.
    for chain in (merge_sim_check_chain(older, newer), merge_sim_check_chain(newer, older)):
        assert len(chain) == 2
        g1 = chain[chain["game_id"] == "g1"].iloc[0]
        assert g1["home_score"] == 30 and g1["graded_stamp"] == "20260915T120000Z"
        # By game date, so the site's table reads as a season.
        assert list(chain["game_id"]) == ["g2", "g1"]
