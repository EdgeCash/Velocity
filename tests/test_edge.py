"""Edge & EV — closed-form EV/Kelly, line shopping, and threshold gating."""

from __future__ import annotations

import pytest
from velocity.wagering.edge import (
    best_price,
    evaluate,
    expected_value,
    kelly_fraction,
    probability_edge,
)


def test_expected_value_break_even() -> None:
    # Fair coin at +100 has exactly zero EV.
    assert expected_value(0.5, 100) == pytest.approx(0.0)
    assert expected_value(0.6, 100) == pytest.approx(0.2)


def test_expected_value_with_juice() -> None:
    # 55% at -110: 0.55 * 0.9091 - 0.45 = 0.05.
    assert expected_value(0.55, -110) == pytest.approx(0.05, abs=1e-6)


def test_kelly_fraction_closed_form() -> None:
    # Kelly = EV / b = 0.05 / 0.9091.
    assert kelly_fraction(0.55, -110) == pytest.approx(0.05 / 0.909090909, abs=1e-6)
    assert kelly_fraction(0.5, 100) == pytest.approx(0.0)


def test_kelly_negative_when_no_edge() -> None:
    assert kelly_fraction(0.4, -110) < 0.0


def test_probability_edge() -> None:
    assert probability_edge(0.57, 0.52) == pytest.approx(0.05)


def test_best_price_picks_highest_payout() -> None:
    # +100 pays more than any -juice; among favorites, -150 beats -200.
    assert best_price([-110, -105, 100]) == 100
    assert best_price([-200, -150, -175]) == -150


def test_best_price_empty_rejected() -> None:
    with pytest.raises(ValueError):
        best_price([])


def test_evaluate_qualifies_on_real_edge() -> None:
    # Model 57% vs fair 52% at -110: edge 5% ≥ 2% and EV > 0.
    sig = evaluate(0.57, -110, 0.52, min_edge=0.02)
    assert sig.qualifies
    assert sig.edge == pytest.approx(0.05)
    assert sig.ev > 0


def test_evaluate_rejects_thin_edge() -> None:
    sig = evaluate(0.53, -110, 0.52, min_edge=0.02)
    assert not sig.qualifies  # 1% edge is below threshold


def test_evaluate_rejects_negative_ev_despite_edge() -> None:
    # A probability edge that still doesn't overcome the price loses money.
    sig = evaluate(0.50, -400, 0.47, min_edge=0.02)
    assert sig.edge >= 0.02
    assert sig.ev < 0
    assert not sig.qualifies
