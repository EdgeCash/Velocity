"""Assemble one shoppable board from both prediction exchanges.

Each venue names games its own way — The Odds API an event uuid, Kalshi an
event ticker, Polymarket a slug — and labels teams with venue-specific codes
whose meanings collide (``sdst`` is South Dakota State on one exchange and San
Diego State on the other). Left alone, the same game arrives three times under
three names and no price is ever shopped across venues, which is the whole
point of carrying an exchange board (docs/BUILD_EXCHANGES.md E6).

So each venue is canonicalized **in its own scope**, in this order, before its
rows meet anyone else's:

1. normalize the venue payload to :class:`~velocity.store.schema.Lines`,
2. resolve that venue's team codes to rating keys from its own display names
   (:func:`~velocity.wagering.live.exchange_aliases`), rewriting sides and the
   events frame together,
3. re-key its games onto the sportsbook board's ids by team pair and kickoff
   (:func:`~velocity.wagering.live.align_game_ids`), matching against a
   canonicalized *copy* of that board (:func:`canonical_base_events`) — the
   slate itself keeps the sportsbook's own team names.

Afterwards every row speaks the same team and game language, so the boards
concatenate and ``shop_best_prices`` compares a sportsbook and an exchange on
one American scale. Anything that fails to resolve is dropped and counted in
the returned notes — a wrong team silently mis-prices a game, and an
unshoppable duplicate is worse than a missing row.

This module never places an order: it is a price feed (BUILD_EXCHANGES §4).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import pandas as pd

from velocity.ingest import kalshi as kalshi_ingest
from velocity.ingest import polymarket as pm_ingest
from velocity.wagering.live import (
    align_game_ids,
    apply_team_aliases,
    canonicalize_sides,
    exchange_aliases,
)

# Our league → the series each venue lists it under. Every sport files the same
# four full-game series; the halves, quarters and inning markets beside them
# have no sim support and stay out (docs/BUILD_EXCHANGES.md §4). Verified
# against Kalshi's own sports catalogue on 2026-09-12.
KALSHI_SERIES_BY_LEAGUE = {
    "nfl": ("KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL", "KXNFLTEAMTOTAL"),
    "ncaaf": ("KXNCAAFGAME", "KXNCAAFSPREAD", "KXNCAAFTOTAL", "KXNCAAFTEAMTOTAL"),
    "mlb": ("KXMLBGAME", "KXMLBSPREAD", "KXMLBTOTAL", "KXMLBTEAMTOTAL"),
    "wnba": ("KXWNBAGAME", "KXWNBASPREAD", "KXWNBATOTAL", "KXWNBATEAMTOTAL"),
}
POLYMARKET_LEAGUE = {"nfl": "nfl", "ncaaf": "cfb", "mlb": "mlb", "wnba": "wnba"}

# The leagues either venue can quote at all — what the runner asks before it
# bothers fetching a board.
EXCHANGE_LEAGUES = frozenset(KALSHI_SERIES_BY_LEAGUE) | frozenset(POLYMARKET_LEAGUE)


def kalshi_board(
    payloads: Mapping[str, Any],
    known_teams: Iterable[str],
    base_events: pd.DataFrame,
    timestamp: Any,
    league: str = "nfl",
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Kalshi's markets → lines re-keyed onto ``base_events``' game ids.

    ``payloads`` maps series ticker → that series' ``/markets`` response. The
    winner series is what names teams, so it also supplies the events frame.
    """
    is_winner = kalshi_ingest.GAME_MARKET_BY_SERIES.get
    winner = next(
        (payload for series, payload in payloads.items() if is_winner(series) == "moneyline"),
        None,
    )
    if winner is None:
        return _empty_lines(), {"games": 0, "lines": 0, "unresolved_sides": 0}

    known = list(known_teams)
    frames = [
        kalshi_ingest.normalize_kalshi_markets(payload, timestamp) for payload in payloads.values()
    ]
    lines = pd.concat(frames, ignore_index=True) if frames else _empty_lines()
    events = kalshi_ingest.extract_kalshi_events(winner)
    aliases = exchange_aliases(
        kalshi_ingest.team_names_by_code(winner), known,
        kalshi_ingest.CODE_FIXUPS_BY_LEAGUE.get(league, {}),
    )
    lines, events = apply_team_aliases(lines, events, aliases)
    lines, events = align_game_ids(lines, events, canonical_base_events(base_events, known))
    return _canonicalized(lines, events)


def polymarket_board(
    events_payload: Any,
    books: Any,
    known_teams: Iterable[str],
    base_events: pd.DataFrame,
    timestamp: Any,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Polymarket's events + books → lines re-keyed onto ``base_events``' game ids."""
    known = list(known_teams)
    lines = pm_ingest.normalize_polymarket_events(events_payload, books, timestamp)
    events = pm_ingest.extract_polymarket_events(events_payload)
    aliases = exchange_aliases(pm_ingest.team_names_by_code(events_payload), known)
    lines, events = apply_team_aliases(lines, events, aliases)
    lines, events = align_game_ids(lines, events, canonical_base_events(base_events, known))
    return _canonicalized(lines, events)


def _canonicalized(
    lines: pd.DataFrame, events: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Speak the slate's side language, in this venue's own scope.

    A venue's spread and moneyline rows name a **team** in ``side``, and by
    this point that name is a rating key, because :func:`apply_team_aliases`
    rewrote it. The slate's own board still carries The Odds API's full names,
    so the side mapping it runs later cannot match a rating key against a
    provider name: left alone, every exchange spread and moneyline is dropped
    without a word and only totals reach the card. Mapping here, against this
    venue's own aliased events, is the fix; the later pass is idempotent and
    leaves these rows alone.

    The count of rows that still fail to resolve rides in the notes — a venue
    board that quietly loses its two biggest market families is exactly the
    failure this is closing.
    """
    kept = canonicalize_sides(lines, events)
    return kept, {
        "games": len(events),
        "lines": len(kept),
        "unresolved_sides": int(len(lines) - len(kept)),
    }


def canonical_base_events(
    base_events: pd.DataFrame, known_teams: Iterable[str]
) -> pd.DataFrame:
    """The sportsbook board's teams in rating keys — a copy, for matching only.

    The Odds API writes teams out in full ("Kansas City Chiefs", "Georgia
    Bulldogs") and the slate deliberately leaves them that way: projection
    resolves provider names through its own alias map rather than rewriting
    the board. By the time an exchange board reaches
    :func:`~velocity.wagering.live.align_game_ids` it has already been
    canonicalized to the model's rating keys, so the two sides of that merge
    speak different languages — ``"KC"`` against ``"Kansas City Chiefs"``
    matches nothing, every exchange row is dropped, and the venue reports an
    empty board with no error to show for it. That is a silent failure, which
    is the worst kind: the slate still prints, just without a single exchange
    price on it.

    So the base frame is canonicalized the same way here, on a private copy
    that never leaves this module. A board already keyed by rating keys passes
    through unchanged, and a name that resolves to nothing is dropped — it can
    only ever have matched by accident.
    """
    if base_events.empty:
        return base_events
    names = set(base_events["home_team"].astype(str)) | set(
        base_events["away_team"].astype(str)
    )
    aliases = exchange_aliases({name: name for name in names}, known_teams)
    out = base_events.copy()
    for column in ("home_team", "away_team"):
        out[column] = out[column].astype(str).map(aliases)
    return out.dropna(subset=["home_team", "away_team"]).reset_index(drop=True)


def combine(base_lines: pd.DataFrame, *venue_lines: pd.DataFrame) -> pd.DataFrame:
    """Concatenate a sportsbook board with venue boards already aligned to it."""
    frames = [frame for frame in (base_lines, *venue_lines) if not frame.empty]
    if not frames:
        return base_lines.copy()
    return pd.concat(frames, ignore_index=True)


def board_from_payloads(
    league: str,
    known_teams: Sequence[str],
    base_events: pd.DataFrame,
    timestamp: Any,
    *,
    kalshi_payloads: Mapping[str, Any] | None = None,
    polymarket_events: Any = None,
    polymarket_books: Any = None,
) -> tuple[pd.DataFrame, dict[str, dict[str, int]]]:
    """One aligned board from payloads already in hand — no network.

    The same assembly :func:`fetch_exchange_board` performs after fetching,
    split out so a *banked* snapshot can be rebuilt exactly the way the live
    slate built it. That is what lets grading find an exchange contract's own
    close: the collectors bank the raw payloads hourly, and a close is simply
    the last snapshot before the game started, re-keyed onto the slate's games.
    """
    boards: list[pd.DataFrame] = []
    notes: dict[str, dict[str, int]] = {}
    if kalshi_payloads:
        board, note = kalshi_board(
            kalshi_payloads, known_teams, base_events, timestamp, league=league
        )
        boards.append(board)
        notes["kalshi"] = note
    if polymarket_events is not None:
        board, note = polymarket_board(
            polymarket_events, polymarket_books or [], known_teams, base_events, timestamp
        )
        boards.append(board)
        notes["polymarket"] = note
    return combine(_empty_lines(), *boards), notes


def fetch_exchange_board(  # pragma: no cover - network
    league: str,
    known_teams: Sequence[str],
    base_events: pd.DataFrame,
    timestamp: Any,
    venues: Sequence[str] = ("kalshi", "polymarket"),
) -> tuple[pd.DataFrame, dict[str, dict[str, int]]]:
    """Pull both exchanges live and return one board aligned to ``base_events``.

    Best-effort per venue: a venue that errors is reported and skipped rather
    than failing the slate, matching how the optional team-total fetch behaves.
    Both APIs are key-less, so there is no secret to configure.
    """
    boards: list[pd.DataFrame] = []
    notes: dict[str, dict[str, int]] = {}

    if "kalshi" in venues and league in KALSHI_SERIES_BY_LEAGUE:
        try:
            kalshi_client = kalshi_ingest.KalshiClient()
            payloads = {
                series: kalshi_client.markets(series)
                for series in KALSHI_SERIES_BY_LEAGUE[league]
            }
            board, note = kalshi_board(
                payloads, known_teams, base_events, timestamp, league=league
            )
            boards.append(board)
            notes["kalshi"] = note
        except Exception as exc:  # noqa: BLE001 - an optional venue
            notes["kalshi"] = {"games": 0, "lines": 0, "unresolved_sides": 0}
            print(f"kalshi board skipped: {exc}")

    if "polymarket" in venues and league in POLYMARKET_LEAGUE:
        try:
            pm_client = pm_ingest.PolymarketClient()
            events_payload = pm_client.events(POLYMARKET_LEAGUE[league])
            books = pm_client.books(pm_ingest.token_ids(events_payload))
            board, note = polymarket_board(
                events_payload, books, known_teams, base_events, timestamp
            )
            boards.append(board)
            notes["polymarket"] = note
        except Exception as exc:  # noqa: BLE001 - an optional venue
            notes["polymarket"] = {"games": 0, "lines": 0, "unresolved_sides": 0}
            print(f"polymarket board skipped: {exc}")

    combined = combine(_empty_lines(), *boards)
    return combined, notes


def _empty_lines() -> pd.DataFrame:
    return kalshi_ingest.normalize_kalshi_markets({"markets": []}, pd.Timestamp("2026-01-01"))
