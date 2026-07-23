"""First-pitch weather — committed ballpark sites + Open-Meteo forecast.

A matchup card's conditions strip wants the game-time temperature, wind, and rain
chance. Open-Meteo serves an hourly forecast for any lat/long with no API key, so
the only committed data is the ballpark table (:data:`PARK_SITES`): each park's
coordinates and roof type. A fixed-roof park (Tropicana) reports "indoors" without
a fetch; a retractable roof is noted but still gets a forecast (the roof may be
open).

Pure/​network split as ever: ``normalize_weather`` picks the forecast hour nearest
first pitch from an already-parsed Open-Meteo payload; ``load_weather`` fetches.
Weather is a *forecast* — the card labels it as such. Any missing field is
``None`` and simply isn't shown.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

_OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
_FETCH_TIMEOUT = 60

_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


@dataclass(frozen=True)
class ParkSite:
    """A ballpark's coordinates and roof type (``open``/``retractable``/``fixed``)."""

    lat: float
    lon: float
    roof: str


@dataclass(frozen=True)
class Weather:
    """First-pitch conditions; a fixed roof carries ``roof="fixed"`` and no forecast."""

    temp_f: float | None = None
    wind_mph: float | None = None
    wind_dir: str | None = None
    precip_pct: int | None = None
    roof: str | None = None

    @property
    def indoors(self) -> bool:
        return self.roof == "fixed"


# Keyed by the card's team code. Coordinates in decimal degrees.
PARK_SITES: dict[str, ParkSite] = {
    "ARI": ParkSite(33.4455, -112.0667, "retractable"),
    "ATL": ParkSite(33.8907, -84.4677, "open"),
    "ATH": ParkSite(38.5804, -121.5130, "open"),
    "BAL": ParkSite(39.2839, -76.6218, "open"),
    "BOS": ParkSite(42.3467, -71.0972, "open"),
    "CHC": ParkSite(41.9484, -87.6553, "open"),
    "CWS": ParkSite(41.8299, -87.6338, "open"),
    "CIN": ParkSite(39.0975, -84.5069, "open"),
    "CLE": ParkSite(41.4962, -81.6852, "open"),
    "COL": ParkSite(39.7559, -104.9942, "open"),
    "DET": ParkSite(42.3390, -83.0485, "open"),
    "HOU": ParkSite(29.7572, -95.3555, "retractable"),
    "KC": ParkSite(39.0517, -94.4803, "open"),
    "LAA": ParkSite(33.8003, -117.8827, "open"),
    "LAD": ParkSite(34.0739, -118.2400, "open"),
    "MIA": ParkSite(25.7781, -80.2196, "retractable"),
    "MIL": ParkSite(43.0280, -87.9712, "retractable"),
    "MIN": ParkSite(44.9817, -93.2776, "open"),
    "NYM": ParkSite(40.7571, -73.8458, "open"),
    "NYY": ParkSite(40.8296, -73.9262, "open"),
    "PHI": ParkSite(39.9061, -75.1665, "open"),
    "PIT": ParkSite(40.4469, -80.0057, "open"),
    "SD": ParkSite(32.7073, -117.1566, "open"),
    "SEA": ParkSite(47.5914, -122.3325, "retractable"),
    "SF": ParkSite(37.7786, -122.3893, "open"),
    "STL": ParkSite(38.6226, -90.1928, "open"),
    "TB": ParkSite(27.7683, -82.6534, "fixed"),
    "TEX": ParkSite(32.7473, -97.0842, "retractable"),
    "TOR": ParkSite(43.6414, -79.3894, "retractable"),
    "WSH": ParkSite(38.8730, -77.0074, "open"),
}


def _compass(degrees: float | None) -> str | None:
    if degrees is None:
        return None
    return _COMPASS[int((degrees % 360) / 22.5 + 0.5) % 16]


def _nearest_hour(times: list[str], target: datetime) -> int | None:
    """Index of the forecast hour closest to ``target`` (naive-datetime compare)."""
    best_i: int | None = None
    best_gap = None
    for i, raw in enumerate(times):
        try:
            t = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        gap = abs((t.replace(tzinfo=None) - target.replace(tzinfo=None)).total_seconds())
        if best_gap is None or gap < best_gap:
            best_gap, best_i = gap, i
    return best_i


def normalize_weather(
    payload: Mapping[str, Any], first_pitch: datetime, *, roof: str = "open"
) -> Weather:
    """Pick the Open-Meteo hour nearest ``first_pitch`` into a :class:`Weather`."""
    hourly = payload.get("hourly") or {}
    times = list(hourly.get("time") or [])
    idx = _nearest_hour(times, first_pitch) if times else None
    if idx is None:
        return Weather(roof=roof)

    def at(key: str) -> Any:
        series = hourly.get(key) or []
        return series[idx] if idx < len(series) else None

    temp = at("temperature_2m")
    wind = at("wind_speed_10m")
    wdir = at("wind_direction_10m")
    precip = at("precipitation_probability")
    return Weather(
        temp_f=None if temp is None else round(float(temp)),
        wind_mph=None if wind is None else round(float(wind)),
        wind_dir=_compass(None if wdir is None else float(wdir)),
        precip_pct=None if precip is None else int(precip),
        roof=roof,
    )


def load_weather(  # pragma: no cover - network
    home_code: str, first_pitch: datetime
) -> Weather | None:
    """Forecast at first pitch for the home park, or ``None`` if the park is unknown."""
    site = PARK_SITES.get(home_code)
    if site is None:
        return None
    if site.roof == "fixed":
        return Weather(roof="fixed")  # climate-controlled; no forecast needed
    day = first_pitch.strftime("%Y-%m-%d")
    url = (
        f"{_OPEN_METEO}?latitude={site.lat}&longitude={site.lon}"
        "&hourly=temperature_2m,wind_speed_10m,wind_direction_10m,precipitation_probability"
        "&temperature_unit=fahrenheit&wind_speed_unit=mph"
        f"&start_date={day}&end_date={day}&timezone=auto"
    )
    with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
        payload = json.loads(resp.read())
    return normalize_weather(payload, first_pitch, roof=site.roof)
