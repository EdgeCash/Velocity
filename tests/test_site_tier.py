"""The public tier must not be able to leak a paid-odds number.

``docs/SITE.md``'s rule is that the site carries paid-odds-derived numbers, so
it deploys behind Access and is never public. ``--tier public`` is the seam
that would let a public surface exist, and the whole point of putting that seam
in the DATA BUILD rather than in the pages is that it can be checked like this:
not "does the page decline to render the column" but "is the value in the file
at all".

A page that merely hides a column still ships it, sitting in a parquet the
browser downloads and anyone can open with DuckDB. These tests are the
difference.
"""

from __future__ import annotations

import pandas as pd
import pytest
from scripts.build_site_data import (
    PUBLIC_DROP_COLUMNS,
    PUBLIC_DROP_TABLES,
    PUBLIC_VENUES,
    SENTINEL_LEAGUE,
    apply_tier,
    site_meta_frame,
)


def board_frame() -> pd.DataFrame:
    """A board shaped like the real one: two paid books and two exchanges."""
    return pd.DataFrame([
        {"game_id": "g1", "market": "total", "side": "over", "point": 48.5,
         "book": "fanduel", "venue": "sportsbook", "price": -110.0,
         "p_model": 0.53, "p_fair": 0.508, "edge": 0.022, "stake": 0.9,
         "stake_sized": 0.45, "conviction": 0.61, "league": "nfl"},
        {"game_id": "g1", "market": "total", "side": "over", "point": 48.5,
         "book": "lowvig", "venue": "sportsbook", "price": -105.0,
         "p_model": 0.53, "p_fair": 0.512, "edge": 0.018, "stake": 0.9,
         "stake_sized": 0.45, "conviction": 0.61, "league": "nfl"},
        {"game_id": "g1", "market": "total", "side": "over", "point": 48.5,
         "book": "kalshi", "venue": "kalshi", "price": 113.0,
         "p_model": 0.53, "p_fair": 0.470, "edge": 0.060, "stake": 0.9,
         "stake_sized": 0.45, "conviction": 0.61, "league": "nfl"},
        {"game_id": "g1", "market": "total", "side": "over", "point": 48.5,
         "book": "polymarket", "venue": "polymarket", "price": 108.0,
         "p_model": 0.53, "p_fair": 0.481, "edge": 0.049, "stake": 0.9,
         "stake_sized": 0.45, "conviction": 0.61, "league": "nfl"},
    ])


def test_the_private_tier_is_left_exactly_as_it_was() -> None:
    board = board_frame()
    out = apply_tier("board", board, "private")
    pd.testing.assert_frame_equal(out, board)


def test_a_public_board_keeps_only_the_exchanges() -> None:
    # A sportsbook row is a paid quote even with its price blanked: that a book
    # has a line on this game AT ALL is the feed's information, and the row
    # count alone would carry it.
    out = apply_tier("board", board_frame(), "public")
    assert set(out["venue"]) == set(PUBLIC_VENUES)
    assert len(out) == 2


def test_a_public_board_keeps_the_exchange_price() -> None:
    # The one venue the public tier is allowed to quote. Kalshi and Polymarket
    # prices are public market data; stripping them would leave the tier with
    # no market at all, which is not the rule — the rule is about PAID feeds.
    out = apply_tier("board", board_frame(), "public")
    assert out["price"].notna().all()
    assert sorted(out["price"]) == [108.0, 113.0]


def test_a_public_board_drops_every_number_measured_against_a_paid_price() -> None:
    # `edge` is p_model less the de-vigged fair probability of the BEST price
    # across all venues — so shipping it beside p_model lets the paid book's
    # price be solved for exactly. Same for p_fair, and for the stakes that
    # ride on them.
    out = apply_tier("board", board_frame(), "public")
    for column in ("p_fair", "edge", "stake", "stake_sized"):
        assert out[column].isna().all(), f"{column} survived into the public tier"
    # The model's own probability is ours and stays.
    assert out["p_model"].notna().all()


def test_the_blanked_columns_keep_their_place_so_the_page_still_parses() -> None:
    # Blanking rather than dropping is what lets ONE page of SQL serve both
    # tiers. A build that deleted the columns would fail to parse instead of
    # rendering a public tier.
    out = apply_tier("board", board_frame(), "public")
    assert list(out.columns) == list(board_frame().columns)


@pytest.mark.parametrize("table", PUBLIC_DROP_TABLES)
def test_every_wholly_private_table_comes_back_empty(table: str) -> None:
    frame = pd.DataFrame([{"league": "nfl", "value": 1.0},
                          {"league": "mlb", "value": 2.0}])
    assert apply_tier(table, frame, "public").empty
    # ...and is untouched on the private tier.
    assert len(apply_tier(table, frame, "private")) == 2


@pytest.mark.parametrize("table,columns", sorted(PUBLIC_DROP_COLUMNS.items()))
def test_every_named_private_column_is_blanked(table: str, columns: tuple) -> None:
    frame = pd.DataFrame([
        {**dict.fromkeys(columns, 1.0), "league": "nfl", "keep": 7.0},
    ])
    out = apply_tier(table, frame, "public")
    if out.empty:  # a table that is both column-blanked and wholly private
        return
    for column in columns:
        assert out[column].isna().all(), f"{table}.{column} survived"
    assert out["keep"].notna().all(), "a column nobody asked to drop was dropped"


def test_a_dfs_lineup_keeps_our_points_and_drops_draftkings_salary() -> None:
    # The projected points are the model's own output and are the whole reason
    # the lineup is interesting. The salary is DraftKings' data, and the repo
    # already quarantines it to private artifacts.
    lineups = pd.DataFrame([
        {"player_name": "C. Keith", "team": "DET", "salary": 3200.0,
         "points": 8.77, "league": "mlb"},
    ])
    out = apply_tier("dfs_lineup", lineups, "public")
    assert out["salary"].isna().all()
    assert out["points"].tolist() == [8.77]


def test_the_meta_row_names_the_tier_and_the_newest_stamp() -> None:
    tables = {
        "games": pd.DataFrame([{"stamp": "20260912T000116Z"}]),
        "board": pd.DataFrame([{"stamp": "20260912T000748Z"}]),
        "empty": pd.DataFrame(),
    }
    meta = site_meta_frame("public", tables)
    assert meta.iloc[0]["tier"] == "public"
    assert meta.iloc[0]["stamp"] == "20260912T000748Z"


def test_the_meta_row_survives_a_build_with_nothing_stamped() -> None:
    # A completely quiet slate still has to write a meta row, or the site's
    # only unconditional query returns nothing and the surface cannot even say
    # which tier it is.
    meta = site_meta_frame("private", {"games": pd.DataFrame()})
    assert len(meta) == 1
    assert meta.iloc[0]["stamp"] == ""


def test_an_emptied_table_still_carries_its_sentinel_downstream() -> None:
    # apply_tier hands an empty frame to the existing sentinel path, which is
    # what makes the public build render an ordinary empty state rather than a
    # missing-parquet build failure.
    assert apply_tier("bankroll", pd.DataFrame([{"league": "nfl"}]), "public").empty
    assert SENTINEL_LEAGUE == "__none__"
