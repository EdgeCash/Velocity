"""Per-game matchup cards (velocity.report.cards + card_html) — pure, offline.

Covers the one bit of new logic — the per-market PLAY / LEAN / PASS grading off
the de-vigged edge — plus card assembly (joining projections, lines, and context
by team code) and that the HTML renderer emits a self-contained page with the
official-CDN logo/headshot URLs and a graceful fallback when an id is missing.
"""

from __future__ import annotations

import pandas as pd
from velocity.ingest.mlb_advanced import TeamAdvanced
from velocity.ingest.mlb_context import GameContext, PitcherContext, TeamContext
from velocity.ingest.mlb_stats import TeamHitting, TeamPitching, TeamSplits
from velocity.ingest.mlb_weather import Weather
from velocity.report.card_html import (
    headshot_url,
    render_cards_page,
    team_logo_url,
    write_cards_html,
)
from velocity.report.cards import GridSources, build_cards, recommend_for_game

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


# --- stat grid + conditions -------------------------------------------------


def _grid_sources() -> GridSources:
    return GridSources(
        # keyed by StatsAPI team id (137 = SF away, 119 = LAD home)
        hitting=(
            TeamHitting("137", "SF", ops=0.700, avg=0.244, obp=0.312, slg=0.388,
                        runs_per_game=4.0, home_runs=120, k_pct=0.20, bb_pct=0.10),
            TeamHitting("119", "LAD", ops=0.780, avg=0.262, obp=0.338, slg=0.442,
                        runs_per_game=5.0, home_runs=180, k_pct=0.20, bb_pct=0.10),
        ),
        pitching=(
            TeamPitching("137", "SF", era=4.00, whip=1.25, k_per_9=8.0,
                         runs_allowed_per_game=4.2),
            TeamPitching("119", "LAD", era=3.50, whip=1.10, k_per_9=9.5,
                         runs_allowed_per_game=3.8),
        ),
        # keyed by card code
        splits={"LAD": TeamSplits(vs_lhp_ops=0.760, vs_rhp_ops=0.800,
                                  last_n=15, last_n_runs_per_game=5.47)},
        advanced={"LAD": TeamAdvanced(wrc_plus=118, xfip=3.65, barrel_pct=9.8, xwoba=0.335)},
        weather={"g1": Weather(temp_f=83, wind_mph=9, wind_dir="NW", precip_pct=20, roof="open")},
    )


def test_build_cards_attaches_grid_and_conditions() -> None:
    cards = build_cards(EVENTS, {"g1": _FakeProj(0.6, 0.5, 0.5)}, _lines(),
                        [_context()], grid=_grid_sources())
    card = cards[0]
    home = card["grid"]["home"]
    assert home["bat"]["ops"] == 0.780
    assert home["bat"]["ops_rank"] == 1  # LAD .780 ranks above SF .700
    assert home["bat"]["wrc_plus"] == 118  # from advanced (by code)
    assert home["pit"]["era_rank"] == 1
    assert home["splits"]["vs_lhp_ops"] == 0.760
    away = card["grid"]["away"]
    assert away["bat"]["ops_rank"] == 2  # SF second of two
    # Conditions: weather (by game_id) + park factor (LAD home = Dodger Stadium).
    assert card["conditions"]["weather"]["temp_f"] == 83
    assert card["conditions"]["park"]["name"] == "Dodger Stadium"


def test_grid_renders_with_ranks_and_conditions() -> None:
    cards = build_cards(EVENTS, {"g1": _FakeProj(0.6, 0.5, 0.5)}, _lines(),
                        [_context()], grid=_grid_sources())
    html = render_cards_page(cards, "mlb", "now")
    assert "Team profile" in html
    assert "wRC+" in html and ".780" in html
    assert "#1" in html  # a rank badge rendered
    assert "Dodger Stadium" in html
    assert "Wind 9 NW" in html


def test_grid_absent_when_no_sources() -> None:
    cards = build_cards(EVENTS, {"g1": _FakeProj(0.6, 0.5, 0.5)}, _lines(), [_context()])
    card = cards[0]
    assert card["grid"]["home"] is None and card["grid"]["away"] is None
    html = render_cards_page(cards, "mlb", "now")
    assert "Team profile" not in html
    # Park factor still shows from the home code even with no StatsAPI grid.
    assert card["conditions"]["park"]["name"] == "Dodger Stadium"


def test_advanced_only_populates_without_context() -> None:
    """No context (no team ids) → StatsAPI grid can't join, but code-keyed advanced can."""
    sources = GridSources(
        advanced={"LAD": TeamAdvanced(wrc_plus=118)},
    )
    cards = build_cards(EVENTS, {"g1": _FakeProj(0.6, 0.5, 0.5)}, _lines(), grid=sources)
    home = cards[0]["grid"]["home"]
    assert home is not None and home["bat"]["wrc_plus"] == 118
    assert "bat" in home and "pit" not in home  # only the advanced metric survived


def test_indoors_park_shows_roof_closed() -> None:
    # Tampa Bay home → fixed roof.
    events = pd.DataFrame({
        "game_id": ["g9"], "away_team": ["New York Yankees"],
        "home_team": ["Tampa Bay Rays"],
        "kickoff": pd.to_datetime(["2026-07-24T23:10:00"]),
    })
    sources = GridSources(weather={"g9": Weather(roof="fixed")})
    cards = build_cards(events, {"g9": _FakeProj(0.6, 0.5, 0.5)},
                        pd.DataFrame(columns=["game_id", "market", "side", "point", "price"]),
                        grid=sources)
    html = render_cards_page(cards, "mlb", "now")
    assert "Roof closed" in html
