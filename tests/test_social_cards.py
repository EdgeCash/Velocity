"""Football social cards — data build, sim check math, and render smokes.

The card data layer is pinned exactly (facts, market strip, watch selection,
distributions); the renderers get offline smokes (no asset cache → no network)
asserting a real PNG lands. Everything runs on synthetic projections, so the
suite stays deterministic and network-free.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from velocity.models.game_nfl import GameProjection
from velocity.models.props_football import FootballPropSim
from velocity.models.simulate import GameSim
from velocity.report.sim_check import (
    build_sim_checks,
    mid_percentile,
    ordinal,
    sim_check_caption,
)
from velocity.report.social import (
    MarketView,
    SocialCard,
    build_social_cards,
    caption,
    distributions_frame,
    market_strip,
    market_view,
)

RNG = np.random.default_rng(9)


def _projection(mu_home: float = 25.0, mu_away: float = 21.0) -> GameProjection:
    n = 4000
    home = np.maximum(RNG.normal(mu_home, 10.0, n).round(), 0.0)
    away = np.maximum(RNG.normal(mu_away, 10.0, n).round(), 0.0)
    return GameProjection("KC", "BUF", mu_home, mu_away, GameSim(home, away))


EVENTS = pd.DataFrame({
    "game_id": ["g1"],
    "away_team": ["Buffalo Bills"],
    "home_team": ["Kansas City Chiefs"],
    "kickoff": pd.to_datetime(["2026-09-11 00:20"]),
})


def _props() -> FootballPropSim:
    return FootballPropSim({
        ("qb1", "pass_yards"): np.array([300.0] * 70 + [200.0] * 30),
        ("te1", "receptions"): np.array([8.0] * 60 + [5.0] * 40),
    })


def _prop_lines() -> pd.DataFrame:
    return pd.DataFrame([
        {"game_id": "g1", "market": "pass_yards", "player": "Patrick Mahomes",
         "side": "over", "point": 249.5, "price": -110, "book": "dk",
         "timestamp": pd.Timestamp("2026-09-10"), "is_closing": False,
         "line_id": "x1"},
    ])


def test_build_social_cards_carries_the_model_facts() -> None:
    proj = _projection()
    cards = build_social_cards(
        {"g1": proj}, EVENTS,
        props_by_game={"g1": _props()},
        key_to_name={"qb1": "Patrick Mahomes", "te1": "Travis Kelce"},
        prop_lines=_prop_lines(),
        record_line="SEASON 3-1 · +2.1U",
    )
    assert len(cards) == 1
    card = cards[0]
    assert (card.away_code, card.home_code) == ("BUF", "KC")
    assert card.p_home_win == pytest.approx(proj.p_home_win())
    assert card.fair_total == pytest.approx(proj.fair_total())
    assert card.n_sims == 4000
    assert sum(card.total_points_pmf.values()) == pytest.approx(1.0)
    assert card.record_line == "SEASON 3-1 · +2.1U"
    # Watch strip: Mahomes rides the board line (marked from_board), Kelce the
    # model's own median-anchored line.
    by_player = {w.player: w for w in card.watch}
    assert by_player["Patrick Mahomes"].from_board
    assert by_player["Patrick Mahomes"].line == 249.5
    assert by_player["Patrick Mahomes"].p_over == pytest.approx(0.7)
    assert not by_player["Travis Kelce"].from_board


def test_spread_label_anchors_the_favorite() -> None:
    card = SocialCard(
        game_id="g", away_name="A", home_name="H", away_code="BUF", home_code="KC",
        kickoff=None, p_home_win=0.6, mu_away=21.0, mu_home=25.0,
        fair_spread=-3.5, fair_total=46.0, total_points_pmf={46: 1.0},
    )
    assert card.spread_label() == "KC -3.5"
    dog = SocialCard(
        game_id="g", away_name="A", home_name="H", away_code="BUF", home_code="KC",
        kickoff=None, p_home_win=0.4, mu_away=25.0, mu_home=21.0,
        fair_spread=3.5, fair_total=46.0, total_points_pmf={46: 1.0},
    )
    assert dog.spread_label() == "BUF -3.5"


def test_market_strip_condenses_the_board() -> None:
    lines = pd.DataFrame([
        {"game_id": "g1", "market": "moneyline", "side": "away", "price": 125, "point": None},
        {"game_id": "g1", "market": "moneyline", "side": "home", "price": -145, "point": None},
        {"game_id": "g1", "market": "spread", "side": "home", "price": -110, "point": -2.5},
        {"game_id": "g1", "market": "total", "side": "over", "price": -105, "point": 47.5},
    ])
    strip = market_strip(lines, "g1", "BUF", "KC")
    assert strip == "BUF +125 · KC -145 · KC -2.5 · O/U 47.5"
    assert market_strip(lines, "gX", "A", "B") is None


def _card(**overrides: object) -> SocialCard:
    base: dict = {
        "game_id": "g", "away_name": "A", "home_name": "H", "away_code": "BUF",
        "home_code": "KC", "kickoff": None, "p_home_win": 0.60, "mu_away": 21.0,
        "mu_home": 25.0, "fair_spread": -2.0, "fair_total": 46.0,
        "total_points_pmf": {46: 1.0},
    }
    base.update(overrides)
    return SocialCard(**base)


def test_market_view_condenses_the_board_to_numerics() -> None:
    lines = pd.DataFrame([
        {"game_id": "g1", "market": "moneyline", "side": "away", "price": 125,
         "point": None, "book": "a", "timestamp": pd.Timestamp("2026-09-10 12:00")},
        {"game_id": "g1", "market": "moneyline", "side": "home", "price": -145,
         "point": None, "book": "b", "timestamp": pd.Timestamp("2026-09-10 13:00")},
        {"game_id": "g1", "market": "spread", "side": "home", "price": -110,
         "point": -2.5, "book": "a", "timestamp": pd.Timestamp("2026-09-10 12:00")},
        {"game_id": "g1", "market": "total", "side": "over", "price": -105,
         "point": 47.5, "book": "a", "timestamp": pd.Timestamp("2026-09-10 12:00")},
    ])
    view = market_view(lines, "g1")
    assert view is not None
    assert view.spread_home == -2.5
    assert view.total == 47.5
    assert (view.ml_away, view.ml_home) == (125, -145)
    assert view.books == 2
    assert view.captured == pd.Timestamp("2026-09-10 13:00")
    implied = view.implied_home_prob()
    assert implied is not None and 0.55 < implied < 0.62  # de-vigged, not raw
    assert market_view(lines, "gX") is None
    assert market_view(None, "g1") is None


def test_edges_fire_only_past_the_published_thresholds() -> None:
    # Market has KC -6.5; model says KC -2.0 → 4.5-pt disagreement, lean BUF +6.5.
    view = MarketView(spread_home=-6.5, total=47.5, ml_away=125, ml_home=-145)
    edges = _card(market_view=view).edges()
    assert edges["spread"].fired
    assert edges["spread"].label == "BUF +6.5"
    assert "4.5" in edges["spread"].detail
    # Total diff 1.5 < 3.0 and win diff < 7 pts → honest no-edge cells.
    assert not edges["total"].fired and edges["total"].detail == "no edge"
    assert not edges["win"].fired

    # The other spread direction: model likes home MORE than the market does.
    strong_home = _card(fair_spread=-10.0, market_view=view).edges()
    assert strong_home["spread"].label == "KC -6.5"
    # Total and moneyline leans at magnitude.
    hot = _card(fair_total=52.0, p_home_win=0.75, market_view=view).edges()
    assert hot["total"].fired and hot["total"].label == "OVER 47.5"
    assert hot["win"].fired and hot["win"].label == "KC ML"
    cold = _card(fair_total=40.0, p_home_win=0.40, market_view=view).edges()
    assert cold["total"].label == "UNDER 47.5"
    assert cold["win"].label == "BUF ML"


def test_edges_without_a_board_never_claim_anything() -> None:
    edges = _card().edges()
    assert all(not call.fired and call.detail == "—" for call in edges.values())


def test_caption_states_facts_without_odds() -> None:
    proj = _projection()
    card = build_social_cards({"g1": proj}, EVENTS)[0]
    text = caption(card)
    assert "BUF @ KC" in text
    assert "%" in text and "fair total" in text
    assert "bet" not in text.lower()  # facts, never imperatives


def test_distributions_frame_sums_to_one_per_kind() -> None:
    frame = distributions_frame({"g1": _projection()})
    sums = frame.groupby(["game_id", "kind"])["prob"].sum()
    assert sums.to_numpy() == pytest.approx([1.0, 1.0])
    assert set(frame["kind"]) == {"margin", "total"}


# --- sim check ----------------------------------------------------------------


def test_mid_percentile_and_ordinal() -> None:
    pmf = {40: 0.25, 47: 0.5, 54: 0.25}
    assert mid_percentile(pmf, 47) == pytest.approx(0.5)
    assert mid_percentile(pmf, 60) == pytest.approx(1.0)
    assert ordinal(0.96) == "96th"
    assert ordinal(0.51) == "51st"
    assert ordinal(0.0) == "1st"  # never overstate certainty


def test_build_sim_checks_places_the_actual_result() -> None:
    projections = pd.DataFrame([{
        "game_id": "g1", "away": "BUF", "home": "KC", "n_sims": 4000,
        "mu_away": 21.0, "mu_home": 25.0, "p_home_win": 0.62,
        "fair_spread": -3.5, "fair_total": 46.0,
    }])
    distributions = distributions_frame({"g1": _projection()})
    finals = pd.DataFrame([{"game_id": "g1", "home_score": 27.0, "away_score": 20.0}])
    cards = build_sim_checks(projections, distributions, finals, EVENTS)
    assert len(cards) == 1
    card = cards[0]
    assert card.winner_code == "KC"
    assert card.p_winner_pregame == pytest.approx(0.62)
    assert card.actual_total == 47
    assert 0.0 < card.total_percentile < 1.0
    assert "combined points" in sim_check_caption(card)


# --- render smokes (offline: no asset dir → no network) -----------------------


def test_render_model_card_writes_a_png(tmp_path: Path) -> None:
    from velocity.report.social_png import render_cards

    lines = pd.DataFrame([
        {"game_id": "g1", "market": "moneyline", "side": "away", "price": 125,
         "point": None, "book": "a", "timestamp": pd.Timestamp("2026-09-10 12:00")},
        {"game_id": "g1", "market": "moneyline", "side": "home", "price": -145,
         "point": None, "book": "a", "timestamp": pd.Timestamp("2026-09-10 12:00")},
        {"game_id": "g1", "market": "spread", "side": "home", "price": -110,
         "point": -7.5, "book": "a", "timestamp": pd.Timestamp("2026-09-10 12:00")},
        {"game_id": "g1", "market": "total", "side": "over", "price": -105,
         "point": 47.5, "book": "a", "timestamp": pd.Timestamp("2026-09-10 12:00")},
    ])
    cards = build_social_cards(
        {"g1": _projection()}, EVENTS,
        props_by_game={"g1": _props()},
        key_to_name={"qb1": "Patrick Mahomes", "te1": "Travis Kelce"},
        lines=lines,
    )
    assert cards[0].market_view is not None  # the matrix has a MARKET row
    paths = render_cards(cards, tmp_path, "20260910T120000Z", league="nfl")
    assert len(paths) == 1
    assert paths[0].name == "social_nfl_20260910T120000Z_BUF_at_KC.png"
    assert paths[0].stat().st_size > 20_000  # a real rendered frame, not a stub
    captions = tmp_path / "social_nfl_20260910T120000Z_captions.md"
    assert "BUF @ KC" in captions.read_text()


def test_render_sim_check_and_record_card(tmp_path: Path) -> None:
    from velocity.report.social_png import render_record_card, render_sim_checks

    projections = pd.DataFrame([{
        "game_id": "g1", "away": "BUF", "home": "KC", "n_sims": 4000,
        "mu_away": 21.0, "mu_home": 25.0, "p_home_win": 0.62,
        "fair_spread": -3.5, "fair_total": 46.0,
    }])
    distributions = distributions_frame({"g1": _projection()})
    finals = pd.DataFrame([{"game_id": "g1", "home_score": 27.0, "away_score": 20.0}])
    checks = build_sim_checks(projections, distributions, finals, EVENTS)
    paths = render_sim_checks(checks, tmp_path, "20260911T120000Z", league="nfl")
    assert paths and paths[0].stat().st_size > 20_000

    record = pd.DataFrame([
        {"section": "games", "play": "BUF @ KC", "market": "total", "side": "over",
         "point": 47.5, "price": -110, "stake": 2.0, "result": "win",
         "profit": 1.82, "slate_date": pd.Timestamp("2026-09-10")},
    ])
    dest = tmp_path / "recordcard_nfl_20260911T120000Z.png"
    render_record_card(record, record, dest, date_label="Sep 10")
    assert dest.stat().st_size > 20_000
