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

LEAGUES = ["nfl", "ncaaf", "mlb"]
SEASON_TYPES = ["PRE", "REG", "POST"]
# Game-level markets shared by every league. MLB's derivative markets (run line,
# and first-5-innings segments) are added with the MLB wagering phase; see
# docs/BUILD_MLB.md. Until then MLB uses the same three game markets as football.
MARKETS = ["spread", "total", "moneyline"]
# Baseball player roles for :class:`BaseballStats`.
BASEBALL_ROLES = ["bat", "pit"]


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


class BaseballStats(pa.DataFrameModel):
    """One row per player-season, per role — the rate inputs the MLB model consumes.

    Counting stats over plate appearances (``pa`` is batters faced for pitchers),
    left as counts here; the projection phase turns them into shrunk per-PA rates
    (see docs/BUILD_MLB.md, Phase M2). Every count is nullable so a partial or
    messy provider extract still validates rather than crashing the ingest.

    Pitchers reliably own K/BB/HBP/HR; the single/double/triple breakdown of balls
    in play is not part of the season pitching split, so those are left null for
    ``pit`` rows (BABIP is handled separately downstream).
    """

    player_id: Series[str] = Field()
    player_name: Series[str] = Field()
    team: Series[str] = Field(nullable=True)
    season: Series[int] = Field(ge=1999, le=2100)
    role: Series[str] = Field(isin=BASEBALL_ROLES)
    pa: Series[float] = Field(nullable=True, ge=0)
    k: Series[float] = Field(nullable=True, ge=0)
    bb: Series[float] = Field(nullable=True, ge=0)
    hbp: Series[float] = Field(nullable=True, ge=0)
    singles: Series[float] = Field(nullable=True, ge=0)
    doubles: Series[float] = Field(nullable=True, ge=0)
    triples: Series[float] = Field(nullable=True, ge=0)
    hr: Series[float] = Field(nullable=True, ge=0)

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
