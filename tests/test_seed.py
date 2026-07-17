"""Determinism guard for the seeded RNG."""

from __future__ import annotations

from velocity.util.seed import make_rng


def test_same_seed_same_draws() -> None:
    a = make_rng(42).normal(size=100)
    b = make_rng(42).normal(size=100)
    assert (a == b).all()


def test_different_seed_differs() -> None:
    a = make_rng(1).normal(size=100)
    b = make_rng(2).normal(size=100)
    assert not (a == b).all()
