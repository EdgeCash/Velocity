"""Polymarket ingest adapter — CLOB outcome tokens → canonical store.

Polymarket's sports markets are binary outcome tokens priced in [0, 1] = the
probability, settling $1 in USDC. Market data is free and key-less (verified
live 2026-09-09), so — like PrizePicks and Kalshi — the client is a polite
key-less one, not a secret-bearing one. Plan: docs/BUILD_EXCHANGES.md E3.

Two layers, kept strictly separate so the test gate stays offline:

* ``normalize_polymarket_events`` / ``normalize_polymarket_props`` — **pure**:
  flatten Gamma event JSON plus CLOB order books onto
  :class:`~velocity.store.schema.Lines` / :class:`~velocity.store.schema.PropLines`.
* :class:`PolymarketClient` — the network layer (Gamma discovery + CLOB books).

Convention notes (docs/BUILD_EXCHANGES.md D1-D6):

* Prices come from the **order book's best ask** — the price we could actually
  buy — never the best bid, never Gamma's ``outcomePrices`` (a midpoint), and
  never ``GET /price?side=BUY`` (which returns the *bid*). Book levels arrive
  worst-first, so the best ask is ``min(asks)``, not ``asks[0]``. Asks are
  converted to integer American odds at this boundary (D1).
* ``point`` is signed from the side's own perspective. A spread market's
  ``line`` is already signed from ``outcomes[0]``'s perspective (verified
  across 2,735 live markets), and its slug names whether that team is home or
  away; the opposite side takes ``-line``. Totals and team totals share one
  point across Over/Under. Only half-integer lines are kept — every one of
  8,422 live lines was a half-integer, and a binary contract cannot push.
* **Sides are the slug's team codes** (``den``/``kc``), not display nicknames.
  Polymarket's own labels are not internally consistent — one live event
  quoted ``Texans`` on the moneyline and ``HOU`` on the spread — and
  :func:`velocity.wagering.live.canonicalize_sides` matches sides to the
  events frame exactly, so an inconsistent label would silently drop the row.
  The codes are machine-generated and uniform, and resolve to our teams
  through the same alias path Kalshi's codes use.
* ``game_id`` is the game event's slug (``nfl-den-kc-2026-09-15``); the
  companion player-props event (``…-player-props``) resolves to the same id.
  Slug dates are **UTC**, so a prime-time game's slug date is the day after
  its US/ET date — the opposite convention from Kalshi's tickers. Exact
  kickoff is not inferred from either: each market carries ``gameStartTime``.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from velocity.store.schema import Lines, PropLines
from velocity.wagering.odds import prob_to_american

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
_FETCH_TIMEOUT = 60
_USER_AGENT = "velocity-research/0.1 (odds research; contact via repo)"
_BOOK_BATCH = 500

# League tag ids on Gamma, and the leagues they map to in our schema.
TAG_IDS = {"nfl": 450, "cfb": 100351}
LEAGUE_BY_SLUG_PREFIX = {"nfl": "nfl", "cfb": "ncaaf"}

# Gamma ``sportsMarketType`` → canonical Lines market. ``team_totals`` resolves
# to ``team_total_home``/``team_total_away`` from the slug's team code. Halves,
# quarters, exact margin, first-TD and friends have no sim support and are
# dropped (docs/BUILD_EXCHANGES.md §4).
GAME_MARKET_BY_TYPE = {
    "moneyline": "moneyline",
    "spreads": "spread",
    "totals": "total",
    "team_totals": "team_total",
}

# Gamma ``sportsMarketType`` → canonical PropLines stat. ``anytime_touchdowns``
# is deliberately absent: it carries no line, and PropLines requires a point
# (the same reason Kalshi's KXNFLTD is unmapped).
PROP_MARKET_BY_TYPE = {
    "passing_yards": "pass_yards",
    "passing_touchdowns": "pass_tds",
    "rushing_yards": "rush_yards",
    "receiving_yards": "receiving_yards",
    "receptions": "receptions",
}

_BOOK = "polymarket"

# Game event slug: {league}-{away}-{home}-{YYYY-MM-DD}, dates in UTC.
_SLUG_RE = re.compile(
    r"^(?P<league>nfl|cfb)-(?P<away>[a-z0-9]+)-(?P<home>[a-z0-9]+)-(?P<date>\d{4}-\d{2}-\d{2})$"
)
_PROPS_SUFFIX = "-player-props"
# Spread slugs name the side the line belongs to: …-spread-home-1pt5.
_SPREAD_SIDE_RE = re.compile(r"-spread-(home|away)-")
# Team-total slugs name the team: …-team-total-kc-16pt5.
_TEAM_TOTAL_RE = re.compile(r"-team-total-([a-z0-9]+)-")
_TOTAL_SIDES = {"over": "Over", "under": "Under"}

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
_PROP_COLUMNS = [
    "line_id",
    "game_id",
    "book",
    "market",
    "player",
    "side",
    "price",
    "point",
    "timestamp",
    "is_closing",
]


@dataclass(frozen=True)
class ParsedSlug:
    """The pieces of one Polymarket game-event slug."""

    league: str
    away: str
    home: str
    date_part: str

    @property
    def date(self) -> pd.Timestamp | None:
        """The slug's **UTC** date (a prime-time game's ET date is the day before)."""
        stamp = pd.to_datetime(self.date_part, errors="coerce")
        return None if pd.isna(stamp) else stamp


def parse_event_slug(slug: str) -> ParsedSlug | None:
    """Split a game-event slug; ``None`` if it isn't one (futures, props, derivatives).

    The companion player-props event (``…-player-props``) parses to the same
    pieces as its game, so props and game lines share a ``game_id``.
    """
    base = slug[: -len(_PROPS_SUFFIX)] if slug.endswith(_PROPS_SUFFIX) else slug
    match = _SLUG_RE.match(base)
    if match is None:
        return None
    return ParsedSlug(
        league=match["league"], away=match["away"], home=match["home"], date_part=match["date"]
    )


def game_id_of(slug: str) -> str:
    """The canonical game id for an event slug (props events fold onto their game)."""
    return slug[: -len(_PROPS_SUFFIX)] if slug.endswith(_PROPS_SUFFIX) else slug


def _loads(raw: Any) -> Any:
    """Gamma nests JSON inside strings (``outcomes``, ``clobTokenIds``)."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None
    return raw


def best_ask(book: Mapping[str, Any] | None) -> float | None:
    """The executable buy price: the lowest ask. Book levels arrive worst-first."""
    if not book:
        return None
    prices = []
    for level in book.get("asks") or []:
        try:
            prices.append(float(level["price"]))
        except (TypeError, ValueError, KeyError):
            continue
    return min(prices) if prices else None


def _ask_to_american(ask: float | None) -> int | None:
    """Best ask → integer American odds; ``None`` if not an executable price."""
    if ask is None or not 0.0 < ask < 1.0:
        # $0 = no offer, $1 = buying certainty; neither is executable.
        return None
    return round(prob_to_american(ask))


def _half_line(value: Any) -> float | None:
    """The line as float if it is a half-integer (push-free), else ``None``."""
    try:
        line = float(value)
    except (TypeError, ValueError):
        return None
    return line if line % 1.0 == 0.5 else None


def _books_by_token(books: Any) -> dict[str, Mapping[str, Any]]:
    return {str(b["asset_id"]): b for b in books or [] if b and b.get("asset_id")}


def _token_asks(
    market: Mapping[str, Any], by_token: Mapping[str, Mapping[str, Any]]
) -> list[int | None]:
    """Each outcome's executable ask, in outcome order, as American odds."""
    tokens = _loads(market.get("clobTokenIds")) or []
    return [_ask_to_american(best_ask(by_token.get(str(t)))) for t in tokens]


def _empty_frame(columns: list[str]) -> pd.DataFrame:
    dtypes: dict[str, Any] = {
        "price": "int64",
        "point": float,
        "timestamp": "datetime64[ns]",
        "is_closing": bool,
    }
    return pd.DataFrame({c: pd.Series(dtype=dtypes.get(c, str)) for c in columns})


def _market_rows(
    parsed: ParsedSlug,
    game_id: str,
    market: Mapping[str, Any],
    canonical: str,
    asks: Sequence[int | None],
    is_closing: bool,
) -> list[dict[str, object]]:
    """Rows for one market's two outcomes, per the module conventions above."""
    outcomes = _loads(market.get("outcomes")) or []
    if len(outcomes) != 2 or len(asks) != 2:
        return []
    slug = str(market.get("slug", ""))
    rows: list[dict[str, object]] = []

    if canonical == "moneyline":
        # Outcome order is away, then home (verified across 155 live games).
        for side, price in zip((parsed.away, parsed.home), asks, strict=False):
            if price is None:
                continue
            rows.append(
                {
                    "game_id": game_id,
                    "book": _BOOK,
                    "market": canonical,
                    "side": side,
                    "price": price,
                    "point": None,
                    "is_closing": is_closing,
                }
            )
        return rows

    line = _half_line(market.get("line"))
    if line is None:
        return rows

    if canonical == "spread":
        # The slug names whose line this is; ``line`` is signed from that
        # team's perspective and the other side takes its mirror.
        match = _SPREAD_SIDE_RE.search(slug)
        if match is None:
            return rows
        named = parsed.home if match.group(1) == "home" else parsed.away
        other = parsed.away if match.group(1) == "home" else parsed.home
        for side, point, price in (
            (named, line, asks[0]),
            (other, -line, asks[1]),
        ):
            if price is None:
                continue
            rows.append(
                {
                    "game_id": game_id,
                    "book": _BOOK,
                    "market": canonical,
                    "side": side,
                    "price": price,
                    "point": point,
                    "is_closing": is_closing,
                }
            )
        return rows

    if canonical == "team_total":
        match = _TEAM_TOTAL_RE.search(slug)
        team = None if match is None else match.group(1)
        if team == parsed.home:
            market_name = "team_total_home"
        elif team == parsed.away:
            market_name = "team_total_away"
        else:
            # An unresolvable team is skipped, never guessed.
            return rows
    else:
        market_name = canonical

    for outcome, price in zip(outcomes, asks, strict=False):
        total_side = _TOTAL_SIDES.get(str(outcome).strip().lower())
        if total_side is None or price is None:
            continue
        rows.append(
            {
                "game_id": game_id,
                "book": _BOOK,
                "market": market_name,
                "side": total_side,
                "price": price,
                "point": line,
                "is_closing": is_closing,
            }
        )
    return rows


def _finish(rows: list[dict[str, object]], timestamp: Any, key_cols: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(timestamp, utc=True).tz_localize(None)
    side_key = df["side"].str.lower().str.replace(r"\s+", "-", regex=True)
    point_key = df["point"].map(lambda v: "" if v is None else f"{float(v):g}")
    player_key = (
        df["player"].str.lower().str.replace(r"\s+", "-", regex=True) + "|"
        if "player" in key_cols
        else ""
    )
    df["line_id"] = (
        df["game_id"]
        + "|" + df["market"]
        + "|" + player_key
        + side_key
        + "|" + df["book"]
        + "|" + point_key
    )
    df["point"] = pd.to_numeric(df["point"], errors="coerce")
    return df.drop_duplicates("line_id").reset_index(drop=True)


def normalize_polymarket_events(
    events: Any,
    books: Any,
    timestamp: Any,
    is_closing: bool = False,
) -> pd.DataFrame:
    """Flatten Gamma game events + CLOB books onto the canonical ``Lines`` schema.

    ``events`` is a Gamma ``/events`` array; ``books`` the CLOB ``POST /books``
    response covering their outcome tokens. Polymarket quotes carry no per-quote
    timestamp, so ``timestamp`` — the snapshot pull time — stamps every row.
    Only the market types in :data:`GAME_MARKET_BY_TYPE` are kept.
    """
    by_token = _books_by_token(books)
    rows: list[dict[str, object]] = []
    for event in events or []:
        slug = str(event.get("slug", ""))
        parsed = parse_event_slug(slug)
        if parsed is None or slug.endswith(_PROPS_SUFFIX):
            continue
        game_id = game_id_of(slug)
        for market in event.get("markets") or []:
            canonical = GAME_MARKET_BY_TYPE.get(str(market.get("sportsMarketType")))
            if canonical is None or market.get("closed"):
                continue
            rows.extend(
                _market_rows(
                    parsed, game_id, market, canonical, _token_asks(market, by_token), is_closing
                )
            )

    if not rows:
        return Lines.validate(_empty_frame(_LINES_COLUMNS))
    return Lines.validate(_finish(rows, timestamp, _LINES_COLUMNS)[_LINES_COLUMNS])


def normalize_polymarket_props(
    events: Any,
    books: Any,
    timestamp: Any,
    is_closing: bool = False,
) -> pd.DataFrame:
    """Flatten Polymarket player-prop markets onto the canonical ``PropLines``.

    Prop markets live in a companion ``…-player-props`` event but fold onto
    their game's id. Each is one rung ("Jalen Hurts: Passing Yards O/U 149.5"):
    the player is the question's colon head, ``line`` is the point, and the two
    outcomes are Over/Under priced at their own book asks.
    """
    by_token = _books_by_token(books)
    rows: list[dict[str, object]] = []
    for event in events or []:
        parsed = parse_event_slug(str(event.get("slug", "")))
        if parsed is None:
            continue
        game_id = game_id_of(str(event.get("slug", "")))
        for market in event.get("markets") or []:
            stat = PROP_MARKET_BY_TYPE.get(str(market.get("sportsMarketType")))
            if stat is None or market.get("closed"):
                continue
            line = _half_line(market.get("line"))
            question = str(market.get("question", ""))
            player = question.split(":", 1)[0].strip() if ":" in question else None
            outcomes = _loads(market.get("outcomes")) or []
            if line is None or not player or len(outcomes) != 2:
                continue
            asks = _token_asks(market, by_token)
            for outcome, price in zip(outcomes, asks, strict=False):
                side = str(outcome).strip().lower()
                if side not in ("over", "under") or price is None:
                    continue
                rows.append(
                    {
                        "game_id": game_id,
                        "book": _BOOK,
                        "market": stat,
                        "player": player,
                        "side": side,
                        "price": price,
                        "point": line,
                        "is_closing": is_closing,
                    }
                )

    if not rows:
        return PropLines.validate(_empty_frame(_PROP_COLUMNS))
    return PropLines.validate(_finish(rows, timestamp, _PROP_COLUMNS)[_PROP_COLUMNS])


def extract_polymarket_events(events: Any) -> pd.DataFrame:
    """Per-game metadata from a Gamma events payload.

    Returns ``[game_id, kickoff, home_team, away_team, league]``. Teams are the
    slug's codes — the same labels :func:`normalize_polymarket_events` puts in
    ``side``, so ``canonicalize_sides`` matches exactly. ``kickoff`` is the
    markets' own ``gameStartTime`` (UTC), not inferred from the slug date.
    """
    rows: list[dict[str, object]] = []
    for event in events or []:
        slug = str(event.get("slug", ""))
        parsed = parse_event_slug(slug)
        if parsed is None or slug.endswith(_PROPS_SUFFIX):
            continue
        starts = [
            m.get("gameStartTime") for m in event.get("markets") or [] if m.get("gameStartTime")
        ]
        kickoff = pd.to_datetime(starts[0], errors="coerce", utc=True) if starts else pd.NaT
        rows.append(
            {
                "game_id": game_id_of(slug),
                "kickoff": None if pd.isna(kickoff) else kickoff.tz_localize(None),
                "home_team": parsed.home,
                "away_team": parsed.away,
                "league": LEAGUE_BY_SLUG_PREFIX.get(parsed.league, parsed.league),
            }
        )
    df = pd.DataFrame(rows, columns=["game_id", "kickoff", "home_team", "away_team", "league"])
    if not df.empty:
        df["kickoff"] = pd.to_datetime(df["kickoff"])
    return df


def team_names_by_code(events: Any) -> dict[str, str]:
    """Team code → the venue's display name, read off moneyline outcomes.

    Slug codes are opaque (``txst``, ``lcdbfc25``); the moneyline outcomes name
    the teams in away-then-home order. Resolving those names beats hand-keying
    college codes: 98% of a live CFB board resolves this way against 12% from
    the codes alone. Codes are **not** portable across venues — ``sdst`` is
    South Dakota State on one exchange and San Diego State on the other — so a
    table built here is only ever valid for Polymarket rows.
    """
    out: dict[str, str] = {}
    for event in events or []:
        parsed = parse_event_slug(str(event.get("slug", "")))
        if parsed is None or str(event.get("slug", "")).endswith(_PROPS_SUFFIX):
            continue
        for market in event.get("markets") or []:
            if str(market.get("sportsMarketType")) != "moneyline":
                continue
            outcomes = _loads(market.get("outcomes")) or []
            if len(outcomes) == 2:
                out[parsed.away] = str(outcomes[0])
                out[parsed.home] = str(outcomes[1])
    return out


def token_ids(events: Any) -> list[str]:
    """Every outcome-token id nested in a Gamma events payload, de-duplicated."""
    out: list[str] = []
    seen: set[str] = set()
    for event in events or []:
        for market in event.get("markets") or []:
            for tid in _loads(market.get("clobTokenIds")) or []:
                key = str(tid)
                if key and key not in seen:
                    seen.add(key)
                    out.append(key)
    return out


@dataclass
class PolymarketClient:
    """Network client for Polymarket market data (key-less; be polite).

    Reads need no key or account (verified live 2026-09-09) — only order
    placement does, which this build never touches. Gamma discovery pages by
    offset; CLOB books batch at 500 tokens per POST.
    """

    gamma_url: str = GAMMA
    clob_url: str = CLOB
    sleep_seconds: float = 0.25
    page_size: int = 100
    max_pages: int = 8

    def _request(self, url: str, body: bytes | None = None) -> Any:  # pragma: no cover - network
        headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers)
        for attempt, delay in enumerate((0, 10, 30)):
            if delay:
                time.sleep(delay)
            try:
                with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503) or attempt == 2:
                    raise
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                # Transient connection reset under sustained pulls; retry.
                if attempt == 2:
                    raise
        raise RuntimeError("unreachable")

    def events(self, league: str) -> list[dict]:  # pragma: no cover - network
        """All open events under one league tag, offset pages merged."""
        tag_id = TAG_IDS[league]
        merged: list[dict] = []
        for page in range(self.max_pages):
            query = urllib.parse.urlencode(
                {
                    "tag_id": tag_id,
                    "closed": "false",
                    "limit": self.page_size,
                    "offset": page * self.page_size,
                }
            )
            batch = self._request(f"{self.gamma_url}/events?{query}")
            merged.extend(batch)
            if len(batch) < self.page_size:
                break
            time.sleep(self.sleep_seconds)
        return merged

    def books(self, tokens: Sequence[str]) -> list[dict]:  # pragma: no cover - network
        """Order books for every token, batched at the CLOB's 500-per-POST cap."""
        out: list[dict] = []
        for start in range(0, len(tokens), _BOOK_BATCH):
            chunk = list(tokens[start : start + _BOOK_BATCH])
            body = json.dumps([{"token_id": t} for t in chunk]).encode()
            out.extend(self._request(f"{self.clob_url}/books", body))
            time.sleep(self.sleep_seconds)
        return out
