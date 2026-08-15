"""Game-day weather — stadium coordinates and the Open-Meteo history join.

Wind is the one weather variable with a well-documented totals effect
(sustained wind above ~15 mph measurably depresses passing and scoring); cold
and precipitation matter less and less reliably. This module holds the
stadium coordinate table (city-level precision is plenty for wind) and the
pure join from a fetched daily-weather frame onto the games frame.

Only ``outdoors``/``open`` roofs get weather — domes and closed roofs are
weatherproof by construction. Relocations are handled with per-era rows
(STL→LA 2016, SD→LAC 2017, OAK→LV 2020); a new stadium in the same metro
keeps the same coordinates for wind purposes.

The fetch itself (``scripts/fetch_weather.py``) hits the free Open-Meteo
archive API once per location and banks a compact per-game parquet; this
module never touches the network.
"""

from __future__ import annotations

import pandas as pd

# team code → (first_season, last_season, latitude, longitude). ``None`` for
# last_season = current. Coordinates are the stadium site (city precision).
STADIUM_ERAS: list[tuple[str, int, int | None, float, float]] = [
    ("ARI", 2000, None, 33.5276, -112.2626),
    ("ATL", 2000, None, 33.7554, -84.4008),
    ("BAL", 2000, None, 39.2780, -76.6227),
    ("BUF", 2000, None, 42.7738, -78.7870),
    ("CAR", 2000, None, 35.2258, -80.8528),
    ("CHI", 2000, None, 41.8623, -87.6167),
    ("CIN", 2000, None, 39.0955, -84.5161),
    ("CLE", 2000, None, 41.5061, -81.6995),
    ("DAL", 2000, None, 32.7473, -97.0945),
    ("DEN", 2000, None, 39.7439, -105.0201),
    ("DET", 2000, None, 42.3400, -83.0456),
    ("GB", 2000, None, 44.5013, -88.0622),
    ("HOU", 2000, None, 29.6847, -95.4107),
    ("IND", 2000, None, 39.7601, -86.1639),
    ("JAX", 2000, None, 30.3240, -81.6373),
    ("KC", 2000, None, 39.0489, -94.4839),
    ("LA", 2000, 2015, 38.6328, -90.1885),   # St. Louis era (Edward Jones Dome)
    ("LA", 2016, None, 33.9535, -118.3392),  # Los Angeles (Coliseum → SoFi)
    ("LAC", 2000, 2016, 32.7830, -117.1195),  # San Diego era
    ("LAC", 2017, None, 33.9535, -118.3392),  # Los Angeles
    ("LV", 2000, 2019, 37.7516, -122.2005),  # Oakland era
    ("LV", 2020, None, 36.0909, -115.1833),  # Las Vegas
    ("MIA", 2000, None, 25.9580, -80.2389),
    ("MIN", 2000, None, 44.9738, -93.2577),
    ("NE", 2000, None, 42.0909, -71.2643),
    ("NO", 2000, None, 29.9511, -90.0812),
    ("NYG", 2000, None, 40.8135, -74.0745),
    ("NYJ", 2000, None, 40.8135, -74.0745),
    ("PHI", 2000, None, 39.9008, -75.1675),
    ("PIT", 2000, None, 40.4468, -80.0158),
    ("SEA", 2000, None, 47.5952, -122.3316),
    ("SF", 2000, 2013, 37.7136, -122.3863),  # Candlestick era
    ("SF", 2014, None, 37.4030, -121.9700),  # Levi's Stadium
    ("TB", 2000, None, 27.9759, -82.5033),
    ("TEN", 2000, None, 36.1665, -86.7713),
    ("WAS", 2000, None, 38.9077, -76.8645),
]

# Roof values that actually expose the game to weather.
OUTDOOR_ROOFS = ("outdoors", "open")


def stadium_coords(team: str, season: int) -> tuple[float, float] | None:
    """The home stadium's (lat, lon) for ``team`` in ``season``, or ``None``."""
    for code, first, last, lat, lon in STADIUM_ERAS:
        if code == team and season >= first and (last is None or season <= last):
            return lat, lon
    return None


def join_weather(games: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """Attach game-day weather to outdoor games.

    ``daily`` is the fetched archive frame: one row per (team-era location,
    date) with ``team``/``date``/``wind_max``/``temp_mean``/``precip``. Games
    join on (home_team, kickoff date); indoor games get NaN weather columns
    (never zero — a dome has no wind *measurement*, not calm wind).
    """
    out = games.copy()
    out["_date"] = pd.to_datetime(out["kickoff"]).dt.normalize()
    d = daily.copy()
    d["_date"] = pd.to_datetime(d["date"]).dt.normalize()
    merged = out.merge(
        d[["team", "_date", "wind_max", "temp_mean", "precip"]],
        left_on=["home_team", "_date"], right_on=["team", "_date"], how="left",
    ).drop(columns=["team", "_date"])
    indoor = ~merged["roof"].astype(str).isin(OUTDOOR_ROOFS)
    merged.loc[indoor, ["wind_max", "temp_mean", "precip"]] = float("nan")
    return merged


def wind_total_bonus(
    wind_max: float | None,
    *,
    threshold_mph: float = 15.0,
    points_per_mph: float = 0.15,
    cap_points: float = 3.0,
) -> float:
    """The per-team point suppression for a windy outdoor game (≤ 0).

    Zero below the threshold and for missing weather (indoor, or no data —
    absence of a measurement is never treated as wind). Linear above the
    threshold, capped: at the default settings a 25 mph day takes 1.5 points
    off each team (3 combined off the total).
    """
    if wind_max is None or pd.isna(wind_max):
        return 0.0
    excess = float(wind_max) - threshold_mph
    if excess <= 0:
        return 0.0
    return -min(excess * points_per_mph, cap_points)


# Teams whose current home is weatherproof (dome or reliably-closed roof) —
# the live forecast path skips them; historical joins use the per-game roof.
INDOOR_TEAMS = frozenset(
    {"ARI", "ATL", "DAL", "DET", "HOU", "IND", "LA", "LAC", "LV", "MIN", "NO"}
)


def forecast_frame(days: int = 7) -> pd.DataFrame:  # pragma: no cover - network
    """Open-Meteo wind *forecast* for every outdoor home stadium, next ``days``.

    The live sibling of the archive fetch: one request per outdoor team's
    current location, returning the ``join_weather``-shaped frame
    (``home_team``/``kickoff``/``roof``/``wind_max``) that
    ``WeatherAdjustedModel`` keys on. Best-effort per team — a failed fetch
    just means no adjustment for that stadium (never a fake calm zero).
    """
    import json
    import urllib.parse
    import urllib.request

    rows: list[dict[str, object]] = []
    for team, _first, last, lat, lon in STADIUM_ERAS:
        if last is not None or team in INDOOR_TEAMS:
            continue  # historical era, or weatherproof
        query = urllib.parse.urlencode({
            "latitude": lat, "longitude": lon,
            "daily": "wind_speed_10m_max", "wind_speed_unit": "mph",
            "forecast_days": max(1, min(days, 16)), "timezone": "UTC",
        })
        try:
            with urllib.request.urlopen(  # noqa: S310 - fixed https host
                f"https://api.open-meteo.com/v1/forecast?{query}", timeout=20
            ) as resp:
                daily = json.loads(resp.read()).get("daily") or {}
        except Exception:  # noqa: BLE001 - a stadium without forecast gets no adjustment
            continue
        for date, wind in zip(daily.get("time", []),
                              daily.get("wind_speed_10m_max", []), strict=False):
            rows.append({"home_team": team, "kickoff": pd.Timestamp(date),
                         "roof": "outdoors", "wind_max": wind,
                         "temp_mean": None, "precip": None})
    return pd.DataFrame(
        rows, columns=["home_team", "kickoff", "roof", "wind_max",
                       "temp_mean", "precip"]
    )
