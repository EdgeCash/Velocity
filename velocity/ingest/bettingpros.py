"""BettingPros ingest adapter — live multi-book lines → canonical store.

BettingPros is a **live** feed (no historical archive): given a sport it returns
current *events*, the *markets* offered on them, and *offers* carrying every
book's line for each selection. It is the production line source that plugs into
:class:`~velocity.ingest.odds.LiveOddsAdapter`; historical closing lines for the
backtest come from a separate archive (The Odds API).

Two layers, kept strictly separate so the test gate stays offline:

* ``normalize_offers`` / ``to_lines`` — **pure** functions that flatten the
  nested offers→selections→books→lines JSON into a long frame and (for the three
  game markets) onto the canonical :class:`~velocity.store.schema.Lines` schema.
* :class:`BettingProsClient` — the thin network layer. Credentials come from the
  environment (``BP_API_KEY`` header plus the ``BP_USER_ID`` / ``BP_USER_KEY``
  premium triple); nothing is ever hard-coded, and the client is never touched by
  the offline unit tests.

Convention notes:

* Auth. Every request carries the ``x-api-key`` partner header. Sending
  ``auth=user`` with the ``user`` id and ``key`` upgrades the call to the premium
  tier (needed for BettingPros' own projections); without all three the response
  is served free-tier with premium fields nulled.
* Markets. The three game markets are identified by **slug** (``spread``,
  ``total``, ``moneyline``), which is stable across sports where the numeric
  ``market_id`` is not (NFL moneyline is 1, NCAAF's is 198). ``price`` is the
  American ``cost``; ``point`` is the ``line`` (null for moneyline). Player-prop
  markets are kept in the long frame but excluded from the ``Lines`` view, which
  only models game markets.
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

_BASE = "https://api.bettingpros.com/v3"
_FETCH_TIMEOUT = 60

# BettingPros game-market slug → canonical Lines market. These are the slugs the
# API returns on market objects (category ``game-odds``); other slugs (props,
# futures, game-props) are absent on purpose — they don't fit the Lines schema.
GAME_MARKET_BY_SLUG = {
    "spread": "spread",
    "total": "total",
    "moneyline": "moneyline",
}

# The long-frame columns produced by ``normalize_offers`` (a faithful flattening
# that also carries player-prop rows).
_LONG_COLUMNS = [
    "offer_id",
    "event_id",
    "market_id",
    "market_slug",
    "market_category",
    "player_id",
    "team_id",
    "book",
    "side",
    "price",
    "point",
    "timestamp",
    "is_main",
    "is_best",
]


def _selection_side(selection: Mapping[str, Any]) -> str:
    """Human-readable side for a selection ('Over', 'Under', a participant, …)."""
    for key in ("selection", "label", "short_label"):
        value = selection.get(key)
        if value:
            return str(value)
    participant = selection.get("participant")
    return str(participant) if participant is not None else ""


def normalize_offers(
    offers: Iterable[Mapping[str, Any]],
    markets: Iterable[Mapping[str, Any]] | None = None,
) -> pd.DataFrame:
    """Flatten BettingPros ``/offers`` JSON into one row per (offer, selection, book, line).

    ``offers`` is the ``offers`` array of an ``/offers`` response; ``markets`` (the
    ``markets`` array of a ``/markets`` response) supplies each market's slug and
    category. Every active, non-stale line becomes a row — alternate points from
    the same book included — so downstream line-shopping sees the full board. The
    result carries game and player-prop markets alike; use :func:`to_lines` for
    the canonical game-market view.
    """
    slug_by_id: dict[int, str] = {}
    category_by_id: dict[int, str] = {}
    for market in markets or []:
        mid = market.get("id")
        if mid is None:
            continue
        slug_by_id[int(mid)] = str(market.get("slug") or "")
        category_by_id[int(mid)] = str(market.get("category") or "")

    rows: list[dict[str, object]] = []
    for offer in offers:
        market_id = offer.get("market_id")
        market_id_int = int(market_id) if market_id is not None else None
        offer_id = offer.get("id")
        event_id = offer.get("event_id")
        player_id = offer.get("player_id")
        team_id = offer.get("team_id")
        slug = slug_by_id.get(market_id_int, "") if market_id_int is not None else ""
        category = category_by_id.get(market_id_int, "") if market_id_int is not None else ""

        selections = offer.get("selections") or []
        for selection in selections:
            side = _selection_side(selection)
            for book in selection.get("books") or []:
                book_id = book.get("id")
                for line in book.get("lines") or []:
                    # Skip lines a book has pulled or that were replaced.
                    if line.get("is_off") or line.get("replaced") or line.get("active") is False:
                        continue
                    cost = line.get("cost")
                    if cost is None:
                        continue
                    rows.append(
                        {
                            "offer_id": None if offer_id is None else str(offer_id),
                            "event_id": None if event_id is None else str(event_id),
                            "market_id": market_id_int,
                            "market_slug": slug,
                            "market_category": category,
                            "player_id": None if player_id is None else str(player_id),
                            "team_id": None if team_id is None else str(team_id),
                            "book": None if book_id is None else str(book_id),
                            "side": side,
                            "price": cost,
                            "point": line.get("line"),
                            "timestamp": line.get("updated"),
                            "is_main": bool(line.get("main", False)),
                            "is_best": bool(line.get("best", False)),
                        }
                    )
    return pd.DataFrame(rows, columns=_LONG_COLUMNS)


def to_lines(long: pd.DataFrame, is_closing: bool = False) -> pd.DataFrame:
    """Project the long offers frame onto the canonical ``Lines`` schema.

    Keeps only the three game markets (mapped by slug), synthesizes a stable
    ``line_id`` per (offer, book, side, point), and validates. ``is_closing`` marks
    a snapshot taken at close (for CLV); live snapshots pass ``False``.
    """
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
    if long.empty:
        return Lines.validate(empty)

    game = long[long["market_slug"].isin(GAME_MARKET_BY_SLUG)].copy()
    game = game[game["event_id"].notna() & game["book"].notna()]
    if game.empty:
        return Lines.validate(empty)

    market = game["market_slug"].map(GAME_MARKET_BY_SLUG)
    side = game["side"].fillna("").astype(str)
    point = pd.to_numeric(game["point"], errors="coerce")
    ts = pd.to_datetime(game["timestamp"], errors="coerce", utc=True).dt.tz_localize(None)
    # Moneyline has no number; a null point keeps line_id stable and distinct.
    point_key = point.where(market != "moneyline").map(
        lambda v: "" if pd.isna(v) else f"{float(v):g}"
    )
    line_id = (
        game["event_id"].astype(str)
        + "|" + market.astype(str)
        + "|" + side.str.lower().str.replace(r"\s+", "-", regex=True)
        + "|" + game["book"].astype(str)
        + "|" + point_key
    )
    out = pd.DataFrame(
        {
            "line_id": line_id,
            "game_id": game["event_id"].astype(str),
            "book": game["book"].astype(str),
            "market": market,
            "side": side,
            "price": pd.to_numeric(game["price"], errors="coerce").round().astype("Int64"),
            "point": point.where(market != "moneyline"),
            "timestamp": ts,
            "is_closing": is_closing,
        }
    )
    out = out.dropna(subset=["price"]).drop_duplicates("line_id").reset_index(drop=True)
    out["price"] = out["price"].astype(int)
    return Lines.validate(out)


@dataclass
class BettingProsClient:
    """Network client for the BettingPros partner API (premium tier when keyed).

    Instantiate with :meth:`from_env`. ``api_key`` is required; ``user_id`` and
    ``user_key`` together unlock premium fields (BettingPros projections). All
    three come from the environment — never a literal — so the collector reads
    them from GitHub Actions secrets and the sandbox never sees them.
    """

    api_key: str
    user_id: str | None = None
    user_key: str | None = None

    @classmethod
    def from_env(cls) -> BettingProsClient:
        api_key = os.environ.get("BP_API_KEY", "")
        if not api_key:
            raise RuntimeError("BP_API_KEY is not set (needed for the BettingPros partner header)")
        return cls(
            api_key=api_key,
            user_id=os.environ.get("BP_USER_ID") or None,
            user_key=os.environ.get("BP_USER_KEY") or None,
        )

    @property
    def is_premium(self) -> bool:
        return bool(self.user_id and self.user_key)

    def _get(self, endpoint: str, **params: object) -> dict:  # pragma: no cover - network
        query = {k: v for k, v in params.items() if v is not None}
        if self.is_premium:
            query.update({"auth": "user", "user": self.user_id, "key": self.user_key})
        url = f"{_BASE}/{endpoint.lstrip('/')}?{urllib.parse.urlencode(query, doseq=True)}"
        req = urllib.request.Request(url, headers={"x-api-key": self.api_key})
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
            return json.loads(resp.read())

    def events(self, sport: str, **params: object) -> list[dict]:  # pragma: no cover - network
        """Return the ``events`` list for ``sport`` (e.g. ``NFL``, ``NCAAF``)."""
        return self._get("events", sport=sport, **params).get("events", [])

    def markets(self, sport: str, **params: object) -> list[dict]:  # pragma: no cover - network
        """Return the ``markets`` offered for ``sport``."""
        return self._get("markets", sport=sport, **params).get("markets", [])

    def offers(
        self, sport: str, market_id: object, event_id: object = None, **params: object
    ) -> list[dict]:  # pragma: no cover - network
        """Return the ``offers`` for ``sport`` / ``market_id`` (optionally one event)."""
        return self._get(
            "offers", sport=sport, market_id=market_id, event_id=event_id, **params
        ).get("offers", [])

    def game_lines(
        self, sport: str, event_ids: Iterable[object] | None = None
    ) -> pd.DataFrame:  # pragma: no cover - network
        """Snapshot the three game markets for ``sport`` → a canonical ``Lines`` frame.

        The ``/offers`` endpoint requires an ``event_id`` for game markets, so we
        first pull the current events (unless the caller supplies ``event_ids``),
        then request all three markets across those events in one batched call.
        """
        markets = self.markets(sport, market_category="game-odds")
        wanted = [m for m in markets if str(m.get("slug")) in GAME_MARKET_BY_SLUG]
        market_ids = ":".join(str(m["id"]) for m in wanted)
        if not market_ids:
            return to_lines(pd.DataFrame(columns=_LONG_COLUMNS))
        if event_ids is None:
            event_ids = [e["id"] for e in self.events(sport)]
        ids = ":".join(str(e) for e in event_ids)
        if not ids:
            return to_lines(pd.DataFrame(columns=_LONG_COLUMNS))
        offers = self.offers(sport, market_id=market_ids, event_id=ids)
        return to_lines(normalize_offers(offers, wanted))
