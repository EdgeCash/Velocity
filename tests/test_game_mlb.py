"""MLB game model + wagering integration (Phase M4).

The full-game projection is a football ``GameProjection`` over the baseball sim,
so the existing slate engine prices the run line / total / moneyline unchanged.
These tests pin: prices are mutually consistent (all off one simulation), F5 is a
smaller game than the full nine, all 30 clubs resolve to rating keys, and a live
MLB snapshot flows end-to-end into a staked, CLV-logged bet.

The multi-season backtest in the M4 Definition of Done needs committed MLB
history (a network ingest), so it is an acceptance step, not part of this offline
gate — exactly as the football backtests are.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from velocity.features.baseball import DEFAULT_BIP_PRIOR, DEFAULT_PIT_PRIOR
from velocity.ingest.theoddsapi import extract_events, normalize_odds_events
from velocity.models.game_mlb import MLBGameModel
from velocity.models.simulate_baseball import (
    BaseballSimConfig,
    Team,
    batter_from_rates,
    pitcher_from_rates,
)
from velocity.wagering.live import MLB_TEAM_ALIASES, build_live_slate, resolve_team
from velocity.wagering.slate import SlateConfig

FIXTURES = Path(__file__).parent / "fixtures"

STRONG_BAT = {"k": 0.18, "bb": 0.10, "hbp": 0.01, "hr": 0.055, "in_play": 0.655}
WEAK_BAT = {"k": 0.27, "bb": 0.06, "hbp": 0.01, "hr": 0.020, "in_play": 0.640}
AVG_BAT = {"k": 0.225, "bb": 0.085, "hbp": 0.011, "hr": 0.035, "in_play": 0.644}


def _team(prefix: str, bat_rates: dict[str, float]) -> Team:
    lineup = [batter_from_rates(f"{prefix}{i}", bat_rates, DEFAULT_BIP_PRIOR) for i in range(9)]
    return Team(lineup=lineup, pitcher=pitcher_from_rates(f"{prefix}_p", DEFAULT_PIT_PRIOR))


def _model(n_sims: int = 3000) -> MLBGameModel:
    teams = {"LAD": _team("lad", STRONG_BAT), "SF": _team("sf", WEAK_BAT)}
    return MLBGameModel(teams=teams, config=BaseballSimConfig(n_sims=n_sims), seed=7)


def test_full_game_prices_are_mutually_consistent() -> None:
    proj = _model().project("LAD", "SF").full
    # All three markets come off one simulation, so they must agree:
    # complementary win probabilities,
    assert proj.p_home_win() + proj.p_away_win() == pytest.approx(1.0)
    # the run line at 0 is exactly the moneyline (baseball has no ties),
    assert proj.prob_home_cover(0.0) == pytest.approx(proj.p_home_win())
    # the fair total sits near a coin flip and the over is monotonic in the number.
    assert proj.prob_over(proj.fair_total()) == pytest.approx(0.5, abs=0.06)
    assert proj.prob_over(proj.fair_total() + 3.0) < proj.prob_over(proj.fair_total())


def test_strong_team_is_favored() -> None:
    proj = _model().project("LAD", "SF")
    # The stronger lineup wins more often and is projected for more runs.
    assert proj.p_home_win() > 0.5
    assert proj.full.mu_home > proj.full.mu_away


def test_f5_is_a_smaller_game_than_the_full_nine() -> None:
    proj = _model().project("LAD", "SF")
    assert proj.f5.fair_total() < proj.full.fair_total()
    # Five innings of runs cannot exceed nine, sim for sim.
    assert (proj.result.f5.home_score <= proj.result.full.home_score).all()


def test_team_total_probability_is_bounded() -> None:
    proj = _model().project("LAD", "SF")
    p = proj.prob_team_over("home", 4.5)
    assert 0.0 <= p <= 1.0


def test_all_thirty_clubs_resolve() -> None:
    codes = set(MLB_TEAM_ALIASES.values())
    assert len(codes) == 30
    for full_name in MLB_TEAM_ALIASES:
        assert resolve_team(full_name, codes, MLB_TEAM_ALIASES) is not None
    # A name outside the league still returns None rather than a wrong guess.
    assert resolve_team("Springfield Isotopes", codes, MLB_TEAM_ALIASES) is None


def test_end_to_end_live_slate_logs_a_staked_bet() -> None:
    payload = json.loads((FIXTURES / "theoddsapi_mlb.json").read_text())
    events = extract_events(payload)
    lines = normalize_odds_events(payload)

    model = _model(n_sims=2000)
    config = SlateConfig(exclude_closing=False, min_edge=0.0)
    log, unresolved = build_live_slate(
        events, lines, model.project_full, model.known_teams, config, aliases=MLB_TEAM_ALIASES
    )

    assert not unresolved  # both clubs resolve via the alias table
    bets = list(log)
    assert bets, "a clear skill mismatch should clear a 0% edge threshold"
    for bet in bets:
        assert bet.stake > 0.0
        assert 0.0 < bet.p_model < 1.0
        assert bet.market in {"spread", "total", "moneyline"}
        # The CLV field is present on every logged bet (measured later vs the close).
        assert hasattr(bet, "closing_price")
