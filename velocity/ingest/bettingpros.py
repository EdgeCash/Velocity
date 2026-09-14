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
import re
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import pandas as pd

from velocity.store.schema import PROP_MARKETS, Lines

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


_PROP_COLUMNS = [
    "sport", "market_id", "market_slug", "event_id", "player_id", "player_name",
    "team", "position", "over_line", "over_odds", "over_book", "under_line",
    "under_odds", "under_book", "consensus_over_line", "consensus_under_line",
    "projection", "recommended_side", "probability", "expected_value",
    "bet_rating", "diff",
]

# BettingPros prop market slug → our canonical prop market name. Deliberately
# conservative: only the slugs whose meaning is unambiguous are mapped, and an
# unmapped slug (or an old snapshot without the column) simply abstains
# downstream — skipped, never guessed. Extend as real snapshots confirm slugs.
BP_PROP_SLUG_TO_MARKET: Mapping[str, str] = {
    "passing-yards": "pass_yards",
    "passing-touchdowns": "pass_tds",
    "rushing-yards": "rush_yards",
    "receiving-yards": "receiving_yards",
    "receptions": "receptions",
}


def normalize_props(
    payload: Mapping[str, Any] | None,
    market_slugs: Mapping[int, str] | None = None,
) -> pd.DataFrame:
    """Flatten a ``/props`` response into one row per (player, market) prop.

    Best over/under lines with their books, consensus lines, and the
    BettingPros projection block. Premium-gated fields (projection value,
    probability, EV, bet rating) are null on free-tier credentials and stay
    NaN here — absence of a paid field is data, not an error. Rows without a
    participant or market are dropped.

    ``market_slugs`` (market_id → slug, from the ``/markets`` listing) stamps
    a human-readable ``market_slug`` onto each row — numeric prop market ids
    are not stable across sports, slugs are, so the banked snapshot carries
    the durable key. Unknown ids get an empty slug.
    """

    def _num(value: Any) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    rows: list[dict[str, object]] = []
    for prop in (payload or {}).get("props") or []:
        if not isinstance(prop, Mapping):
            continue
        market_id = prop.get("market_id")
        participant = prop.get("participant") or {}
        if market_id is None or not isinstance(participant, Mapping):
            continue
        player = participant.get("player") or {}
        over = prop.get("over") or {}
        under = prop.get("under") or {}
        projection = prop.get("projection") or {}
        rows.append(
            {
                "sport": str(prop.get("sport") or ""),
                "market_id": int(market_id),
                "market_slug": str((market_slugs or {}).get(int(market_id), "")),
                "event_id": None if prop.get("event_id") is None
                else str(prop.get("event_id")),
                "player_id": None if participant.get("id") is None
                else str(participant.get("id")),
                "player_name": None if participant.get("name") is None
                else str(participant.get("name")),
                "team": None if player.get("team") is None else str(player.get("team")),
                "position": None if player.get("position") is None
                else str(player.get("position")),
                "over_line": _num(over.get("line")),
                "over_odds": _num(over.get("odds")),
                "over_book": None if over.get("book") is None else int(over["book"]),
                "under_line": _num(under.get("line")),
                "under_odds": _num(under.get("odds")),
                "under_book": None if under.get("book") is None else int(under["book"]),
                "consensus_over_line": _num(over.get("consensus_line")),
                "consensus_under_line": _num(under.get("consensus_line")),
                "projection": _num(projection.get("value")),
                "recommended_side": None if projection.get("recommended_side") is None
                else str(projection.get("recommended_side")),
                "probability": _num(projection.get("probability")),
                "expected_value": _num(projection.get("expected_value")),
                "bet_rating": _num(projection.get("bet_rating")),
                "diff": _num(projection.get("diff")),
            }
        )
    return pd.DataFrame(rows, columns=_PROP_COLUMNS)


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

    def books(  # pragma: no cover - network
        self, sport: str | None = None, **params: object
    ) -> dict[str, str]:
        """``{book id: name}`` from the ``/books`` listing.

        The board keys books by name where this resolves them and by raw id
        where it does not, so a listing that errors degrades the labels and
        nothing else — the same posture ``/markets`` slugs take.
        """
        return normalize_books(self._get("books", sport=sport, **params))

    def offers(
        self, sport: str, market_id: object, event_id: object = None, **params: object
    ) -> list[dict]:  # pragma: no cover - network
        """Return the ``offers`` for ``sport`` / ``market_id`` (optionally one event)."""
        return self._get(
            "offers", sport=sport, market_id=market_id, event_id=event_id, **params
        ).get("offers", [])

    def props(self, sport: str, **params: object) -> dict:  # pragma: no cover - network
        """One page of the ``/props`` board (prop projections + EV) for ``sport``.

        The endpoint serves NFL/NBA/MLB/NHL only (no NCAAF — the spec's prop
        sport enum). Projection/EV/bet-rating fields are premium-gated: they
        come back null on a free-tier key pair, and :func:`normalize_props`
        keeps them as NaN.
        """
        defaults: dict[str, object] = {
            "limit": 5000,
            "ev_threshold": "false",  # the full board, not just the flagged edges
            "include_selections": "false",
            "include_markets": "false",
        }
        defaults.update(params)
        return self._get("props", sport=sport, **defaults)

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


# ---------------------------------------------------------------------------
# The shoppable board — banked snapshots → rows aligned onto the slate's games
# ---------------------------------------------------------------------------
#
# The collector has banked NFL/NCAAF (and now MLB) game lines every three hours
# since it was built, and nothing read them: the live board came from The Odds
# API alone, so a BettingPros price was never shopped and never graded. These
# helpers close that, reusing the exact venue-alignment path the exchanges take
# (docs/BUILD_EXCHANGES.md E6) rather than inventing a second one.
#
# Two things make a BettingPros row different from an exchange row, and both
# are handled here rather than left to the caller:
#
# * **Its books are OUR books.** BettingPros quotes the same sportsbooks The
#   Odds API does, under numeric ids. Left bare, book ``10`` would sit beside
#   ``draftkings`` as if it were a different venue, and a grader could not tell
#   which feed a bet came from. Every key is therefore prefixed ``bp:`` — the
#   feed is part of the book's identity, because the price came from that
#   feed's snapshot at that feed's cadence.
# * **The snapshot is up to a cadence old.** An exchange board is fetched live;
#   this one is read off a parquet the collector banked up to three hours ago.
#   Betting a number that has already moved manufactures edge that was never
#   available (the same reasoning behind the runner's ``--board-max-age-min``),
#   so the board carries a hard age gate and the runner papers it by default.

BP_BOOK_PREFIX = "bp:"
# How old a banked snapshot may be and still be priced. The collector runs
# every three hours, so the freshest bank averages ~90 minutes old and can
# legitimately reach 180; 200 leaves headroom for a late run without ever
# admitting a board from the previous cycle. Tighten it hard before staking
# these rows — see ``--bp-max-age-min``.
DEFAULT_BP_MAX_AGE_MIN = 200.0


def normalize_books(payload: Any) -> dict[str, str]:
    """``{book id: display name}`` from a ``/books`` listing (or an offers blob).

    BettingPros identifies a book by a small integer; the name lives in a
    separate listing. Names are lowercased and space-collapsed so they read
    like the rest of our book keys (``caesars sportsbook`` → ``caesars-
    sportsbook``). A listing we cannot parse yields an empty map, and the
    board then keys books by their id — degraded, never guessed.
    """
    books = payload.get("books") if isinstance(payload, Mapping) else payload
    out: dict[str, str] = {}
    for book in books or []:
        if not isinstance(book, Mapping):
            continue
        book_id = book.get("id")
        name = book.get("name") or book.get("display_name") or book.get("slug")
        if book_id is None or not name:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
        if slug:
            out[str(book_id)] = slug
    return out


def bp_book_key(book: object, names: Mapping[str, str] | None = None) -> str:
    """The board key for a BettingPros book — ``bp:<name>``, else ``bp:<id>``."""
    raw = str(book)
    return BP_BOOK_PREFIX + (names or {}).get(raw, raw)


def snapshot_age_minutes(lines: pd.DataFrame, now: Any) -> float | None:
    """Age of a banked board in minutes — ``None`` when it carries no stamp.

    Age is measured from ``collected_at`` (when the collector last *saw* the
    board), not from a line's own ``timestamp`` (when that price last
    *moved*). A book that has not repriced a game in a day still has a live
    number on today's board; judging it by its own staleness would throw away
    exactly the prices that are most settled.
    """
    column = "collected_at" if "collected_at" in lines.columns else "timestamp"
    if lines.empty or column not in lines.columns:
        return None
    stamps = pd.to_datetime(lines[column], errors="coerce")
    if stamps.isna().all():
        return None
    delta = pd.Timestamp(now) - stamps.max()
    return float(delta.total_seconds() / 60.0)


def _name_tokens(name: object) -> frozenset[str]:
    """Lowercased alphanumeric word set — ``"Kansas City Chiefs"`` → {kansas, city, chiefs}."""
    return frozenset(t for t in re.split(r"[^a-z0-9]+", str(name).lower()) if t)


def resolve_sides_within_game(lines: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Map BettingPros side labels to ``home``/``away`` using that game's own two teams.

    BettingPros labels a spread or moneyline selection with the team's
    **nickname** ("Chiefs", "Bulldogs") while its events frame names the team
    in full ("Kansas City Chiefs"). The slate's own
    :func:`~velocity.wagering.live.canonicalize_sides` needs those two to be
    equal strings, so left alone every spread and moneyline on the board is
    dropped without a word and only totals survive — the same silent failure
    the exchange boards hit (docs/BUILD_EXCHANGES.md E6).

    Matching inside one game is a two-way choice between names from the same
    payload, which is what makes a looser rule safe here: a label resolves when
    its words are a subset of one side's words, or that side's are a subset of
    its own. A label matching **both** teams or neither is dropped, so a
    genuinely ambiguous row never guesses its way onto the card.

    Over/under sides and rows already speaking the slate's language pass
    through untouched, so this is safe to run before the canonical pass.
    """
    if lines.empty or events.empty:
        return lines.copy()
    home = dict(zip(events["game_id"].astype(str), events["home_team"], strict=False))
    away = dict(zip(events["game_id"].astype(str), events["away_team"], strict=False))

    def _matches(label: frozenset[str], team: object) -> bool:
        other = _name_tokens(team)
        if not label or not other:
            return False
        return label <= other or other <= label

    def _side(row: Mapping[Any, Any]) -> str | None:
        raw = str(row["side"])
        low = raw.strip().lower()
        if low in ("home", "away", "over", "under"):
            return low
        gid = str(row["game_id"])
        label = _name_tokens(raw)
        is_home = _matches(label, home.get(gid))
        is_away = _matches(label, away.get(gid))
        if is_home == is_away:  # both or neither — ambiguous, so not ours to call
            return None
        return "home" if is_home else "away"

    out = lines.copy()
    out["side"] = [_side(row) for row in out.to_dict("records")]
    return out[out["side"].notna()].reset_index(drop=True)


def bp_board(
    lines: pd.DataFrame,
    events: pd.DataFrame,
    known_teams: Iterable[str],
    base_events: pd.DataFrame,
    *,
    now: Any,
    league: str | None = None,
    max_age_minutes: float = DEFAULT_BP_MAX_AGE_MIN,
    book_names: Mapping[str, str] | None = None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """A banked BettingPros snapshot → rows keyed onto ``base_events``' games.

    ``lines`` and ``events`` are the collector's ``bp_lines_*`` /
    ``bp_events_*`` parquets; ``base_events`` is the sportsbook board the slate
    is already pricing. The assembly is the exchange path
    (:mod:`velocity.ingest.exchanges`) applied to a sportsbook feed:

    1. drop anything not in ``league`` and fail the age gate as a whole board,
    2. resolve this feed's team names to rating keys in lines and events alike,
    3. re-key its games onto the base board's ids by team pair and kickoff,
    4. canonicalize sides while its own events are still in scope,
    5. prefix every book with ``bp:`` so the feed stays identifiable.

    Returns the aligned frame and a notes dict the runner prints. A board that
    fails the age gate comes back empty with ``stale`` set — never silently
    dropped, and never priced.
    """
    from velocity.ingest.exchanges import canonical_base_events
    from velocity.wagering.live import (
        align_game_ids,
        canonicalize_sides,
        exchange_aliases,
    )

    notes: dict[str, object] = {
        "games": 0, "lines": 0, "unresolved_sides": 0, "stale": False, "age_min": None,
    }
    empty = to_lines(pd.DataFrame(columns=_LONG_COLUMNS))
    if lines.empty or events.empty:
        return empty, notes

    if league is not None and "league" in lines.columns:
        lines = lines[lines["league"].astype(str).str.lower() == league.lower()]
    if league is not None and "league" in events.columns:
        events = events[events["league"].astype(str).str.lower() == league.lower()]
    if lines.empty or events.empty:
        return empty, notes

    age = snapshot_age_minutes(lines, now)
    notes["age_min"] = None if age is None else round(age, 1)
    # An unstamped board cannot be shown to be fresh, so it is treated as
    # stale: the failure mode this gate exists to prevent (pricing a number
    # that has already moved) is exactly the one an unknown age hides.
    if age is None or age > max_age_minutes:
        notes["stale"] = True
        return empty, notes

    # Sides first, in BettingPros' own scope: its nicknames only mean anything
    # against its own event names, and once they are home/away every later
    # pass leaves them alone.
    before = len(lines)
    lines = resolve_sides_within_game(lines, events)
    unresolved = before - len(lines)
    notes["unresolved_sides"] = unresolved
    if lines.empty:
        notes["games"] = len(events)
        return empty, notes

    # Only the EVENTS need rewriting to rating keys — the lines' sides are
    # already home/away/over/under, and ``apply_team_aliases`` would drop them
    # precisely because they are (it passes through over/under and looks
    # everything else up in the alias table). So the events are mapped here and
    # the lines simply follow the games that survived.
    aliases = exchange_aliases(
        {
            name: name
            for name in set(events["home_team"].astype(str))
            | set(events["away_team"].astype(str))
        },
        known_teams,
    )
    events = events.copy()
    for column in ("home_team", "away_team"):
        events[column] = events[column].astype(str).map(aliases)
    events = events.dropna(subset=["home_team", "away_team"]).reset_index(drop=True)
    lines = lines[lines["game_id"].astype(str).isin(set(events["game_id"].astype(str)))]
    if lines.empty or events.empty:
        notes["games"] = len(events)
        return empty, notes

    lines, events = align_game_ids(lines, events, canonical_base_events(base_events, known_teams))
    if lines.empty:
        notes["games"] = len(events)
        return empty, notes

    # Idempotent by contract — every side is already canonical — but kept so a
    # future payload shape that slips through cannot reach the slate unmapped.
    kept = canonicalize_sides(lines, events)
    notes["games"] = len(events)
    notes["lines"] = len(kept)
    notes["unresolved_sides"] = unresolved + (len(lines) - len(kept))
    if kept.empty:
        return empty, notes

    kept = kept.copy()
    kept["book"] = kept["book"].map(lambda b: bp_book_key(b, book_names))
    return kept.reset_index(drop=True), notes


def bp_book_keys(board: pd.DataFrame) -> frozenset[str]:
    """Every ``bp:`` book key on a board — what the runner papers as a venue."""
    if board.empty or "book" not in board.columns:
        return frozenset()
    return frozenset(str(b).strip().lower() for b in board["book"].unique())


# ---------------------------------------------------------------------------
# Slug coverage — what the prop board actually serves vs what we map
# ---------------------------------------------------------------------------
#
# ``BP_PROP_SLUG_TO_MARKET`` was written from reasoning, not observation, and
# docs/INTEL.md has carried "confirming the slug table against a real
# post-deploy snapshot" as an open item ever since. Meanwhile the collector has
# been banking the raw /props payload every three hours, so the answer has been
# sitting in the artifacts the whole time — nothing had asked it.
#
# These are the asking. An unmapped slug is not an error: the intel layer
# abstains on it, which is the correct behaviour for a market whose meaning we
# have not established. It *is* a gap, and a gap nobody can see is one nobody
# closes — so the collector prints this report every run, and
# ``scripts/inspect_bp_slugs.py`` prints it from a snapshot already banked.

# ``PROP_MARKETS`` (imported at the top) is the canonical set this system
# actually prices. A slug mapping to anything outside it would bank rows no
# model can use, so the report calls that out rather than quietly accepting it.
_SLUG_REPORT_COLUMNS = ["market_slug", "rows", "market", "mapped", "priced"]


def slug_coverage(props: pd.DataFrame) -> pd.DataFrame:
    """Per-slug coverage of a banked ``/props`` frame, busiest first.

    Columns: the slug, how many rows carry it, the canonical market it maps to
    (empty when unmapped), whether it is mapped at all, and whether that market
    is one the props stack actually prices. Rows with no slug — a snapshot
    taken while the ``/markets`` listing was failing — are reported under
    ``"(no slug)"`` rather than dropped, because that is a collector problem
    and it should be visible as one.
    """
    if props.empty or "market_slug" not in props.columns:
        return pd.DataFrame(columns=_SLUG_REPORT_COLUMNS)
    slugs = props["market_slug"].fillna("").astype(str).str.strip()
    counts = slugs.replace("", "(no slug)").value_counts()
    rows = []
    for slug, n in counts.items():
        market = BP_PROP_SLUG_TO_MARKET.get(str(slug), "")
        rows.append({
            "market_slug": str(slug),
            "rows": int(n),
            "market": market,
            "mapped": bool(market),
            "priced": market in PROP_MARKETS,
        })
    return pd.DataFrame(rows, columns=_SLUG_REPORT_COLUMNS)


def describe_slug_coverage(props: pd.DataFrame, sport: str = "") -> list[str]:
    """The coverage report as printable lines — what a run log should say.

    Deliberately loud about the unmapped slugs and silent about nothing: a
    board where four of five markets abstain reads as a healthy snapshot on
    every other line of the log.
    """
    label = f"{sport} " if sport else ""
    table = slug_coverage(props)
    if table.empty:
        return [f"  {label}slug coverage: no prop rows to report"]
    mapped = table[table["mapped"]]
    unmapped = table[~table["mapped"]]
    lines = [
        f"  {label}slug coverage: {len(mapped)} of {len(table)} slug(s) mapped, "
        f"{int(mapped['rows'].sum())} of {int(table['rows'].sum())} rows usable"
    ]
    for row in mapped.to_dict("records"):
        flag = "" if row["priced"] else "  <-- mapped to a market we do not price"
        lines.append(f"    mapped   {row['market_slug']:<28} {row['rows']:>5} -> "
                     f"{row['market']}{flag}")
    for row in unmapped.to_dict("records"):
        lines.append(f"    UNMAPPED {row['market_slug']:<28} {row['rows']:>5} "
                     "(intel abstains on every row)")
    if len(unmapped):
        lines.append("    -> add the ones worth pricing to BP_PROP_SLUG_TO_MARKET "
                     "(velocity/ingest/bettingpros.py)")
    return lines
