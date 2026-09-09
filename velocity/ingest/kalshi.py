"""Kalshi ingest adapter — exchange event contracts → canonical store.

Kalshi (Trade API v2) is a CFTC-regulated exchange whose sports contracts are
binary: price = probability, $1 notional, $0.01 tick. Market data is free and
key-less (verified live 2026-09-09) — only trading and the WebSocket need an
API key — so the client follows the PrizePicks key-less polite posture, not
the secret-bearing TheOddsAPI one. Plan and conventions: docs/BUILD_EXCHANGES.md.

Two layers, kept strictly separate so the test gate stays offline:

* ``normalize_kalshi_markets`` / ``normalize_kalshi_props`` — **pure**: flatten
  a ``/markets`` payload onto :class:`~velocity.store.schema.Lines` /
  :class:`~velocity.store.schema.PropLines`. Offline, deterministic, tested
  against frozen samples.
* :class:`KalshiClient` — the network layer. Key-less; unauthenticated read
  limits are undocumented, so it paces itself and backs off on 429.

Convention notes (BUILD_EXCHANGES.md D1–D6):

* Prices land as integer American odds, converted from the **executable ask**
  (never bid or mid): the yes-ask prices the contract's named outcome, the
  no-ask prices its complement. An ask at $0 or $1 is no offer — dropped.
* Winner events carry TWO markets, one per team; each contributes only its own
  yes-ask (emitting no-asks too would double-count sides in the devig bucket).
  Ladder rungs are ONE market with two economic sides: yes and no both emit.
* ``point`` is signed from the side's own perspective (the ``Bet`` convention):
  a spread rung's named team gets ``-floor_strike``, the opponent
  ``+floor_strike``. Only half-integer strikes are kept (binary contracts
  can't push; an integer strike would need explicit push semantics).
* ``game_id`` is Kalshi's own event ticker (e.g. ``KXNFLGAME-26SEP21NYGLAR``);
  joining to our games is done later by (teams, date) — the ticker carries the
  **US/ET game date** (prime-time games differ from the UTC date) and team
  codes, but no kickoff time.
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

_BASE = "https://api.elections.kalshi.com/trade-api/v2"
_FETCH_TIMEOUT = 60
_USER_AGENT = "velocity-research/0.1 (odds research; contact via repo)"

# Kalshi series ticker → canonical Lines market. ``team_total`` is resolved to
# ``team_total_home``/``team_total_away`` from the ticker's team blob (the event
# grammar is {AWAY}{HOME}). Anything else — combo/parlay series (``KXMVE*``),
# halves, quarters, exact-margin — is ignored: no sim support (BUILD_EXCHANGES §4).
GAME_MARKET_BY_SERIES = {
    "KXNFLGAME": "moneyline",
    "KXNCAAFGAME": "moneyline",
    "KXNFLSPREAD": "spread",
    "KXNCAAFSPREAD": "spread",
    "KXNFLTOTAL": "total",
    "KXNCAAFTOTAL": "total",
    "KXNFLTEAMTOTAL": "team_total",
    "KXNCAAFTEAMTOTAL": "team_total",
}

# Kalshi prop series → canonical PropLines stat. The player is named in the
# market title ("Tua Tagovailoa: 300+ passing yards"); the strike is the line.
# ``KXNFLTD`` (anytime TD) is deliberately absent: its markets carry no strike,
# and PropLines requires a point — wire it once the point convention is decided.
PROP_MARKET_BY_SERIES = {
    "KXNFLPASSYDS": "pass_yards",
    "KXNFLPASSTDS": "pass_tds",
    "KXNFLRECYDS": "receiving_yards",
    "KXNFLREC": "receptions",
    "KXNFLRSHYDS": "rush_yards",
}

_BOOK = "kalshi"

# Market ticker grammar: {SERIES}-{YY}{MON}{DD}{TEAMS}-{SUFFIX}. Football team
# blobs are letters only (MLB inserts a start time — out of scope here).
_TICKER_RE = re.compile(
    r"^(?P<series>KX[A-Z0-9]+?)-(?P<date>\d{2}[A-Z]{3}\d{2})(?P<teams>[A-Z]+)-(?P<suffix>.+)$"
)
_SUFFIX_TEAM_RE = re.compile(r"^([A-Z]+)")

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
class ParsedTicker:
    """The pieces of one Kalshi market ticker."""

    series: str
    date_part: str  # e.g. "26SEP21"
    teams: str  # away+home codes concatenated, e.g. "NYGLAR"
    suffix: str  # outcome part, e.g. "NYG", "KC8", "64", "ATLTTAGOVAILOA1-300"

    @property
    def event_ticker(self) -> str:
        """Kalshi's own event id — series-scoped, so it differs per market type."""
        return f"{self.series}-{self.date_part}{self.teams}"

    @property
    def game_key(self) -> str:
        """The game itself, independent of which series quotes it.

        Kalshi files each market type under its own event
        (``KXNFLGAME-26SEP14DENKC`` vs ``KXNFLSPREAD-26SEP14DENKC``), so using
        the event ticker as ``game_id`` would split one game's winner, spread
        and total rows into three unrelated games — and the ladders, whose
        series has no winner market to name teams from, would be dropped
        wholesale. The date-and-teams blob is what every series shares.
        """
        return f"{self.date_part}{self.teams}"

    @property
    def date(self) -> pd.Timestamp | None:
        """The US/ET game date named in the ticker (no kickoff time in football
        tickers; prime-time games land on the previous day vs UTC)."""
        raw = self.date_part[:2] + self.date_part[2:5].title() + self.date_part[5:]
        stamp = pd.to_datetime(raw, format="%y%b%d", errors="coerce")
        return None if pd.isna(stamp) else stamp


def parse_market_ticker(ticker: str) -> ParsedTicker | None:
    """Split a market ticker on the grammar above; ``None`` if it doesn't fit."""
    match = _TICKER_RE.match(ticker)
    if match is None:
        return None
    return ParsedTicker(
        series=match["series"],
        date_part=match["date"],
        teams=match["teams"],
        suffix=match["suffix"],
    )


def _suffix_team(suffix: str) -> str | None:
    """The leading team code of a spread/team-total suffix (``KC8`` → ``KC``)."""
    match = _SUFFIX_TEAM_RE.match(suffix)
    code = None if match is None else match.group(1)
    return code or None


def _other_team(teams: str, code: str) -> str | None:
    """The opponent's code, by stripping ``code`` off one end of the blob.

    The blob is {AWAY}{HOME}. A code matching both ends (or neither) is
    ambiguous → ``None``, and the caller drops rather than guesses.
    """
    starts = teams.startswith(code)
    ends = teams.endswith(code)
    if starts == ends:
        return None
    rest = teams[len(code) :] if starts else teams[: -len(code)]
    return rest or None


def _is_home(teams: str, code: str) -> bool | None:
    """Whether ``code`` is the home side of the blob ({AWAY}{HOME} grammar)."""
    starts = teams.startswith(code)
    ends = teams.endswith(code)
    if starts == ends:
        return None
    return ends


def _ask_to_american(raw: Any) -> int | None:
    """Fixed-point dollar ask → integer American odds; ``None`` if not tradable."""
    if raw is None:
        return None
    try:
        prob = float(raw)
    except (TypeError, ValueError):
        return None
    if not 0.0 < prob < 1.0:
        # $0 = no offer, $1 = buying certainty; neither is an executable price.
        return None
    return round(prob_to_american(prob))


def _half_strike(value: Any) -> float | None:
    """The strike as float if it is a half-integer (push-free), else ``None``."""
    try:
        strike = float(value)
    except (TypeError, ValueError):
        return None
    return strike if strike % 1.0 == 0.5 else None


def _markets_of(payload: Any) -> list[dict]:
    """The markets list from either a ``{"markets": …}`` object or a bare array."""
    if isinstance(payload, Mapping):
        return list(payload.get("markets") or [])
    return list(payload or [])


def _finish_lines(rows: list[dict[str, object]], timestamp: Any = None) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if timestamp is not None:
        # A board snapshot: one pull time stamps every row.
        df["timestamp"] = pd.to_datetime(timestamp, utc=True).tz_localize(None)
    else:
        # A time series (candles): rows carry their own timestamps.
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    point_key = df["point"].map(lambda v: "" if v is None else f"{float(v):g}")
    df["line_id"] = (
        df["game_id"]
        + "|" + df["market"]
        + "|" + df["side"].str.lower().str.replace(r"\s+", "-", regex=True)
        + "|" + df["book"]
        + "|" + point_key
    )
    df["point"] = pd.to_numeric(df["point"], errors="coerce")
    # One contract keeps one row per observation time (a candle series shares
    # the line_id across timestamps; a single snapshot dedupes as before).
    df = df.drop_duplicates(["line_id", "timestamp"]).reset_index(drop=True)
    return df


def _market_rows(
    parsed: ParsedTicker,
    canonical: str,
    yes_price: int | None,
    no_price: int | None,
    floor_strike: Any,
    is_closing: bool,
) -> list[dict[str, object]]:
    """Rows for one market's economic sides, per the module conventions above."""
    rows: list[dict[str, object]] = []
    game_id = parsed.game_key

    if canonical == "moneyline":
        # One market per team; its yes-ask prices that team. The no-ask is a
        # near-duplicate quote of the other team's market — not emitted.
        side = parsed.suffix if parsed.suffix.isalpha() else None
        if side is None or yes_price is None:
            return rows
        rows.append(
            {
                "game_id": game_id,
                "book": _BOOK,
                "market": canonical,
                "side": side,
                "price": yes_price,
                "point": None,
                "is_closing": is_closing,
            }
        )
        return rows

    strike = _half_strike(floor_strike)
    if strike is None:
        return rows

    if canonical == "spread":
        team = _suffix_team(parsed.suffix)
        if team is None or yes_price is None:
            return rows
        rows.append(
            {
                "game_id": game_id,
                "book": _BOOK,
                "market": canonical,
                "side": team,
                "price": yes_price,
                "point": -strike,
                "is_closing": is_closing,
            }
        )
        opponent = _other_team(parsed.teams, team)
        if opponent is not None and no_price is not None:
            rows.append(
                {
                    "game_id": game_id,
                    "book": _BOOK,
                    "market": canonical,
                    "side": opponent,
                    "price": no_price,
                    "point": strike,
                    "is_closing": is_closing,
                }
            )
        return rows

    if canonical == "total":
        for side, price in (("Over", yes_price), ("Under", no_price)):
            if price is None:
                continue
            rows.append(
                {
                    "game_id": game_id,
                    "book": _BOOK,
                    "market": canonical,
                    "side": side,
                    "price": price,
                    "point": strike,
                    "is_closing": is_closing,
                }
            )
        return rows

    # team_total: which team's total lives in the suffix; home/away comes from
    # the ticker blob. Unresolvable teams are skipped, never guessed.
    team = _suffix_team(parsed.suffix)
    home = None if team is None else _is_home(parsed.teams, team)
    if home is None:
        return rows
    market_name = "team_total_home" if home else "team_total_away"
    for side, price in (("Over", yes_price), ("Under", no_price)):
        if price is None:
            continue
        rows.append(
            {
                "game_id": game_id,
                "book": _BOOK,
                "market": market_name,
                "side": side,
                "price": price,
                "point": strike,
                "is_closing": is_closing,
            }
        )
    return rows


def normalize_kalshi_markets(
    payload: Any,
    timestamp: Any,
    is_closing: bool = False,
) -> pd.DataFrame:
    """Flatten a Kalshi ``/markets`` payload onto the canonical ``Lines`` schema.

    ``payload`` is a ``GET /markets`` response (or bare markets array); Kalshi
    quotes carry no per-quote timestamp, so ``timestamp`` — the snapshot pull
    time — stamps every row (the collector passes its own UTC stamp).
    Only the series in :data:`GAME_MARKET_BY_SERIES` are kept; sides and signed
    points follow the module conventions above.
    """
    rows: list[dict[str, object]] = []
    for market in _markets_of(payload):
        parsed = parse_market_ticker(str(market.get("ticker", "")))
        if parsed is None:
            continue
        canonical = GAME_MARKET_BY_SERIES.get(parsed.series)
        if canonical is None:
            continue
        if str(market.get("status", "active")) != "active":
            continue
        rows.extend(
            _market_rows(
                parsed,
                canonical,
                _ask_to_american(market.get("yes_ask_dollars")),
                _ask_to_american(market.get("no_ask_dollars")),
                market.get("floor_strike"),
                is_closing,
            )
        )

    if not rows:
        return Lines.validate(_empty_frame(_LINES_COLUMNS))
    df = _finish_lines(rows, timestamp)
    # Board rows are executable asks; recording the basis keeps CLV
    # like-for-like once a mid-quoted history source joins (E7).
    return Lines.validate(df[_LINES_COLUMNS]).assign(price_basis="ask")


def _candle_close(candle: Mapping[str, Any], side: str) -> Any:
    """A candle's closing quote for one side — live or historical field names."""
    quote = candle.get(side) or {}
    close = quote.get("close_dollars")
    # The /historical archive drops the ``_dollars`` suffix (probe 2026-09-09).
    return close if close is not None else quote.get("close")


def normalize_kalshi_candles(
    market: Mapping[str, Any],
    candles: Any,
    is_closing: bool = True,
) -> pd.DataFrame:
    """One market's candlesticks → a time series of canonical ``Lines`` rows.

    ``market`` is the market object (it supplies the ticker and
    ``floor_strike`` — candles carry neither); ``candles`` is a candlesticks
    response (live or ``/historical`` shape). Each candle contributes the
    market's sides at that bucket's closing quotes: the yes-ask close prices
    the named outcome, and the no-ask is reconstructed as ``1 − yes-bid``
    close (a resting NO ask *is* a YES bid at the complement — verified on
    live boards). In-game certainty quotes (ask $1.00 / bid $0.00) drop via
    the executable guard. The output feeds ``pit.closing_line``, which keeps
    the last pre-kickoff row per contract.
    """
    parsed = parse_market_ticker(str(market.get("ticker", "")))
    canonical = None if parsed is None else GAME_MARKET_BY_SERIES.get(parsed.series)
    if parsed is None or canonical is None:
        return Lines.validate(_empty_frame(_LINES_COLUMNS))

    entries = candles.get("candlesticks") if isinstance(candles, Mapping) else candles
    rows: list[dict[str, object]] = []
    for candle in entries or []:
        end_ts = candle.get("end_period_ts")
        if end_ts is None:
            continue
        yes_price = _ask_to_american(_candle_close(candle, "yes_ask"))
        no_price: int | None = None
        bid_close = _candle_close(candle, "yes_bid")
        if bid_close is not None:
            try:
                no_price = _ask_to_american(1.0 - float(bid_close))
            except (TypeError, ValueError):
                no_price = None
        stamp = pd.Timestamp(int(end_ts), unit="s")
        for row in _market_rows(
            parsed, canonical, yes_price, no_price, market.get("floor_strike"), is_closing
        ):
            row["timestamp"] = stamp
            rows.append(row)

    if not rows:
        return Lines.validate(_empty_frame(_LINES_COLUMNS))
    df = _finish_lines(rows)
    # Candle closes are ask-quoted, the same basis as the live board, so CLV
    # against a board entry is like-for-like (docs/BUILD_EXCHANGES.md E7).
    validated = Lines.validate(df[_LINES_COLUMNS])
    return validated.assign(price_basis="ask")


def normalize_kalshi_props(
    payload: Any,
    timestamp: Any,
    is_closing: bool = False,
) -> pd.DataFrame:
    """Flatten Kalshi player-prop ladders onto the canonical ``PropLines``.

    Only the series in :data:`PROP_MARKET_BY_SERIES` are kept. Each market is
    one rung ("Tua Tagovailoa: 300+ passing yards", ``floor_strike`` 299.5):
    yes-ask → ``over``, no-ask → ``under``, player from the title's colon head.
    """
    rows: list[dict[str, object]] = []
    for market in _markets_of(payload):
        parsed = parse_market_ticker(str(market.get("ticker", "")))
        if parsed is None:
            continue
        stat = PROP_MARKET_BY_SERIES.get(parsed.series)
        if stat is None:
            continue
        if str(market.get("status", "active")) != "active":
            continue
        strike = _half_strike(market.get("floor_strike"))
        title = str(market.get("title", ""))
        player = title.split(":", 1)[0].strip() if ":" in title else None
        if strike is None or not player:
            continue
        yes_price = _ask_to_american(market.get("yes_ask_dollars"))
        no_price = _ask_to_american(market.get("no_ask_dollars"))
        for side, price in (("over", yes_price), ("under", no_price)):
            if price is None:
                continue
            rows.append(
                {
                    "game_id": parsed.game_key,
                    "book": _BOOK,
                    "market": stat,
                    "player": player,
                    "side": side,
                    "price": price,
                    "point": strike,
                    "is_closing": is_closing,
                }
            )

    if not rows:
        return PropLines.validate(_empty_frame(_PROP_COLUMNS))
    stamp = pd.to_datetime(timestamp, utc=True).tz_localize(None)
    df = pd.DataFrame(rows)
    df["timestamp"] = stamp
    player_key = df["player"].str.lower().str.replace(r"\s+", "-", regex=True)
    df["line_id"] = (
        df["game_id"]
        + "|" + df["market"]
        + "|" + player_key
        + "|" + df["side"]
        + "|" + df["book"]
        + "|" + df["point"].map(lambda v: f"{float(v):g}")
    )
    df = df.drop_duplicates("line_id").reset_index(drop=True)
    return PropLines.validate(df[_PROP_COLUMNS])


# Kalshi NFL codes that differ from our rating keys and whose display names are
# too truncated to resolve ("Los Angeles R"). Everything else resolves from the
# code or the market subtitle, so this list stays deliberately tiny.
NFL_CODE_FIXUPS = {"LAR": "LA", "JAC": "JAX"}


def team_names_by_code(payload: Any) -> dict[str, str]:
    """Team code → the venue's display name, read off winner markets.

    A ticker's code is opaque (``UWGA``, ``SJSU``), but the winner market's
    subtitle names the team ("San Jose St."). Callers resolve those names
    against the model's team universe, which beats hand-keying hundreds of
    college codes: from a live board, 97% of Kalshi's NCAAF codes resolve this
    way against 8% from the code alone.
    """
    out: dict[str, str] = {}
    for market in _markets_of(payload):
        parsed = parse_market_ticker(str(market.get("ticker", "")))
        if parsed is None or GAME_MARKET_BY_SERIES.get(parsed.series) != "moneyline":
            continue
        name = market.get("yes_sub_title") or str(market.get("title", "")).removesuffix(" wins")
        if parsed.suffix.isalpha() and name:
            out[parsed.suffix] = str(name)
    return out


def extract_kalshi_events(payload: Any) -> pd.DataFrame:
    """Per-event metadata from a winner-series (``*GAME``) markets payload.

    The winner event's two market suffixes name both teams, which lets the
    away+home blob be split without a team-code table: the code the blob ends
    with is home ({AWAY}{HOME} grammar). Returns
    ``[game_id, date, home_team, away_team]`` with ``date`` the ticker's game
    date — kickoff time is not in the ticker and is joined later from our own
    schedule by (teams, date) (docs/BUILD_EXCHANGES.md E1).
    """
    codes_by_event: dict[str, tuple[ParsedTicker, set[str]]] = {}
    for market in _markets_of(payload):
        parsed = parse_market_ticker(str(market.get("ticker", "")))
        if parsed is None or GAME_MARKET_BY_SERIES.get(parsed.series) != "moneyline":
            continue
        if not parsed.suffix.isalpha():
            continue
        entry = codes_by_event.setdefault(parsed.game_key, (parsed, set()))
        entry[1].add(parsed.suffix)

    rows: list[dict[str, object]] = []
    for game_key, (parsed, codes) in codes_by_event.items():
        home = next((c for c in codes if _is_home(parsed.teams, c)), None)
        away = None if home is None else _other_team(parsed.teams, home)
        if home is None or away is None or away not in codes:
            continue
        rows.append(
            {
                "game_id": game_key,
                "date": parsed.date,
                "home_team": home,
                "away_team": away,
            }
        )
    return pd.DataFrame(rows, columns=["game_id", "date", "home_team", "away_team"])


def _empty_frame(columns: list[str]) -> pd.DataFrame:
    dtypes: dict[str, Any] = {
        "price": "int64",
        "point": float,
        "timestamp": "datetime64[ns]",
        "is_closing": bool,
    }
    return pd.DataFrame({c: pd.Series(dtype=dtypes.get(c, str)) for c in columns})


@dataclass
class KalshiClient:
    """Network client for Kalshi market data (key-less; be polite).

    Reads need no key (verified live 2026-09-09); unauthenticated limits are
    undocumented, so requests are spaced and 429s backed off. Order placement
    and WebSocket need RSA request signing — deliberately not built here
    (docs/BUILD_EXCHANGES.md §4: no execution in this build).
    """

    base_url: str = _BASE
    sleep_seconds: float = 0.35
    max_pages: int = 30

    def _get(  # pragma: no cover - network
        self, path: str, params: Mapping[str, Any] | None = None
    ) -> Any:
        query = f"?{urllib.parse.urlencode(dict(params))}" if params else ""
        req = urllib.request.Request(
            f"{self.base_url}{path}{query}",
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        for attempt, delay in enumerate((0, 5, 15, 45)):
            if delay:
                time.sleep(delay)
            try:
                with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503) or attempt == 3:
                    raise
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                # Sustained pulls occasionally hit a connection reset / TLS
                # EOF (observed live 2026-09-09); transient — back off, retry.
                if attempt == 3:
                    raise
        raise RuntimeError("unreachable")

    def markets(  # pragma: no cover - network
        self, series_ticker: str, status: str = "open", min_close_ts: int | None = None
    ) -> dict[str, list[dict]]:
        """All markets of one series, cursor pages merged to ``{"markets": […]}``.

        ``min_close_ts`` narrows to markets closing at/after that unix time —
        how the candle collector finds recently settled markets.
        """
        merged: list[dict] = []
        cursor: str | None = None
        for _ in range(self.max_pages):
            params: dict[str, Any] = {"series_ticker": series_ticker, "limit": 1000}
            if status:
                params["status"] = status
            if min_close_ts is not None:
                params["min_close_ts"] = min_close_ts
            if cursor:
                params["cursor"] = cursor
            payload = self._get("/markets", params)
            merged.extend(payload.get("markets") or [])
            cursor = payload.get("cursor") or None
            if not cursor:
                break
            time.sleep(self.sleep_seconds)
        return {"markets": merged}

    def candlesticks_batch(  # pragma: no cover - network
        self,
        tickers: Sequence[str],
        start_ts: int,
        end_ts: int,
        period_interval: int = 1,
    ) -> dict[str, list[dict]]:
        """Candles for many markets in one call → ``{market_ticker: candles}``.

        The batch endpoint caps a request at 10,000 candles **across all
        markets** (verified live), so the caller sizes each batch from the
        window: an 8-hour minute window is 480 candles a market, hence 20
        markets a call. Pulling one market at a time instead costs two orders
        of magnitude more requests — a college Saturday's ~10k settled rungs
        would take hours rather than minutes.
        """
        out: dict[str, list[dict]] = {}
        payload = self._get(
            "/markets/candlesticks",
            {
                "market_tickers": ",".join(tickers),
                "start_ts": start_ts,
                "end_ts": end_ts,
                "period_interval": period_interval,
            },
        )
        for entry in payload.get("markets") or []:
            ticker = entry.get("market_ticker")
            if ticker:
                out[str(ticker)] = list(entry.get("candlesticks") or [])
        return out

    def candlesticks(
        self,
        series_ticker: str,
        market_ticker: str,
        start_ts: int,
        end_ts: int,
        period_interval: int = 60,
    ) -> Any:  # pragma: no cover - network
        """OHLC candles (trade + bid + ask series) for one market."""
        return self._get(
            f"/series/{series_ticker}/markets/{market_ticker}/candlesticks",
            {"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval},
        )

    def exchange_status(self) -> Any:  # pragma: no cover - network
        """Exchange open/close status — the collector's smoke check."""
        return self._get("/exchange/status")
