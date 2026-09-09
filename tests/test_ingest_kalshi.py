"""Kalshi ingest — binary exchange contracts flatten to canonical lines, offline.

Exercises the pure normalizers and ticker parsing against a frozen real payload
(``tests/fixtures/kalshi_nfl.json``, pulled 2026-09-09) plus inline edge cases.
The network ``KalshiClient`` is not touched here. Conventions under test are
docs/BUILD_EXCHANGES.md D1–D6: ask-only executable prices converted to integer
American, yes-ask-only winner emission, signed ladder points, half-integer
strikes, and drop-don't-guess everywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from velocity.ingest.kalshi import (
    extract_kalshi_events,
    normalize_kalshi_candles,
    normalize_kalshi_markets,
    normalize_kalshi_props,
    parse_market_ticker,
)
from velocity.ingest.odds import LiveOddsAdapter, OddsAdapter
from velocity.store.pit import closing_line
from velocity.store.schema import Lines, PropLines

REPO = Path(__file__).resolve().parents[1]
SNAPSHOT = json.loads((REPO / "tests" / "fixtures" / "kalshi_nfl.json").read_text())
PROPS = json.loads((REPO / "tests" / "fixtures" / "kalshi_nfl_props.json").read_text())
STAMP = "2026-09-09T00:15:00Z"


def _lines() -> pd.DataFrame:
    return normalize_kalshi_markets(SNAPSHOT, STAMP)


def test_normalize_validates_and_maps_all_series() -> None:
    lines = _lines()
    Lines.validate(lines)
    assert set(lines["market"]) == {"moneyline", "spread", "total", "team_total_away"}
    assert set(lines["book"]) == {"kalshi"}
    # 2 winners (yes-ask only) + 2 spread rungs x 2 sides + 2 total rungs x 2
    # sides + team totals 2 + 1 (SF43's no-ask is $1.0000 = no offer, dropped).
    assert len(lines) == 13


def test_winner_markets_emit_own_yes_ask_only() -> None:
    ml = _lines().query("market == 'moneyline'").set_index("side")
    # Two markets per event, one per team; no-asks must not double-emit sides.
    assert sorted(ml.index) == ["LAR", "NYG"]
    assert ml.loc["NYG", "price"] == 426  # yes-ask $0.19
    assert ml.loc["LAR", "price"] == -456  # yes-ask $0.82
    assert ml["point"].isna().all()
    assert set(ml["game_id"]) == {"KXNFLGAME-26SEP21NYGLAR"}


def test_spread_rungs_emit_both_sides_with_signed_points() -> None:
    spreads = _lines().query("market == 'spread'")
    rows = {(r.side, r.point): r.price for r in spreads.itertuples()}
    # "KC wins by over 7.5" = KC -7.5 at the yes-ask, DEN +7.5 at the no-ask.
    assert rows[("KC", -7.5)] == 223  # yes-ask $0.31
    assert rows[("DEN", 7.5)] == -245  # no-ask $0.71
    assert rows[("KC", -6.5)] == 178  # yes-ask $0.36
    assert rows[("DEN", 6.5)] == -194  # no-ask $0.66


def test_total_rungs_share_the_point_across_sides() -> None:
    totals = _lines().query("market == 'total'")
    rows = {(r.side, r.point): r.price for r in totals.itertuples()}
    assert rows[("Over", 63.5)] == 1150  # yes-ask $0.08
    assert rows[("Under", 63.5)] == -1329  # no-ask $0.93
    assert rows[("Over", 60.5)] == 900
    assert rows[("Under", 60.5)] == -1011


def test_team_total_resolves_side_from_ticker_blob() -> None:
    # SFLAR = SF @ LAR ({AWAY}{HOME}), so SF's total is team_total_away.
    tt = _lines().query("market == 'team_total_away'")
    rows = {(r.side, r.point): r.price for r in tt.itertuples()}
    assert rows[("Over", 7.5)] == -2400  # yes-ask $0.96
    assert rows[("Under", 7.5)] == 1329  # no-ask $0.07
    # SF43: yes-ask $0.03 kept; no-ask $1.0000 is no offer, dropped.
    assert rows[("Over", 42.5)] == 3233
    assert ("Under", 42.5) not in rows


def test_line_ids_unique_and_stable() -> None:
    first, second = _lines(), _lines()
    assert first["line_id"].is_unique
    assert list(first["line_id"]) == list(second["line_id"])
    assert (first["timestamp"] == pd.Timestamp("2026-09-09 00:15:00")).all()


def test_unknown_series_combo_and_integer_strikes_dropped() -> None:
    stray = {
        "markets": [
            # Combo/parlay market — series not in the map.
            {"ticker": "KXMVECROSSCATEGORY-26SEP12ABC-DEF", "yes_ask_dollars": "0.5000"},
            # Quarter market — no sim support, not mapped.
            {"ticker": "KXNFL4Q-26SEP14DENKC-KC", "yes_ask_dollars": "0.5000"},
            # Integer strike on a mapped ladder — push semantics undefined, drop.
            {
                "ticker": "KXNFLTOTAL-26SEP14DENKC-60",
                "floor_strike": 60.0,
                "yes_ask_dollars": "0.4000",
                "no_ask_dollars": "0.6200",
            },
            # Non-active market on a mapped series.
            {
                "ticker": "KXNFLGAME-26SEP14DENKC-KC",
                "status": "settled",
                "yes_ask_dollars": "0.9900",
            },
        ]
    }
    lines = normalize_kalshi_markets(stray, STAMP)
    Lines.validate(lines)
    assert lines.empty


def test_empty_payload_yields_valid_empty_frame() -> None:
    lines = normalize_kalshi_markets({"markets": []}, STAMP)
    Lines.validate(lines)
    assert lines.empty
    props = normalize_kalshi_props({"markets": []}, STAMP)
    PropLines.validate(props)
    assert props.empty


def test_ticker_parser() -> None:
    parsed = parse_market_ticker("KXNFLSPREAD-26SEP14DENKC-KC8")
    assert parsed is not None
    assert parsed.series == "KXNFLSPREAD"
    assert parsed.event_ticker == "KXNFLSPREAD-26SEP14DENKC"
    assert parsed.teams == "DENKC"
    assert parsed.suffix == "KC8"
    assert parsed.date == pd.Timestamp("2026-09-14")
    assert parse_market_ticker("not-a-kalshi-ticker") is None


def test_props_normalize_player_sides_and_points() -> None:
    props = normalize_kalshi_props(PROPS, STAMP)
    PropLines.validate(props)
    assert set(props["market"]) == {"pass_yards"}
    assert set(props["player"]) == {"Tua Tagovailoa"}
    rows = {(r.side, r.point): r.price for r in props.itertuples()}
    assert rows[("over", 299.5)] == 900  # yes-ask $0.10
    assert rows[("under", 299.5)] == -1567  # no-ask $0.94
    assert rows[("over", 274.5)] == 400
    assert rows[("under", 274.5)] == -669
    assert props["line_id"].is_unique


def test_extract_events_splits_blob_via_winner_suffixes() -> None:
    events = extract_kalshi_events(SNAPSHOT)
    assert len(events) == 1
    row = events.iloc[0]
    assert row["game_id"] == "KXNFLGAME-26SEP21NYGLAR"
    assert row["away_team"] == "NYG"
    assert row["home_team"] == "LAR"
    assert row["date"] == pd.Timestamp("2026-09-21")


def test_normalized_frame_satisfies_adapter_contract() -> None:
    lines = _lines()
    adapter = LiveOddsAdapter(fetch=lambda game_ids: lines)
    assert isinstance(adapter, OddsAdapter)
    Lines.validate(adapter.current_lines())


# --- Candles → Lines (the CLV archive path, frozen from a real settled game) --

CANDLE_FIXTURE = json.loads(
    (REPO / "tests" / "fixtures" / "kalshi_candles_settled.json").read_text()
)


def test_candles_normalize_to_a_time_series() -> None:
    lines = normalize_kalshi_candles(CANDLE_FIXTURE["market"], CANDLE_FIXTURE["candles"])
    Lines.validate(lines)
    # 6 candles; the final in-game candle's yes-ask is $1.0000 (certainty — no
    # executable offer) and drops. A winner market emits its yes side only.
    assert len(lines) == 5
    assert set(lines["market"]) == {"moneyline"}
    assert set(lines["side"]) == {"CHI"}
    assert lines["is_closing"].all()
    assert lines["line_id"].nunique() == 1  # one contract, many observations
    assert lines["timestamp"].nunique() == 5
    by_ts = lines.set_index("timestamp")["price"]
    assert by_ts[pd.Timestamp("2026-08-29 22:00:00")] == -113  # yes-ask $0.53
    assert by_ts[pd.Timestamp("2026-08-29 23:59:00")] == -809  # yes-ask $0.89


def test_candle_closes_feed_pit_closing_line() -> None:
    lines = normalize_kalshi_candles(CANDLE_FIXTURE["market"], CANDLE_FIXTURE["candles"])
    games = pd.DataFrame(
        {
            "game_id": ["KXNFLGAME-26AUG29CHITEN"],
            "kickoff": [pd.Timestamp("2026-08-30 00:00:00")],
        }
    )
    close = closing_line(lines, games)
    # The honest close is the last candle strictly before kickoff (23:59) —
    # the 00:00:00 candle stamps AT kickoff and is excluded.
    assert len(close) == 1
    assert close.loc[0, "timestamp"] == pd.Timestamp("2026-08-29 23:59:00")
    assert close.loc[0, "price"] == -809


def test_ladder_candles_emit_both_sides_with_derived_no_ask() -> None:
    market = {
        "ticker": "KXNFLSPREAD-26SEP14DENKC-KC7",
        "status": "settled",
        "floor_strike": 6.5,
    }
    candles = {
        "candlesticks": [
            {
                "end_period_ts": 1788040800,
                "yes_ask": {"close_dollars": "0.3600"},
                "yes_bid": {"close_dollars": "0.3400"},
            },
            {
                "end_period_ts": 1788040860,
                "yes_ask": {"close_dollars": "0.3700"},
                "yes_bid": {"close_dollars": "0.3500"},
            },
        ]
    }
    lines = normalize_kalshi_candles(market, candles)
    Lines.validate(lines)
    assert len(lines) == 4  # 2 candles x 2 sides
    rows = {(r.side, r.point, r.timestamp): r.price for r in lines.itertuples()}
    t0 = pd.Timestamp("2026-08-29 22:00:00")
    assert rows[("KC", -6.5, t0)] == 178  # yes-ask $0.36
    # NO ask reconstructed as 1 − yes-bid: $0.66 → −194.
    assert rows[("DEN", 6.5, t0)] == -194


def test_historical_candle_field_names_also_parse() -> None:
    # The /historical archive drops the ``_dollars`` suffix (probe 2026-09-09).
    market = {"ticker": "KXNFLGAME-25SEP29CINDEN-DEN", "floor_strike": None}
    candles = {
        "candlesticks": [
            {
                "end_period_ts": 1759190400,
                "yes_ask": {"close": "0.4700"},
                "yes_bid": {"close": "0.4500"},
            }
        ]
    }
    lines = normalize_kalshi_candles(market, candles)
    assert len(lines) == 1
    assert lines.loc[0, "price"] == 113  # yes-ask $0.47 → +112.8 → +113
