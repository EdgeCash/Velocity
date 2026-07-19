"""The Odds API ingest — nested odds JSON flattens to canonical lines, offline.

Exercises only the pure ``normalize_odds_events`` / ``unwrap`` mappings against a
frozen sample mimicking the ``/odds`` (live array) and ``/historical`` (``{data}``
wrapper) shapes. The network ``TheOddsAPIClient`` is not touched here.
"""

from __future__ import annotations

from velocity.ingest.theoddsapi import (
    GAME_MARKET_BY_KEY,
    SPORT_KEYS,
    TheOddsAPIClient,
    normalize_odds_events,
    unwrap,
)
from velocity.store.schema import Lines

# One event, one book, all three game markets — plus a props market that must be
# ignored (it isn't a game-level Lines market).
EVENTS = [
    {
        "id": "evt-abc",
        "sport_key": "americanfootball_nfl",
        "commence_time": "2026-09-10T00:20:00Z",
        "home_team": "Kansas City Chiefs",
        "away_team": "Buffalo Bills",
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "last_update": "2026-09-09T23:55:00Z",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Kansas City Chiefs", "price": -165},
                            {"name": "Buffalo Bills", "price": 140},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Kansas City Chiefs", "price": -110, "point": -3.5},
                            {"name": "Buffalo Bills", "price": -110, "point": 3.5},
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": -108, "point": 48.5},
                            {"name": "Under", "price": -112, "point": 48.5},
                        ],
                    },
                    {
                        "key": "player_pass_yds",  # a prop — must be ignored
                        "outcomes": [{"name": "Over", "price": -115, "point": 275.5}],
                    },
                ],
            }
        ],
    }
]

HISTORICAL = {
    "timestamp": "2026-09-09T23:55:00Z",
    "previous_timestamp": "2026-09-09T23:50:00Z",
    "next_timestamp": "2026-09-10T00:00:00Z",
    "data": EVENTS,
}


def test_normalize_validates_and_keeps_only_game_markets() -> None:
    lines = normalize_odds_events(EVENTS)
    Lines.validate(lines)
    assert set(lines["market"]) == {"moneyline", "spread", "total"}
    # 2 h2h + 2 spreads + 2 totals = 6; the prop market is dropped.
    assert len(lines) == 6


def test_moneyline_point_null_others_carry_number() -> None:
    lines = normalize_odds_events(EVENTS)
    ml = lines[lines["market"] == "moneyline"]
    assert ml["point"].isna().all()
    assert set(lines[lines["market"] == "spread"]["point"]) == {-3.5, 3.5}
    assert set(lines[lines["market"] == "total"]["point"]) == {48.5}


def test_prices_are_integer_american() -> None:
    lines = normalize_odds_events(EVENTS)
    assert lines["price"].dtype.kind == "i"
    assert {-165, 140}.issubset(set(lines["price"]))


def test_timestamp_falls_back_to_book_update() -> None:
    lines = normalize_odds_events(EVENTS)
    # No market-level last_update in the sample → book's last_update is used.
    assert lines["timestamp"].notna().all()
    assert str(lines["timestamp"].iloc[0]).startswith("2026-09-09 23:55")


def test_is_closing_flag_propagates() -> None:
    live = normalize_odds_events(EVENTS, is_closing=False)
    close = normalize_odds_events(EVENTS, is_closing=True)
    assert not live["is_closing"].any()
    assert close["is_closing"].all()


def test_unwrap_handles_live_array_and_historical_wrapper() -> None:
    assert unwrap(EVENTS) == EVENTS
    assert unwrap(HISTORICAL) == EVENTS
    assert unwrap(None) == []
    assert unwrap({}) == []


def test_historical_wrapper_normalizes_same_as_live() -> None:
    from_live = normalize_odds_events(unwrap(EVENTS), is_closing=True)
    from_hist = normalize_odds_events(unwrap(HISTORICAL), is_closing=True)
    assert list(from_live["line_id"]) == list(from_hist["line_id"])


def test_line_id_unique_and_stable() -> None:
    lines = normalize_odds_events(EVENTS)
    assert lines["line_id"].is_unique
    again = normalize_odds_events(EVENTS)
    assert list(lines["line_id"]) == list(again["line_id"])


def test_empty_events_yield_empty_valid_lines() -> None:
    lines = normalize_odds_events([])
    Lines.validate(lines)
    assert lines.empty


def test_market_and_sport_maps() -> None:
    assert set(GAME_MARKET_BY_KEY.values()) == {"moneyline", "spread", "total"}
    assert SPORT_KEYS["nfl"] == "americanfootball_nfl"
    assert SPORT_KEYS["ncaaf"] == "americanfootball_ncaaf"


def test_sport_key_passthrough_and_mapping() -> None:
    assert TheOddsAPIClient.sport_key("nfl") == "americanfootball_nfl"
    assert TheOddsAPIClient.sport_key("NCAAF") == "americanfootball_ncaaf"
    # An already-qualified key passes through unchanged.
    assert TheOddsAPIClient.sport_key("basketball_nba") == "basketball_nba"


def test_from_env_requires_key(monkeypatch) -> None:
    monkeypatch.delenv("THE_ODDS_API", raising=False)
    try:
        TheOddsAPIClient.from_env()
    except RuntimeError as exc:
        assert "THE_ODDS_API" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected RuntimeError when THE_ODDS_API is unset")


def test_from_env_builds_client(monkeypatch) -> None:
    monkeypatch.setenv("THE_ODDS_API", "secret-key")
    client = TheOddsAPIClient.from_env()
    assert client.api_key == "secret-key"
    assert client.odds_format == "american"
