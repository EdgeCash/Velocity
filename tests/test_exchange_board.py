"""One shoppable board from many venues — ids aligned, prices comparable.

Each venue names games and teams its own way, so a raw concatenation leaves the
same game sitting under three ids and no price ever shopped across venues.
These pin the alignment and the resulting cross-venue comparison
(docs/BUILD_EXCHANGES.md E6).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from velocity.ingest.exchanges import (
    _canonicalized,
    canonical_base_events,
    combine,
    kalshi_board,
    polymarket_board,
)
from velocity.ingest.odds import shop_best_prices
from velocity.wagering.live import align_game_ids, canonicalize_sides

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures"
KALSHI = json.loads((FIX / "kalshi_nfl.json").read_text())
PM_EVENTS = json.loads((FIX / "polymarket_nfl_events.json").read_text())
PM_BOOKS = json.loads((FIX / "polymarket_nfl_books.json").read_text())
NFL_TEAMS = ["BUF", "DEN", "HOU", "KC", "LA", "NYG"]
STAMP = pd.Timestamp("2026-09-09 01:00:00")

# A sportsbook board carrying the two games the Polymarket fixture covers.
BASE_EVENTS = pd.DataFrame(
    {
        "game_id": ["evt-denkc", "evt-bufhou"],
        "kickoff": [pd.Timestamp("2026-09-15 00:15:00"), pd.Timestamp("2026-09-13 17:00:00")],
        "home_team": ["KC", "HOU"],
        "away_team": ["DEN", "BUF"],
    }
)


def test_polymarket_rows_take_the_sportsbook_game_ids() -> None:
    lines, note = polymarket_board(PM_EVENTS, PM_BOOKS, NFL_TEAMS, BASE_EVENTS, STAMP)
    assert note["games"] == 2
    assert set(lines["game_id"]) == {"evt-denkc", "evt-bufhou"}
    assert set(lines["book"]) == {"polymarket"}
    # line_id stays unique once re-keyed, so rows never collide on the board.
    assert lines["line_id"].is_unique


def test_a_venue_game_missing_from_the_base_board_is_dropped() -> None:
    # The Kalshi fixture's only winner event is NYG at LA, which this
    # sportsbook board doesn't carry — an unshoppable duplicate is worse than
    # a missing row, so it goes.
    lines, note = kalshi_board(
        {"KXNFLGAME": KALSHI}, NFL_TEAMS, BASE_EVENTS, STAMP
    )
    assert note == {"games": 0, "lines": 0, "unresolved_sides": 0}
    assert lines.empty


def test_kickoff_tolerance_absorbs_the_venues_date_conventions() -> None:
    # Kalshi tickers carry an ET calendar date and Polymarket slugs a UTC one,
    # so a prime-time game legitimately lands a day apart. Matching on the team
    # pair plus a generous window keeps it together.
    base = pd.DataFrame(
        {
            "game_id": ["evt-nyg-la"],
            "kickoff": [pd.Timestamp("2026-09-22 00:15:00")],
            "home_team": ["LA"],
            "away_team": ["NYG"],
        }
    )
    lines, note = kalshi_board({"KXNFLGAME": KALSHI}, NFL_TEAMS, base, STAMP)
    assert note["games"] == 1
    assert set(lines["game_id"]) == {"evt-nyg-la"}


def test_unmatched_teams_yield_an_empty_board_not_a_guess() -> None:
    empty = pd.DataFrame(columns=["game_id", "kickoff", "home_team", "away_team"])
    lines, events = align_game_ids(
        pd.DataFrame({"game_id": ["x"], "line_id": ["l"], "side": ["home"]}),
        pd.DataFrame(
            {
                "game_id": ["x"],
                "kickoff": [STAMP],
                "home_team": ["KC"],
                "away_team": ["DEN"],
            }
        ),
        empty,
    )
    assert lines.empty and events.empty


def test_a_provider_named_base_board_still_matches() -> None:
    # THE live shape, and the one the fixtures above quietly skip: the slate
    # hands over The Odds API's own board, whose teams are full display names
    # ("Kansas City Chiefs") because projection resolves them through an alias
    # map instead of rewriting the frame. The exchange rows arrive already
    # canonicalized to rating keys, so an unmediated merge matches nothing and
    # every venue reports an empty board with no error raised.
    provider_named = BASE_EVENTS.assign(
        home_team=["Kansas City Chiefs", "Houston Texans"],
        away_team=["Denver Broncos", "Buffalo Bills"],
    )
    lines, note = polymarket_board(
        PM_EVENTS, PM_BOOKS, NFL_TEAMS, provider_named, STAMP
    )
    assert note["games"] == 2
    assert set(lines["game_id"]) == {"evt-denkc", "evt-bufhou"}


def test_canonicalizing_the_base_board_leaves_rating_keys_alone() -> None:
    # Idempotent, so a board that already speaks rating keys is untouched, and
    # a team the model has never heard of is dropped rather than guessed.
    out = canonical_base_events(
        pd.concat(
            [
                BASE_EVENTS,
                pd.DataFrame(
                    {
                        "game_id": ["evt-unknown"],
                        "kickoff": [pd.Timestamp("2026-09-14 17:00:00")],
                        "home_team": ["Sheffield Wednesday"],
                        "away_team": ["KC"],
                    }
                ),
            ],
            ignore_index=True,
        ),
        NFL_TEAMS,
    )
    assert list(out["home_team"]) == ["KC", "HOU"]
    assert list(out["away_team"]) == ["DEN", "BUF"]


def test_prices_shop_across_venues_on_one_scale() -> None:
    # The payoff: once ids and teams agree, an exchange price and a sportsbook
    # price for the same outcome compete in the same shop.
    exchange, _ = polymarket_board(PM_EVENTS, PM_BOOKS, NFL_TEAMS, BASE_EVENTS, STAMP)
    moneyline = exchange.query("market == 'moneyline' and game_id == 'evt-denkc'")
    # A venue board leaves with canonical sides, so KC — the home team in this
    # game — is "home" here rather than a rating key. Its ask is $0.57, i.e.
    # -133. Quote the same side at a clearly worse sportsbook number so the
    # shop has an unambiguous winner.
    kc = moneyline.query("side == 'home'")
    assert len(kc) == 1 and kc.iloc[0]["price"] == -133
    sportsbook = kc.copy()
    sportsbook["book"] = "bookA"
    sportsbook["line_id"] = sportsbook["line_id"] + "|bookA"
    sportsbook["price"] = -180

    board = combine(sportsbook, exchange)
    best = shop_best_prices(board)
    winner = best[
        (best["game_id"] == "evt-denkc")
        & (best["market"] == "moneyline")
        & (best["side"] == "home")
    ]
    assert len(winner) == 1
    # The exchange's better number wins the shop.
    assert winner.iloc[0]["book"] == "polymarket"


def test_team_named_exchange_rows_survive_the_slates_own_side_pass() -> None:
    """Spread and moneyline rungs must reach the card, not totals alone.

    A venue board is canonicalized in its own scope, so its sides are already
    ``home``/``away`` by the time the slate re-runs
    :func:`~velocity.wagering.live.canonicalize_sides` over the combined board
    against the sportsbook's provider-named events. Before that passthrough
    existed the second pass compared a rating key to "Kansas City Chiefs",
    matched nothing, and dropped every exchange spread and moneyline without a
    word — leaving a card that could only ever hold totals.
    """
    provider_named = BASE_EVENTS.assign(
        home_team=["Kansas City Chiefs", "Houston Texans"],
        away_team=["Denver Broncos", "Buffalo Bills"],
    )
    lines, note = polymarket_board(
        PM_EVENTS, PM_BOOKS, NFL_TEAMS, provider_named, STAMP
    )
    assert note["unresolved_sides"] == 0

    team_markets = lines[lines["market"].isin(["moneyline", "spread"])]
    assert not team_markets.empty, "the fixture must carry team-named markets"
    assert set(team_markets["side"]) == {"home", "away"}

    # The slate's pass over the concatenated board is a no-op on these rows.
    assert len(canonicalize_sides(lines, provider_named)) == len(lines)


def test_a_side_that_still_will_not_resolve_is_counted_not_hidden() -> None:
    # A venue row naming a team that is not in the game it was aligned onto
    # cannot be priced. It is dropped — but the note says how many, so a board
    # that quietly loses a market family is visible in the run log.
    events = pd.DataFrame(
        {
            "game_id": ["evt-denkc"],
            "kickoff": [pd.Timestamp("2026-09-15 00:15:00")],
            "home_team": ["KC"],
            "away_team": ["DEN"],
        }
    )
    lines = pd.DataFrame(
        {
            "game_id": ["evt-denkc", "evt-denkc"],
            "line_id": ["a", "b"],
            "book": ["kalshi", "kalshi"],
            "market": ["moneyline", "moneyline"],
            "side": ["KC", "SEA"],
            "price": [-110, 150],
            "point": [None, None],
            "timestamp": [STAMP, STAMP],
            "is_closing": [False, False],
        }
    )
    kept, note = _canonicalized(lines, events)
    assert list(kept["side"]) == ["home"]
    assert note["unresolved_sides"] == 1
