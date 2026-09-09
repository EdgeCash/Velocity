"""Polymarket ingest — outcome tokens + order books flatten to canonical lines.

Exercises the pure normalizers against frozen real payloads (Gamma events plus
CLOB books, pulled 2026-09-09). The network ``PolymarketClient`` is not touched.
Conventions under test are docs/BUILD_EXCHANGES.md D1-D6: the executable price
is the book's best ask (never the bid, never Gamma's midpoint), points are
signed from the side's own perspective, sides are slug team codes, and
unmappable markets are dropped rather than guessed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from velocity.ingest.odds import LiveOddsAdapter, OddsAdapter
from velocity.ingest.polymarket import (
    best_ask,
    extract_polymarket_events,
    normalize_polymarket_events,
    normalize_polymarket_props,
    parse_event_slug,
    token_ids,
)
from velocity.store.schema import Lines, PropLines

REPO = Path(__file__).resolve().parents[1]
EVENTS = json.loads((REPO / "tests" / "fixtures" / "polymarket_nfl_events.json").read_text())
BOOKS = json.loads((REPO / "tests" / "fixtures" / "polymarket_nfl_books.json").read_text())
STAMP = "2026-09-09T01:01:00Z"


def _lines() -> pd.DataFrame:
    return normalize_polymarket_events(EVENTS, BOOKS, STAMP)


def test_normalize_validates_and_keeps_only_game_markets() -> None:
    lines = _lines()
    Lines.validate(lines)
    assert set(lines["market"]) == {"moneyline", "spread", "total", "team_total_home"}
    # DEN@KC: ml 2 + spread 2 + total 2 + team total 2; BUF@HOU: ml 2 + spread 2.
    # The q1_moneyline and exact_margin markets have no sim support and drop.
    assert len(lines) == 12
    assert set(lines["book"]) == {"polymarket"}


def test_price_is_the_book_ask_not_the_bid_or_gamma_midpoint() -> None:
    ml = _lines().query("market == 'moneyline' and game_id == 'nfl-den-kc-2026-09-15'")
    prices = dict(zip(ml["side"], ml["price"], strict=True))
    # Broncos token: book bid 0.43 / ask 0.44; Gamma's outcomePrices mid is
    # 0.435 and its bestAsk field covers only outcome[0]. Only the ask is
    # executable, so only +127 (from $0.44) is a correct price here.
    assert prices["den"] == 127
    assert prices["kc"] == -133  # ask $0.57 (the mid, $0.565, would be -130)


def test_best_ask_takes_the_lowest_ask_not_the_first_level() -> None:
    # CLOB books arrive worst-first, so asks[0] is the *highest* ask.
    book = {"asks": [{"price": "0.99"}, {"price": "0.60"}, {"price": "0.44"}]}
    assert best_ask(book) == 0.44
    assert best_ask({"asks": []}) is None
    assert best_ask(None) is None


def test_spread_points_are_signed_per_side_from_the_slug_direction() -> None:
    lines = _lines().query("market == 'spread'")
    rows = {(r.game_id, r.side, r.point): r.price for r in lines.itertuples()}
    # "Spread: Chiefs (-1.5)" on a -spread-home- slug: KC is home, so KC -1.5
    # at its own ask ($0.56) and DEN +1.5 at the other token's ask ($0.45).
    assert rows[("nfl-den-kc-2026-09-15", "kc", -1.5)] == -127
    assert rows[("nfl-den-kc-2026-09-15", "den", 1.5)] == 122
    # An -spread-away- slug names the away team instead: BUF -1.5, HOU +1.5.
    assert rows[("nfl-buf-hou-2026-09-13", "buf", -1.5)] == 104
    assert rows[("nfl-buf-hou-2026-09-13", "hou", 1.5)] == -108


def test_totals_and_team_totals_share_a_point_across_sides() -> None:
    lines = _lines()
    totals = {(r.side, r.point): r.price for r in lines.query("market == 'total'").itertuples()}
    assert totals[("Over", 41.5)] == -138
    assert totals[("Under", 41.5)] == 117
    # …-team-total-kc-…: KC is the home code in nfl-den-kc-…, so home.
    home_tt = lines.query("market == 'team_total_home'")
    tt = {(r.side, r.point): r.price for r in home_tt.itertuples()}
    assert tt[("Over", 16.5)] == -1567
    assert tt[("Under", 16.5)] == 186


def test_sides_are_slug_codes_even_when_provider_labels_disagree() -> None:
    # Live quirk: this event's moneyline says "Texans" while its spread says
    # "HOU". canonicalize_sides matches sides to the events frame exactly, so
    # emitting display nicknames would silently drop rows; codes never do.
    game = _lines().query("game_id == 'nfl-buf-hou-2026-09-13'")
    assert set(game["side"]) == {"buf", "hou"}


def test_line_ids_unique_and_stable() -> None:
    first, second = _lines(), _lines()
    assert first["line_id"].is_unique
    assert list(first["line_id"]) == list(second["line_id"])
    assert (first["timestamp"] == pd.Timestamp("2026-09-09 01:01:00")).all()


def test_slug_parser_reads_utc_dates_and_folds_props_events() -> None:
    parsed = parse_event_slug("nfl-den-kc-2026-09-15")
    assert parsed is not None
    assert (parsed.league, parsed.away, parsed.home) == ("nfl", "den", "kc")
    # The slug date is UTC: this is the Sep 14 US/ET prime-time game, which
    # Kalshi tickers as 26SEP14 — the opposite convention.
    assert parsed.date == pd.Timestamp("2026-09-15")
    # A props event parses to its game's pieces.
    props = parse_event_slug("nfl-was-phi-2026-09-13-player-props")
    assert props is not None
    assert (props.away, props.home) == ("was", "phi")
    assert parse_event_slug("nfl-ne-sea-2026-09-10-highest-scoring-quarter") is None
    assert parse_event_slug("pro-football-2027-champion") is None


def test_empty_payload_yields_valid_empty_frames() -> None:
    lines = normalize_polymarket_events([], [], STAMP)
    Lines.validate(lines)
    assert lines.empty
    props = normalize_polymarket_props([], [], STAMP)
    PropLines.validate(props)
    assert props.empty


def test_markets_without_books_are_dropped_not_priced() -> None:
    # No books supplied → no executable price anywhere.
    lines = normalize_polymarket_events(EVENTS, [], STAMP)
    Lines.validate(lines)
    assert lines.empty


def test_props_normalize_player_sides_and_points() -> None:
    props = normalize_polymarket_props(EVENTS, BOOKS, STAMP)
    PropLines.validate(props)
    # anytime_touchdowns carries no line and PropLines requires a point: dropped.
    assert set(props["market"]) == {"pass_yards", "receptions"}
    assert set(props["player"]) == {"Jalen Hurts", "Dallas Goedert"}
    # Props fold onto their game's id, not the -player-props event slug.
    assert set(props["game_id"]) == {"nfl-was-phi-2026-09-13"}
    rows = {(r.market, r.side, r.point): r.price for r in props.itertuples()}
    assert rows[("pass_yards", "over", 149.5)] == -3233
    assert rows[("receptions", "under", 1.5)] == -3233
    assert props["line_id"].is_unique


def test_extract_events_gives_codes_and_real_kickoffs() -> None:
    events = extract_polymarket_events(EVENTS)
    # The -player-props companion event is not a game.
    assert len(events) == 2
    row = events.set_index("game_id").loc["nfl-den-kc-2026-09-15"]
    assert (row["home_team"], row["away_team"]) == ("kc", "den")
    # Kickoff comes from the market's gameStartTime, never the slug date.
    assert row["kickoff"] == pd.Timestamp("2026-09-15 00:15:00")
    assert row["league"] == "nfl"


def test_events_frame_sides_match_the_lines_for_canonicalization() -> None:
    from velocity.wagering.live import canonicalize_sides

    lines, events = _lines(), extract_polymarket_events(EVENTS)
    mapped = canonicalize_sides(lines, events)
    # Every row survives: sides and the events frame use the same code labels.
    assert len(mapped) == len(lines)
    assert set(mapped["side"]) <= {"home", "away", "over", "under"}


def test_token_ids_are_deduped_in_order() -> None:
    tokens = token_ids(EVENTS)
    assert len(tokens) == len(set(tokens))
    assert all(t.isdigit() for t in tokens)


def test_normalized_frame_satisfies_adapter_contract() -> None:
    lines = _lines()
    adapter = LiveOddsAdapter(fetch=lambda game_ids: lines)
    assert isinstance(adapter, OddsAdapter)
    Lines.validate(adapter.current_lines())
