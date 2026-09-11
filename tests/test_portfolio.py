"""Portfolio staking — correlation de-scaling, caps, and the kill-switch."""

from __future__ import annotations

import pytest
from velocity.wagering.portfolio import (
    BetCandidate,
    PortfolioConfig,
    correlation_scale,
    drawdown,
    should_halt,
    size_portfolio,
)


def test_correlation_scale_bounds() -> None:
    assert correlation_scale(1, 0.5) == 1.0  # a lone bet is never de-scaled
    assert correlation_scale(2, 0.0) == 1.0  # independent bets are untouched
    assert correlation_scale(2, 1.0) == pytest.approx(0.5)  # perfectly correlated → halve
    assert correlation_scale(3, 0.5) == pytest.approx(1.0 / 2.0)


def test_drawdown_and_halt() -> None:
    assert drawdown(100, 100) == 0.0
    assert drawdown(70, 100) == pytest.approx(0.30)
    assert should_halt(70, 100, 0.30) is True
    assert should_halt(75, 100, 0.30) is False


def test_correlated_group_is_descaled() -> None:
    cands = [
        BetCandidate("g1_a", 0.05, "g1"),
        BetCandidate("g1_b", 0.05, "g1"),
    ]
    cfg = PortfolioConfig(group_correlation=0.5, group_cap_fraction=1.0, max_portfolio_fraction=1.0)
    sized = size_portfolio(cands, 1000.0, cfg)
    # Two bets correlated at 0.5 → scale 1/(1+0.5)=0.667; 1000*0.05*0.667=33.33.
    assert sized["g1_a"] == pytest.approx(1000 * 0.05 * (1 / 1.5))


def test_group_cap_bounds_single_game() -> None:
    cands = [BetCandidate(f"g1_{i}", 0.08, "g1") for i in range(4)]
    cfg = PortfolioConfig(
        group_correlation=0.0, group_cap_fraction=0.10, max_portfolio_fraction=1.0
    )
    sized = size_portfolio(cands, 1000.0, cfg)
    assert sum(sized.values()) == pytest.approx(100.0)  # 10% of 1000, not 4×80


def test_aggregate_cap_bounds_the_slate() -> None:
    cands = [BetCandidate(f"g{i}_x", 0.08, f"g{i}") for i in range(6)]
    cfg = PortfolioConfig(
        group_correlation=0.0, group_cap_fraction=1.0, max_portfolio_fraction=0.25
    )
    sized = size_portfolio(cands, 1000.0, cfg)
    assert sum(sized.values()) == pytest.approx(250.0)  # capped at 25% aggregate


def test_kill_switch_zeroes_all_stakes() -> None:
    cands = [BetCandidate("g1_a", 0.05, "g1"), BetCandidate("g2_b", 0.05, "g2")]
    cfg = PortfolioConfig(max_drawdown_fraction=0.30)
    sized = size_portfolio(cands, 1000.0, cfg, current_bankroll=650.0, peak_bankroll=1000.0)
    assert sum(sized.values()) == 0.0  # 35% drawdown > 30% threshold → halt


def test_no_halt_when_within_drawdown() -> None:
    cands = [BetCandidate("g1_a", 0.05, "g1")]
    cfg = PortfolioConfig(max_drawdown_fraction=0.30)
    sized = size_portfolio(cands, 1000.0, cfg, current_bankroll=800.0, peak_bankroll=1000.0)
    assert sized["g1_a"] > 0.0


def test_zero_bankroll_stakes_nothing() -> None:
    cands = [BetCandidate("g1_a", 0.05, "g1")]
    assert size_portfolio(cands, 0.0) == {"g1_a": 0.0}


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        PortfolioConfig(max_portfolio_fraction=0.0)
    with pytest.raises(ValueError):
        PortfolioConfig(group_correlation=1.5)


def test_model_weight_anchors_belief_to_the_market() -> None:
    """model_weight=0 collapses every edge to the market's own probability —
    nothing can clear min_edge, so the slate goes empty; 1.0 is bit-identical
    to the default. The knob is wagering policy, never a fit change."""
    from dataclasses import replace

    import numpy as np
    import pandas as pd
    from velocity.models.game_nfl import GameProjection
    from velocity.models.simulate import GameSim
    from velocity.wagering.slate import SlateConfig, build_slate

    rng = np.random.default_rng(5)
    home = np.maximum(rng.normal(30.0, 10.0, 4000).round(), 0)
    away = np.maximum(rng.normal(17.0, 10.0, 4000).round(), 0)
    proj = {"g1": GameProjection("KC", "BUF", 30.0, 17.0, GameSim(home, away))}
    ts = pd.Timestamp("2026-09-10 12:00")
    lines = pd.DataFrame([
        {"line_id": "l1", "game_id": "g1", "book": "b", "market": "moneyline",
         "side": "home", "price": -110, "point": None, "timestamp": ts,
         "is_closing": False},
        {"line_id": "l2", "game_id": "g1", "book": "b", "market": "moneyline",
         "side": "away", "price": -110, "point": None, "timestamp": ts,
         "is_closing": False},
    ])
    games = pd.DataFrame([{"game_id": "g1", "home_team": "KC", "away_team": "BUF",
                           "kickoff": pd.Timestamp("2026-09-11 00:20"),
                           "neutral_site": False}])
    cfg = SlateConfig(exclude_closing=False)

    raw = build_slate(proj, lines, games, cfg)
    assert len(raw) >= 1  # the mispriced coin-flip line is a fat raw edge
    anchored = build_slate(proj, lines, games, replace(cfg, model_weight=0.0))
    assert len(anchored) == 0  # full anchoring = the market is the belief: no bets
    same = build_slate(proj, lines, games, replace(cfg, model_weight=1.0))
    assert len(same) == len(raw)  # 1.0 is bit-identical to the default


def test_a_venue_cap_bounds_where_the_bets_live_not_just_what_they_are() -> None:
    """Model risk and venue risk are different risks, so they are different caps.

    A market class answers for the model: sixty-six moneyline dogs out of one
    sim's tail are one assumption, however many games they span. A venue
    answers for where the bet lives — an exchange's liquidity and fills, its
    settlement, and a ladder priced by a shape gate with no live record behind
    it yet. A bet carries both at once, so the venue cap is its own dimension
    rather than a relabelling of the class: these twenty bets are on twenty
    separate games and ten different markets, so neither the group cap nor the
    class cap touches the exchange half.
    """
    book = [BetCandidate(f"b{i}", 0.03, f"g{i}", market_class=f"m{i}") for i in range(10)]
    exchange = [BetCandidate(f"x{i}", 0.03, f"h{i}", market_class=f"m{i}", venue="exchange")
                for i in range(10)]
    bankroll, slate = 100.0, 0.25

    uncapped = size_portfolio(book + exchange, bankroll,
                              PortfolioConfig(max_portfolio_fraction=slate))
    held = sum(v for k, v in uncapped.items() if k.startswith("x"))
    assert held / (slate * bankroll) > 0.45, (
        "without a venue cap the exchange takes about half the card — which is "
        "the exposure this cap exists to bound on a first live run"
    )

    capped = size_portfolio(
        book + exchange, bankroll,
        PortfolioConfig(max_portfolio_fraction=slate, venue_caps={"exchange": 0.25}),
    )
    held = sum(v for k, v in capped.items() if k.startswith("x"))
    assert held <= 0.25 * slate * bankroll + 1e-9
    # Every exchange bet is smaller than it was, and the slate cap still binds.
    for key in (k for k in capped if k.startswith("x")):
        assert capped[key] <= uncapped[key] + 1e-9
    assert sum(capped.values()) <= slate * bankroll + 1e-9

    # The sportsbook side goes *up*, which is the cap working rather than a
    # leak: the slate cap is a budget, so without the venue cap the exchange's
    # half was being paid for by scaling everything down proportionally. Freeing
    # that share hands it back to bets that were already under their own Kelly
    # and their own group and class caps — it never lifts a bet past those.
    assert sum(v for k, v in capped.items() if k.startswith("b")) > sum(
        v for k, v in uncapped.items() if k.startswith("b")
    )
    standalone = size_portfolio(book, bankroll, PortfolioConfig(max_portfolio_fraction=slate))
    for key in (k for k in capped if k.startswith("b")):
        assert capped[key] <= standalone[key] + 1e-9


def test_venue_caps_are_off_unless_named() -> None:
    # Absent config changes nothing, and an unlisted venue is uncapped — so
    # this is invisible to every caller that does not ask for it.
    cands = [BetCandidate(f"x{i}", 0.05, f"g{i}", venue="exchange") for i in range(8)]
    plain = size_portfolio(cands, 100.0, PortfolioConfig())
    other = size_portfolio(cands, 100.0, PortfolioConfig(venue_caps={"somewhere-else": 0.1}))
    assert plain == other
    with pytest.raises(ValueError, match="venue_caps"):
        PortfolioConfig(venue_caps={"exchange": 1.5})
