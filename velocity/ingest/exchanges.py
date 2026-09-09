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
   (:func:`~velocity.wagering.live.align_game_ids`).

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
from velocity.wagering.live import align_game_ids, apply_team_aliases, exchange_aliases

# Our league → the series each venue lists it under.
KALSHI_SERIES_BY_LEAGUE = {
    "nfl": ("KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL", "KXNFLTEAMTOTAL"),
    "ncaaf": ("KXNCAAFGAME", "KXNCAAFSPREAD", "KXNCAAFTOTAL", "KXNCAAFTEAMTOTAL"),
}
POLYMARKET_LEAGUE = {"nfl": "nfl", "ncaaf": "cfb"}


def kalshi_board(
    payloads: Mapping[str, Any],
    known_teams: Iterable[str],
    base_events: pd.DataFrame,
    timestamp: Any,
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
        return _empty_lines(), {"games": 0, "lines": 0}

    frames = [
        kalshi_ingest.normalize_kalshi_markets(payload, timestamp) for payload in payloads.values()
    ]
    lines = pd.concat(frames, ignore_index=True) if frames else _empty_lines()
    events = kalshi_ingest.extract_kalshi_events(winner)
    aliases = exchange_aliases(
        kalshi_ingest.team_names_by_code(winner), known_teams, kalshi_ingest.NFL_CODE_FIXUPS
    )
    lines, events = apply_team_aliases(lines, events, aliases)
    lines, events = align_game_ids(lines, events, base_events)
    return lines, {"games": len(events), "lines": len(lines)}


def polymarket_board(
    events_payload: Any,
    books: Any,
    known_teams: Iterable[str],
    base_events: pd.DataFrame,
    timestamp: Any,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Polymarket's events + books → lines re-keyed onto ``base_events``' game ids."""
    lines = pm_ingest.normalize_polymarket_events(events_payload, books, timestamp)
    events = pm_ingest.extract_polymarket_events(events_payload)
    aliases = exchange_aliases(pm_ingest.team_names_by_code(events_payload), known_teams)
    lines, events = apply_team_aliases(lines, events, aliases)
    lines, events = align_game_ids(lines, events, base_events)
    return lines, {"games": len(events), "lines": len(lines)}


def combine(base_lines: pd.DataFrame, *venue_lines: pd.DataFrame) -> pd.DataFrame:
    """Concatenate a sportsbook board with venue boards already aligned to it."""
    frames = [frame for frame in (base_lines, *venue_lines) if not frame.empty]
    if not frames:
        return base_lines.copy()
    return pd.concat(frames, ignore_index=True)


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
            board, note = kalshi_board(payloads, known_teams, base_events, timestamp)
            boards.append(board)
            notes["kalshi"] = note
        except Exception as exc:  # noqa: BLE001 - an optional venue
            notes["kalshi"] = {"games": 0, "lines": 0}
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
            notes["polymarket"] = {"games": 0, "lines": 0}
            print(f"polymarket board skipped: {exc}")

    combined = combine(_empty_lines(), *boards)
    return combined, notes


def _empty_lines() -> pd.DataFrame:
    return kalshi_ingest.normalize_kalshi_markets({"markets": []}, pd.Timestamp("2026-01-01"))
