"""Staking — fractional Kelly, hard caps, and correlated-group scaling."""

from __future__ import annotations

import pytest
from velocity.wagering.staking import (
    StakingConfig,
    apply_group_cap,
    stake_amount,
    stake_fraction,
)


def test_fractional_kelly_scales_full_kelly() -> None:
    cfg = StakingConfig(kelly_fraction=0.25, max_bet_fraction=1.0)
    # Full Kelly for 55% at -110 is ~0.055; quarter of that.
    assert stake_fraction(0.55, -110, cfg) == pytest.approx(0.25 * 0.055, abs=1e-4)


def test_cap_binds_on_big_edge() -> None:
    cfg = StakingConfig(kelly_fraction=0.5, max_bet_fraction=0.05)
    # Huge edge would want far more than 5% — the cap must bind exactly.
    assert stake_fraction(0.9, -110, cfg) == pytest.approx(0.05)


def test_no_stake_without_edge() -> None:
    assert stake_fraction(0.5, 100) == 0.0  # zero edge
    assert stake_fraction(0.4, -110) == 0.0  # negative edge


def test_stake_amount_scales_with_bankroll() -> None:
    cfg = StakingConfig(kelly_fraction=0.25, max_bet_fraction=1.0)
    frac = stake_fraction(0.6, 100, cfg)
    assert stake_amount(1000.0, 0.6, 100, cfg) == pytest.approx(1000.0 * frac)


def test_stake_never_exceeds_cap_across_many_prices() -> None:
    cfg = StakingConfig(kelly_fraction=0.5, max_bet_fraction=0.03)
    for price in (-500, -200, -110, 100, 200, 400):
        assert stake_fraction(0.85, price, cfg) <= 0.03 + 1e-12


def test_group_cap_scales_correlated_stakes_proportionally() -> None:
    # Two 8-unit bets on one game exceed a 10-unit (10% of 100) group cap.
    capped = apply_group_cap({"a": 8.0, "b": 8.0}, group_cap_fraction=0.10, bankroll=100.0)
    assert sum(capped.values()) == pytest.approx(10.0)
    assert capped["a"] == pytest.approx(5.0)  # relative sizing preserved
    assert capped["b"] == pytest.approx(5.0)


def test_group_cap_leaves_small_groups_untouched() -> None:
    stakes = {"a": 2.0, "b": 3.0}
    assert apply_group_cap(stakes, 0.10, 100.0) == stakes


def test_invalid_configs_rejected() -> None:
    with pytest.raises(ValueError):
        StakingConfig(kelly_fraction=0.0)
    with pytest.raises(ValueError):
        StakingConfig(max_bet_fraction=1.5)
    with pytest.raises(ValueError):
        apply_group_cap({"a": 1.0}, 0.0, 100.0)
    with pytest.raises(ValueError):
        stake_amount(-1.0, 0.6, 100)
