"""Pick'em board builder — legs from the prop board, slips from the sim.

The builder is trustworthy only if: the freshest two-sided quote wins, the
calibrated threshold reproduces the book's marginal inside the sim, resolved
players gate on the sim actually covering them, and correlated stacks
out-rank independent pairs with identical marginals.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.models.props_football import FootballPropSim
from velocity.wagering.pickem_slate import build_pickem_board

N = 40_000
_RNG = np.random.default_rng(9)
_SHARED = _RNG.normal(size=N)  # the game script both stacked players ride


def _sim() -> FootballPropSim:
    qb = 250 + 40 * _SHARED + 5 * _RNG.normal(size=N)
    wr = 70 + 25 * _SHARED + 4 * _RNG.normal(size=N)
    rb = 60 + 20 * _RNG.normal(size=N)  # independent of the stack
    return FootballPropSim({
        ("qb1", "player_pass_yds"): qb,
        ("wr1", "player_reception_yds"): wr,
        ("rb1", "player_rush_yds"): rb,
    })


_NAME_TO_ID = {"patquarter": "qb1", "wesreceiver": "wr1", "reggierusher": "rb1"}


def _line(player: str, market: str, point: float, over: float, under: float,
          ts: str = "2026-09-07T12:00:00", game: str = "g1") -> list[dict]:
    return [
        {"game_id": game, "market": market, "player": player, "side": side,
         "price": price, "point": point, "book": "dk", "timestamp": pd.Timestamp(ts)}
        for side, price in (("over", over), ("under", under))
    ]


def _board() -> pd.DataFrame:
    rows = (
        _line("Pat Quarter", "player_pass_yds", 240.5, -180, +150)
        + _line("Wes Receiver", "player_reception_yds", 62.5, -180, +150)
        + _line("Reggie Rusher", "player_rush_yds", 52.5, -180, +150)
        # A stale, different number for the QB — the fresher quote must win.
        + _line("Pat Quarter", "player_pass_yds", 260.5, +100, -120,
                ts="2026-09-07T09:00:00")
    )
    return pd.DataFrame(rows)


def test_legs_devig_resolve_and_prefer_the_freshest_quote() -> None:
    legs, slips = build_pickem_board({"g1": _sim()}, _board(), _NAME_TO_ID, min_ev=0.0)
    assert set(legs["player"]) == {"Pat Quarter", "Wes Receiver", "Reggie Rusher"}
    qb = legs.set_index("player").loc["Pat Quarter"]
    assert qb["line"] == 240.5  # the newer quote, not the stale 260.5
    assert qb["side"] == "over"
    # Multiplicative devig of -180/+150: 0.6429/(0.6429+0.4) ≈ 0.6165.
    assert qb["p_book"] == pytest.approx(0.6165, abs=1e-3)
    assert 0.0 < qb["p_model"] < 1.0
    assert not slips.empty


def test_calibration_reproduces_the_book_marginal_in_the_sim() -> None:
    # A power-2 of two INDEPENDENT legs must price at p_book² × 3, regardless
    # of what the sim's own marginals think of the line.
    sim = _sim()
    board = pd.DataFrame(
        _line("Pat Quarter", "player_pass_yds", 240.5, -180, +150)
        + _line("Reggie Rusher", "player_rush_yds", 52.5, -180, +150, game="g1")
    )
    legs, slips = build_pickem_board(
        {"g1": sim}, board, _NAME_TO_ID, tables=("power-2",), min_ev=0.0
    )
    p = legs["p_book"].iloc[0]
    pair = slips[slips["legs"].str.contains("Reggie")].iloc[0]
    assert pair["ev"] == pytest.approx(3.0 * p * p, abs=0.03)


def test_correlated_stack_outranks_independent_pair() -> None:
    legs, slips = build_pickem_board(
        {"g1": _sim()}, _board(), _NAME_TO_ID, tables=("power-2",), min_ev=0.0, top=3
    )
    top = slips.iloc[0]["legs"]
    assert "Pat Quarter" in top and "Wes Receiver" in top  # the stack wins
    # And its EV clears the independent product priced by calibration.
    p = legs.set_index("player")["p_book"]
    independent = 3.0 * p["Pat Quarter"] * p["Wes Receiver"]
    assert slips.iloc[0]["ev"] > independent + 0.05


def test_gates_min_leg_p_unresolved_and_uncovered() -> None:
    # Near-coin-flip prices fall below the leg floor; unknown names and
    # players the sim doesn't cover are skipped, never guessed.
    board = pd.DataFrame(
        _line("Pat Quarter", "player_pass_yds", 240.5, -105, -115)
        + _line("Total Stranger", "player_pass_yds", 200.5, -180, +150)
        + _line("Wes Receiver", "player_rush_yds", 30.5, -180, +150)  # wrong market
    )
    legs, slips = build_pickem_board({"g1": _sim()}, board, _NAME_TO_ID)
    assert legs.empty
    assert slips.empty


def test_empty_inputs_are_empty_results() -> None:
    legs, slips = build_pickem_board({}, pd.DataFrame(), _NAME_TO_ID)
    assert legs.empty and slips.empty
