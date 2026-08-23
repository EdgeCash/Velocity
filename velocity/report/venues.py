"""Home-venue reference data for the weather panel.

Coordinates are stadium/city-level (Open-Meteo's forecast grid is coarser
than any stadium footprint) and ``covered`` marks fixed or retractable
roofs — for a covered venue the site shows "indoors" instead of a
forecast. Only the outdoor-weather leagues are mapped: NFL and MLB.
College football's venue list is large enough to be its own project, and
the basketball leagues play indoors.

Keys are the full home-team names as they appear in the games frames
(The Odds API naming).
"""

from __future__ import annotations

from typing import NamedTuple


class Venue(NamedTuple):
    lat: float
    lon: float
    covered: bool


_NFL: dict[str, Venue] = {
    "Arizona Cardinals": Venue(33.53, -112.26, True),
    "Atlanta Falcons": Venue(33.76, -84.40, True),
    "Baltimore Ravens": Venue(39.28, -76.62, False),
    "Buffalo Bills": Venue(42.77, -78.79, False),
    "Carolina Panthers": Venue(35.23, -80.85, False),
    "Chicago Bears": Venue(41.86, -87.62, False),
    "Cincinnati Bengals": Venue(39.10, -84.52, False),
    "Cleveland Browns": Venue(41.51, -81.70, False),
    "Dallas Cowboys": Venue(32.75, -97.09, True),
    "Denver Broncos": Venue(39.74, -105.02, False),
    "Detroit Lions": Venue(42.34, -83.05, True),
    "Green Bay Packers": Venue(44.50, -88.06, False),
    "Houston Texans": Venue(29.68, -95.41, True),
    "Indianapolis Colts": Venue(39.76, -86.16, True),
    "Jacksonville Jaguars": Venue(30.32, -81.64, False),
    "Kansas City Chiefs": Venue(39.05, -94.48, False),
    "Las Vegas Raiders": Venue(36.09, -115.18, True),
    "Los Angeles Chargers": Venue(33.95, -118.34, True),
    "Los Angeles Rams": Venue(33.95, -118.34, True),
    "Miami Dolphins": Venue(25.96, -80.24, False),
    "Minnesota Vikings": Venue(44.97, -93.26, True),
    "New England Patriots": Venue(42.09, -71.26, False),
    "New Orleans Saints": Venue(29.95, -90.08, True),
    "New York Giants": Venue(40.81, -74.07, False),
    "New York Jets": Venue(40.81, -74.07, False),
    "Philadelphia Eagles": Venue(39.90, -75.17, False),
    "Pittsburgh Steelers": Venue(40.45, -80.02, False),
    "San Francisco 49ers": Venue(37.40, -121.97, False),
    "Seattle Seahawks": Venue(47.60, -122.33, False),
    "Tampa Bay Buccaneers": Venue(27.98, -82.50, False),
    "Tennessee Titans": Venue(36.17, -86.77, False),
    "Washington Commanders": Venue(38.91, -76.86, False),
}

_MLB: dict[str, Venue] = {
    "Arizona Diamondbacks": Venue(33.45, -112.07, True),
    "Atlanta Braves": Venue(33.89, -84.47, False),
    "Baltimore Orioles": Venue(39.28, -76.62, False),
    "Boston Red Sox": Venue(42.35, -71.10, False),
    "Chicago Cubs": Venue(41.95, -87.66, False),
    "Chicago White Sox": Venue(41.83, -87.63, False),
    "Cincinnati Reds": Venue(39.10, -84.51, False),
    "Cleveland Guardians": Venue(41.50, -81.69, False),
    "Colorado Rockies": Venue(39.76, -104.99, False),
    "Detroit Tigers": Venue(42.34, -83.05, False),
    "Houston Astros": Venue(29.76, -95.36, True),
    "Kansas City Royals": Venue(39.05, -94.48, False),
    "Los Angeles Angels": Venue(33.80, -117.88, False),
    "Los Angeles Dodgers": Venue(34.07, -118.24, False),
    "Miami Marlins": Venue(25.78, -80.22, True),
    "Milwaukee Brewers": Venue(43.03, -87.97, True),
    "Minnesota Twins": Venue(44.98, -93.28, False),
    "New York Mets": Venue(40.76, -73.85, False),
    "New York Yankees": Venue(40.83, -73.93, False),
    "Athletics": Venue(38.58, -121.51, False),
    "Oakland Athletics": Venue(38.58, -121.51, False),
    "Philadelphia Phillies": Venue(39.91, -75.17, False),
    "Pittsburgh Pirates": Venue(40.45, -80.01, False),
    "San Diego Padres": Venue(32.71, -117.16, False),
    "San Francisco Giants": Venue(37.78, -122.39, False),
    "Seattle Mariners": Venue(47.59, -122.33, True),
    "St. Louis Cardinals": Venue(38.62, -90.19, False),
    "Tampa Bay Rays": Venue(27.77, -82.65, True),
    "Texas Rangers": Venue(32.75, -97.08, True),
    "Toronto Blue Jays": Venue(43.64, -79.39, True),
    "Washington Nationals": Venue(38.87, -77.01, False),
}

VENUES: dict[str, dict[str, Venue]] = {"nfl": _NFL, "mlb": _MLB}


def venue_for(league: str, home_team: str) -> Venue | None:
    """The home venue, or None for unmapped teams/leagues (indoor sports)."""
    return VENUES.get(league, {}).get(home_team)
