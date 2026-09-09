"""The bankroll ledger — exact-value settlement, idempotence, and the views.

The tests docs/WAGERING.md W1 names: a recommend → place → settle round trip
moves the bankroll by exactly ``stake·b`` / ``−stake`` / 0; re-settlement is
a no-op; pending games leave the bankroll alone; the peak tracks through a
win–loss–win; an empty ledger seeds cleanly.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from velocity.wagering.bet_log import settle_profit
from velocity.wagering.ledger import (
    PLACED,
    RECOMMENDED,
    SETTLED,
    Ledger,
    bet_id,
    merge_ledgers,
    results_from_graded,
)

T0 = pd.Timestamp("2026-09-10 14:00")


def _card() -> pd.DataFrame:
    return pd.DataFrame([
        {"game_id": "g1", "market": "total", "side": "under", "point": 44.5,
         "book": "draftkings", "price": -110.0, "stake": 2.0, "p_model": 0.56,
         "p_fair": 0.51, "kind": "game", "note": None},
        {"game_id": "g2", "market": "spread", "side": "home", "point": -3.0,
         "book": "fanduel", "price": 120.0, "stake": 1.0, "p_model": 0.50,
         "p_fair": 0.45, "kind": "game", "note": None},
        {"game_id": "g1", "market": "receptions", "side": "over", "point": 5.5,
         "book": "draftkings", "price": -115.0, "stake": 0.5, "p_model": 0.58,
         "p_fair": 0.52, "kind": "prop", "player": "A. Brown", "note": None},
        # Paper: recommended at zero, with the reason. Never open.
        {"game_id": "g3", "market": "team_total_home", "side": "over", "point": 24.5,
         "book": "draftkings", "price": 100.0, "stake": 0.0, "p_model": 0.60,
         "p_fair": 0.50, "kind": "game", "note": "paper: team totals"},
    ])


def _seeded(tmp_path: Path) -> Ledger:
    ledger = Ledger.load(tmp_path / "ledger.parquet")
    assert not ledger.seeded
    assert ledger.seed(100.0, at=T0) is True
    assert ledger.recommend(_card(), league="nfl", stamp="20260910T140000Z", at=T0) == 4
    return ledger


def test_empty_ledger_seeds_cleanly_and_seeds_only_once(tmp_path: Path) -> None:
    ledger = Ledger.load(tmp_path / "ledger.parquet")
    assert ledger.current_bankroll() == 0.0 and ledger.peak_bankroll() == 0.0
    assert ledger.open_exposure() == 0.0 and ledger.drawdown() == 0.0
    assert ledger.seed(250.0, at=T0)
    assert not ledger.seed(999.0, at=T0)  # a second seed is refused, not stacked
    assert ledger.seed_amount() == 250.0
    assert ledger.current_bankroll() == 250.0 == ledger.peak_bankroll()
    with pytest.raises(ValueError):
        Ledger().seed(0.0, at=T0)


def test_recommend_place_settle_round_trip_moves_bankroll_exactly(tmp_path: Path) -> None:
    ledger = _seeded(tmp_path)
    under = bet_id("nfl", "g1", "total", "under")
    fav = bet_id("nfl", "g2", "spread", "home")
    prop = bet_id("nfl", "g1", "receptions", "over", "A. Brown")

    # Placed at the recommended terms unless told otherwise; the prop is
    # taken at a better price and a bigger stake than the card said.
    ledger.place(under, 2.0, at=T0 + pd.Timedelta(hours=1))
    ledger.place(fav, 1.0, at=T0 + pd.Timedelta(hours=1))
    ledger.place(prop, 1.0, price=-105.0, at=T0 + pd.Timedelta(hours=1))
    assert ledger.open_exposure() == pytest.approx(4.0)
    assert len(ledger.open_bets()) == 3
    assert ledger.current_bankroll() == 100.0  # placing moves nothing until settled

    results = pd.DataFrame([
        {"bet_id": under, "result": "win", "line_clv": 0.5},
        {"bet_id": fav, "result": "loss"},
        {"bet_id": prop, "result": "push"},
    ])
    settled = ledger.settle(results, at=T0 + pd.Timedelta(days=1))
    assert len(settled) == 3
    # +stake·b on the win (2 × 100/110), −stake on the loss, 0 on the push.
    expected = 100.0 + 2.0 * (100.0 / 110.0) - 1.0 + 0.0
    assert ledger.current_bankroll() == pytest.approx(expected)
    assert ledger.open_exposure() == 0.0
    by_bet = settled.set_index("bet_id")
    assert by_bet.loc[under, "profit"] == pytest.approx(2.0 * 100.0 / 110.0)
    assert by_bet.loc[under, "line_clv"] == 0.5
    assert by_bet.loc[prop, "price"] == -105.0 and by_bet.loc[prop, "stake"] == 1.0

    # The seed and the settlements survive a save/load round trip exactly.
    ledger.save()
    again = Ledger.load(ledger.path)
    assert again.current_bankroll() == pytest.approx(expected)
    assert len(again) == len(ledger)


def test_resettlement_is_idempotent_and_pending_leaves_bankroll_alone(tmp_path: Path) -> None:
    ledger = _seeded(tmp_path)
    under = bet_id("nfl", "g1", "total", "under")
    fav = bet_id("nfl", "g2", "spread", "home")
    ledger.place(under, 2.0, at=T0)
    ledger.place(fav, 1.0, at=T0)

    first = ledger.settle(pd.DataFrame([{"bet_id": under, "result": "win"},
                                        {"bet_id": fav, "result": "pending"}]), at=T0)
    assert len(first) == 1
    after = ledger.current_bankroll()
    assert after == pytest.approx(100.0 + 2.0 * 100.0 / 110.0)
    assert len(ledger.open_bets()) == 1  # the pending bet stays open
    assert ledger.open_exposure() == 1.0

    # Grading the same day again: the settled bet is skipped, nothing moves.
    again = ledger.settle(pd.DataFrame([{"bet_id": under, "result": "win"},
                                        {"bet_id": fav, "result": "pending"}]), at=T0)
    assert again.empty
    assert ledger.current_bankroll() == pytest.approx(after)
    assert int((ledger.frame["record_type"] == SETTLED).sum()) == 1

    # An unknown bet in the results is ignored; a result on nothing open is too.
    assert ledger.settle(pd.DataFrame([{"bet_id": "nfl|zz|total|over|", "result": "win"}]),
                         at=T0).empty


def test_peak_tracks_through_a_win_loss_win(tmp_path: Path) -> None:
    ledger = Ledger(path=tmp_path / "l.parquet")
    ledger.seed(100.0, at=T0)
    fields = {"league": "nfl", "game_id": "g", "market": "moneyline", "side": "home"}
    curve = []
    for i, (result, stake, price) in enumerate(
        [("win", 10.0, 100.0), ("loss", 20.0, 100.0), ("win", 5.0, 100.0)]
    ):
        when = T0 + pd.Timedelta(days=i)
        f = {**fields, "game_id": f"g{i}"}
        row = ledger.place(None, stake, price=price, at=when, fields=f)
        ledger.settle(pd.DataFrame([{"bet_id": row["bet_id"], "result": result}]), at=when)
        curve.append(ledger.current_bankroll())
    assert curve == pytest.approx([110.0, 90.0, 95.0])
    assert ledger.peak_bankroll() == pytest.approx(110.0)
    assert ledger.drawdown() == pytest.approx(1.0 - 95.0 / 110.0)
    state = ledger.state()
    assert state.peak == 110.0 and state.current == 95.0 and state.n_settled == 3
    assert "drawdown 13.6%" in state.describe()


def test_skip_closes_without_exposure_and_status_reads_right(tmp_path: Path) -> None:
    ledger = _seeded(tmp_path)
    under = bet_id("nfl", "g1", "total", "under")
    fav = bet_id("nfl", "g2", "spread", "home")
    ledger.place(under, 2.0, at=T0)
    ledger.skip(fav, at=T0)
    assert ledger.open_exposure() == 2.0
    todo = ledger.latest_recommendations("nfl").set_index("bet_id")["status"]
    assert todo[under] == "placed"
    assert todo[fav] == "skipped"
    assert todo[bet_id("nfl", "g1", "receptions", "over", "A. Brown")] == "open"
    assert todo[bet_id("nfl", "g3", "team_total_home", "over")] == "paper"
    # A skipped bet never settles: its result row is ignored.
    assert ledger.settle(pd.DataFrame([{"bet_id": fav, "result": "win"}]), at=T0).empty


def test_placing_needs_a_seed_a_price_and_a_known_bet(tmp_path: Path) -> None:
    ledger = Ledger(path=tmp_path / "l.parquet")
    with pytest.raises(ValueError, match="seed"):
        ledger.place(None, 1.0, price=-110.0, at=T0,
                     fields={"league": "nfl", "game_id": "g", "market": "total", "side": "over"})
    ledger.seed(100.0, at=T0)
    with pytest.raises(ValueError, match="unknown bet"):
        ledger.place("nfl|g|total|over|", 1.0, at=T0)
    with pytest.raises(ValueError, match="price"):
        ledger.place(None, 1.0, at=T0,
                     fields={"league": "nfl", "game_id": "g", "market": "total", "side": "over"})
    with pytest.raises(ValueError, match="adjust"):
        Ledger().adjust(5.0, at=T0)


def test_adjustments_are_new_records_that_move_the_curve(tmp_path: Path) -> None:
    ledger = Ledger(path=tmp_path / "l.parquet")
    ledger.seed(100.0, at=T0)
    ledger.adjust(50.0, at=T0 + pd.Timedelta(days=1), note="deposit")
    ledger.adjust(-30.0, at=T0 + pd.Timedelta(days=2), note="withdrawal")
    assert ledger.current_bankroll() == 120.0
    assert ledger.peak_bankroll() == 150.0
    curve = ledger.curve()
    assert curve["bankroll"].tolist() == [100.0, 150.0, 120.0]


def test_settle_from_finals_grades_open_game_bets_and_leaves_props(tmp_path: Path) -> None:
    ledger = _seeded(tmp_path)
    under = bet_id("nfl", "g1", "total", "under")
    fav = bet_id("nfl", "g2", "spread", "home")
    prop = bet_id("nfl", "g1", "receptions", "over", "A. Brown")
    ledger.place(under, 2.0, at=T0)
    ledger.place(fav, 1.0, at=T0)
    ledger.place(prop, 0.5, at=T0)
    finals = pd.DataFrame([
        {"game_id": "g1", "home_score": 20, "away_score": 17},   # 37 < 44.5 → under wins
        {"game_id": "g2", "home_score": 21, "away_score": 20},   # home −3 loses
        {"game_id": "g9", "home_score": None, "away_score": None},
    ])
    settled = ledger.settle_from_finals(finals, at=T0 + pd.Timedelta(days=1), league="nfl")
    assert set(settled["bet_id"]) == {under, fav}
    assert set(settled["result"]) == {"win", "loss"}
    assert ledger.current_bankroll() == pytest.approx(100.0 + 2.0 * 100.0 / 110.0 - 1.0)
    # The prop has no score to grade against: still open.
    assert ledger.open_bets()["bet_id"].tolist() == [prop]
    # Another league's finals never touch this ledger's NFL bets.
    assert ledger.settle_from_finals(finals, at=T0, league="ncaaf").empty


def test_two_placements_of_one_bet_settle_together(tmp_path: Path) -> None:
    ledger = _seeded(tmp_path)
    under = bet_id("nfl", "g1", "total", "under")
    ledger.place(under, 1.0, price=-110.0, at=T0)
    ledger.place(under, 1.0, price=-100.0, book="fanduel", at=T0 + pd.Timedelta(minutes=5))
    assert len(ledger.open_bets()) == 1
    assert ledger.open_exposure() == 2.0
    settled = ledger.settle(pd.DataFrame([{"bet_id": under, "result": "win"}]), at=T0)
    assert len(settled) == 1
    assert settled.iloc[0]["stake"] == 2.0
    assert settled.iloc[0]["profit"] == pytest.approx(100.0 / 110.0 + 1.0)
    assert settled.iloc[0]["price"] == pytest.approx(-105.0)  # stake-weighted


def test_exchange_tie_settles_at_fifty_cents() -> None:
    # A sportsbook tie is a push; an exchange contract bought at 60c on a
    # tied game settles at 50c and loses a sixth of the stake.
    assert settle_profit("tie", 6.0, -150.0, "draftkings") == 0.0
    assert settle_profit("tie", 6.0, -150.0, "kalshi") == pytest.approx(6.0 * (0.5 - 0.6) / 0.6)
    assert settle_profit("win", 1.0, 150.0) == 1.5
    assert settle_profit("loss", 1.0, 150.0) == -1.0
    with pytest.raises(ValueError):
        settle_profit("pending", 1.0, 100.0)


def test_pnl_views_and_results_from_graded(tmp_path: Path) -> None:
    ledger = _seeded(tmp_path)
    under = bet_id("nfl", "g1", "total", "under")
    fav = bet_id("nfl", "g2", "spread", "home")
    ledger.place(under, 2.0, at=T0)
    ledger.place(fav, 1.0, at=T0)
    games = pd.DataFrame([
        {"game_id": "g1", "market": "total", "side": "under", "result": "win",
         "closing_point": 45.0, "line_clv": 0.5},
        {"game_id": "g2", "market": "spread", "side": "home", "result": "loss"},
    ])
    props = pd.DataFrame([
        {"game_id": "g1", "player": "A. Brown", "market": "receptions", "side": "over",
         "result": "pending"},
    ])
    results = results_from_graded("nfl", games, props)
    assert results["bet_id"].tolist() == [
        under, fav, bet_id("nfl", "g1", "receptions", "over", "A. Brown")]
    ledger.settle(results, at=T0)
    by_league = ledger.pnl(("league",)).set_index("league")
    assert by_league.loc["nfl", "bets"] == 2
    assert by_league.loc["nfl", "wins"] == 1 and by_league.loc["nfl", "losses"] == 1
    assert by_league.loc["nfl", "staked"] == 3.0
    assert by_league.loc["nfl", "profit"] == pytest.approx(2.0 * 100.0 / 110.0 - 1.0)
    by_market = ledger.pnl(("league", "market")).set_index("market")
    assert by_market.loc["total", "roi"] == pytest.approx(100.0 / 110.0)
    assert by_market.loc["spread", "roi"] == -1.0
    assert results_from_graded("nfl", None, None).empty


def test_merge_is_a_union_by_record_identity(tmp_path: Path) -> None:
    ledger = _seeded(tmp_path)
    ledger.save()
    # The operator's local copy gains a placement; the workflow's copy gains
    # a settlement of something else. Pushing merges both, once each.
    local = Ledger.load(ledger.path)
    local.place(bet_id("nfl", "g1", "total", "under"), 2.0, at=T0)
    remote = Ledger.load(ledger.path)
    remote.recommend(_card().head(1), league="nfl", stamp="20260911T140000Z", at=T0)
    merged = merge_ledgers(local.frame, remote.frame, local.frame)
    assert len(merged) == len(ledger) + 2
    assert merged["record_id"].is_unique
    assert int((merged["record_type"] == PLACED).sum()) == 1
    assert int((merged["record_type"] == RECOMMENDED).sum()) == 5
    assert merge_ledgers(None, pd.DataFrame()).empty
