"""Sim Check + season record chain — percentile math, card assembly, renders.

The Sim Check is the account's accountability surface, so its math is pinned
exactly: mid-distribution percentiles, ordinal labels, winner-relative margin
percentiles, honest skips (no final, tie, missing pmf), and the cumulative
record chain that puts the season line on every card.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import pytest
from velocity.report.daily_record import (
    accumulate_record,
    record_summary,
    season_record_line,
)
from velocity.report.sim_check import (
    build_sim_checks,
    mid_percentile,
    ordinal,
    sim_check_caption,
)
from velocity.report.social_png import render_record_card, render_sim_check

# --- percentile math ----------------------------------------------------------


def test_mid_percentile_exact() -> None:
    pmf = {4: 0.25, 6: 0.5, 8: 0.25}
    assert mid_percentile(pmf, 6) == pytest.approx(0.5)  # 0.25 below + half of 0.5
    assert mid_percentile(pmf, 8) == pytest.approx(0.875)
    assert mid_percentile(pmf, 3) == pytest.approx(0.0)
    assert mid_percentile(pmf, 9) == pytest.approx(1.0)
    assert mid_percentile(pmf, 5) == pytest.approx(0.25)  # between buckets


def test_ordinal_labels() -> None:
    assert ordinal(0.96) == "96th"
    assert ordinal(0.51) == "51st"
    assert ordinal(0.02) == "2nd"
    assert ordinal(0.03) == "3rd"
    assert ordinal(0.11) == "11th"
    assert ordinal(0.12) == "12th"
    assert ordinal(0.13) == "13th"
    assert ordinal(0.21) == "21st"
    # 0th/100th overstate what a finite pmf can claim — clamped.
    assert ordinal(0.0) == "1st"
    assert ordinal(1.0) == "99th"


# --- card assembly ------------------------------------------------------------


PROJECTIONS = pd.DataFrame([
    {"game_id": "g1", "away": "SF", "home": "LAD", "p_home_win": 0.6,
     "fair_total": 8.5, "p_yrfi": 0.52, "n_sims": 4},
    {"game_id": "g2", "away": "NYY", "home": "BOS", "p_home_win": 0.5,
     "fair_total": 9.0, "p_yrfi": 0.5, "n_sims": 4},
])
DISTRIBUTIONS = pd.DataFrame(
    [{"game_id": "g1", "kind": "total", "value": v, "prob": p}
     for v, p in ((6, 0.25), (8, 0.5), (10, 0.25))]
    + [{"game_id": "g1", "kind": "margin", "value": v, "prob": p}
       for v, p in ((-2, 0.25), (1, 0.5), (3, 0.25))]
)
FINALS = pd.DataFrame([
    {"game_id": "g1", "home_score": 7.0, "away_score": 3.0,
     "home_score_i1": 1.0, "away_score_i1": 0.0},
])
GAMES_MAP = pd.DataFrame([
    {"game_id": "g1", "away_team": "San Francisco Giants",
     "home_team": "Los Angeles Dodgers", "kickoff": pd.Timestamp("2026-07-26 23:10")},
    {"game_id": "g2", "away_team": "New York Yankees",
     "home_team": "Boston Red Sox", "kickoff": pd.Timestamp("2026-07-26 23:10")},
])


def test_build_sim_checks_exact() -> None:
    cards = build_sim_checks(PROJECTIONS, DISTRIBUTIONS, FINALS, GAMES_MAP)
    assert len(cards) == 1  # g2 has no final and no pmf → skipped
    card = cards[0]
    assert (card.away_score, card.home_score) == (3, 7)
    assert card.actual_total == 10
    # total pmf: 0.75 below 10 + half of 0.25 at 10 → 87.5th percentile.
    assert card.total_percentile == pytest.approx(0.875)
    # Home won by 4 — beyond the margin pmf's support → 100th → clamped label.
    assert card.winner_code == "LAD"
    assert card.winner_percentile == pytest.approx(1.0)
    assert card.p_winner_pregame == pytest.approx(0.6)
    assert card.yrfi_actual is True
    assert card.n_sims == 4


def test_build_sim_checks_away_winner_flips_percentile() -> None:
    finals = pd.DataFrame([
        {"game_id": "g1", "home_score": 2.0, "away_score": 4.0},  # margin −2
    ])
    card = build_sim_checks(PROJECTIONS, DISTRIBUTIONS, finals, GAMES_MAP)[0]
    assert card.winner_code == "SF"
    # Home-margin mid-percentile at −2 is 0.125 → the away winner sits at 0.875.
    assert card.winner_percentile == pytest.approx(0.875)
    assert card.p_winner_pregame == pytest.approx(0.4)
    assert card.yrfi_actual is None  # no linescore columns → not graded


def test_build_sim_checks_skips_ties() -> None:
    finals = pd.DataFrame([{"game_id": "g1", "home_score": 3.0, "away_score": 3.0}])
    assert build_sim_checks(PROJECTIONS, DISTRIBUTIONS, finals, GAMES_MAP) == []


def test_sim_check_caption_states_the_facts() -> None:
    card = build_sim_checks(PROJECTIONS, DISTRIBUTIONS, FINALS, GAMES_MAP)[0]
    text = sim_check_caption(card)
    assert "Final: SF 3 — LAD 7." in text
    assert "88th-percentile outcome" in text  # 0.875 rounds to 88
    assert "fair total 8.5" in text
    assert "the model had LAD at 60% pregame" in text
    assert "model 52% pregame — teams scored" in text
    for banned in ("bet", "tail", "smash", "lock"):
        assert banned not in text.lower()


# --- season record chain ------------------------------------------------------


def _day(date: str, play: str, result: str, profit: float) -> pd.DataFrame:
    return pd.DataFrame([{
        "section": "games", "play": play, "market": "moneyline", "side": "home",
        "point": None, "price": 100, "stake": 1.0, "result": result,
        "profit": profit, "slate_date": pd.Timestamp(date),
    }])


def test_accumulate_dedups_and_extends() -> None:
    day1 = _day("2026-07-24", "SF @ LAD", "win", 1.0)
    day2 = _day("2026-07-25", "NYY @ BOS", "loss", -1.0)
    chain = accumulate_record(accumulate_record(None, day1), day2)
    assert len(chain) == 2
    # Regrading the same play replaces, never duplicates.
    regraded = _day("2026-07-25", "NYY @ BOS", "win", 1.0)
    chain = accumulate_record(chain, regraded)
    assert len(chain) == 2
    assert record_summary(chain) == {"wins": 2, "losses": 0, "pushes": 0,
                                     "units": pytest.approx(2.0)}


def test_season_record_line() -> None:
    chain = accumulate_record(_day("2026-07-24", "a", "win", 1.8),
                              _day("2026-07-25", "b", "loss", -1.0))
    assert season_record_line(chain) == "SEASON 1-1 · +0.8U"
    assert season_record_line(None) is None
    assert season_record_line(accumulate_record(None, None)) is None


# --- renders ------------------------------------------------------------------


def test_render_sim_check_fixed_frame(tmp_path: Path) -> None:
    card = build_sim_checks(PROJECTIONS, DISTRIBUTIONS, FINALS, GAMES_MAP)[0]
    path = render_sim_check(card, tmp_path / "check.png")
    image = plt.imread(path)
    assert image.shape[0] == 900
    assert image.shape[1] == 1600


def test_render_record_card_fixed_frame(tmp_path: Path) -> None:
    record = pd.concat([
        _day("2026-07-25", "SF @ LAD", "win", 1.8),
        _day("2026-07-25", "NYY @ BOS", "loss", -1.0),
    ], ignore_index=True)
    chain = accumulate_record(None, record)
    path = render_record_card(record, chain, tmp_path / "record.png",
                              date_label="Jul 25")
    image = plt.imread(path)
    assert image.shape[0] == 900
    assert image.shape[1] == 1600
