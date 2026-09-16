"""Game-time weather — the bank the home-run model said it did not have.

`props_hr.py` declined to price weather because there was nothing banked to
fit a coefficient on, and audit finding 2 called that circular while pointing
at BettingPros' forecast block. That was the right destination by the wrong
route: a forecast is for a game that has not happened, so banking it starts a
clock. statsapi serves the **observed** reading for every game already played,
and it serves it in the terms a home run cares about — ballpark-relative wind,
and a closed roof named outright.
"""

from __future__ import annotations

import pandas as pd
from velocity.ingest.mlb_weather import (
    WEATHER_COLUMNS,
    normalize_weather,
    parse_wind,
    weather_slice,
    wind_vector,
)


def _payload(**weather: object) -> dict:
    return {"gameData": {"weather": weather,
                         "venue": {"name": "Wrigley Field"},
                         "datetime": {"officialDate": "2025-06-21"}}}


# --- reading the wind ---------------------------------------------------------


def test_the_wind_string_splits_into_speed_and_direction() -> None:
    assert parse_wind("15 mph, R To L") == (15.0, "R To L")
    assert parse_wind("4 mph, In From LF") == (4.0, "In From LF")
    assert parse_wind("0 mph, None") == (0.0, "None")


def test_a_wind_string_in_no_shape_we_know_reads_as_nothing() -> None:
    """Absent is a fact; a guessed speed is a mispriced home run."""
    for text in (None, "", "breezy", "15", "mph, R To L", float("nan")):
        assert parse_wind(text) == (None, None)


def test_the_direction_is_resolved_along_the_line_of_fire() -> None:
    """Out carries a fly ball, in holds it up, a crosswind does neither.

    The corner winds get less than centre because a ball hit that way rides
    only the component along its own path. These are a prior setting the axis;
    the size is whatever the fit on this column turns out to be.
    """
    assert wind_vector("Out To CF") == 1.0
    assert wind_vector("In From CF") == -1.0
    assert wind_vector("Out To LF") == wind_vector("Out To RF") == 0.7
    assert wind_vector("In From LF") == wind_vector("In From RF") == -0.7
    assert wind_vector("L To R") == wind_vector("R To L") == 0.0
    assert wind_vector("Calm") == wind_vector("None") == wind_vector("Varies") == 0.0


def test_the_direction_lookup_ignores_case_and_padding() -> None:
    assert wind_vector("  out to cf ") == 1.0


def test_a_direction_the_feed_has_not_used_before_resolves_to_nothing() -> None:
    """Skipped and reported, never placed on an axis it may not belong on."""
    assert wind_vector("Out To Left-Center") is None
    assert wind_vector(None) is None


# --- the banked row -----------------------------------------------------------


def test_a_closed_roof_is_weather_switched_off_not_a_calm_day() -> None:
    """The one reading that must never be modelled as an outdoor calm.

    It also saves the NFL path's per-stadium roof table: statsapi names the
    condition, so nothing here has to know which parks have roofs.
    """
    frame = normalize_weather({"1": _payload(
        condition="Roof Closed", temp="68", wind="0 mph, None")})
    row = frame.iloc[0]
    assert bool(row["roof_closed"]) is True
    assert row["wind_vector"] == 0.0  # measured zero, not an unknown


def test_an_outdoor_game_carries_its_reading(  ) -> None:
    frame = normalize_weather({"1": _payload(
        condition="Partly Cloudy", temp="89", wind="15 mph, Out To CF")})
    row = frame.iloc[0]
    assert row["temp_f"] == 89.0
    assert row["wind_mph"] == 15.0
    assert row["wind_text"] == "Out To CF"
    assert row["wind_vector"] == 1.0
    assert bool(row["roof_closed"]) is False
    assert row["venue"] == "Wrigley Field" and row["date"] == "2025-06-21"


def test_a_game_with_no_weather_block_still_banks_a_row() -> None:
    """An absent game and a game with no reading are different things.

    Only the first is worth re-fetching, so the bank has to record that it
    looked — otherwise the incremental walk re-buys the same nothing forever.
    """
    frame = normalize_weather({"1": {"gameData": {}}})
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["game_id"] == "1"
    assert pd.isna(row["temp_f"]) and pd.isna(row["wind_mph"])
    assert bool(row["roof_closed"]) is False


def test_a_temperature_that_is_not_a_number_does_not_become_one() -> None:
    frame = normalize_weather({"1": _payload(
        condition="Clear", temp="", wind="3 mph, L To R")})
    assert pd.isna(frame.iloc[0]["temp_f"])
    assert frame.iloc[0]["wind_mph"] == 3.0  # the rest of the row survives


def test_the_frame_is_the_banked_shape_even_when_empty() -> None:
    frame = normalize_weather({})
    assert list(frame.columns) == list(WEATHER_COLUMNS)
    assert frame.empty


# --- and it has to fit in memory ----------------------------------------------


def test_the_payload_is_trimmed_at_the_point_of_fetch() -> None:
    """A feed/live payload is the whole game — every pitch, about 0.87 MB.

    Holding one per game across the committed frame is ~6 GB of JSON before it
    is parsed into dicts: an out-of-memory kill rather than a bank, which is
    what the first version of the backfill would have done. The slice keeps
    the three blocks the normalizer reads and drops the rest.
    """
    payload = {"gameData": {
        "weather": {"condition": "Clear", "temp": "70", "wind": "5 mph, Out To CF"},
        "venue": {"name": "Wrigley Field", "id": 17, "location": {"city": "Chicago"}},
        "datetime": {"officialDate": "2025-06-21", "dayNight": "day"},
        "players": {f"ID{i}": {"fullName": "x" * 200} for i in range(60)},
    }, "liveData": {"plays": {"allPlays": [{"junk": "y" * 500} for _ in range(300)]}}}

    slim = weather_slice(payload)

    assert len(repr(slim)) < len(repr(payload)) / 50
    assert set(slim["gameData"]) == {"weather", "venue", "datetime"}
    # And it still normalizes to the same row the full payload would.
    assert normalize_weather({"1": slim}).iloc[0]["wind_vector"] == 1.0
    assert normalize_weather({"1": slim}).iloc[0]["venue"] == "Wrigley Field"


def test_slicing_a_payload_with_nothing_in_it_does_not_raise() -> None:
    for payload in (None, {}, {"gameData": None}, {"gameData": {"venue": None}}):
        assert normalize_weather({"1": weather_slice(payload)}).shape[0] == 1


def test_both_spellings_of_a_roof_are_a_roof() -> None:
    """"Roof Closed" across the banked seasons, "Dome" on the live board.

    Observed at Tropicana Field on 2026-09-16. Either one missed would price a
    domed park as an outdoor calm, which is the one reading that must never be
    modelled that way.
    """
    for condition in ("Roof Closed", "Dome", "dome", "  ROOF CLOSED "):
        frame = normalize_weather({"1": _payload(
            condition=condition, temp="72", wind="0 mph, None")})
        assert bool(frame.iloc[0]["roof_closed"]) is True, condition
        assert frame.iloc[0]["wind_vector"] == 0.0


def test_a_game_too_far_out_to_forecast_reads_as_no_reading() -> None:
    """statsapi carries a forecast from Pre-Game, and ``{}`` before that.

    The live board will meet both. A game with no reading must come back null
    rather than zero — a zero wind is a calm night, which is a claim.
    """
    frame = normalize_weather({"1": {"gameData": {"weather": {},
                                                  "venue": {"name": "Wrigley Field"},
                                                  "datetime": {}}}})
    row = frame.iloc[0]
    assert pd.isna(row["wind_mph"]) and pd.isna(row["temp_f"])
    assert pd.isna(row["wind_vector"])
    assert bool(row["roof_closed"]) is False

