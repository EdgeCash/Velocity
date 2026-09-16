"""Measuring whether weather moves the home-run rate.

`props_hr` priced no weather term and said why: nothing was banked to fit one
on. The bank exists now, so the question becomes a measurement — and a
measurement has to be able to come back empty. These pin the estimator on
data where the answer is known by construction, including the case where the
honest answer is "no effect".
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from velocity.models.hr_weather import (
    REFERENCE_TEMP_F,
    HRWeather,
    fit_linear,
    game_rates,
    rate_ratio,
    rate_ratio_table,
)


def _bank(games: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(batters, weather, games) for a list of {game_id, hr, pa, ...} rows."""
    batters = pd.DataFrame([
        {"game_id": g["game_id"], "hr": g["hr"], "pa": g["pa"]} for g in games])
    weather = pd.DataFrame([{
        "game_id": g["game_id"], "venue": g.get("park", "Park A"),
        "date": g.get("date", "2025-06-15"), "condition": "Clear",
        "temp_f": g.get("temp_f", 70.0), "wind_mph": g.get("wind_mph", 0.0),
        "wind_text": "Out To CF", "wind_vector": g.get("wind_vector", 1.0),
        "roof_closed": g.get("roof_closed", False)} for g in games])
    frame = pd.DataFrame([
        {"game_id": g["game_id"], "season": g.get("season", 2025)} for g in games])
    return batters, weather, frame


# --- the baseline each ratio is measured against ------------------------------


def test_the_baseline_is_the_games_own_park_season_and_month() -> None:
    """Coors is not Oracle, 2024's ball is not 2025's, and April is not July.

    Without all three the weather terms measure the building, the ball and the
    calendar — which the park factor and the batter's own rate already carry.
    """
    batters, weather, games = _bank([
        {"game_id": "a", "hr": 4, "pa": 80, "park": "Coors", "date": "2025-07-04"},
        {"game_id": "b", "hr": 2, "pa": 80, "park": "Coors", "date": "2025-07-08"},
        {"game_id": "c", "hr": 1, "pa": 80, "park": "Oracle", "date": "2025-07-04"},
        {"game_id": "d", "hr": 1, "pa": 80, "park": "Oracle", "date": "2025-07-08"},
    ])
    frame = game_rates(batters, weather, games).set_index("game_id")
    # Coors' cell runs 6 HR / 160 PA; each of its games expects 80 × that.
    assert frame.loc["a", "expected"] == 3.0
    assert frame.loc["b", "expected"] == 3.0
    # Oracle's own cell is half that rate, and is not dragged up by Coors.
    assert frame.loc["c", "expected"] == 1.0
    assert frame.loc["d", "expected"] == 1.0


def test_two_months_at_one_park_get_their_own_baselines() -> None:
    batters, weather, games = _bank([
        {"game_id": "a", "hr": 4, "pa": 80, "date": "2025-04-10"},
        {"game_id": "b", "hr": 4, "pa": 80, "date": "2025-04-12"},
        {"game_id": "c", "hr": 8, "pa": 80, "date": "2025-07-10"},
        {"game_id": "d", "hr": 8, "pa": 80, "date": "2025-07-12"},
    ])
    frame = game_rates(batters, weather, games).set_index("game_id")
    assert frame.loc["a", "expected"] == 4.0
    assert frame.loc["c", "expected"] == 8.0  # July is its own cell, not diluted


def test_the_wind_component_is_speed_times_direction() -> None:
    batters, weather, games = _bank([
        {"game_id": "a", "hr": 1, "pa": 40, "wind_mph": 10.0, "wind_vector": 1.0},
        {"game_id": "b", "hr": 1, "pa": 40, "wind_mph": 10.0, "wind_vector": -0.7},
    ])
    frame = game_rates(batters, weather, games).set_index("game_id")
    assert frame.loc["a", "wind_out"] == 10.0
    assert frame.loc["b", "wind_out"] == -7.0


def test_a_game_with_no_plate_appearances_is_not_a_game() -> None:
    batters, weather, games = _bank([
        {"game_id": "a", "hr": 0, "pa": 0},
        {"game_id": "b", "hr": 2, "pa": 80},
    ])
    assert list(game_rates(batters, weather, games)["game_id"]) == ["b"]


def test_an_empty_bank_measures_nothing() -> None:
    assert game_rates(pd.DataFrame(), pd.DataFrame(), pd.DataFrame()).empty


# --- the ratio ----------------------------------------------------------------


def test_the_ratio_is_observed_over_expected() -> None:
    frame = pd.DataFrame({"hr": [4.0, 2.0], "expected": [2.0, 2.0]})
    ratio, se = rate_ratio(frame)
    assert ratio == 1.5
    assert se == np.sqrt(6.0) / 4.0


def test_a_bin_with_nothing_in_it_says_nothing_rather_than_zero() -> None:
    """A rate ratio of 0.0 means "no home runs"; this means "no games"."""
    ratio, se = rate_ratio(pd.DataFrame({"hr": [], "expected": []}))
    assert np.isnan(ratio) and np.isnan(se)


def test_the_table_bins_on_the_half_open_interval() -> None:
    frame = pd.DataFrame({"wind_out": [-5.0, 0.0, 5.0], "hr": [1.0, 1.0, 1.0],
                          "expected": [1.0, 1.0, 1.0]})
    table = rate_ratio_table(frame, "wind_out", (-10, 0, 10))
    assert list(table["games"]) == [1, 2]  # 0.0 lands in the upper bin


# --- the fit ------------------------------------------------------------------


def _synthetic(slope: float, n: int = 4000, seed: int = 0) -> pd.DataFrame:
    """Games whose home-run rate really does move with the wind, by ``slope``."""
    rng = np.random.default_rng(seed)
    wind = rng.uniform(-15.0, 15.0, n)
    pa = np.full(n, 76.0)
    base = 0.028
    hr = rng.binomial(pa.astype(int), np.clip(base * (1.0 + slope * wind), 0, 1))
    return pd.DataFrame({"wind_out": wind, "pa": pa, "hr": hr.astype(float),
                         "expected": pa * base})


def test_the_fit_recovers_an_effect_that_is_really_there() -> None:
    fit = fit_linear(_synthetic(0.012), "wind_out")
    assert fit.significant
    assert abs(fit.per_unit - 0.012) < 0.003
    assert fit.factor(10.0) == 1.0 + fit.per_unit * 10.0


def test_the_fit_refuses_an_effect_that_is_not_there() -> None:
    """The measurement has to be able to come back empty.

    A weather term that gets priced because the estimator cannot say "no" is
    worse than the absent one the model shipped with.
    """
    fit = fit_linear(_synthetic(0.0), "wind_out")
    assert not fit.significant
    assert abs(fit.per_unit) < 0.004


def test_the_line_passes_through_one_at_the_reference() -> None:
    """The fit is a deviation from average conditions, not a second estimate
    of the average — the park factor already carries that."""
    frame = _synthetic(0.01).assign(temp_f=lambda d: d["wind_out"] + REFERENCE_TEMP_F)
    fit = fit_linear(frame, "temp_f", centre=REFERENCE_TEMP_F)
    assert fit.factor(0.0) == 1.0


def test_a_factor_can_never_go_negative() -> None:
    """A 40 mph gale in must not price a negative probability."""
    fit = fit_linear(_synthetic(0.05), "wind_out")
    assert fit.factor(-1000.0) == 0.0


def test_fitting_nothing_returns_nothing_rather_than_raising() -> None:
    empty = pd.DataFrame({"wind_out": [], "hr": [], "expected": []})
    fit = fit_linear(empty, "wind_out")
    assert np.isnan(fit.per_unit) and not fit.significant and fit.games == 0


# --- the fitted effect, and what it refuses to do -----------------------------


def test_the_reference_conditions_are_exactly_neutral() -> None:
    """A calm 70F night must not nudge the rate at all."""
    w = HRWeather()
    assert w.factor(wind_out=0.0, temp_f=REFERENCE_TEMP_F) == 1.0


def test_the_fitted_directions_are_the_ones_football_and_physics_agree_on() -> None:
    w = HRWeather()
    assert w.wind_factor(10.0) > 1.0 > w.wind_factor(-10.0)
    assert w.temp_factor(90.0) > 1.0 > w.temp_factor(50.0)


def test_the_clamp_refuses_to_extrapolate_past_the_evidence() -> None:
    """The one pathology a straight line has.

    Unclamped, +0.00743/mph predicts x1.19 for a 25 mph tailwind. The games
    above 12 mph actually come in at x1.03, so the line is extrapolating a
    trend past the data that produced it. The clamp is a safety rail, not an
    accuracy claim — it barely binds inside the fitted range.
    """
    w = HRWeather()
    assert w.wind_factor(25.0) == w.wind_factor(12.0)
    assert w.wind_factor(-25.0) == w.wind_factor(-12.0)
    assert w.wind_factor(25.0) < 1.0 + 0.00743 * 25.0
    assert w.temp_factor(115.0) == w.temp_factor(95.0)
    assert w.temp_factor(20.0) == w.temp_factor(45.0)


def test_a_game_with_no_reading_gets_exactly_one_and_says_so() -> None:
    """The dangerous case, and the only thing that can tell it apart.

    A missing forecast produces the same multiplier as a calm 70F night, so
    the output cannot distinguish them — only the coverage count can. This is
    the Statcast prior that shipped at zero for months, in a different coat.
    """
    w = HRWeather()
    assert w.factor() == 1.0
    assert w.factor(wind_out=None, temp_f=None) == 1.0
    assert w.has_reading(None, None) is False
    assert w.has_reading(0.0, None) is True   # a measured calm IS a reading
    assert w.has_reading(None, 70.0) is True


def test_a_closed_roof_is_a_reading_not_a_gap() -> None:
    """There is no weather in there, so 1.0 is measured rather than missing.

    Checked against the bank: closed-roof games land at 0.993 +/- 0.019 of
    their own park-season-month baseline.
    """
    w = HRWeather()
    assert w.factor(wind_out=9.0, temp_f=95.0, roof_closed=True) == 1.0


def test_one_driver_alone_still_prices() -> None:
    """A feed that gives temperature but no usable wind is not nothing."""
    w = HRWeather()
    assert w.factor(temp_f=90.0) > 1.0
    assert w.factor(wind_out=-8.0) < 1.0


def test_the_factor_can_never_go_negative() -> None:
    absurd = HRWeather(wind_per_mph=-10.0, wind_clamp_mph=1e6)
    assert absurd.wind_factor(1000.0) == 0.0

