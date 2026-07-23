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
    # the fair total is the median: over-prob brackets 0.5 across it (discrete runs
    # put real push mass on the median itself), and the over is monotonic.
    assert proj.prob_over(proj.fair_total() - 1.0) >= 0.5 >= proj.prob_over(proj.fair_total() + 1.0)
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


def test_park_hr_factor_lifts_the_home_park_total() -> None:
    """A hitter's park at the home venue prices the total over a neutral one."""
    teams = {"LAD": _team("lad", AVG_BAT), "SF": _team("sf", AVG_BAT)}
    cfg = BaseballSimConfig(n_sims=4000)
    neutral = MLBGameModel(teams=teams, config=cfg, seed=7)
    coors = MLBGameModel(teams=teams, config=cfg, seed=7, park_hr_factors={"LAD": 1.12})

    base = neutral.project_full("LAD", "SF")
    boosted = coors.project_full("LAD", "SF")
    assert boosted.fair_total() >= base.fair_total()
    assert boosted.mu_home + boosted.mu_away > base.mu_home + base.mu_away
    # A home team not in the map projects unchanged (neutral default).
    assert coors.park_hr_factors.get("SF", 1.0) == 1.0


def test_run_env_tilt_lifts_the_home_total() -> None:
    """A positive run-environment tilt at the home venue prices the total up."""
    teams = {"LAD": _team("lad", AVG_BAT), "SF": _team("sf", AVG_BAT)}
    cfg = BaseballSimConfig(n_sims=4000)
    neutral = MLBGameModel(teams=teams, config=cfg, seed=7)
    tilted = MLBGameModel(teams=teams, config=cfg, seed=7, run_env_tilts={"LAD": 0.05})
    base = neutral.project_full("LAD", "SF")
    boosted = tilted.project_full("LAD", "SF")
    assert boosted.mu_home + boosted.mu_away > base.mu_home + base.mu_away
    # A home team not in the map is neutral.
    assert tilted.run_env_tilts.get("SF", 0.0) == 0.0


def test_league_average_model_is_tto_and_park_aware() -> None:
    """The production baseline carries the TTO penalty and a park/run environment."""
    from velocity.models.game_mlb import league_average_model
    from velocity.models.simulate_baseball import DEFAULT_TTO_PENALTY
    from velocity.report.park_factors import run_environment_maps

    hr, tilt = run_environment_maps()
    model = league_average_model(
        ["LAD", "SF", "COL"], n_sims=300, park_hr_factors=hr, run_env_tilts=tilt
    )
    assert model.config.tto_penalty == DEFAULT_TTO_PENALTY
    # Coors carries a hitter's HR factor + a non-HR tilt; a neutral-ish park less so.
    assert model.park_hr_factors["COL"] > 1.0
    assert model.run_env_tilts["COL"] != 0.0


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
