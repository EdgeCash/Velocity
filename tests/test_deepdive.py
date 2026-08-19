"""Deep Dive card — form math, rank/advantage logic, sim probabilities, render."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from velocity.models.game_nfl import GameProjection
from velocity.models.simulate import GameSim
from velocity.report.deepdive import (
    build_deep_dives,
    build_rows,
    deep_dive_caption,
    epa_form,
    scoring_form,
)
from velocity.report.social import MarketView, SocialCard

RNG = np.random.default_rng(11)


def _games() -> pd.DataFrame:
    rows = [
        # season, week, home, away, home_score, away_score
        (2025, 1, "KC", "BUF", 27, 24),
        (2025, 2, "BUF", "NYJ", 31, 10),
        (2025, 2, "KC", "NYJ", 20, 23),
        (2025, 3, "BUF", "KC", 20, 20),  # a tie
        (2024, 18, "KC", "BUF", 40, 3),  # old season: excluded from form
        (2025, 20, "DEN", "LV", None, None),  # unplayed: excluded
    ]
    return pd.DataFrame(rows, columns=["season", "week", "home_team", "away_team",
                                       "home_score", "away_score"])


def _plays() -> pd.DataFrame:
    rows = []
    # BUF offense strong on passes (+0.3), KC defense leaky (+0.2 allowed).
    for epa, pos, deft, kind in [
        (0.3, "BUF", "KC", "pass"), (0.3, "BUF", "KC", "pass"),
        (0.0, "BUF", "KC", "run"),
        (-0.1, "KC", "BUF", "pass"), (0.1, "KC", "BUF", "run"),
        (0.0, "NYJ", "BUF", "pass"), (-0.2, "NYJ", "KC", "run"),
        (9.9, "BUF", "KC", "punt"),  # non-scrimmage: excluded
    ]:
        rows.append({"season": 2025, "week": 1, "posteam": pos, "defteam": deft,
                     "play_type": kind, "epa": epa})
    return pd.DataFrame(rows)


def test_scoring_form_uses_newest_completed_season() -> None:
    season, form = scoring_form(_games())
    assert season == 2025
    kc = form.loc["KC"]
    # KC 2025: W 27-24, L 20-23, T 20-20 → 1-1-1, ppg (27+20+20)/3
    assert (int(kc["wins"]), int(kc["losses"]), int(kc["ties"])) == (1, 1, 1)
    assert kc["ppg"] == pytest.approx((27 + 20 + 20) / 3)
    assert kc["papg"] == pytest.approx((24 + 23 + 20) / 3)
    assert "LV" not in form.index  # unplayed games contribute nothing


def test_epa_form_splits_units_and_drops_special_teams() -> None:
    form = epa_form(_plays(), 2025)
    assert form.loc["BUF", "pass_off"] == pytest.approx(0.3)
    assert form.loc["BUF", "off_epa"] == pytest.approx((0.3 + 0.3 + 0.0) / 3)
    # KC's defense faced BUF's three plays and NYJ's -0.2 run.
    assert form.loc["KC", "def_epa"] == pytest.approx((0.3 + 0.3 + 0.0 - 0.2) / 4)
    # The 9.9-EPA punt never contaminates the averages.
    assert form["off_epa"].max() < 1.0


def test_build_rows_marks_only_clear_advantages() -> None:
    scoring = pd.DataFrame({
        "ppg": {"BUF": 30.0, "KC": 20.0, "NYJ": 21.0, "DEN": 22.0},
        "papg": {"BUF": 20.0, "KC": 20.4, "NYJ": 28.0, "DEN": 27.0},
    })
    rows = build_rows("BUF", "KC", scoring, None)
    ppg = next(r for r in rows if r.label == "POINTS / GM")
    assert ppg.advantage == "away"  # 30 vs 20 clears any threshold
    assert (ppg.away_rank, ppg.home_rank) == (1, 4)
    papg = next(r for r in rows if r.label == "POINTS ALLOWED / GM")
    assert papg.advantage is None  # 20.0 vs 20.4: a coin flip stays unmarked
    assert papg.away_rank == 1  # lower allowed = better rank


def _projection(mu_home: float = 24.0, mu_away: float = 21.0) -> GameProjection:
    n = 4000
    home = np.maximum(RNG.normal(mu_home, 10.0, n).round(), 0.0)
    away = np.maximum(RNG.normal(mu_away, 10.0, n).round(), 0.0)
    return GameProjection("KC", "BUF", mu_home, mu_away, GameSim(home, away))


def _card(**overrides: object) -> SocialCard:
    base: dict = {
        "game_id": "g1", "away_name": "Buffalo Bills",
        "home_name": "Kansas City Chiefs", "away_code": "BUF", "home_code": "KC",
        "kickoff": pd.Timestamp("2026-09-11 00:20"), "p_home_win": 0.58,
        "mu_away": 21.0, "mu_home": 24.0, "fair_spread": -3.0,
        "fair_total": 45.0, "total_points_pmf": {45: 1.0}, "n_sims": 4000,
        "market_view": MarketView(spread_home=-6.5, total=47.5),
    }
    base.update(overrides)
    return SocialCard(**base)


def test_build_deep_dives_computes_cover_and_over() -> None:
    proj = _projection()
    dives = build_deep_dives([_card()], {"g1": proj}, _games(), _plays())
    assert len(dives) == 1
    dive = dives[0]
    margin = proj.sim.margin
    assert dive.p_home_cover == pytest.approx(float(np.mean(margin > 6.5)))
    assert dive.p_over == pytest.approx(float(np.mean(proj.sim.total > 47.5)))
    assert sum(dive.margin_pmf.values()) == pytest.approx(1.0)
    assert dive.stat_season == 2025
    assert dive.away_record and dive.home_record
    assert len(dive.rows) == 8  # scoring + six EPA unit rows
    text = deep_dive_caption(dive)
    assert "covers in" in text and "sims" in text


def test_build_deep_dives_maps_display_codes_to_dataset_keys() -> None:
    card = _card(away_code="BUFF", home_code="KAN")
    dives = build_deep_dives(
        [card], {"g1": _projection()}, _games(), None,
        team_names={"BUFF": "BUF", "KAN": "KC"},
    )
    assert dives[0].away_record != ""  # resolved through the mapping
    assert len(dives[0].rows) == 2  # scoring rows only without plays


def test_render_deep_dive_writes_a_png(tmp_path: Path) -> None:
    from velocity.report.deepdive_png import render_deep_dives

    dives = build_deep_dives([_card()], {"g1": _projection()}, _games(), _plays())
    paths = render_deep_dives(dives, tmp_path, "20260910T120000Z", league="nfl")
    assert paths[0].name == "deepdive_nfl_20260910T120000Z_BUF_at_KC.png"
    assert paths[0].stat().st_size > 20_000
    captions = tmp_path / "deepdive_nfl_20260910T120000Z_captions.md"
    assert "BUF @ KC" in captions.read_text()


def test_team_form_last5_streak_and_rest() -> None:
    from velocity.report.deepdive import team_form

    games = _games().copy()
    games["kickoff"] = pd.to_datetime("2025-09-01") + pd.to_timedelta(
        games["week"] * 7, unit="D"
    )
    form = team_form(games, "KC", 2025, kickoff=pd.Timestamp("2025-09-24"))
    # KC 2025: W (vs BUF), L (vs NYJ), T (at BUF) — newest last.
    assert form["last5"] == ("W", "L", "T")
    assert form["streak"] == "T1"
    # Last game week 3 → Sep 22; kickoff Sep 24 → 2 days rest.
    assert form["rest"] == 2
    buf = team_form(games, "BUF", 2025)
    assert buf["last5"] == ("L", "W", "T") and buf["rest"] is None
    empty = team_form(games, "NOPE", 2025)
    assert empty["last5"] == () and empty["streak"] == ""


def test_probable_line_formats_the_banked_stats() -> None:
    from velocity.report.deepdive import probable_line

    starters = pd.DataFrame([
        {"game_id": "a", "starter_id": "99", "starter_name": "Paul Skenes",
         "outs": 18.0, "k": 8},
        {"game_id": "b", "starter_id": "99", "starter_name": "Paul Skenes",
         "outs": 20.0, "k": 9},
        {"game_id": "c", "starter_id": "77", "starter_name": "No Outs Guy",
         "outs": 0.0, "k": 0},
    ])
    line = probable_line(starters, "99")
    # 38 outs = 12.2 IP; 17 K over 38 outs = 12.08 K/9.
    assert line == "SKENES · 12.2 IP · 12.1 K/9"
    assert probable_line(starters, "77") is None  # no banked innings
    assert probable_line(starters, None) is None
    assert probable_line(None, "99") is None


def test_build_deep_dives_carries_form_and_probables(tmp_path: Path) -> None:
    games = _games().copy()
    games["kickoff"] = pd.to_datetime("2025-09-01") + pd.to_timedelta(
        games["week"] * 7, unit="D"
    )
    games["game_id"] = [f"g{i}" for i in range(len(games))]
    starters = pd.DataFrame([
        # g0 is a 2025 game; g4 is the 2024 game — the old season's start
        # must NOT count toward the probable's current-season line.
        {"game_id": "g0", "starter_id": "5", "starter_name": "Home Ace",
         "outs": 27.0, "k": 12},
        {"game_id": "g4", "starter_id": "5", "starter_name": "Home Ace",
         "outs": 27.0, "k": 3},
    ])
    probables = {("KC", "BUF", None): ("5", None)}  # (home SP, away SP)
    dives = build_deep_dives(
        [_card(kickoff=pd.Timestamp("2025-09-24"))], {"g1": _projection()},
        games, None, starters=starters, probables=probables,
    )
    dive = dives[0]
    assert dive.away_last5 and dive.home_last5
    assert dive.home_sp == "ACE · 9.0 IP · 12.0 K/9"
    assert dive.away_sp is None  # unannounced probable stays honest
    assert dive.away_rest is not None and dive.home_rest is not None
    # And the renderer accepts the new fields end-to-end.
    from velocity.report.deepdive_png import render_deep_dives

    paths = render_deep_dives(dives, tmp_path, "20250924T120000Z", league="mlb")
    assert paths[0].stat().st_size > 20_000
