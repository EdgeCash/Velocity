"""The MLB anchoring sweep — the pieces that decide a staking weight.

The closes it runs on are paid data and live only in a private artifact, so
these exercise the machinery on constructed frames: the de-vig, the
doubleheader-safe join, and the estimator the verdict rests on.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT = Path(__file__).parent.parent / "scripts" / "sweep_mlb_anchoring.py"


def _sweeper():
    spec = importlib.util.spec_from_file_location("sweep_mlb_anchoring", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lines(rows: list[dict]) -> pd.DataFrame:
    base = {"game_id": "g1", "book": "b1", "market": "moneyline",
            "timestamp": pd.Timestamp("2026-05-01 22:00"), "point": None}
    return pd.DataFrame([{**base, **row} for row in rows])


_EVENTS = pd.DataFrame([{"game_id": "g1", "kickoff": pd.Timestamp("2026-05-01 23:05"),
                         "home_team": "Atlanta Braves",
                         "away_team": "Philadelphia Phillies"}])


# --- the close ---------------------------------------------------------------


def test_a_pair_is_devigged_before_the_books_are_medianed() -> None:
    """Order matters: median the fair probabilities, not the prices.

    De-vigging after a price median folds a lopsided book's overround into the
    consensus instead of removing it.
    """
    sweeper = _sweeper()
    closes = sweeper.closing_moneylines(
        _lines([{"side": "Atlanta Braves", "price": -110},
                {"side": "Philadelphia Phillies", "price": -110}]), _EVENTS)
    # A symmetric −110/−110 pair is 50/50 once its 4.5% vig is removed.
    assert abs(float(closes["p_home_fair"].iloc[0]) - 0.5) < 1e-9


def test_the_last_snapshot_before_first_pitch_is_the_close() -> None:
    sweeper = _sweeper()
    lines = _lines([
        {"side": "Atlanta Braves", "price": -200,
         "timestamp": pd.Timestamp("2026-05-01 18:00")},
        {"side": "Philadelphia Phillies", "price": +170,
         "timestamp": pd.Timestamp("2026-05-01 18:00")},
        {"side": "Atlanta Braves", "price": -110,
         "timestamp": pd.Timestamp("2026-05-01 22:00")},
        {"side": "Philadelphia Phillies", "price": -110,
         "timestamp": pd.Timestamp("2026-05-01 22:00")},
    ])
    closes = sweeper.closing_moneylines(lines, _EVENTS)
    assert abs(float(closes["p_home_fair"].iloc[0]) - 0.5) < 1e-9


def test_an_in_play_price_is_not_a_close() -> None:
    sweeper = _sweeper()
    lines = _lines([{"side": "Atlanta Braves", "price": -110,
                     "timestamp": pd.Timestamp("2026-05-02 01:00")},
                    {"side": "Philadelphia Phillies", "price": -110,
                     "timestamp": pd.Timestamp("2026-05-02 01:00")}])
    assert sweeper.closing_moneylines(lines, _EVENTS).empty


def test_a_book_quoting_only_one_side_is_skipped() -> None:
    sweeper = _sweeper()
    lines = _lines([
        {"side": "Atlanta Braves", "price": -110, "book": "half"},
        {"side": "Atlanta Braves", "price": -150, "book": "whole"},
        {"side": "Philadelphia Phillies", "price": +130, "book": "whole"},
    ])
    closes = sweeper.closing_moneylines(lines, _EVENTS)
    assert int(closes["n_books"].iloc[0]) == 1
    assert float(closes["p_home_fair"].iloc[0]) > 0.5


def test_the_price_kept_is_the_best_a_shopper_could_take() -> None:
    sweeper = _sweeper()
    lines = _lines([
        {"side": "Atlanta Braves", "price": -150, "book": "a"},
        {"side": "Philadelphia Phillies", "price": +130, "book": "a"},
        {"side": "Atlanta Braves", "price": -135, "book": "b"},
        {"side": "Philadelphia Phillies", "price": +145, "book": "b"},
    ])
    closes = sweeper.closing_moneylines(lines, _EVENTS)
    assert float(closes["home_price"].iloc[0]) == -135.0
    assert float(closes["away_price"].iloc[0]) == 145.0


def test_an_empty_archive_returns_an_empty_frame() -> None:
    sweeper = _sweeper()
    assert sweeper.closing_moneylines(pd.DataFrame(), _EVENTS).empty
    assert sweeper.closing_moneylines(_lines([]), pd.DataFrame()).empty


# --- the join ----------------------------------------------------------------


def test_a_doubleheader_gets_two_closes_rather_than_one_twice() -> None:
    """The same rule the close-join uses, and for the same reason."""
    sweeper = _sweeper()
    games = pd.DataFrame([
        {"game_id": "early", "kickoff": pd.Timestamp("2026-05-01 17:10"),
         "home_team": "Atlanta Braves", "away_team": "Philadelphia Phillies"},
        {"game_id": "late", "kickoff": pd.Timestamp("2026-05-01 23:10"),
         "home_team": "Atlanta Braves", "away_team": "Philadelphia Phillies"},
    ])
    closes = pd.DataFrame([
        {"game_id": "p1", "home_team": "Atlanta Braves",
         "away_team": "Philadelphia Phillies",
         "kickoff": pd.Timestamp("2026-05-01 17:05"), "p_home_fair": 0.55,
         "home_price": -120.0, "away_price": 110.0},
        {"game_id": "p2", "home_team": "Atlanta Braves",
         "away_team": "Philadelphia Phillies",
         "kickoff": pd.Timestamp("2026-05-01 23:05"), "p_home_fair": 0.61,
         "home_price": -150.0, "away_price": 130.0},
    ])
    joined = sweeper.attach_closes(games, closes).set_index("game_id")
    assert float(joined.loc["early", "p_home_fair"]) == 0.55
    assert float(joined.loc["late", "p_home_fair"]) == 0.61


def test_a_close_too_far_from_the_kickoff_is_refused() -> None:
    sweeper = _sweeper()
    games = pd.DataFrame([{"game_id": "g", "kickoff": pd.Timestamp("2026-05-01 23:10"),
                           "home_team": "A", "away_team": "B"}])
    closes = pd.DataFrame([{"game_id": "p", "home_team": "A", "away_team": "B",
                            "kickoff": pd.Timestamp("2026-05-03 23:05"),
                            "p_home_fair": 0.5, "home_price": -110.0,
                            "away_price": -110.0}])
    assert sweeper.attach_closes(games, closes)["p_home_fair"].isna().all()


# --- the estimator the verdict rests on --------------------------------------


def test_the_slope_recovers_a_known_weight() -> None:
    """A model that is right about a HALF of its disagreement scores w = 0.5."""
    sweeper = _sweeper()
    rng = np.random.default_rng(5)
    n = 40_000
    fair = rng.uniform(0.3, 0.7, n)
    disagreement = rng.normal(0.0, 0.08, n)
    truth = np.clip(fair + 0.5 * disagreement, 0.01, 0.99)
    won = rng.random(n) < truth
    frame = pd.DataFrame({
        "p_home_fair": fair, "p_model": fair + disagreement,
        "home_score": np.where(won, 5.0, 3.0), "away_score": np.where(won, 3.0, 5.0),
    })
    w, se, count = sweeper.calibration_slope(frame)
    assert count == n
    assert abs(w - 0.5) < 3 * se


def test_a_model_that_adds_nothing_scores_a_weight_near_zero() -> None:
    sweeper = _sweeper()
    rng = np.random.default_rng(9)
    n = 30_000
    fair = rng.uniform(0.3, 0.7, n)
    won = rng.random(n) < fair  # the market is exactly right; the model is noise
    frame = pd.DataFrame({
        "p_home_fair": fair, "p_model": fair + rng.normal(0.0, 0.08, n),
        "home_score": np.where(won, 5.0, 3.0), "away_score": np.where(won, 3.0, 5.0),
    })
    w, se, _n = sweeper.calibration_slope(frame)
    assert abs(w) < 3 * se


def test_a_frame_with_nothing_to_fit_returns_nan_rather_than_raising() -> None:
    sweeper = _sweeper()
    w, se, n = sweeper.calibration_slope(pd.DataFrame(
        {"p_home_fair": [], "p_model": [], "home_score": [], "away_score": []}))
    assert np.isnan(w) and np.isnan(se) and n == 0


# --- the gate-selected sweep -------------------------------------------------


def test_the_side_picked_does_not_depend_on_the_weight() -> None:
    """Why the sweep works: the weight sizes the claim, it does not select.

    ``belief − fair = w·(p_model − fair)``, so every positive weight backs the
    same side; only how much it claims moves. A weight that changed the side
    would make the claimed and realized columns incomparable.
    """
    sweeper = _sweeper()
    frame = pd.DataFrame({
        "p_home_fair": [0.50, 0.60], "p_model": [0.62, 0.52],
        "home_score": [5.0, 2.0], "away_score": [3.0, 4.0],
        "home_price": [-110.0, -150.0], "away_price": [-110.0, 130.0],
    })
    table = sweeper.sweep(frame, weights=(0.5, 1.0), min_edge=0.0)
    # Both weights bet both games, and on the same sides, so the realized
    # column is identical — only the claim scales.
    assert list(table["n"]) == [2, 2]
    assert table["realized_edge"].nunique() == 1
    assert table["claimed_edge"].iloc[1] > table["claimed_edge"].iloc[0]


def test_the_gate_keeps_only_claims_big_enough_to_bet() -> None:
    sweeper = _sweeper()
    frame = pd.DataFrame({
        "p_home_fair": [0.50, 0.50], "p_model": [0.90, 0.51],
        "home_score": [5.0, 5.0], "away_score": [3.0, 3.0],
        "home_price": [-110.0, -110.0], "away_price": [-110.0, -110.0],
    })
    table = sweeper.sweep(frame, weights=(0.2,), min_edge=0.02)
    assert int(table["n"].iloc[0]) == 1  # only the 0.40 disagreement clears it
