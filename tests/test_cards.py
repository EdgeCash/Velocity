"""Per-game matchup cards (velocity.report.cards + card_html) — pure, offline.

Covers the one bit of new logic — the per-market PLAY / LEAN / PASS grading off
the de-vigged edge — plus card assembly (joining projections, lines, and context
by team code) and that the HTML renderer emits a self-contained page with the
official-CDN logo/headshot URLs and a graceful fallback when an id is missing.
"""

from __future__ import annotations

import pandas as pd
from velocity.ingest.mlb_context import GameContext, PitcherContext, TeamContext
from velocity.report.card_html import (
    headshot_url,
    render_cards_page,
    team_logo_url,
    write_cards_html,
)
from velocity.report.cards import build_cards, recommend_for_game

EVENTS = pd.DataFrame({
    "game_id": ["g1"],
    "away_team": ["San Francisco Giants"],
    "home_team": ["Los Angeles Dodgers"],
    "kickoff": pd.to_datetime(["2026-07-24T02:10:00"]),
})


class _FakeProj:
    def __init__(self, ph: float, cover: float, over: float) -> None:
        self.mu_away, self.mu_home = 3.9, 5.1
        self._ph, self._cover, self._over = ph, cover, over

    def p_home_win(self) -> float:
        return self._ph

    def prob_home_cover(self, _: float) -> float:
        return self._cover

    def prob_over(self, _: float) -> float:
        return self._over

    def fair_spread(self) -> float:
        return -1.0


def _lines() -> pd.DataFrame:
    return pd.DataFrame({
        "game_id": ["g1"] * 6,
        "market": ["moneyline", "moneyline", "spread", "spread", "total", "total"],
        "side": ["home", "away", "home", "away", "over", "under"],
        "point": [None, None, -1.5, 1.5, 8.5, 8.5],
        "price": [-150, 130, 130, -150, -110, -105],
    })


def _context() -> GameContext:
    return GameContext(
        game_pk="745804",
        away=TeamContext(team_id="137", name="San Francisco Giants", record="58-44"),
        home=TeamContext(team_id="119", name="Los Angeles Dodgers", record="64-38"),
        away_sp=PitcherContext(player_id="592791", name="Logan Webb", hand="R",
                               line="9-6 · 2.94 ERA"),
        home_sp=PitcherContext(player_id="808967", name="Yoshinobu Yamamoto", hand="R",
                               line="11-3 · 2.51 ERA"),
    )


# --- grading ----------------------------------------------------------------


def test_strong_edge_grades_play() -> None:
    # Model 68% home ML vs a de-vig near 58% → edge ~0.10 → conf ~9.x → PLAY.
    recs = recommend_for_game(_FakeProj(0.68, 0.5, 0.5), _lines(), "LAD", "SF")
    ml = next(r for r in recs if r["label"] == "MONEYLINE")
    assert ml["pick"].startswith("LAD")
    assert ml["call"] == "PLAY"
    assert ml["conf"] >= 8


def test_no_edge_grades_pass() -> None:
    # Model agrees with the market → no positive edge either side → PASS.
    recs = recommend_for_game(_FakeProj(0.58, 0.5, 0.5), _lines(), "LAD", "SF")
    total = next(r for r in recs if r["label"] == "TOTAL")
    assert total["call"] == "PASS"
    assert total["conf"] < 4


def test_missing_market_passes_cleanly() -> None:
    ml_only = _lines()[_lines()["market"] == "moneyline"]
    recs = recommend_for_game(_FakeProj(0.6, 0.5, 0.5), ml_only, "LAD", "SF")
    labels = {r["label"]: r for r in recs}
    assert labels["RUN LINE"]["call"] == "PASS"
    assert labels["TOTAL"]["call"] == "PASS"


# --- assembly ---------------------------------------------------------------


def test_build_cards_joins_context_and_projection() -> None:
    cards = build_cards(EVENTS, {"g1": _FakeProj(0.6, 0.5, 0.5)}, _lines(), [_context()])
    assert len(cards) == 1
    card = cards[0]
    assert card["away"]["code"] == "SF" and card["home"]["code"] == "LAD"
    assert card["home"]["record"] == "64-38"
    assert card["home"]["logo_id"] == "119"
    assert card["home_sp"]["name"] == "Yoshinobu Yamamoto"
    assert card["proj"]["away_runs"] == 3.9
    assert card["proj"]["home_win"] == 60
    assert len(card["recs"]) == 3


def test_build_cards_without_context_uses_tbd() -> None:
    cards = build_cards(EVENTS, {"g1": _FakeProj(0.6, 0.5, 0.5)}, _lines())
    card = cards[0]
    assert card["home"]["logo_id"] is None
    assert card["home"]["record"] is None
    assert card["home_sp"]["name"] == "TBD"


def test_unprojected_game_is_skipped() -> None:
    assert build_cards(EVENTS, {}, _lines()) == []


# --- rendering --------------------------------------------------------------


def test_cdn_urls() -> None:
    assert team_logo_url("119") == "https://www.mlbstatic.com/team-logos/119.svg"
    assert headshot_url("808967").endswith("/people/808967/spots/120")
    assert team_logo_url(None) is None
    assert headshot_url(None) is None


def test_render_page_is_self_contained() -> None:
    cards = build_cards(EVENTS, {"g1": _FakeProj(0.6, 0.5, 0.5)}, _lines(), [_context()])
    html = render_cards_page(cards, "mlb", "2026-07-24 00:00:00")
    assert html.startswith("<!doctype html>")
    assert "team-logos/119.svg" in html
    assert "people/808967/spots/120" in html
    assert "Yoshinobu Yamamoto" in html
    # No external CSS/JS — the style is inlined.
    assert "http-equiv" not in html.lower()
    assert "<script" not in html.lower()


def test_missing_ids_render_without_images() -> None:
    proj = {"g1": _FakeProj(0.6, 0.5, 0.5)}
    html = render_cards_page(build_cards(EVENTS, proj, _lines()), "mlb", "now")
    # No context → no logo/headshot imgs, but the card (and TBD starter) still render.
    assert "<img" not in html
    assert "TBD" in html


def test_empty_slate_renders_placeholder() -> None:
    html = render_cards_page([], "mlb", "now")
    assert "No games on the board" in html


def test_write_cards_html(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cards = build_cards(EVENTS, {"g1": _FakeProj(0.6, 0.5, 0.5)}, _lines(), [_context()])
    dest = tmp_path / "cards.html"
    out = write_cards_html(dest, cards, league="mlb", generated_at="now")
    assert out == dest and dest.exists()
    assert "team-logos/119.svg" in dest.read_text()
