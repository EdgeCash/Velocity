"""De-vigging — closed-form values, normalization, and favorite-longshot behavior."""

from __future__ import annotations

import pytest
from velocity.wagering.devig import devig, overround

_METHODS = ["multiplicative", "additive", "shin", "power"]


def test_overround_of_standard_juice() -> None:
    # Two -110 sides carry ~4.76% vig.
    assert overround([-110, -110]) == pytest.approx(0.047619, abs=1e-5)


@pytest.mark.parametrize("method", _METHODS)
def test_symmetric_market_is_fifty_fifty(method: str) -> None:
    fair = devig([-110, -110], method=method)
    assert fair[0] == pytest.approx(0.5)
    assert fair[1] == pytest.approx(0.5)


@pytest.mark.parametrize("method", _METHODS)
def test_fair_probs_sum_to_one(method: str) -> None:
    assert sum(devig([-400, 300], method=method)) == pytest.approx(1.0)
    assert sum(devig([-250, 200], method=method)) == pytest.approx(1.0)


def test_multiplicative_closed_form() -> None:
    # -400 → 0.8, +300 → 0.25, overround 1.05.
    fair = devig([-400, 300], method="multiplicative")
    assert fair[0] == pytest.approx(0.8 / 1.05)
    assert fair[1] == pytest.approx(0.25 / 1.05)


def test_additive_closed_form() -> None:
    # Subtract half the 0.05 overround from each side.
    fair = devig([-400, 300], method="additive")
    assert fair[0] == pytest.approx(0.775)
    assert fair[1] == pytest.approx(0.225)


def test_shin_corrects_favorite_longshot_versus_multiplicative() -> None:
    mult = devig([-400, 300], method="multiplicative")
    shin = devig([-400, 300], method="shin")
    # Multiplicative strips vig proportionally (over-taxing the favorite); Shin
    # shrinks the favorite proportionally *less*, so its fair prob sits higher
    # and the longshot's lower.
    assert shin[0] > mult[0]
    assert shin[1] < mult[1]


def test_shin_equals_additive_for_two_way_market() -> None:
    # A known identity: for exactly two outcomes, Shin reduces to the additive
    # (balanced-book) method. The favorite-longshot correction only diverges
    # from additive with three or more outcomes.
    for prices in ([-400, 300], [-1000, 600], [-140, 120]):
        shin = devig(prices, method="shin")
        additive = devig(prices, method="additive")
        assert shin == pytest.approx(additive)


def test_power_also_lifts_the_favorite() -> None:
    mult = devig([-400, 300], method="multiplicative")
    power = devig([-400, 300], method="power")
    assert power[0] > mult[0]
    assert sum(power) == pytest.approx(1.0)


def test_unknown_method_rejected() -> None:
    with pytest.raises(ValueError, match="unknown devig method"):
        devig([-110, -110], method="bogus")


def test_single_outcome_rejected() -> None:
    with pytest.raises(ValueError, match="at least two outcomes"):
        devig([-110])


def test_three_way_market_normalizes() -> None:
    # A soccer-style 1X2 market still de-vigs to a valid distribution.
    fair = devig([150, 220, 180], method="multiplicative")
    assert sum(fair) == pytest.approx(1.0)
    assert all(0.0 < f < 1.0 for f in fair)
