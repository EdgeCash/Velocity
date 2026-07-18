"""Odds conversions — exact known values and round-trips."""

from __future__ import annotations

import pytest
from velocity.wagering.odds import (
    american_to_decimal,
    american_to_prob,
    decimal_to_american,
    net_payout,
    prob_to_american,
)


def test_american_to_decimal_known_values() -> None:
    assert american_to_decimal(150) == pytest.approx(2.5)
    assert american_to_decimal(-200) == pytest.approx(1.5)
    assert american_to_decimal(100) == pytest.approx(2.0)
    assert american_to_decimal(-110) == pytest.approx(1.909090909)


def test_american_to_prob_known_values() -> None:
    assert american_to_prob(100) == pytest.approx(0.5)
    assert american_to_prob(-110) == pytest.approx(0.523809523)
    assert american_to_prob(-200) == pytest.approx(2 / 3)


def test_net_payout() -> None:
    assert net_payout(100) == pytest.approx(1.0)
    assert net_payout(-110) == pytest.approx(0.909090909)


@pytest.mark.parametrize("price", [-500, -200, -110, 100, 150, 300])
def test_decimal_round_trip(price: int) -> None:
    assert decimal_to_american(american_to_decimal(price)) == pytest.approx(price)


@pytest.mark.parametrize("prob", [0.1, 0.25, 0.5, 0.75, 0.9])
def test_prob_round_trip(prob: float) -> None:
    assert american_to_prob(prob_to_american(prob)) == pytest.approx(prob)


@pytest.mark.parametrize("bad", [0, 50, -50, 99, -99])
def test_invalid_american_rejected(bad: int) -> None:
    with pytest.raises(ValueError):
        american_to_decimal(bad)


@pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 1.5])
def test_invalid_probability_rejected(bad: float) -> None:
    with pytest.raises(ValueError):
        prob_to_american(bad)


def test_invalid_decimal_rejected() -> None:
    with pytest.raises(ValueError):
        decimal_to_american(1.0)
