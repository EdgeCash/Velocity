"""Matchup card — the leans' arithmetic, the labels' signs, and a render smoke.

The sign of a lean is the one thing on this card that can be wrong *publicly*:
"DET +3.5" and "DET -3.5" are opposite bets, and the card goes to an account,
not a log file. So every direction of every market gets pinned here rather than
sampled. The render is an offline smoke — no asset cache, so no network — that
only asserts a real PNG of the right frame lands.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PIL import Image
from velocity.report.matchup import (
    WIDE_GAP_PTS,
    FormGame,
    MarketNumbers,
    MatchupCard,
    PlayerLine,
    TeamSide,
    UnitRank,
)
from velocity.report.matchup_png import HEIGHT, WIDTH, matchup_filename, render_matchup_card
from velocity.report.social import SPREAD_EDGE_PTS, TOTAL_EDGE_PTS

AWAY = TeamSide(code="DAL", city="Dallas", nickname="Cowboys", record="6-5-1",
                color="#003594")
HOME = TeamSide(code="DET", city="Detroit", nickname="Lions", record="7-5",
                color="#0076B6")


def _card(*, fair_spread_home: float = 3.5, fair_total: float = 48.0,
          spread_home: float | None = 3.5, total: float | None = 48.0,
          **kw: object) -> MatchupCard:
    return MatchupCard(
        game_id="g1", league="nfl", week_label="Week 14",
        away=kw.pop("away", AWAY), home=kw.pop("home", HOME),  # type: ignore[arg-type]
        mu_away=22.0, mu_home=26.0,
        fair_spread_home=fair_spread_home, fair_total=fair_total,
        p_home_win=0.62,
        market=MarketNumbers(spread_home=spread_home, total=total),
        **kw,  # type: ignore[arg-type]
    )


# --- the spread lean, in all four directions -------------------------------
# ``spread_home``/``fair_spread_home`` are positive when the HOME side is
# favored, which is a *negative* betting line for that side. Each case names
# the bet a reader would place off the label.

@pytest.mark.parametrize(("fair", "market", "label"), [
    # model likes the home favorite more than the board → lay the home number
    (7.0, 3.5, "DET -3.5"),
    # model likes the home dog more than the board → take the home dog's points
    (-1.0, -4.0, "DET +4"),
    # model likes the away side more, home is favored → take the away points
    (0.5, 3.5, "DAL +3.5"),
    # model likes the away side more, away is favored → lay the away number
    (-7.0, -3.5, "DAL -3.5"),
])
def test_spread_lean_states_the_side_at_its_own_number(
        fair: float, market: float, label: str) -> None:
    lean = _card(fair_spread_home=fair, spread_home=market).spread_lean()
    assert lean.fired
    assert lean.label == label


def test_spread_lean_holds_inside_the_bar() -> None:
    lean = _card(fair_spread_home=3.5 + SPREAD_EDGE_PTS - 0.1,
                 spread_home=3.5).spread_lean()
    assert not lean.fired
    assert lean.detail == "no edge"


def test_spread_lean_fires_exactly_at_the_bar() -> None:
    assert _card(fair_spread_home=3.5 + SPREAD_EDGE_PTS,
                 spread_home=3.5).spread_lean().fired


def test_spread_lean_without_a_market_says_so() -> None:
    lean = _card(spread_home=None).spread_lean()
    assert not lean.fired
    assert lean.detail == "no market"


def test_a_very_wide_gap_is_flagged_rather_than_amplified() -> None:
    # Our own graded record puts the worst closing-line value in the
    # highest-edge bucket, so the card marks the outlier instead of leaning in.
    lean = _card(fair_spread_home=3.5 + WIDE_GAP_PTS, spread_home=3.5).spread_lean()
    assert lean.fired and lean.wide


# --- the total lean --------------------------------------------------------

@pytest.mark.parametrize(("fair", "market", "label"), [
    (48.0 + TOTAL_EDGE_PTS, 48.0, "OVER 48"),
    (48.0 - TOTAL_EDGE_PTS, 48.0, "UNDER 48"),
])
def test_total_lean_names_the_side_at_the_posted_number(
        fair: float, market: float, label: str) -> None:
    lean = _card(fair_total=fair, total=market).total_lean()
    assert lean.fired
    assert lean.label == label


def test_total_lean_holds_inside_the_bar() -> None:
    assert not _card(fair_total=48.0 + TOTAL_EDGE_PTS - 0.1, total=48.0).total_lean().fired


# --- the printed lines -----------------------------------------------------

@pytest.mark.parametrize(("fair", "label"), [
    (8.0, "DET -8.0"),
    (-8.0, "DAL -8.0"),
    (0.0, "PK"),
])
def test_spread_label_is_team_anchored(fair: float, label: str) -> None:
    assert _card(fair_spread_home=fair).spread_label() == label


@pytest.mark.parametrize(("market", "label"), [
    (3.5, "DET -3.5"),
    (-3.5, "DAL -3.5"),
    (0.0, "PK"),
    (None, "—"),
])
def test_market_spread_label_is_team_anchored(market: float | None, label: str) -> None:
    assert _card(spread_home=market).market_spread_label() == label


def test_stamp_is_present_when_generated_at_is() -> None:
    card = _card(generated_at=pd.Timestamp("2026-09-16 22:44"))
    assert card.stamp() == "GENERATED 16 SEP 2026 · 22:44 UTC"
    assert _card().stamp() == ""


# --- the small value objects ----------------------------------------------

@pytest.mark.parametrize(("game", "label"), [
    (FormGame("W", 24, 21, "PHI", True), "vsPHI 24-21"),
    (FormGame("L", 16, 33, "LV", False), "@LV 16-33"),
])
def test_form_game_label(game: FormGame, label: str) -> None:
    assert game.label() == label


def test_unit_rank_position_spans_best_to_worst() -> None:
    assert UnitRank(1, 32, 0.0).position() == 0.0
    assert UnitRank(32, 32, 0.0).position() == 1.0
    assert UnitRank(1, 1, 0.0).position() == 0.0  # a one-team league never divides by 0


# --- render smokes ---------------------------------------------------------

def _pmf(mu: float, sd: float) -> dict[int, float]:
    rng = np.random.default_rng(7)
    draws = rng.normal(mu, sd, 20_000).round().astype(int)
    counts = np.bincount(draws - draws.min())
    return {int(draws.min() + i): float(c) / len(draws) for i, c in enumerate(counts)}


def _full_card() -> MatchupCard:
    form = (FormGame("W", 24, 21, "KC", True), FormGame("L", 17, 27, "PHI", False),
            FormGame("W", 31, 28, "NYG", True))
    ranks = {"off": UnitRank(9, 32, 0.07), "def": UnitRank(30, 32, 0.10),
             "off_pass": UnitRank(6, 32, 0.15), "def_rush": UnitRank(27, 32, 0.01)}
    players = (PlayerLine("QB", "Dak Prescott", "PASS YDS", "271", "PASS TD", "1.8"),
               PlayerLine("RB", "Javonte Williams", "RUSH YDS", "68", "TOT TD", "0.6"),
               PlayerLine("WR", "CeeDee Lamb", "REC YDS", "88", "REC", "6.9"),
               PlayerLine("WR", "George Pickens", "REC YDS", "71", "REC", "4.8"))
    side = {"last3": form, "ranks": ranks, "projections": players}
    return _card(
        away=TeamSide(code="DAL", city="Dallas", nickname="Cowboys", record="6-5-1",
                      color="#003594", **side),
        home=TeamSide(code="DET", city="Detroit", nickname="Lions", record="7-5",
                      color="#0076B6", **side),
        fair_spread_home=6.3, fair_total=51.0,
        margin_pmf=_pmf(6.3, 13.0), total_pmf=_pmf(51.0, 10.0),
        n_sims=20_000, venue="Ford Field",
        kickoff=pd.Timestamp("2025-12-07 13:00"),
        generated_at=pd.Timestamp("2026-09-16 22:44"),
        notes=("Detroit's rush defense meets a Dallas ground game that has "
               "cleared 100 yards twice in six weeks.",),
        confidence={"spread": 6.4, "total": 7.1},
    )


def test_render_lands_a_png_of_the_right_frame(tmp_path: Path) -> None:
    out = render_matchup_card(_full_card(), tmp_path / "card.png")
    assert out.exists()
    assert Image.open(out).size == (WIDTH, HEIGHT)


def test_render_survives_a_card_with_nothing_optional(tmp_path: Path) -> None:
    # No pmf, no ranks, no players, no market, no logo cache: a card never
    # fails to render because a nicety was unavailable.
    out = render_matchup_card(
        _card(spread_home=None, total=None), tmp_path / "bare.png")
    assert Image.open(out).size == (WIDTH, HEIGHT)


def test_matchup_filename_names_the_game() -> None:
    assert matchup_filename(_card(), "20260101T000000Z") == (
        "matchup_nfl_DAL_DET_20260101T000000Z.png")
