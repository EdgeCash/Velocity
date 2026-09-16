"""Game-time weather from statsapi — the bank the home-run model never had.

``velocity/models/props_hr.py`` states its own gap plainly:

    Weather is deliberately absent: temperature and wind genuinely move
    home-run distance, but we have no banked historical weather to FIT a
    coefficient on.

True, and the audit called it circular (finding 2), pointing at BettingPros'
``forecast_*`` block as the thing to start banking. That was the right
destination by the wrong route. BP serves a **forecast**, three hours out, for
games that have not happened — so banking it starts a clock that has to run a
season before a coefficient can be fitted.

statsapi serves the **observed** weather for every game already played, free,
keyless, on the endpoint next to the one the starters bank already walks.
Sampled across the committed frame it answers for **every** game, and it
answers with the right quantities:

* ``condition`` carries ``Roof Closed`` outright, so the dome exclusion the
  NFL path needs a per-stadium table for (``OUTDOOR_ROOFS``) is served here.
* ``wind`` is ballpark-relative — ``Out To CF``, ``In From LF``, ``L To R``,
  ``Calm`` — which is the vector a home run actually cares about. A compass
  degree, which is what BP serves, would need each park's orientation before
  it meant anything.
* ``temp`` is °F at first pitch.

So the fit comes off this, now, on games already banked.

**And so does the live side**, which is the part worth checking rather than
assuming. The finding's plan was BettingPros for prediction time, on the
reasoning that tonight's weather can only be forecast — but BP serves a
compass *degree*, which means nothing about a home run until you know each
park's orientation, so that route needed a per-stadium bearing table nobody
had written. statsapi turns out not to need one: probed against the live
schedule on 2026-09-16, a game in **Pre-Game** state already carries its
forecast, in the same ballpark-relative vocabulary as a finished one —

    Pre-Game, Great American Ball Park, first pitch 22:40Z
        {'condition': 'Partly Cloudy', 'temp': '76', 'wind': '7 mph, In From LF'}

— while a game still two days out carries ``{}``. One keyless feed, one
parser and one set of units for both the historical fit and the live board,
with no paid dependency and no orientation table.

The catch is the timing, and it has to be *reported* rather than absorbed: a
game the slate prices before it reaches Pre-Game has no reading, and a missing
reading falls back to a multiplier of 1.0 — which is indistinguishable from a
calm night. That is the exact shape of the Statcast prior that shipped at zero
for months (see ``HomeRunModel.statcast_batters``), so the consumer counts how
many boards got a real reading and says so.

Pure functions of payloads; the fetch lives in ``scripts/build_mlb_weather.py``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import pandas as pd

WEATHER_COLUMNS = ("game_id", "venue", "date", "condition", "temp_f",
                   "wind_mph", "wind_text", "wind_vector", "roof_closed")

# statsapi's ballpark-relative wind vocabulary → the component along the
# batter's line of fire, in [-1, 1]. Out is what carries a fly ball; in is what
# holds it up; a crosswind does neither on average, which is a measurement to
# make rather than an assumption to bake in, so it enters as 0 and the fit
# reads the speed term separately.
#
# "Out To LF"/"Out To RF" get less than centre because a ball hit to a corner
# rides only the component of that wind along its own path. The exact numbers
# are a prior, not a fit — the coefficient the model ends up using is fitted on
# this column, so the prior only sets the axis, not the size.
_WIND_VECTOR: Mapping[str, float] = {
    "out to cf": 1.0,
    "out to lf": 0.7,
    "out to rf": 0.7,
    "in from cf": -1.0,
    "in from lf": -0.7,
    "in from rf": -0.7,
    "l to r": 0.0,
    "r to l": 0.0,
    "calm": 0.0,
    "none": 0.0,
    "varies": 0.0,
}

_WIND_RE = re.compile(r"^\s*(?P<mph>-?\d+(?:\.\d+)?)\s*mph\s*,\s*(?P<dir>.+?)\s*$",
                      re.IGNORECASE)
# A closed roof is weather that has been switched off: no wind, no temperature
# swing. It is a *condition* string rather than a flag, and it is the one that
# must never be modelled as a calm day outdoors. Both spellings are observed:
# "Roof Closed" across the banked seasons, and "Dome" on the live board
# (Tropicana Field, 2026-09-16).
_ROOF_CLOSED = frozenset({"roof closed", "dome"})


def parse_wind(text: object) -> tuple[float | None, str | None]:
    """``"15 mph, R To L"`` → ``(15.0, "R To L")``; anything else → ``(None, None)``."""
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return None, None
    match = _WIND_RE.match(str(text))
    if match is None:
        return None, None
    try:
        mph = float(match["mph"])
    except (TypeError, ValueError):
        return None, None
    return mph, match["dir"]


def wind_vector(direction: object) -> float | None:
    """The out-to-centre component of a ballpark-relative wind direction.

    ``None`` for a direction statsapi has not used before — skipped and
    reported rather than guessed onto an axis it may not belong on.
    """
    if direction is None or (isinstance(direction, float) and pd.isna(direction)):
        return None
    return _WIND_VECTOR.get(str(direction).strip().casefold())


def weather_slice(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """The three blocks :func:`normalize_weather` reads, and nothing else.

    A ``feed/live`` payload is the whole game — every pitch of it, about
    **0.87 MB**. Holding one per game while walking the committed frame is
    6 GB of JSON before it is even parsed into dicts, which is an out-of-memory
    kill rather than a bank. Trimming at the point of fetch keeps a couple of
    hundred bytes a game instead, and the caller can hold all of them.
    """
    game_data = (payload or {}).get("gameData") or {}
    return {"gameData": {
        "weather": game_data.get("weather") or {},
        "venue": {"name": (game_data.get("venue") or {}).get("name")},
        "datetime": {"officialDate": (game_data.get("datetime") or {}).get(
            "officialDate")},
    }}


def normalize_weather(payloads: Mapping[str, Any]) -> pd.DataFrame:
    """``{game_id: feed/live payload}`` → one row per game.

    A game whose payload carries no weather block yields a row with nulls
    rather than no row, so the bank records that it *looked* — an absent game
    and a game with no reading are different things, and only the first is
    worth re-fetching.
    """
    rows: list[dict[str, object]] = []
    for game_id, payload in payloads.items():
        game_data = (payload or {}).get("gameData") or {}
        weather = game_data.get("weather") or {}
        condition = weather.get("condition")
        mph, direction = parse_wind(weather.get("wind"))
        temp = weather.get("temp")
        try:
            temp_f = None if temp in (None, "") else float(temp)
        except (TypeError, ValueError):
            temp_f = None
        closed = (str(condition).strip().casefold() in _ROOF_CLOSED
                  if condition is not None else False)
        rows.append({
            "game_id": str(game_id),
            "venue": str((game_data.get("venue") or {}).get("name") or "") or None,
            "date": str((game_data.get("datetime") or {}).get("officialDate") or "")
            or None,
            "condition": None if condition is None else str(condition),
            "temp_f": temp_f,
            "wind_mph": mph,
            "wind_text": direction,
            # Under a closed roof there is no wind to resolve, so the vector is
            # a measured zero rather than an unknown.
            "wind_vector": 0.0 if closed else wind_vector(direction),
            "roof_closed": closed,
        })
    return pd.DataFrame(rows, columns=list(WEATHER_COLUMNS))
