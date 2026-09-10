"""The season chain to carry forward is the one that reaches furthest.

Copies of the chain arrive from two places — recent artifacts and the durable
R2 copy — and the filename stamp says when a copy was written, not how far
its record reaches (docs/STRATEGY_REVIEW.md S1).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "grade_yesterday_chain",
    Path(__file__).resolve().parents[1] / "scripts" / "grade_yesterday.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def _chain(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"slate_date": pd.to_datetime(dates), "result": ["win"] * len(dates)})


def test_the_chain_reaching_the_latest_day_wins_regardless_of_copy_age() -> None:
    stale_artifact = _chain(["2026-09-13", "2026-09-14"])
    durable = _chain(["2026-09-13", "2026-09-14", "2026-09-21", "2026-09-28"])
    assert _MOD.newest_chain([stale_artifact, durable]) is durable
    assert _MOD.newest_chain([durable, stale_artifact]) is durable


def test_ties_go_to_the_longer_chain_and_empties_are_ignored() -> None:
    short = _chain(["2026-09-14"])
    long = _chain(["2026-09-07", "2026-09-14"])
    assert _MOD.newest_chain([short, long, pd.DataFrame(), None]) is long
    assert _MOD.newest_chain([]) is None


def test_dateless_copies_fall_back_to_the_newest_written() -> None:
    # A chain from before dates rode along says nothing about its reach; the
    # stamp order (oldest first) is all there is, and the season is still
    # better carried than dropped.
    older = pd.DataFrame({"result": ["win"]})
    newer = pd.DataFrame({"result": ["win", "loss"]})
    assert _MOD.newest_chain([older, newer]) is newer
    # A dated chain beats any dateless copy.
    dated = _chain(["2026-09-07"])
    assert _MOD.newest_chain([dated, newer]) is dated


def _slate() -> pd.DataFrame:
    return pd.DataFrame([
        {"game_id": "g1", "market": "total", "side": "under", "point": 44.5,
         "price": -110.0, "stake": 1.2, "p_model": 0.56},
        {"game_id": "g1", "market": "spread", "side": "home", "point": -3.0,
         "price": -108.0, "stake": 0.8, "p_model": 0.55},
        # Paper: priced at stake 0 (a ceiling refusal); never on the card.
        {"game_id": "g2", "market": "team_total_home", "side": "over", "point": 24.5,
         "price": 100.0, "stake": 0.0, "p_model": 0.60},
    ])


def _card() -> pd.DataFrame:
    return pd.DataFrame([
        {"game_id": "g1", "market": "total", "side": "under", "kind": "game",
         "stake": 0.9, "stake_solo": 1.2},
        {"game_id": "g1", "market": "spread", "side": "home", "kind": "game",
         "stake": 0.5, "stake_solo": 0.8},
        {"game_id": "g1", "market": "receptions", "side": "over", "kind": "prop",
         "player": "A. Brown", "stake": 0.4, "stake_solo": 0.6},
    ])


def test_sized_stakes_ride_into_the_record() -> None:
    slate = _MOD.attach_sized_stakes(_slate(), _card(), "game")
    assert slate["stake_sized"].tolist() == [0.9, 0.5, 0.0]
    graded = slate.assign(result=["win", "loss", "win"], profit=[1.091, -0.8, 0.0])
    graded = _MOD.sized_profit(graded)
    # Profit is linear in the stake: the same price, the same outcome.
    assert graded["profit_sized"].tolist() == pytest.approx([1.091 * 0.9 / 1.2, -0.5, 0.0])


def test_no_card_means_unknown_not_zero() -> None:
    slate = _MOD.attach_sized_stakes(_slate(), None, "game")
    assert slate["stake_sized"].isna().all()
    graded = _MOD.sized_profit(slate.assign(result="win", profit=1.0))
    assert graded["profit_sized"].isna().all()


def test_props_join_on_the_player_too() -> None:
    props = pd.DataFrame([
        {"game_id": "g1", "market": "receptions", "side": "over", "player": "A. Brown",
         "point": 5.5, "price": -115.0, "stake": 0.6, "p_model": 0.58},
        {"game_id": "g1", "market": "receptions", "side": "over", "player": "B. Smith",
         "point": 3.5, "price": -110.0, "stake": 0.3, "p_model": 0.55},
    ])
    sized = _MOD.attach_sized_stakes(props, _card(), "prop")
    assert sized["stake_sized"].tolist() == [0.4, 0.0]
    # A grader that rebuilt its rows from tickets gets the column back.
    rebuilt = props.drop(columns=["stake"]).assign(result="win", profit=[0.52, 0.27])
    carried = _MOD._carry_sized(rebuilt, sized)
    assert carried["stake_sized"].tolist() == [0.4, 0.0]


def test_prop_closes_are_the_last_pre_kickoff_quote_reduced_across_books(tmp_path: Path) -> None:
    kickoff = pd.Timestamp("2026-09-14 17:00")
    games = pd.DataFrame([{"game_id": "g1", "home_team": "KC", "away_team": "BUF",
                           "kickoff": kickoff}])
    rows = []
    for stamp, dk_point, fd_point in (("20260913T120000Z", 5.5, 5.5),
                                       ("20260914T150000Z", 6.5, 5.5),
                                       ("20260914T180000Z", 9.5, 9.5)):  # post-kickoff
        for book, point in (("dk", dk_point), ("fd", fd_point)):
            for side, price in (("over", -115), ("under", -105)):
                rows.append({"line_id": f"{stamp}{book}{side}", "game_id": "g1", "book": book,
                             "market": "receptions", "player": "Travis Kelce", "side": side,
                             "price": price, "point": point,
                             "timestamp": pd.Timestamp(stamp), "is_closing": False,
                             "league": "nfl"})
        pd.DataFrame(rows[-4:]).to_parquet(tmp_path / f"props_nfl_{stamp}.parquet", index=False)
    props = pd.DataFrame([
        {"game_id": "g1", "player": "Travis  Kelce", "market": "receptions", "side": "over",
         "point": 5.5, "price": -110.0, "stake": 0.5, "p_model": 0.58},
        {"game_id": "g1", "player": "Unknown Guy", "market": "receptions", "side": "over",
         "point": 3.5, "price": -110.0, "stake": 0.3, "p_model": 0.55},
    ])
    consensus = _MOD.prop_closing_for_slate(tmp_path, props, games, "nfl")
    assert consensus is not None
    over = consensus[(consensus["market"] == "receptions") & (consensus["side"] == "over")]
    # The 18:00 snapshot is after kickoff and drops; the close is the median
    # of DK's 6.5 and FD's 5.5 at 15:00.
    assert over["closing_point"].iloc[0] == pytest.approx(6.0)
    assert over["closing_price"].iloc[0] == pytest.approx(-115.0)
    attached = _MOD.attach_prop_closes(props, consensus)
    assert attached["closing_point"].tolist()[0] == pytest.approx(6.0)  # name normalized
    assert pd.isna(attached["closing_point"].iloc[1])  # no close, no crash

    from velocity.backtest.props_football import attach_prop_clv

    graded = attach_prop_clv(attached.assign(result=["win", "loss"], profit=[0.91, -1.0]))
    # Over 5.5 against a 6.0 close: half a reception of line CLV in our favour.
    assert graded["line_clv"].iloc[0] == pytest.approx(0.5)
    assert graded["price_clv"].iloc[0] == pytest.approx(
        (1 + 100 / 110) / (1 + 100 / 115) - 1.0)
    assert pd.isna(graded["line_clv"].iloc[1]) and pd.isna(graded["price_clv"].iloc[1])
    assert _MOD.prop_closing_for_slate(tmp_path / "empty", props, games, "nfl") is None


def test_the_close_prefers_a_sharp_book_and_says_so(tmp_path: Path) -> None:
    kickoff = pd.Timestamp("2026-09-14 17:00")
    games = pd.DataFrame([{"game_id": "g1", "home_team": "KC", "away_team": "BUF",
                           "kickoff": kickoff},
                          {"game_id": "g2", "home_team": "DET", "away_team": "CHI",
                           "kickoff": kickoff}])
    rows = []
    stamp = pd.Timestamp("2026-09-14 15:00")
    # g1 has a Pinnacle quote beside two soft books; g2 has soft books only.
    for gid, books in (("g1", (("dk", 46.5, -110), ("fd", 47.0, -110), ("pinnacle", 47.5, -105))),
                       ("g2", (("dk", 44.5, -110), ("fd", 45.5, -112)))):
        for book, point, price in books:
            for side in ("over", "under"):
                rows.append({"line_id": f"{gid}{book}{side}", "game_id": gid, "book": book,
                             "market": "total", "side": side, "price": price,
                             "point": point, "timestamp": stamp, "is_closing": False,
                             "league": "nfl"})
    pd.DataFrame(rows).to_parquet(tmp_path / "odds_lines_20260914T150000Z.parquet", index=False)
    slate = pd.DataFrame([
        {"game_id": "g1", "market": "total", "side": "over", "point": 46.0, "price": -110.0,
         "stake": 1.0, "p_model": 0.56},
        {"game_id": "g2", "market": "total", "side": "under", "point": 45.0, "price": -110.0,
         "stake": 1.0, "p_model": 0.55},
    ])
    closing = _MOD.closing_for_slate(tmp_path, slate, games, "nfl")
    assert closing is not None
    by = closing.set_index(["game_id", "side"])
    assert by.loc[("g1", "over"), "close_source"] == "sharp"
    assert by.loc[("g1", "over"), "point"] == 47.5 and by.loc[("g1", "over"), "price"] == -105
    assert by.loc[("g2", "under"), "close_source"] == "consensus"
    assert by.loc[("g2", "under"), "point"] == 45.0  # the median of 44.5 / 45.5
    graded = _MOD.attach_close_source(slate.assign(result="win", profit=0.9), closing)
    assert graded["close_source"].tolist() == ["sharp", "consensus"]
    bare = _MOD.attach_close_source(slate.assign(result="win", profit=0.9), None)
    assert bare["close_source"].isna().all()


def test_settle_ledger_settles_open_bets_once(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from velocity.wagering.ledger import Ledger

    path = tmp_path / "ledger.parquet"
    ledger = Ledger(path=path)
    at = pd.Timestamp("2026-09-13 14:00")
    ledger.seed(100.0, at=at)
    ledger.recommend(pd.DataFrame([
        {"game_id": "g1", "market": "total", "side": "under", "point": 44.5,
         "book": "dk", "price": -110.0, "stake": 2.0, "p_model": 0.56, "kind": "game"},
        {"game_id": "g2", "market": "spread", "side": "home", "point": -3.0,
         "book": "fd", "price": 100.0, "stake": 1.0, "p_model": 0.55, "kind": "game"},
        {"game_id": "g1", "market": "receptions", "side": "over", "point": 5.5,
         "book": "dk", "price": -115.0, "stake": 0.5, "p_model": 0.58, "kind": "prop",
         "player": "A. Brown"},
    ]), league="nfl", stamp="20260913T140000Z", at=at)
    for bid, stake in (("nfl|g1|total|under|", 2.0), ("nfl|g2|spread|home|", 1.0),
                       ("nfl|g1|receptions|over|A. Brown", 0.5)):
        ledger.place(bid, stake, at=at)
    ledger.save()

    # The graded slate carries g1 (win, with a close); g2 dropped off the
    # graded card but has a final; the prop is pending.
    games = pd.DataFrame([{"game_id": "g1", "market": "total", "side": "under",
                           "result": "win", "line_clv": 0.5, "closing_point": 45.0}])
    props = pd.DataFrame([{"game_id": "g1", "player": "A. Brown", "market": "receptions",
                           "side": "over", "result": "pending"}])
    finals = pd.DataFrame([{"game_id": "g1", "home_score": 20, "away_score": 17},
                           {"game_id": "g2", "home_score": 21, "away_score": 20}])
    now = datetime(2026, 9, 15, 12, tzinfo=UTC)
    _MOD.settle_ledger(path, "nfl", games, props, finals, now, stamp="20260913T140000Z")
    after = Ledger.load(path)
    assert after.current_bankroll() == pytest.approx(100.0 + 2.0 * 100 / 110 - 1.0)
    assert after.open_bets()["bet_id"].tolist() == ["nfl|g1|receptions|over|A. Brown"]
    settled = after.frame[after.frame["record_type"] == "settled"].set_index("bet_id")
    assert settled.loc["nfl|g1|total|under|", "line_clv"] == 0.5
    assert settled.loc["nfl|g2|spread|home|", "result"] == "loss"

    # The morning re-run: nothing settles twice, the bankroll holds.
    _MOD.settle_ledger(path, "nfl", games, props, finals, now, stamp="20260913T140000Z")
    again = Ledger.load(path)
    assert len(again) == len(after)
    assert again.current_bankroll() == pytest.approx(after.current_bankroll())
    # No seed → nothing to settle, and no crash.
    _MOD.settle_ledger(tmp_path / "none.parquet", "nfl", games, props, finals, now)
    assert not (tmp_path / "none.parquet").exists()


def test_a_zero_byte_chain_copy_is_skipped_not_fatal(tmp_path: Path) -> None:
    """A failed R2 fetch leaves a zero-byte parquet where the chain should be.

    Reading it raised, and the raise landed between writing the day's record
    and writing the season chain — so the chain was never written, never
    parked, and never found on the next run. The record could not bootstrap
    out of it, and the site's whole Performance page read empty.
    """
    (tmp_path / "cumulative_record_nfl_00000000T000000Z.parquet").write_bytes(b"")
    good = _chain(["2026-09-07", "2026-09-14"])
    good.to_parquet(tmp_path / "cumulative_record_nfl_20260914T120000Z.parquet", index=False)
    carried = _MOD._newest_cumulative(tmp_path, "nfl")
    assert carried is not None and len(carried) == 2

    # Corrupt (non-empty, not parquet) copies are skipped the same way.
    (tmp_path / "cumulative_record_nfl_20260915T120000Z.parquet").write_bytes(b"not parquet")
    again = _MOD._newest_cumulative(tmp_path, "nfl")
    assert again is not None and len(again) == 2

    # Nothing readable at all is None, not an exception.
    (tmp_path / "cumulative_record_nfl_20260914T120000Z.parquet").unlink()
    assert _MOD._newest_cumulative(tmp_path, "nfl") is None


def test_the_chain_bootstraps_from_banked_daily_records(tmp_path: Path) -> None:
    """With no chain anywhere, the daily records in the artifacts rebuild it."""
    for i, stamp in enumerate(("20260907T120000Z", "20260908T120000Z")):
        pd.DataFrame([
            {"section": "games", "play": f"A@B {i}", "market": "total", "side": "over",
             "point": 44.5, "price": -110.0, "stake": 1.0, "result": "win", "profit": 0.91,
             "slate_date": pd.Timestamp(f"2026-09-0{6 + i}")},
            {"section": "games", "play": "later", "market": "spread", "side": "home",
             "point": -3.0, "price": -110.0, "stake": 1.0, "result": "pending",
             "profit": None, "slate_date": pd.Timestamp(f"2026-09-0{6 + i}")},
        ]).to_parquet(tmp_path / f"record_nfl_{stamp}.parquet", index=False)

    chain = _MOD._bootstrap_chain(tmp_path, "nfl")
    assert chain is not None
    # Settled rows only: a pending bet is never revisited, so it would be
    # permanent noise on the chain.
    assert chain["result"].tolist() == ["win", "win"]
    assert len(chain) == 2

    # Replaying the same day is idempotent (the accumulate dedup key).
    again = _MOD._bootstrap_chain(tmp_path, "nfl")
    assert len(again) == 2
    # Another league's records are not this league's chain.
    assert _MOD._bootstrap_chain(tmp_path, "ncaaf") is None
