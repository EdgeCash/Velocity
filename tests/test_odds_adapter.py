"""Odds adapters — the swappable-provider contract and free/paid parity."""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.ingest.odds import (
    HistoricalOddsAdapter,
    LiveOddsAdapter,
    OddsAdapter,
    normalize_odds,
    shop_best_prices,
)
from velocity.store.schema import Lines


def test_historical_adapter_satisfies_contract(market: pd.DataFrame) -> None:
    adapter = HistoricalOddsAdapter(market)
    assert isinstance(adapter, OddsAdapter)
    lines = adapter.current_lines()
    Lines.validate(lines)


def test_live_adapter_satisfies_contract(market: pd.DataFrame) -> None:
    adapter = LiveOddsAdapter(fetch=lambda game_ids: market)
    assert isinstance(adapter, OddsAdapter)
    Lines.validate(adapter.current_lines())


def test_adapter_filters_by_game_id(market: pd.DataFrame) -> None:
    adapter = HistoricalOddsAdapter(market)
    assert set(adapter.current_lines(["2023_01_KC_DET"])["game_id"]) == {"2023_01_KC_DET"}
    assert adapter.current_lines(["nonexistent"]).empty


def test_free_and_paid_adapters_agree_on_overlap(market: pd.DataFrame) -> None:
    # Parity: the paid adapter fed the same observations produces the same lines
    # as the free one — the swap is provably behavior-preserving.
    free = HistoricalOddsAdapter(market)
    paid = LiveOddsAdapter(fetch=lambda game_ids: market)
    pd.testing.assert_frame_equal(free.current_lines(), paid.current_lines())


def test_normalize_rejects_missing_columns() -> None:
    with pytest.raises(ValueError, match="missing required columns"):
        normalize_odds(pd.DataFrame({"game_id": ["g1"]}))


def test_shop_best_prices_keeps_the_best_number(market: pd.DataFrame) -> None:
    best = shop_best_prices(market)
    # One row per distinct (game, market, side, point); no duplicates survive.
    keys = ["game_id", "market", "side", "point"]
    assert not best.duplicated(subset=keys, keep=False).any()
    # For the opening over 49.0 offered at bookB -108 vs (no other book at 49.0),
    # the retained price is the most favorable decimal available at that number.
    over = best[best["market"] == "total"]
    over_485 = over[(over["side"] == "over") & (over["point"] == 48.5)]
    assert (over_485["price"] == -110).all()


def test_shop_best_prices_empty() -> None:
    empty = pd.DataFrame(columns=["game_id", "market", "side", "point", "price", "line_id"])
    assert shop_best_prices(empty).empty
