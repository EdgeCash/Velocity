"""The Odds API ingest adapter — historical + live odds → canonical store.

The Odds API is the one paid feed with a real **historical archive**, so it is the
source for the closing-line snapshots that power CLV measurement and the
market-facing backtest (BettingPros, by contrast, is live-only). Both its live
``/odds`` and ``/historical/.../odds`` endpoints return the same event shape, so a
single pure normalizer serves both.

Two layers, kept strictly separate so the test gate stays offline:

* ``normalize_odds_events`` — **pure**: flattens the nested
  ``events → bookmakers → markets → outcomes`` JSON onto the canonical
  :class:`~velocity.store.schema.Lines` schema. Offline, deterministic, tested
  against frozen samples.
* :class:`TheOddsAPIClient` — the network layer. The key comes from the
  environment (``THE_ODDS_API``) only — never a literal — so the collector reads
  it from a GitHub Actions secret and the sandbox never sees it.

Convention notes:

* Markets. The three game markets map by key: ``h2h`` → ``moneyline``,
  ``spreads`` → ``spread``, ``totals`` → ``total``. Other market keys (props,
  alternates) are ignored — they don't fit the game-level Lines schema.
* Prices. Requested in ``american`` format, so ``price`` is already the American
  integer the schema wants. ``point`` is the outcome's number (null for
  moneyline, whose outcomes carry no point).
* ``game_id`` is The Odds API's own event id, which does not match our
  nflverse/CFBD ids; joining a snapshot to our games is done later by
  (teams, date). Credits: historical calls cost more than live — spend the
  100k/month budget deliberately.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd

from velocity.store.schema import Lines

_BASE = "https://api.the-odds-api.com/v4"
_FETCH_TIMEOUT = 60

# The Odds API market key → canonical Lines market. Anything else is ignored.
GAME_MARKET_BY_KEY = {
    "h2h": "moneyline",
    "spreads": "spread",
    "totals": "total",
}

# Friendly league → The Odds API sport key.
SPORT_KEYS = {
    "nfl": "americanfootball_nfl",
    "ncaaf": "americanfootball_ncaaf",
    "mlb": "baseball_mlb",
}

_LINES_COLUMNS = [
    "line_id",
    "game_id",
    "book",
    "market",
    "side",
    "price",
    "point",
    "timestamp",
    "is_closing",
]


def _empty_lines() -> pd.DataFrame:
    empty = pd.DataFrame(
        {
            "line_id": pd.Series(dtype=str),
            "game_id": pd.Series(dtype=str),
            "book": pd.Series(dtype=str),
            "market": pd.Series(dtype=str),
            "side": pd.Series(dtype=str),
            "price": pd.Series(dtype="int64"),
            "point": pd.Series(dtype=float),
            "timestamp": pd.Series(dtype="datetime64[ns]"),
            "is_closing": pd.Series(dtype=bool),
        }
    )
    return Lines.validate(empty)


def normalize_odds_events(
    events: Iterable[Mapping[str, Any]], is_closing: bool = False
) -> pd.DataFrame:
    """Flatten a The Odds API events array onto the canonical ``Lines`` schema.

    ``events`` is the list of event objects (the top-level array of ``/odds`` or
    the ``data`` field of a historical response — use :func:`unwrap` for the
    latter). Only the three game markets are kept. ``is_closing`` marks a snapshot
    taken at the close (the CLV anchor); live snapshots pass ``False``.
    """
    rows: list[dict[str, object]] = []
    for event in events:
        event_id = event.get("id")
        if event_id is None:
            continue
        for book in event.get("bookmakers") or []:
            book_key = book.get("key")
            book_update = book.get("last_update")
            for market in book.get("markets") or []:
                canonical = GAME_MARKET_BY_KEY.get(str(market.get("key")))
                if canonical is None:
                    continue
                market_update = market.get("last_update") or book_update
                for outcome in market.get("outcomes") or []:
                    price = outcome.get("price")
                    if price is None:
                        continue
                    side = str(outcome.get("name", ""))
                    point = outcome.get("point")
                    rows.append(
                        {
                            "game_id": str(event_id),
                            "book": None if book_key is None else str(book_key),
                            "market": canonical,
                            "side": side,
                            "price": price,
                            "point": None if canonical == "moneyline" else point,
                            "timestamp": market_update,
                            "is_closing": is_closing,
                        }
                    )
    if not rows:
        return _empty_lines()

    df = pd.DataFrame(rows)
    point_key = df["point"].map(lambda v: "" if pd.isna(v) else f"{float(v):g}")
    df["line_id"] = (
        df["game_id"]
        + "|" + df["market"]
        + "|" + df["side"].str.lower().str.replace(r"\s+", "-", regex=True)
        + "|" + df["book"].fillna("")
        + "|" + point_key
    )
    df["price"] = pd.to_numeric(df["price"], errors="coerce").round().astype("Int64")
    df["point"] = pd.to_numeric(df["point"], errors="coerce")
    ts = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    df["timestamp"] = ts.dt.tz_localize(None)
    df = df.dropna(subset=["price"]).drop_duplicates("line_id").reset_index(drop=True)
    df["price"] = df["price"].astype(int)
    return Lines.validate(df[_LINES_COLUMNS])


def extract_events(payload: Any) -> pd.DataFrame:
    """Pull per-event metadata (id, kickoff, teams) from an odds payload.

    Companion to :func:`normalize_odds_events`: the same ``/odds`` response that
    carries the lines also names each event's home/away team and commence time.
    Returns a frame ``[game_id, kickoff, home_team, away_team, sport_key]`` — the
    games side of a live slate, internally consistent with the lines (same event
    ids and provider team names).
    """
    rows: list[dict[str, object]] = []
    for event in unwrap(payload):
        event_id = event.get("id")
        if event_id is None:
            continue
        rows.append(
            {
                "game_id": str(event_id),
                "kickoff": event.get("commence_time"),
                "home_team": event.get("home_team"),
                "away_team": event.get("away_team"),
                "sport_key": event.get("sport_key"),
            }
        )
    df = pd.DataFrame(rows, columns=["game_id", "kickoff", "home_team", "away_team", "sport_key"])
    if not df.empty:
        ts = pd.to_datetime(df["kickoff"], errors="coerce", utc=True)
        df["kickoff"] = ts.dt.tz_localize(None)
    return df


def unwrap(payload: Any) -> list[dict]:
    """Return the events list from either a live array or a historical ``{data: …}``.

    The live ``/odds`` endpoint returns a bare array; the historical endpoint wraps
    it as ``{"timestamp": …, "data": [ … ]}``. This accepts either.
    """
    if isinstance(payload, Mapping):
        return list(payload.get("data") or [])
    return list(payload or [])


@dataclass
class TheOddsAPIClient:
    """Network client for The Odds API. Build with :meth:`from_env`.

    ``api_key`` comes from the ``THE_ODDS_API`` environment variable — never a
    literal — so the collector reads it from a GitHub Actions secret and the
    sandbox never sees it. Each response also carries the remaining-credit count in
    the ``x-requests-remaining`` header, surfaced by the fetchers that need it.
    """

    api_key: str
    regions: str = "us"
    odds_format: str = "american"
    remaining: str | None = None  # credits left, from the last response header

    @classmethod
    def from_env(cls) -> TheOddsAPIClient:
        api_key = os.environ.get("THE_ODDS_API", "")
        if not api_key:
            raise RuntimeError("THE_ODDS_API is not set (The Odds API key)")
        return cls(api_key=api_key)

    @staticmethod
    def sport_key(league: str) -> str:
        """Map a friendly league name to The Odds API sport key (pass-through if already one)."""
        return SPORT_KEYS.get(league.lower(), league)

    def _get(  # pragma: no cover - network
        self, endpoint: str, **params: object
    ) -> tuple[Any, dict[str, str]]:
        query = {"apiKey": self.api_key, **{k: v for k, v in params.items() if v is not None}}
        url = f"{_BASE}/{endpoint.lstrip('/')}?{urllib.parse.urlencode(query, doseq=True)}"
        with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
            headers = {
                "remaining": resp.headers.get("x-requests-remaining", ""),
                "used": resp.headers.get("x-requests-used", ""),
            }
            return json.loads(resp.read()), headers

    def sports(self) -> list[dict]:  # pragma: no cover - network
        """Return the list of in-season sports (a cheap, credit-free call)."""
        data, _ = self._get("sports")
        return list(data or [])

    def odds_payload(  # pragma: no cover - network
        self, league: str, markets: str = "h2h,spreads,totals"
    ) -> Any:
        """Raw ``/odds`` payload for ``league`` — carries both lines and event metadata."""
        data, meta = self._get(
            f"sports/{self.sport_key(league)}/odds",
            regions=self.regions,
            markets=markets,
            oddsFormat=self.odds_format,
        )
        self.remaining = meta.get("remaining")
        return data

    def odds(  # pragma: no cover - network
        self, league: str, markets: str = "h2h,spreads,totals"
    ) -> pd.DataFrame:
        """Live game lines for ``league`` → a canonical ``Lines`` frame (snapshot, not closing)."""
        return normalize_odds_events(unwrap(self.odds_payload(league, markets)), is_closing=False)

    def historical_odds(
        self, league: str, date: str, markets: str = "h2h,spreads,totals"
    ) -> pd.DataFrame:  # pragma: no cover - network
        """Historical snapshot at ``date`` (ISO 8601) → canonical ``Lines`` (the CLV archive).

        The API returns the last snapshot at or before ``date``; pass a kickoff
        time to approximate the closing line, so these rows are marked closing.
        """
        data, meta = self._get(
            f"historical/sports/{self.sport_key(league)}/odds",
            regions=self.regions,
            markets=markets,
            oddsFormat=self.odds_format,
            date=date,
        )
        self.remaining = meta.get("remaining")
        return normalize_odds_events(unwrap(data), is_closing=True)
