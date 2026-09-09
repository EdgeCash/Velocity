"""De-vig pairs each ladder rung against its own opposite side, not a neighbour.

`build_slate` buckets prices to de-vig a side against its opposite. Keying that
bucket on (market, book, timestamp) alone is safe only while a feed carries one
number per market — true of the sportsbook board, false of an exchange, where
one snapshot holds a whole ladder. Without the contract in the key, rungs
overwrite each other and a rung's fair probability is computed from a different
rung's prices, inventing enormous phantom edges (docs/BUILD_EXCHANGES.md D2/E5).
"""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.features.team import fit_ratings
from velocity.models.game_nfl import NFLGameModel, NFLModelConfig
from velocity.models.simulate import SimConfig
from velocity.util.seed import make_rng
from velocity.wagering.devig import devig
from velocity.wagering.slate import SlateConfig, build_slate, contract_key
from velocity.wagering.staking import StakingConfig

GAME_ID = "2023_01_KC_DET"
STAMP = pd.Timestamp("2023-09-07 18:00:00")


@pytest.fixture
def projection(plays: pd.DataFrame):
    model = NFLGameModel(fit_ratings(plays), NFLModelConfig(sim=SimConfig(n_sims=40_000)))
    return model.project("DET", "KC", rng=make_rng())


def _rung(market: str, side: str, price: int, point: float | None) -> dict:
    tag = "" if point is None else f"{point:g}"
    return {
        "line_id": f"{GAME_ID}|{market}|{side}|exchange|{tag}",
        "game_id": GAME_ID,
        "book": "exchange",
        "market": market,
        "side": side,
        "price": price,
        "point": point,
        "timestamp": STAMP,
        "is_closing": False,
    }


def test_contract_key_pairs_sides_and_separates_both_teams_ladders() -> None:
    # One spread contract: the two sides carry mirrored points, so both
    # normalize to the home side's number.
    assert contract_key("spread", "home", -7.5) == contract_key("spread", "away", 7.5) == -7.5
    # The opposite contract (the away team laying the same absolute number)
    # must stay distinct — this is why abs(point) is not enough.
    assert contract_key("spread", "away", -7.5) == contract_key("spread", "home", 7.5) == 7.5
    # Totals already share a point across sides; moneyline has none.
    assert contract_key("total", "over", 48.5) == contract_key("total", "under", 48.5) == 48.5
    assert contract_key("moneyline", "home", None) is None


def test_each_total_rung_devigs_against_its_own_opposite(projection, games) -> None:
    # A three-rung total ladder in ONE snapshot, each rung priced very
    # differently — a low number where the over is a heavy favourite, a
    # pick-em, and a high number where the over is a longshot. Every rung's
    # fair probability must come from its own two prices. Pre-fix they shared
    # one bucket, so a rung could be devigged against a neighbour's prices.
    prices = {38.5: (-400, 300), 48.5: (-110, -110), 68.5: (400, -500)}
    ladder = pd.DataFrame(
        [
            _rung("total", side, price, point)
            for point, (over, under) in prices.items()
            for side, price in (("over", over), ("under", under))
        ]
    )
    log = build_slate({GAME_ID: projection}, ladder, games, SlateConfig(exclude_closing=False))
    assert log.bets, "expected the model to bet at least one rung"
    for bet in log.bets:
        over, under = prices[bet.point]
        ordered = [over, under] if bet.side == "over" else [under, over]
        assert bet.p_fair == pytest.approx(devig(ordered)[0], abs=1e-9)


def test_equal_absolute_strikes_are_separate_contracts(projection, games) -> None:
    # Both teams' ladders at the same |strike|: home −7.5 (with away +7.5) and
    # away −7.5 (with home +7.5). Keying on abs(point) would fuse these four
    # rows into one bucket and cross-pair them.
    board = pd.DataFrame(
        [
            _rung("spread", "home", -110, -7.5),
            _rung("spread", "away", -110, 7.5),
            _rung("spread", "away", 150, -7.5),
            _rung("spread", "home", -180, 7.5),
        ]
    )
    log = build_slate({GAME_ID: projection}, board, games, SlateConfig(exclude_closing=False))
    for bet in log.bets:
        if bet.point in (-7.5, 7.5) and bet.price == -110:
            # The symmetric contract: fair is 0.5 a side.
            assert bet.p_fair == pytest.approx(0.5, abs=1e-9)
        else:
            # The lopsided contract devigs to its own pair, never 0.5.
            assert bet.p_fair != pytest.approx(0.5, abs=1e-3)


def test_sportsbook_single_number_boards_are_unchanged(projection, market, games) -> None:
    # Regression: the fixture board carries one number per market, so adding
    # the contract to the bucket key must not change any existing outcome.
    log = build_slate({GAME_ID: projection}, market, games, SlateConfig())
    totals = [b for b in log.bets if b.market == "total"]
    assert len(totals) == 1
    assert (totals[0].side, totals[0].point, totals[0].price) == ("over", 48.5, -110)


def test_exchange_fees_reach_the_slate(projection, games) -> None:
    """The same board costs more on an exchange than at a book (D3).

    Identical prices, identical model — only the book name differs, which is
    how a row's fee schedule is identified. The exchange bet must be smaller,
    because its taker fee is charged against the payout before Kelly sizes it.
    """
    def board(book: str) -> pd.DataFrame:
        rows = [_rung("total", "over", -110, 48.5), _rung("total", "under", -110, 48.5)]
        for row in rows:
            row["book"] = book
            row["line_id"] = row["line_id"].replace("exchange", book)
        return pd.DataFrame(rows)

    # Lift the per-bet and per-game caps so Kelly, not a cap, sets the stake —
    # otherwise this fixture's large edge pins both sides to the same ceiling
    # and the fee's effect is invisible.
    staking = StakingConfig(max_bet_fraction=1.0)
    config = SlateConfig(exclude_closing=False, staking=staking, group_cap_fraction=1.0)
    book_log = build_slate({GAME_ID: projection}, board("bookA"), games, config)
    exch_log = build_slate({GAME_ID: projection}, board("kalshi"), games, config)
    assert book_log.bets and exch_log.bets
    book_bet, exch_bet = book_log.bets[0], exch_log.bets[0]
    assert (book_bet.market, book_bet.side) == (exch_bet.market, exch_bet.side)
    assert exch_bet.stake < book_bet.stake

    # Turning the fee off reproduces the sportsbook stake exactly — the switch
    # is the only difference, so a maker fill can be modelled later.
    free = build_slate(
        {GAME_ID: projection}, board("kalshi"), games,
        SlateConfig(
            exclude_closing=False,
            staking=staking,
            group_cap_fraction=1.0,
            charge_exchange_fees=False,
        ),
    )
    assert free.bets[0].stake == pytest.approx(book_bet.stake)
