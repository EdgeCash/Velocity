"""First-pitch weather (velocity.ingest.mlb_weather) — pure normalizer + sites.

Covers picking the forecast hour nearest first pitch, the wind-degrees → compass
conversion, the fixed-roof "indoors" path, and the committed ballpark table's
coverage. Network ``load_weather`` untouched.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from velocity.ingest.mlb_weather import (
    PARK_SITES,
    Weather,
    _compass,
    normalize_weather,
)
from velocity.wagering.live import MLB_TEAM_ALIASES

FIXTURES = Path(__file__).parent / "fixtures"
PAYLOAD = json.loads((FIXTURES / "mlb_weather.json").read_text())


def test_picks_hour_nearest_first_pitch() -> None:
    # First pitch 19:10 → the 19:00 hour is nearest.
    w = normalize_weather(PAYLOAD, datetime(2026, 7, 23, 19, 10))
    assert w.temp_f == 83  # rounded from 83.2
    assert w.wind_mph == 9  # rounded from 9.2
    assert w.wind_dir == "NW"  # 315°
    assert w.precip_pct == 20
    assert w.roof == "open"


def test_compass_maps_degrees() -> None:
    assert _compass(0) == "N"
    assert _compass(90) == "E"
    assert _compass(180) == "S"
    assert _compass(270) == "W"
    assert _compass(None) is None


def test_no_times_degrades_to_roof_only() -> None:
    w = normalize_weather({"hourly": {}}, datetime(2026, 7, 23, 19, 10), roof="retractable")
    assert w.temp_f is None and w.roof == "retractable"


def test_fixed_roof_indoors_flag() -> None:
    assert Weather(roof="fixed").indoors is True
    assert Weather(roof="open").indoors is False


def test_every_club_has_a_site() -> None:
    assert set(PARK_SITES) == set(MLB_TEAM_ALIASES.values())
    assert PARK_SITES["TB"].roof == "fixed"
    assert PARK_SITES["COL"].roof == "open"
