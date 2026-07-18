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
