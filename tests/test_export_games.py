"""games.csv — market against model, with the sim's own probabilities."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.export.games import (
    GAMES_COLUMNS,
    build_games,
    market_numbers,
    prob_above,
    prob_home_cover,
    weather_text,
)
from velocity.models.simulate import SimConfig, simulate_game
from velocity.report.social import distributions_frame


class _Proj:
    """The minimum GameProjection surface distributions_frame reads."""

    def __init__(self, sim: object) -> None:
        self.sim = sim


def _sim_projection(seed: int = 11) -> object:
    rng = np.random.default_rng(seed)
    return _Proj(simulate_game(3.0, 45.0, rng, SimConfig(n_sims=20_000)))


def test_cover_probability_is_the_sims_own_number() -> None:
    """The pmf route and the sim's method must not be two pricing models."""
    proj = _sim_projection()
    dists = distributions_frame({"g1": proj})
    margin = dists[dists["kind"] == "margin"][["value", "prob"]]
    for spread in (-7.5, -3.0, 0.0, 2.5, 6.0):
        assert prob_home_cover(margin, spread) == pytest.approx(
            proj.sim.prob_home_cover(spread), abs=1e-12
        )


def test_over_probability_is_the_sims_own_number() -> None:
    proj = _sim_projection()
    dists = distributions_frame({"g1": proj})
    total = dists[dists["kind"] == "total"][["value", "prob"]]
    for point in (38.5, 44.0, 51.5):
        assert prob_above(total, point) == pytest.approx(
            proj.sim.prob_over(point), abs=1e-12
        )


def test_probabilities_abstain_without_a_distribution() -> None:
    assert prob_above(None, 44.0) is None
    assert prob_above(pd.DataFrame(columns=["value", "prob"]), 44.0) is None
    assert prob_home_cover(pd.DataFrame({"value": [0], "prob": [1.0]}), None) is None
    assert prob_above(pd.DataFrame({"value": [0], "prob": [1.0]}), float("nan")) is None


def test_market_numbers_normalizes_the_spread_to_home() -> None:
    board = pd.DataFrame([
        {"game_id": "g1", "market": "spread", "side": "home", "point": -3.0, "price": -110},
        {"game_id": "g1", "market": "spread", "side": "away", "point": 3.0, "price": -110},
        {"game_id": "g1", "market": "total", "side": "over", "point": 44.5, "price": -108},
        {"game_id": "g1", "market": "total", "side": "under", "point": 44.5, "price": -112},
        {"game_id": "g1", "market": "moneyline", "side": "home", "point": None, "price": -160},
        {"game_id": "g1", "market": "moneyline", "side": "away", "point": None, "price": 140},
    ])
    row = market_numbers(board).set_index("game_id").loc["g1"]
    # Averaging -3 with its own negation would read as a pick'em.
    assert row["market_spread"] == pytest.approx(-3.0)
    assert row["market_total"] == pytest.approx(44.5)
    assert row["moneyline"] == pytest.approx(-160)


def test_market_numbers_is_empty_on_nothing() -> None:
    assert market_numbers(None).empty
    assert market_numbers(pd.DataFrame({"nope": [1]})).empty


def test_weather_text_says_nothing_when_the_forecast_did_not_cover_it() -> None:
    assert weather_text({"wind_mph": None, "precip_in": None, "temp_f": None}) == ""
    text = weather_text({"wind_mph": 14.0, "precip_in": 0.0, "temp_f": 38.0,
                         "total_points": -1.4})
    assert "38" in text and "14 mph" in text and "-1.4" in text
    # A dry game does not get a "precip 0.00 in" clause.
    assert "precip" not in text


def test_build_games_signs_edges_toward_home_and_over() -> None:
    games = pd.DataFrame([{"game_id": "g1", "league": "nfl",
                           "home_team": "Panthers", "away_team": "Falcons"}])
    proj = pd.DataFrame([{"game_id": "g1", "mu_home": 21.0, "mu_away": 23.2,
                          "fair_spread": 2.2, "fair_total": 44.2}])
    board = pd.DataFrame([
        {"game_id": "g1", "market": "spread", "side": "home", "point": -2.5, "price": -110},
        {"game_id": "g1", "market": "total", "side": "over", "point": 41.0, "price": -110},
    ])
    row = build_games(games, proj, board=board).iloc[0]
    # Market sells home at -2.5 where the model wants +2.2: 4.7 points to home.
    assert row["spread_edge"] == pytest.approx(-4.7)
    # Model total 44.2 against a market 41.0: 3.2 points to the over.
    assert row["total_edge"] == pytest.approx(3.2)
    assert row["model_total"] == pytest.approx(44.2)


def test_build_games_keeps_the_column_contract_with_nothing_to_say() -> None:
    games = pd.DataFrame([{"game_id": "g1", "league": "nfl",
                           "home_team": "Panthers", "away_team": "Falcons"}])
    out = build_games(games)
    expected = [c for c in GAMES_COLUMNS if c not in ("generated_at", "season", "week")]
    assert list(out.columns) == expected
    assert len(out) == 1
    for column in ("market_spread", "cover_probability", "confidence", "kelly_fraction"):
        assert pd.isna(out.loc[0, column])
    assert out.loc[0, "weather"] == ""


def test_build_games_is_empty_without_a_game_map() -> None:
    assert list(build_games(None).columns) == list(GAMES_COLUMNS)
    assert build_games(pd.DataFrame()).empty


def test_kelly_fraction_is_the_biggest_stake_over_bankroll() -> None:
    games = pd.DataFrame([{"game_id": "g1", "league": "nfl",
                           "home_team": "H", "away_team": "A"}])
    slate = pd.DataFrame([
        {"game_id": "g1", "market": "total", "side": "under", "point": 44.5,
         "price": -110, "stake": 1.8, "edge": 0.03},
        {"game_id": "g1", "market": "spread", "side": "home", "point": -3.0,
         "price": -110, "stake": 3.2, "edge": 0.02},
    ])
    row = build_games(games, slate=slate, bankroll=100.0).iloc[0]
    assert row["kelly_fraction"] == pytest.approx(0.032)


def test_the_games_board_carries_the_kickoff() -> None:
    """Whether a row is still bettable is not derivable from the numbers.

    The column is selected explicitly out of the banked frame, and the first
    version of this change added it to the contract without adding it to that
    selection — so every cell exported empty while the input had it all along.
    """
    games = pd.DataFrame([{
        "game_id": "g1", "league": "nfl", "home_team": "Atlanta Falcons",
        "away_team": "Green Bay Packers", "kickoff": "2026-09-22T00:15:00Z",
        "mu_home": 24.0, "mu_away": 21.0,
    }])

    assert build_games(games)["kickoff"].iloc[0] == "2026-09-22T00:15:00Z"


def test_a_frame_with_no_kickoff_still_exports() -> None:
    """Older banked frames predate the column; the board is not worth losing."""
    games = pd.DataFrame([{
        "game_id": "g1", "league": "nfl", "home_team": "Atlanta Falcons",
        "away_team": "Green Bay Packers", "mu_home": 24.0, "mu_away": 21.0,
    }])

    out = build_games(games)
    assert len(out) == 1
    assert pd.isna(out["kickoff"].iloc[0])
