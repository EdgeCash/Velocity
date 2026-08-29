"""Per-market selectivity (docs/WAGERING.md Phase W4, DESIGN §6.2).

Pins the two levers exactly: a market listed in ``min_edge_by_market`` clears
its own bar instead of the global — tighter or looser, override wins — and
``exclude_markets`` now sits a market out of the *game* slate the same way it
always sat one out of the prop slate (NCAAF spreads: 50.1% ATS flat, no edge
at any disagreement threshold — docs/BACKTEST_NCAAF.md).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from velocity.models.game_nfl import GameProjection
from velocity.models.simulate import GameSim
from velocity.wagering.props_slate import build_prop_slate
from velocity.wagering.slate import SlateConfig, build_slate

GAMES = pd.DataFrame({"game_id": ["g1"], "kickoff": [pd.Timestamp("2026-09-05 19:00")]})


def test_min_edge_for_prefers_the_override() -> None:
    cfg = SlateConfig(min_edge=0.02, min_edge_by_market={"pass_yards": 0.05})
    assert cfg.min_edge_for("pass_yards") == 0.05
    assert cfg.min_edge_for("total") == 0.02  # everything else keeps the global
    assert SlateConfig().min_edge_by_market == {}  # default: no overrides


# --- game slate ---------------------------------------------------------------

def _projection() -> GameProjection:
    """Home 30, away 20 in every sim — a full point of edge on every market."""
    sim = GameSim(home_score=np.full(400, 30.0), away_score=np.full(400, 20.0))
    return GameProjection("HOME", "AWAY", 30.0, 20.0, sim)


def _board() -> pd.DataFrame:
    ts = pd.Timestamp("2026-09-05 12:00")
    rows = [
        {"line_id": f"ml-{side}", "game_id": "g1", "book": "b", "market": "moneyline",
         "side": side, "price": -110, "point": None, "timestamp": ts,
         "is_closing": False}
        for side in ("home", "away")
    ] + [
        {"line_id": f"t-{side}", "game_id": "g1", "book": "b", "market": "total",
         "side": side, "price": -110, "point": 47.0, "timestamp": ts,
         "is_closing": False}
        for side in ("over", "under")
    ]
    return pd.DataFrame(rows)


def test_per_market_threshold_overrides_the_global_exactly() -> None:
    # Both markets carry a 0.5 probability edge against a -110/-110 board.
    base = SlateConfig(exclude_closing=False, min_edge=0.02)
    markets = {b.market for b in build_slate({"g1": _projection()}, _board(), GAMES, base)}
    assert markets == {"moneyline", "total"}

    # A tighter total bar blocks totals only; the global still admits moneyline.
    tighter = SlateConfig(
        exclude_closing=False, min_edge=0.02, min_edge_by_market={"total": 0.6},
    )
    markets = {b.market for b in build_slate({"g1": _projection()}, _board(), GAMES, tighter)}
    assert markets == {"moneyline"}

    # And the override wins in the loose direction too: an unreachable global
    # with a market-level bar the edge clears admits exactly that market.
    looser = SlateConfig(
        exclude_closing=False, min_edge=0.99, min_edge_by_market={"total": 0.1},
    )
    markets = {b.market for b in build_slate({"g1": _projection()}, _board(), GAMES, looser)}
    assert markets == {"total"}


def test_game_slate_honors_exclude_markets() -> None:
    cfg = SlateConfig(
        exclude_closing=False, min_edge=0.0, exclude_markets=frozenset({"moneyline"}),
    )
    bets = list(build_slate({"g1": _projection()}, _board(), GAMES, cfg))
    assert bets  # the rest of the board still prices
    assert all(b.market != "moneyline" for b in bets)


# --- prop slate ---------------------------------------------------------------

class _Props:
    """Allen clears 249.5 pass yards in 70% of sims; Kelce 6.5 receptions in 60%."""

    samples = {
        ("qb1", "pass_yards"): np.array([300.0] * 70 + [200.0] * 30),
        ("te1", "receptions"): np.array([8.0] * 60 + [5.0] * 40),
    }

    def has(self, player_id: str, market: str) -> bool:
        return (player_id, market) in self.samples

    def prob_over(self, player_id: str, market: str, point: float) -> float:
        return float(np.mean(self.samples[(player_id, market)] > point))

    def prob_under(self, player_id: str, market: str, point: float) -> float:
        return float(np.mean(self.samples[(player_id, market)] < point))


def _prop_board() -> pd.DataFrame:
    ts = pd.Timestamp("2026-09-05 12:00")
    rows = []
    for market, player, point in (
        ("pass_yards", "Josh Allen", 249.5),
        ("receptions", "Travis Kelce", 6.5),
    ):
        for side in ("over", "under"):
            rows.append({
                "line_id": f"{market}-{side}", "game_id": "g1", "book": "b",
                "market": market, "player": player, "side": side, "price": -110,
                "point": point, "timestamp": ts, "is_closing": False,
            })
    return pd.DataFrame(rows)


NAMES = {"joshallen": "qb1", "traviskelce": "te1"}


def test_prop_slate_honors_per_market_thresholds() -> None:
    # Edges vs the -110/-110 fair coin: pass_yards 0.20, receptions 0.10.
    both, _ = build_prop_slate(
        {"g1": _Props()}, _prop_board(), NAMES,
        SlateConfig(exclude_closing=False, min_edge=0.05),
    )
    assert {b.market for b in both} == {"pass_yards", "receptions"}

    gated, _ = build_prop_slate(
        {"g1": _Props()}, _prop_board(), NAMES,
        SlateConfig(
            exclude_closing=False, min_edge=0.05,
            min_edge_by_market={"receptions": 0.15},
        ),
    )
    assert {b.market for b in gated} == {"pass_yards"}
