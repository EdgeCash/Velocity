"""College venues, parsed from the CFBD payload rather than typed from memory.

134 FBS stadiums is too many to hand-write and far too many to keep right,
and a wrong coordinate does not fail loudly — it returns a confident forecast
for the wrong place. So the table is parsed from the ``/teams/fbs`` payload
the identity fetch already makes and caches, and these pin what it does with
the rows that are not clean (docs/FOOTBALL_PAL.md).
"""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.report.venues import Venue, parse_ncaaf_venues, venue_for

# CFBD's own shape, trimmed to the fields the parser reads.
PAYLOAD = [
    {"school": "Georgia", "mascot": "Bulldogs",
     "location": {"name": "Sanford Stadium", "latitude": 33.9498,
                  "longitude": -83.3733, "dome": False}},
    {"school": "Syracuse", "mascot": "Orange",
     "location": {"name": "JMA Wireless Dome", "latitude": 43.0362,
                  "longitude": -76.1363, "dome": True}},
    # Every shape that must be dropped rather than defaulted.
    {"school": "No Location"},
    {"school": "Null Location", "location": None},
    {"school": "No Coords", "location": {"name": "Somewhere", "dome": False}},
    {"school": "Bad Coords", "location": {"latitude": "n/a", "longitude": None}},
    {"school": "Transposed", "location": {"latitude": -83.37, "longitude": 33.95}},
    {"location": {"latitude": 40.0, "longitude": -80.0}},  # no school
]


def test_the_parser_reads_coordinates_and_the_dome_flag() -> None:
    venues = parse_ncaaf_venues(PAYLOAD)
    assert venues["Georgia"] == Venue(33.9498, -83.3733, False)
    assert venues["Syracuse"].covered is True
    assert venues["Georgia"].covered is False


@pytest.mark.parametrize(
    "school", ["No Location", "Null Location", "No Coords", "Bad Coords"])
def test_a_row_without_usable_coordinates_is_dropped(school: str) -> None:
    """Not defaulted to zero: a venue at (0, 0) is in the Atlantic, and a
    forecast for it would look like a forecast rather than a mistake."""
    assert school not in parse_ncaaf_venues(PAYLOAD)


def test_a_transposed_pair_is_dropped_rather_than_believed() -> None:
    # Swapping latitude and longitude is the one transposition that still
    # parses as a number, so the range check is the only thing that catches it.
    assert "Transposed" not in parse_ncaaf_venues(PAYLOAD)


def test_a_row_with_no_school_has_nothing_to_key_on() -> None:
    assert len(parse_ncaaf_venues(PAYLOAD)) == 2


def test_an_empty_payload_is_an_empty_table_not_an_error() -> None:
    assert parse_ncaaf_venues([]) == {}


def test_venue_for_reads_the_college_table_only_when_given_one() -> None:
    venues = parse_ncaaf_venues(PAYLOAD)
    assert venue_for("ncaaf", "Georgia", ncaaf=venues) == venues["Georgia"]
    # Without the table college resolves to None, exactly as before there was
    # one — the NFL and MLB literals are untouched by any of this.
    assert venue_for("ncaaf", "Georgia") is None
    assert venue_for("nfl", "Green Bay Packers") is not None
    assert venue_for("nfl", "Green Bay Packers", ncaaf=venues) is not None


def test_the_board_nickname_bridges_onto_the_school_key() -> None:
    """The board writes "Georgia Bulldogs"; CFBD writes "Georgia"."""
    from velocity.wagering.live import nickname_aliases

    venues = parse_ncaaf_venues(PAYLOAD)
    alias = nickname_aliases({"Georgia Bulldogs", "Syracuse Orange"}, venues.keys())
    assert venue_for("ncaaf", alias["Georgia Bulldogs"], ncaaf=venues) == venues["Georgia"]
    assert venue_for("ncaaf", alias["Syracuse Orange"], ncaaf=venues).covered is True


def test_the_college_board_reaches_the_weather_table(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: a college game gets conditions, and a dome says so."""
    import scripts.build_site_data as bsd
    import velocity.report.assets as assets

    slate_dir = tmp_path / "slate"
    slate_dir.mkdir()
    pd.DataFrame([
        {"game_id": "c1", "home_team": "Georgia Bulldogs",
         "away_team": "Auburn Tigers", "league": "ncaaf",
         "kickoff": pd.Timestamp("2026-09-19 19:30")},
        {"game_id": "c2", "home_team": "Syracuse Orange",
         "away_team": "Clemson Tigers", "league": "ncaaf",
         "kickoff": pd.Timestamp("2026-09-19 19:30")},
    ]).to_parquet(slate_dir / "games_ncaaf_20260919T120000Z.parquet", index=False)

    monkeypatch.setattr(assets, "ncaaf_venue_index",
                        lambda key, cache_dir: parse_ncaaf_venues(PAYLOAD))
    monkeypatch.setattr(bsd, "_kickoff_forecast",
                        lambda lat, lon, kickoff: {"temp_f": 71.0, "wind_mph": 9.0,
                                                   "precip_pct": 20.0})
    frame = bsd.build_weather(slate_dir).set_index("game_id")
    assert frame.loc["c1", "wind_mph"] == 9.0
    assert bool(frame.loc["c1", "covered"]) is False
    # The dome is reported as covered and never given a forecast.
    assert bool(frame.loc["c2", "covered"]) is True
    assert pd.isna(frame.loc["c2", "wind_mph"])
