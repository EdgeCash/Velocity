"""The weather record: what the model did to each total, banked per run.

The forecast is fetched, priced into every projection and — until this — was
thrown away, so a windy total could not explain itself anywhere downstream.
These pin the frame the runner writes (docs/FOOTBALL_PAL.md).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).parent.parent


def _runner():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "run_live_slate", REPO / "scripts" / "run_live_slate.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


# The model's own team keys — nflverse abbreviations, which is how the
# forecast frame is keyed and what resolve_team maps the board's names onto.
_KNOWN = ["GB", "MIA", "CHI", "NYJ", "DEN", "LV"]


def _events() -> pd.DataFrame:
    return pd.DataFrame({
        "game_id": ["g1", "g2"],
        "home_team": ["GB", "MIA"],
        "away_team": ["CHI", "NYJ"],
        "kickoff": pd.to_datetime(["2025-12-14 18:00", "2025-10-05 17:00"]),
    })


def _model():  # type: ignore[no-untyped-def]
    from velocity.backtest.lab import WeatherAdjustedModel

    class _Inner:
        def project(self, home, away, **kwargs):  # type: ignore[no-untyped-def]
            return "proj"

    weather = pd.DataFrame({
        "home_team": ["GB", "MIA"],
        "kickoff": pd.to_datetime(["2025-12-14", "2025-10-05"]),
        "roof": ["outdoors", "outdoors"],
        "wind_max": [25.0, 8.0],
        "temp_mean": [20.0, 85.0],
        "precip": [0.0, 0.0],
    })
    return WeatherAdjustedModel(_Inner(), weather, points_per_mph=0.30)


def test_the_frame_records_the_applied_adjustment_per_game() -> None:
    runner = _runner()
    frame = runner.weather_frame(_model(), _events(), _KNOWN)
    assert list(frame.columns) == runner.WEATHER_COLUMNS
    assert set(frame["game_id"]) == {"g1", "g2"}
    windy = frame.set_index("game_id").loc["g1"]
    # (25 − 15) × 0.30 = −3.0 a side, so −6.0 off the total.
    assert windy["wind_mph"] == 25.0
    assert windy["wind_points"] == pytest.approx(-3.0)
    assert windy["total_points"] == pytest.approx(-6.0)
    # A calm outdoor game is recorded, at zero — it was measured and found calm.
    calm = frame.set_index("game_id").loc["g2"]
    assert calm["wind_mph"] == 8.0
    assert calm["total_points"] == 0.0


def test_a_league_with_no_weather_model_records_nothing_not_zeroes() -> None:
    """"Not adjusted" and "adjusted by nothing" are different claims.

    Only the NFL carries a weather wrapper — the lab measured wind on NFL
    totals and there is no college stadium coordinate table — so every other
    league must produce an empty frame rather than a table of zeroes that
    reads as a measurement.
    """
    runner = _runner()
    frame = runner.weather_frame(None, _events(), _KNOWN)
    assert frame.empty
    assert list(frame.columns) == runner.WEATHER_COLUMNS


def test_the_board_name_is_resolved_to_the_model_name_before_the_lookup() -> None:
    """The bug this function was written with, pinned.

    The forecast frame is keyed by nflverse abbreviation ("GB") and The Odds
    API sends club names ("Green Bay Packers"), so looking the raw board name
    up finds nothing, every row comes back NaN, and the record is silently
    empty on every live run. The resolution has to be the same one
    project_board makes, or the recorded weather is not the priced weather.
    """
    runner = _runner()
    events = pd.DataFrame({
        "game_id": ["g1", "g2"],
        "home_team": ["Green Bay Packers", "Miami Dolphins"],
        "away_team": ["Chicago Bears", "New York Jets"],
        "kickoff": pd.to_datetime(["2025-12-14 18:00", "2025-10-05 17:00"]),
    })
    known = ["GB", "MIA", "CHI", "NYJ"]
    frame = runner.weather_frame(_model(), events, known)
    assert len(frame) == 2, "the club names never reached the forecast frame"
    windy = frame.set_index("game_id").loc["g1"]
    assert windy["wind_mph"] == 25.0
    assert windy["total_points"] == pytest.approx(-6.0)
    # The row still carries the BOARD's spelling, which is what every other
    # persisted frame and the site join on.
    assert windy["home_team"] == "Green Bay Packers"

    # Without the resolution there is nothing to find — the shape of the bug.
    assert runner.weather_frame(_model(), events, []).empty


def test_a_game_the_forecast_never_covered_is_dropped() -> None:
    runner = _runner()
    events = _events()
    events.loc[len(events)] = {
        "game_id": "g3", "home_team": "DEN", "away_team": "LV",
        "kickoff": pd.Timestamp("2025-12-14 21:00"),
    }
    frame = runner.weather_frame(_model(), events, _KNOWN)
    # Denver is not in the forecast frame, so it has no row rather than a
    # calm-looking one.
    assert set(frame["game_id"]) == {"g1", "g2"}


def test_an_empty_board_gives_a_typed_empty_frame() -> None:
    runner = _runner()
    frame = runner.weather_frame(_model(), pd.DataFrame(), _KNOWN)
    assert frame.empty
    assert list(frame.columns) == runner.WEATHER_COLUMNS
