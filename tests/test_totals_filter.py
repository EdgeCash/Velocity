"""NCAAF totals selectivity — points of disagreement, not probability.

The one strategy in this project with a decade of backtest behind it
(``docs/BACKTEST_NCAAF.md``): bet a full-game total only when the model's fair
total differs from the offered number by ≥ N points, in the direction of the
side being bet. These pin the exact semantics the backtest measured — the
directional cut, the full-game-only scope (a 4-point filter must never touch a
0.5 first-inning line), and that the gate composes with, rather than replaces,
the probability-edge gate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.models.game_nfl import GameProjection
from velocity.models.simulate import GameSim
from velocity.wagering.slate import SlateConfig, build_slate, total_disagreement


def _projection(total: float) -> GameProjection:
    """A projection whose fair (median) total is exactly ``total``."""
    half = total / 2.0
    home = np.full(400, half)
    away = np.full(400, half)
    sim = GameSim(home_score=home, away_score=away)
    return GameProjection("HOME", "AWAY", half, half, sim)


def test_disagreement_is_directional() -> None:
    proj = _projection(60.0)
    # Model 60 vs a 50 line: the over is 10 points of agreement, the under −10.
    assert total_disagreement(proj, "over", 50.0) == pytest.approx(10.0)
    assert total_disagreement(proj, "under", 50.0) == pytest.approx(-10.0)
    # And the mirror when the model sits below the number.
    assert total_disagreement(proj, "under", 70.0) == pytest.approx(10.0)
    assert total_disagreement(proj, "over", 70.0) == pytest.approx(-10.0)


def _lines(point: float, market: str = "total") -> pd.DataFrame:
    ts = pd.Timestamp("2026-09-05 12:00")
    return pd.DataFrame([
        {"line_id": f"{market}-{side}", "game_id": "g1", "book": "bookA",
         "market": market, "side": side, "price": -110, "point": point,
         "timestamp": ts, "is_closing": False}
        for side in ("over", "under")
    ])


GAMES = pd.DataFrame({"game_id": ["g1"], "kickoff": [pd.Timestamp("2026-09-05 19:00")]})


def _bets(proj: GameProjection, lines: pd.DataFrame, threshold: float) -> list:
    cfg = SlateConfig(
        exclude_closing=False, min_edge=0.0, min_total_disagreement=threshold,
    )
    return list(build_slate({"g1": proj}, lines, GAMES, cfg).bets)


def test_filter_blocks_a_small_disagreement() -> None:
    proj = _projection(52.0)  # model 52 vs a 50 line → only 2 points
    assert _bets(proj, _lines(50.0), 4.0) == []
    # The same game clears a lower bar.
    markets = {(b.market, b.side) for b in _bets(proj, _lines(50.0), 1.0)}
    assert ("total", "over") in markets


def test_filter_admits_a_large_disagreement_on_the_right_side_only() -> None:
    proj = _projection(60.0)  # model 60 vs a 50 line → over by 10
    bets = _bets(proj, _lines(50.0), 4.0)
    sides = {b.side for b in bets if b.market == "total"}
    assert sides == {"over"}  # the under is 10 points the wrong way — never bet


def test_filter_is_off_by_default() -> None:
    proj = _projection(52.0)
    cfg = SlateConfig(exclude_closing=False, min_edge=0.0)
    assert cfg.min_total_disagreement == 0.0
    bets = build_slate({"g1": proj}, _lines(50.0), GAMES, cfg).bets
    assert any(b.market == "total" for b in bets)  # unfiltered, as before


def test_filter_never_touches_segment_totals() -> None:
    """A points threshold calibrated on full-game totals must not reach NRFI.

    ``total_i1`` lines sit at 0.5 runs; a 4-point filter would silently kill
    every one of them, so the gate is scoped to the full-game market alone.
    """
    from types import SimpleNamespace

    from velocity.models.game_mlb import MLBProjection

    runs = GameSim(home_score=np.full(400, 2.0), away_score=np.full(400, 2.0))
    i1_sim = GameSim(home_score=np.full(400, 1.0), away_score=np.zeros(400))
    full_p = GameProjection("HOME", "AWAY", 2.0, 2.0, runs)
    i1_p = GameProjection("HOME", "AWAY", 1.0, 0.0, i1_sim)
    # Pricing reads only the projections; the raw sim result is unused here.
    result = SimpleNamespace(full=runs, f5=runs, i1=i1_sim)
    proj = MLBProjection(full=full_p, f5=full_p, i1=i1_p, result=result)  # type: ignore[arg-type]
    cfg = SlateConfig(
        exclude_closing=False, min_edge=0.0, min_total_disagreement=4.0,
    )
    bets = build_slate({"g1": proj}, _lines(0.5, market="total_i1"), GAMES, cfg).bets
    # The first-inning market is priced normally despite the 4-point full-game gate.
    assert any(b.market == "total_i1" for b in bets)


def test_filter_composes_with_the_probability_gate() -> None:
    """Both gates apply: clearing the points cut does not bypass min_edge."""
    proj = _projection(60.0)
    cfg = SlateConfig(
        exclude_closing=False, min_edge=0.99,  # unreachable probability edge
        min_total_disagreement=1.0,
    )
    assert list(build_slate({"g1": proj}, _lines(50.0), GAMES, cfg).bets) == []
