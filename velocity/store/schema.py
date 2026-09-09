"""Canonical table schemas for the Velocity data store.

These define the *minimum* contract every ingest adapter must satisfy. Extra
columns are allowed (nflverse play-by-play has hundreds), but the columns
declared here must be present, correctly typed, and coercible. Validating
against these schemas at the store boundary is how we keep every downstream
layer honest.

The ``kickoff`` / ``timestamp`` columns are the point-in-time anchors: no
feature may use a row whose anchor is at or after the kickoff it predicts
(see :mod:`velocity.store.pit`).
"""

from __future__ import annotations

import pandera.pandas as pa
from pandera.pandas import Field
from pandera.typing import Series

LEAGUES = ["nfl", "ncaaf", "mlb", "wnba", "ncaab", "nhl"]
SEASON_TYPES = ["PRE", "REG", "POST"]
# Game-level markets, shared by both leagues. Team totals are the censored-
# score derivative (over/under on one side's score); the ``_home``/``_away``
# suffix keys the market to a team without a schema change.
MARKETS = [
    "spread",
    "total",
    "moneyline",
    "team_total_home",
    "team_total_away",
]
# Player-prop markets (canonical stat keys, matching the stats
# :mod:`velocity.models.props` simulates).
PROP_MARKETS = [
    "pass_yards",
    "pass_tds",
    "rush_yards",
    "receiving_yards",
    "receptions",
    "anytime_td",
    # The headline props per sport (docs/PROPS.md): MLB pitcher Ks, NHL
    # shots on goal, NBA rebounds (banked ahead of the NBA vertical).
    "pitcher_strikeouts",
    "shots_on_goal",
    "rebounds",
    # The lottery-ticket prop, modeled off batted-ball skill
    # (velocity/models/props_hr.py, docs/PROPS_HR.md).
    "batter_home_runs",
]
PROP_SIDES = ["over", "under"]

# Books whose board is a **ladder**: every number is its own tradable contract,
# quoted simultaneously, rather than one main line that moves. The distinction
# decides how a closing line is matched (docs/BUILD_EXCHANGES.md E7). A
# sportsbook's close is the same market at a possibly different number — that
# movement is precisely what ``Bet.line_clv`` measures — so its close is matched
# without regard to the number. An exchange rung's close is only ever that same
# rung: matching it loosely would report the -1.5 rung's price as the close for
# a -20.5 bet, inventing ~19 points of CLV that never existed.
LADDER_BOOKS = frozenset({"kalshi", "polymarket"})


def contract_key(market: str, side: str, point: float | None) -> float | None:
    """The contract a ``(side, point)`` row belongs to, normalized across sides.

    Anything that pairs a side against its opposite — de-vigging a quote,
    comparing two venues' closes — must group the two sides of *one* contract
    together. Spread sides carry mirrored points (home −3.5 against away
    +3.5), so both are normalized to the home side's number; totals and team
    totals already share theirs, and a moneyline has none.

    ``abs(point)`` is not a substitute: both teams' ladders exist at the same
    absolute strike (an exchange lists home −7.5 and away −7.5 as separate
    contracts), and collapsing them would cross-pair the very rungs this
    keeps apart.
    """
    if point is None:
        return None
    return -point if market == "spread" and side != "home" else point


class Games(pa.DataFrameModel):
    """One row per game. ``home_score``/``away_score`` are null until played."""

    game_id: Series[str] = pa.Field(unique=True)
    league: Series[str] = pa.Field(isin=LEAGUES)
    season: Series[int] = pa.Field(ge=1999, le=2100)
    week: Series[int] = pa.Field(ge=0, le=25)
    season_type: Series[str] = pa.Field(isin=SEASON_TYPES)
    kickoff: Series[pa.DateTime] = pa.Field()
    home_team: Series[str] = pa.Field()
    away_team: Series[str] = pa.Field()
    neutral_site: Series[bool] = pa.Field()
    roof: Series[str] = pa.Field(nullable=True)
    surface: Series[str] = pa.Field(nullable=True)
    home_score: Series[float] = pa.Field(nullable=True, ge=0)
    away_score: Series[float] = pa.Field(nullable=True, ge=0)

    class Config:
        coerce = True


class Plays(pa.DataFrameModel):
    """One row per play. The canonical minimum; adapters may add columns."""

    play_id: Series[str] = pa.Field()
    game_id: Series[str] = pa.Field()
    season: Series[int] = pa.Field(ge=1999, le=2100)
    week: Series[int] = pa.Field(ge=0, le=25)
    posteam: Series[str] = pa.Field(nullable=True)
    defteam: Series[str] = pa.Field(nullable=True)
    play_type: Series[str] = pa.Field(nullable=True)
    down: Series[float] = pa.Field(nullable=True, ge=1, le=4)
    yards_gained: Series[float] = pa.Field(nullable=True)
    epa: Series[float] = pa.Field(nullable=True)
    success: Series[bool] = pa.Field(nullable=True)

    class Config:
        coerce = True


class Players(pa.DataFrameModel):
    """One row per player-season roster entry."""

    player_id: Series[str] = pa.Field()
    player_name: Series[str] = pa.Field()
    position: Series[str] = pa.Field(nullable=True)
    team: Series[str] = pa.Field(nullable=True)
    season: Series[int] = pa.Field(ge=1999, le=2100)

    class Config:
        coerce = True


class Lines(pa.DataFrameModel):
    """One row per observed line. ``timestamp`` is the point-in-time anchor.

    ``price`` is American odds. ``point`` is the spread/total number and is
    null for moneyline markets.
    """

    line_id: Series[str] = pa.Field()
    game_id: Series[str] = pa.Field()
    book: Series[str] = pa.Field()
    market: Series[str] = pa.Field(isin=MARKETS)
    side: Series[str] = pa.Field()
    price: Series[int] = pa.Field()
    point: Series[float] = pa.Field(nullable=True)
    timestamp: Series[pa.DateTime] = pa.Field()
    is_closing: Series[bool] = pa.Field()

    class Config:
        coerce = True


class PropLines(pa.DataFrameModel):
    """One row per observed player-prop line (an over/under on a player stat).

    Like :class:`Lines` but keyed by ``player`` and ``market`` (the stat), with a
    two-way ``side`` (``over``/``under``). ``point`` is the prop line and is always
    present.
    """

    line_id: Series[str] = Field()
    game_id: Series[str] = Field()
    book: Series[str] = Field()
    market: Series[str] = Field(isin=PROP_MARKETS)
    player: Series[str] = Field()
    side: Series[str] = Field(isin=PROP_SIDES)
    price: Series[int] = Field()
    point: Series[float] = Field()
    timestamp: Series[pa.DateTime] = Field()
    is_closing: Series[bool] = Field()

    class Config:
        coerce = True
