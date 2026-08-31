"""BettingPros ingest — nested live JSON flattens to canonical lines, offline.

Exercises only the pure ``normalize_offers`` / ``to_lines`` mappings against a
frozen sample that mimics the ``/offers`` + ``/markets`` response shapes. The
network :class:`BettingProsClient` is not touched here.
"""

from __future__ import annotations

import pandas as pd
from velocity.ingest.bettingpros import (
    GAME_MARKET_BY_SLUG,
    BettingProsClient,
    normalize_offers,
    to_lines,
)
from velocity.store.schema import Lines

# A tiny but faithful slice: three game markets on one event, plus a player prop.
MARKETS = [
    {"id": 1, "slug": "moneyline", "category": "game-odds"},
    {"id": 2, "slug": "spread", "category": "game-odds"},
    {"id": 3, "slug": "total", "category": "game-odds"},
    {"id": 71, "slug": "receiving-yards", "category": "player-props"},
]

OFFERS = [
    {
        "id": "off-spread",
        "market_id": 2,
        "event_id": 1001,
        "player_id": None,
        "team_id": None,
        "selections": [
            {
                "selection": None,
                "participant": "15154",
                "label": "Chiefs",
                "books": [
                    {"id": 10, "lines": [{"cost": -110, "line": -3.5, "main": True, "best": True}]},
                    {"id": 12, "lines": [{"cost": -108, "line": -3.5, "main": True}]},
                ],
            },
            {
                "selection": None,
                "participant": "15155",
                "label": "Bills",
                "books": [
                    {"id": 10, "lines": [{"cost": -110, "line": 3.5, "main": True, "best": True}]},
                ],
            },
        ],
    },
    {
        "id": "off-total",
        "market_id": 3,
        "event_id": 1001,
        "selections": [
            {
                "selection": "Over",
                "books": [
                    {
                        "id": 10,
                        "lines": [
                            {"cost": -110, "line": 47.5, "main": True, "best": True},
                            # a stale alternate the book pulled — must be skipped
                            {"cost": -105, "line": 48.5, "main": False, "is_off": True},
                        ],
                    }
                ],
            },
            {
                "selection": "Under",
                "books": [
                    {"id": 10, "lines": [{"cost": -110, "line": 47.5, "main": True, "best": True}]}
                ],
            },
        ],
    },
    {
        "id": "off-ml",
        "market_id": 1,
        "event_id": 1001,
        "selections": [
            {"selection": "Chiefs", "books": [{"id": 10, "lines": [{"cost": -175, "line": 0}]}]},
            {"selection": "Bills", "books": [{"id": 10, "lines": [{"cost": 150, "line": 0}]}]},
        ],
    },
    {
        "id": "off-prop",
        "market_id": 71,
        "event_id": 1001,
        "player_id": "20999",
        "selections": [
            {"selection": "Over", "books": [{"id": 10, "lines": [{"cost": -115, "line": 275.5}]}]},
            {"selection": "Under", "books": [{"id": 10, "lines": [{"cost": -105, "line": 275.5}]}]},
        ],
    },
]


def _stamp(offers: list[dict], when: str = "2026-01-05T18:00:00Z") -> list[dict]:
    """Add the required ``updated`` timestamp every real BP line carries."""
    for offer in offers:
        for selection in offer.get("selections", []):
            for book in selection.get("books", []):
                for line in book.get("lines", []):
                    line.setdefault("updated", when)
    return offers


_stamp(OFFERS)


def test_normalize_offers_flattens_every_active_line() -> None:
    long = normalize_offers(OFFERS, MARKETS)
    # spread: 2 books home + 1 book away = 3; total: over(1) + under(1) = 2 (stale
    # alt dropped); ml: 2; prop: 2 → 9 rows.
    assert len(long) == 9
    assert set(long["market_slug"]) == {
        "moneyline",
        "spread",
        "total",
        "receiving-yards",
    }
    # The pulled alternate (48.5) never appears.
    assert 48.5 not in set(long["point"].dropna())


def test_to_lines_keeps_only_game_markets_and_validates() -> None:
    lines = to_lines(normalize_offers(OFFERS, MARKETS))
    Lines.validate(lines)
    assert set(lines["market"]) == {"spread", "total", "moneyline"}
    # The player prop is excluded from the game-level Lines view.
    assert len(lines) == 7  # 3 spread + 2 total + 2 ml


def test_moneyline_point_is_null_others_carry_number() -> None:
    lines = to_lines(normalize_offers(OFFERS, MARKETS))
    ml = lines[lines["market"] == "moneyline"]
    assert ml["point"].isna().all()
    total = lines[lines["market"] == "total"]
    assert set(total["point"]) == {47.5}


def test_price_is_integer_american() -> None:
    lines = to_lines(normalize_offers(OFFERS, MARKETS))
    assert lines["price"].dtype.kind == "i"
    assert -175 in set(lines["price"])


def test_line_id_is_unique_and_stable() -> None:
    lines = to_lines(normalize_offers(OFFERS, MARKETS))
    assert lines["line_id"].is_unique
    # Re-running yields identical ids (no time/random component).
    again = to_lines(normalize_offers(OFFERS, MARKETS))
    assert list(lines["line_id"]) == list(again["line_id"])


def test_empty_offers_yield_empty_valid_lines() -> None:
    lines = to_lines(normalize_offers([], MARKETS))
    Lines.validate(lines)
    assert lines.empty


def test_offers_without_markets_have_blank_slugs() -> None:
    # Without the markets map, slugs are unknown → no game markets survive to_lines.
    long = normalize_offers(OFFERS)
    assert (long["market_slug"] == "").all()
    assert to_lines(long).empty


def test_game_market_slugs_map_to_canonical_markets() -> None:
    assert set(GAME_MARKET_BY_SLUG.values()) == {"spread", "total", "moneyline"}


def test_from_env_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("BP_API_KEY", raising=False)
    try:
        BettingProsClient.from_env()
    except RuntimeError as exc:
        assert "BP_API_KEY" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected RuntimeError when BP_API_KEY is unset")


def test_from_env_premium_flag(monkeypatch) -> None:
    monkeypatch.setenv("BP_API_KEY", "partner-key")
    monkeypatch.setenv("BP_USER_ID", "4455432")
    monkeypatch.setenv("BP_USER_KEY", "user-key")
    client = BettingProsClient.from_env()
    assert client.is_premium
    monkeypatch.delenv("BP_USER_ID")
    assert not BettingProsClient.from_env().is_premium


def test_stale_and_replaced_lines_dropped() -> None:
    offers = [
        {
            "id": "o1",
            "market_id": 3,
            "event_id": 5,
            "selections": [
                {
                    "selection": "Over",
                    "books": [
                        {
                            "id": 9,
                            "lines": [
                                {"cost": -110, "line": 40, "replaced": True},
                                {"cost": -110, "line": 41, "active": False},
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    assert normalize_offers(offers, MARKETS).empty
    assert isinstance(to_lines(normalize_offers(offers, MARKETS)), pd.DataFrame)


def test_normalize_props_flattens_the_board() -> None:
    from velocity.ingest.bettingpros import normalize_props

    payload = {
        "props": [
            {
                "sport": "NFL",
                "market_id": 102,
                "event_id": 555,
                "participant": {
                    "id": "p1", "name": "Josh Allen",
                    "player": {"team": "BUF", "position": "QB"},
                },
                "over": {"line": 249.5, "odds": -115, "book": 12,
                         "consensus_line": 250.5},
                "under": {"line": 249.5, "odds": -105, "book": 10,
                          "consensus_line": 250.5},
                "projection": {
                    "recommended_side": "over", "value": 264.2,
                    "probability": 0.58, "expected_value": 4.1,
                    "bet_rating": 4, "diff": 14.7,
                },
            },
            {  # free-tier row: premium projection fields nulled by the API
                "sport": "NFL",
                "market_id": 103,
                "event_id": 555,
                "participant": {"id": "p2", "name": "James Cook",
                                "player": {"team": "BUF", "position": "RB"}},
                "over": {"line": 84.5, "odds": -110, "book": 12},
                "under": {"line": 84.5, "odds": -110, "book": 12},
                "projection": {"recommended_side": None, "value": None,
                               "probability": None, "expected_value": None,
                               "bet_rating": None, "diff": None},
            },
            {"sport": "NFL", "participant": {"id": "x"}},  # no market_id → dropped
            "junk",
        ]
    }
    df = normalize_props(payload)
    assert len(df) == 2
    assert (df["market_slug"] == "").all()  # no metadata passed → empty, not a guess
    df = normalize_props(payload, {102: "passing-yards"})
    allen = df[df["player_name"] == "Josh Allen"].iloc[0]
    assert allen["market_slug"] == "passing-yards"  # stamped from /markets metadata
    cook_slug = df[df["player_name"] == "James Cook"].iloc[0]["market_slug"]
    assert cook_slug == ""  # id 103 unknown to the listing → empty
    assert allen["market_id"] == 102
    assert allen["team"] == "BUF"
    assert allen["over_line"] == 249.5
    assert allen["over_book"] == 12
    assert allen["consensus_over_line"] == 250.5
    assert allen["projection"] == 264.2
    assert allen["recommended_side"] == "over"
    assert allen["expected_value"] == 4.1
    cook = df[df["player_name"] == "James Cook"].iloc[0]
    assert pd.isna(cook["projection"])  # premium-nulled stays NaN, not an error
    assert pd.isna(cook["expected_value"])
    assert normalize_props(None).empty
    assert normalize_props({}).empty
