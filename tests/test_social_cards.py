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


def test_edges_fire_only_where_a_rule_with_a_record_admits_the_number() -> None:
    # Market has KC -6.5 / 47.5; the model says KC -2.0 and 46.0. The spread
    # gap is 4.5 points — the old fixed 2.5-point bar would have leaned BUF
    # +6.5 — but the lab measured no edge on spreads or the moneyline at any
    # bar, so those cells show the model's number and say why they are blank.
    view = MarketView(spread_home=-6.5, total=47.5, ml_away=125, ml_home=-145)
    edges = _card(market_view=view).edges()
    assert not edges["spread"].fired
    assert edges["spread"].detail == "no rule with a record"
    assert not edges["win"].fired
    assert edges["win"].detail == "no rule with a record"
    # Total: under by 1.5, short of the NFL rule's 4-point bar.
    assert not edges["total"].fired
    assert edges["total"].detail == "under by 1.5 · below the 4 bar"

    # Totals lean where the rule admits the gap, and carry its record. The
    # NFL over side has no row since the level round's ledger (overs 4+ read
    # 49.8%), so an over gap says why it is blank, as college's always has.
    hot = _card(fair_total=52.0, p_home_win=0.75, market_view=view).edges()
    assert not hot["total"].fired and hot["total"].detail == "no rule for overs"
    assert hot["total"].rule is None and hot["total"].points == 4.5
    assert not hot["win"].fired  # 75% vs a ~58% implied: still no rule
    cold = _card(fair_total=40.0, p_home_win=0.40, market_view=view).edges()
    assert cold["total"].fired and cold["total"].label == "UNDER 47.5"
    assert cold["total"].detail == "under by 7.5 · rule A · 56.2% on 299"
    assert cold["total"].rule is not None and cold["total"].rule.tier == "A"
    # The caption carries the rule's full record beside the lean.
    text = caption(_card(fair_total=40.0, p_home_win=0.40, market_view=view))
    assert ("Model lean: UNDER 47.5 (by 7.5 · rule A: "
            "unders 4+ 56.2% over 299 bets, 11 of 15 seasons).") in text


def test_college_edges_take_unders_only_and_tier_the_widest() -> None:
    # The college table: unders 8+ (A, 57.2%) and 4+ (B, 53.6%), no overs.
    view = MarketView(total=60.5)
    wide = _card(fair_total=51.0, market_view=view, league="ncaaf").edges()["total"]
    assert wide.fired and wide.label == "UNDER 60.5"
    assert wide.detail == "under by 9.5 · rule A · 57.2% on 297"
    narrow = _card(fair_total=55.5, market_view=view, league="ncaaf").edges()["total"]
    assert narrow.fired and narrow.detail == "under by 5.0 · rule B · 53.6% on 1263"
    over = _card(fair_total=70.0, market_view=view, league="ncaaf").edges()["total"]
    assert not over.fired and over.detail == "no rule for overs"
    # A league without a table at all: every cell says so.
    none = _card(fair_total=70.0, market_view=view, league="mlb").edges()["total"]
    assert not none.fired and none.detail == "no rule with a record"
    # build_social_cards threads the league onto the card.
    card = build_social_cards({"g1": _projection()}, EVENTS, league="ncaaf")[0]
    assert card.league == "ncaaf"


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
    """Four kinds now: the game's total and margin, and each side's score.

    The per-team scores were added for team totals (docs/DECISIONS.md D2) —
    without them nothing downstream can price that market off the sim rather
    than off a normal standing in for it. Consumers look kinds up by key
    (the Sim Check reads only total and margin), so the addition is safe.
    """
    frame = distributions_frame({"g1": _projection()})
    kinds = set(frame["kind"])
    assert kinds == {"margin", "total", "home_score", "away_score"}
    sums = frame.groupby(["game_id", "kind"])["prob"].sum()
    assert sums.to_numpy() == pytest.approx([1.0] * len(kinds))


def test_the_sim_check_still_finds_the_kinds_it_reads() -> None:
    """The Sim Check keys by kind, so new kinds must not disturb it."""
    from velocity.report.sim_check import _pmfs_by_game

    pmfs = _pmfs_by_game(distributions_frame({"g1": _projection()}))
    assert pmfs.get(("g1", "total"))
    assert pmfs.get(("g1", "margin"))


# --- NCAAF identity (logo-free) -----------------------------------------------


def test_parse_ncaaf_teams_yields_abbrevs_and_colors() -> None:
    from velocity.report.assets import parse_ncaaf_teams

    payload = [
        {"school": "Georgia", "abbreviation": "UGA", "color": "#BA0C2F",
         "altColor": "#000000"},
        {"school": "Alabama", "abbreviation": "ALA", "color": "9E1B32"},
        {"school": "Coastal Carolina", "abbreviation": None, "color": "notahex"},
        {"school": None, "abbreviation": "XX"},  # dropped: no school
    ]
    index = parse_ncaaf_teams(payload)
    assert index["Georgia"].abbreviation == "UGA"
    assert index["Georgia"].color == "#BA0C2F"
    assert index["Alabama"].color == "#9E1B32"  # bare hex normalized
    assert index["Coastal Carolina"].abbreviation == "COASTAL CAROLINA"
    assert index["Coastal Carolina"].color is None  # junk color dropped
    assert len(index) == 3


def test_bar_colors_separates_similar_brands_and_defaults() -> None:
    from velocity.report.assets import bar_colors

    crimson_a, crimson_b = bar_colors("#BA0C2F", "#9E1B32")  # red-vs-red
    assert crimson_a != crimson_b
    neutral = bar_colors(None, None)
    assert neutral == ("#d97706", "#0d9488")  # the fallback pair, distinct


def test_build_social_cards_carries_school_identity() -> None:
    events = pd.DataFrame({
        "game_id": ["g2"],
        "away_team": ["Georgia Bulldogs"],
        "home_team": ["Alabama Crimson Tide"],
        "kickoff": pd.to_datetime(["2026-09-05 23:30"]),
    })
    proj = _projection()
    cards = build_social_cards(
        {"g2": proj}, events,
        aliases={"Georgia Bulldogs": "UGA", "Alabama Crimson Tide": "ALA"},
        team_colors={"UGA": "#BA0C2F", "ALA": "#9E1B32"},
    )
    card = cards[0]
    assert (card.away_code, card.home_code) == ("UGA", "ALA")
    assert (card.away_color, card.home_color) == ("#BA0C2F", "#9E1B32")


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


def test_render_ncaaf_card_without_logos(tmp_path: Path) -> None:
    """School identity: colors + labels only — and long fallback names still fit."""
    from velocity.report.social_png import render_cards

    events = pd.DataFrame({
        "game_id": ["g2"],
        "away_team": ["Georgia Southern Eagles"],  # no alias → long fallback label
        "home_team": ["Alabama Crimson Tide"],
        "kickoff": pd.to_datetime(["2026-09-05 23:30"]),
    })
    cards = build_social_cards(
        {"g2": _projection()}, events,
        aliases={"Alabama Crimson Tide": "ALA"},
        team_colors={"ALA": "#9E1B32"},
    )
    assert cards[0].away_code == "Georgia Southern Eagles"  # honest fallback
    paths = render_cards(cards, tmp_path, "20260905T120000Z", league="ncaaf")
    assert paths and paths[0].stat().st_size > 20_000
    assert paths[0].name.startswith("social_ncaaf_")


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


def test_cards_carry_play_calls_and_caption_states_them() -> None:
    from velocity.report.social import PlayCall, caption
    from velocity.wagering.tiers import RULE_TIERS

    spread = PlayCall("spread", "home", -2.5, -110, "dk", 2.0, tier="A")
    total = PlayCall("total", "under", 47.5, 105, "fd", 0.5)
    cards = build_social_cards(
        {"g1": _projection()}, EVENTS,
        plays_by_game={"g1": (spread, total)},
    )
    assert cards[0].plays == (spread, total)
    text = caption(cards[0])
    assert "The play: KC -2.5 · -110 (dk) · 2.0u · tier A; UNDER 47.5" in text
    # A play that carries its rule states the rule's record in the caption
    # (the deep dive's band, one line per play, leaves it to the WHY text).
    ruled = PlayCall("total", "under", 47.5, 105, "fd", 0.5, tier="A",
                     rule=RULE_TIERS["nfl"][0])
    assert ruled.label("BUF", "KC") == (
        "UNDER 47.5 · +105 (fd) · 0.5u · tier A · unders 4+ 56.2% over 299 bets, "
        "11 of 15 seasons")
    assert ruled.label("BUF", "KC", record=False) == "UNDER 47.5 · +105 (fd) · 0.5u · tier A"
    # A card with no staked plays keeps the plain lean grammar.
    bare = build_social_cards({"g1": _projection()}, EVENTS)[0]
    assert bare.plays == ()
    assert "The play:" not in caption(bare)


def test_play_call_positions() -> None:
    from velocity.report.social import PlayCall

    assert PlayCall("moneyline", "away", None, 140, "dk", 1.0).position(
        "BUF", "KC") == "BUF ML"
    assert PlayCall("spread", "home", -3.5, -110, "dk", 1.0).position(
        "BUF", "KC") == "KC -3.5"
    assert PlayCall("total", "over", 47.5, -108, "dk", 1.0).position(
        "BUF", "KC") == "OVER 47.5"
    assert PlayCall("team_total_away", "under", 21.5, -105, "dk", 1.0).position(
        "BUF", "KC") == "BUF TT UNDER 21.5"


def test_render_card_with_play_badges(tmp_path: Path) -> None:
    from velocity.report.social import PlayCall
    from velocity.report.social_png import render_cards

    plays = (
        PlayCall("spread", "home", -2.5, -182, "fanduel", 2.8, tier="A"),
        # A second spread play (the line-shopped middle) — the badge keeps
        # the higher stake; the deep dive band lists both.
        PlayCall("spread", "away", 2.5, -120, "bovada", 0.3, tier="B"),
        PlayCall("moneyline", "home", None, -145, "dk", 1.0),
    )
    cards = build_social_cards(
        {"g1": _projection()}, EVENTS,
        lines=pd.DataFrame([
            {"game_id": "g1", "market": "spread", "side": "home",
             "point": -2.5, "price": -110, "book": "dk",
             "timestamp": pd.Timestamp("2026-09-10")},
        ]),
        plays_by_game={"g1": plays},
    )
    paths = render_cards(cards, tmp_path, "20260910T120000Z", league="nfl")
    assert paths and paths[0].exists() and paths[0].stat().st_size > 10_000


def test_supplied_watch_entries_override_the_sim_path(tmp_path: Path) -> None:
    from velocity.report.social import WatchEntry, caption
    from velocity.report.social_png import render_cards

    entries = (
        WatchEntry(player="Tarik Skubal", market="pitcher_strikeouts",
                   line=7.5, p_over=0.38, mean=6.4, from_board=True,
                   play="UNDER · -115 · 1.4u"),
        WatchEntry(player="Ryan Pepiot", market="pitcher_strikeouts",
                   line=4.5, p_over=0.55, mean=4.9),
    )
    cards = build_social_cards(
        {"g1": _projection()}, EVENTS,
        props_by_game={"g1": _props()},  # sim path present but overridden
        key_to_name={"qb1": "Patrick Mahomes", "te1": "Travis Kelce"},
        watch_by_game={"g1": entries},
    )
    assert cards[0].watch == entries
    text = caption(cards[0])
    assert "PLAY — Tarik Skubal (UNDER · -115 · 1.4u):" in text
    assert "Ryan Pepiot: 55% to clear 4.5 Ks" in text
    # The strip renders the PLAY pill path without error.
    paths = render_cards(cards, tmp_path, "20260910T120000Z", league="mlb")
    assert paths and paths[0].stat().st_size > 10_000


def test_parse_ncaaf_teams_carries_the_mascot() -> None:
    """The mascot is the only reliable source for a college nickname.

    Splitting "Alabama Crimson Tide" on its last word gives "Alabama Crimson"
    and "Tide"; CFBD states the two fields separately, so the card takes them
    from the source rather than guessing.
    """
    from velocity.report.assets import parse_ncaaf_teams

    index = parse_ncaaf_teams([
        {"school": "Alabama", "abbreviation": "ALA", "mascot": "Crimson Tide"},
        {"school": "Notre Dame", "abbreviation": "ND", "mascot": "Fighting Irish"},
        {"school": "Rice", "abbreviation": "RICE", "mascot": "  "},  # blank → None
        {"school": "Navy", "abbreviation": "NAVY"},                  # absent → None
    ])
    assert index["Alabama"].mascot == "Crimson Tide"
    assert index["Notre Dame"].mascot == "Fighting Irish"
    assert index["Rice"].mascot is None
    assert index["Navy"].mascot is None
