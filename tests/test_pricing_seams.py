"""The seams between model and stake (docs/SYSTEM_REVIEW.md §4).

Consensus de-vig, the drift rule with a real "then", and the market-class cap
— each one a place where the pricing chain measured the model against the
wrong yardstick or no yardstick at all.
"""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.wagering.devig import devig
from velocity.wagering.portfolio import BetCandidate, PortfolioConfig, size_portfolio
from velocity.wagering.slate import consensus_snapshots

STAMP = pd.Timestamp("2026-09-12 12:00:00")


def test_consensus_buckets_need_two_books_and_average_in_decimal_space() -> None:
    # Three books on the moneyline, one book alone on a spread contract.
    snapshots = {
        ("moneyline", "soft", STAMP, None): {"home": (-150.0, None), "away": (140.0, None)},
        ("moneyline", "sharp", STAMP, None): {"home": (-170.0, None), "away": (150.0, None)},
        ("moneyline", "mid", STAMP, None): {"home": (-160.0, None), "away": (145.0, None)},
        ("spread", "soft", STAMP, -3.0): {"home": (-105.0, -3.0), "away": (-115.0, 3.0)},
    }
    wide = consensus_snapshots(snapshots)
    assert set(wide) == {("moneyline", STAMP, None)}  # the lone spread has no consensus
    home, away = wide[("moneyline", STAMP, None)]["home"], wide[("moneyline", STAMP, None)]["away"]
    assert home[0] == pytest.approx(-160.0, abs=1.0)
    assert away[0] == pytest.approx(145.0, abs=1.0)
    # And the consensus anchor is the conservative one. The shop takes the
    # best price on our side — the dog at +150 — and that book's own pair
    # (+150 / −170) is by construction the one most generous to the dog:
    # it hands our side the LOWEST fair probability, hence the biggest edge.
    own = devig([150.0, -170.0])[0]
    consensus = devig([away[0], home[0]])[0]
    assert consensus > own


def test_a_market_class_cannot_take_more_than_its_share_of_the_slate() -> None:
    # Ten moneyline dogs in ten games, one total: the game grouping sees eleven
    # independent bets; the class cap sees one model assumption ten times.
    cands = [BetCandidate(f"ml{i}", 0.04, f"g{i}", market_class="moneyline") for i in range(10)]
    cands.append(BetCandidate("tot", 0.04, "g99", market_class="total"))
    stakes = size_portfolio(cands, 100.0, PortfolioConfig(max_class_fraction=0.5))
    cap = 0.25 * 100.0
    moneyline = sum(v for k, v in stakes.items() if k.startswith("ml"))
    assert moneyline == pytest.approx(0.5 * cap)
    # The other class keeps its full standalone stake (no aggregate scaling
    # needed once the class is capped: 12.5 + 4 < 25).
    assert stakes["tot"] == pytest.approx(4.0)
    # Unlabelled candidates are never class-capped; 1.0 disables the rule.
    plain = size_portfolio([BetCandidate(f"ml{i}", 0.04, f"g{i}") for i in range(10)], 100.0,
                           PortfolioConfig(max_class_fraction=0.5))
    assert sum(plain.values()) == pytest.approx(cap)  # only the aggregate cap binds
    off = size_portfolio(cands, 100.0, PortfolioConfig(max_class_fraction=1.0))
    assert sum(off.values()) == pytest.approx(cap)


def test_current_prices_are_a_consensus_not_the_last_row() -> None:
    from velocity.intel.publish import current_prices

    lines = pd.DataFrame({
        "game_id": ["g1"] * 3,
        "market": ["moneyline"] * 3,
        "side": ["home"] * 3,
        "book": ["a", "b", "c"],
        "price": [-150.0, -170.0, -160.0],
        "timestamp": [STAMP] * 3,
    })
    prices = current_prices(lines)
    assert prices[("g1", "moneyline", "home")] == pytest.approx(-160.0, abs=1.0)
    # An older snapshot in the same frame is ignored — only the newest counts.
    older = lines.assign(timestamp=STAMP - pd.Timedelta(hours=1), price=[-200.0] * 3)
    prices = current_prices(pd.concat([older, lines]))
    assert prices[("g1", "moneyline", "home")] == pytest.approx(-160.0, abs=1.0)


def test_the_drift_rule_needs_a_then_and_reads_it_against_now() -> None:
    from velocity.intel.publish import gate_bet
    from velocity.intel.score import Conviction
    from velocity.wagering.bet_log import Bet

    bet = Bet("g1", "moneyline", "home", "a", -150.0, 1.0, 0.65, p_fair=0.60)
    conviction = Conviction(bet=bet, edge_score=1.0, context_score=0.5, score=0.9, tier="A")
    # No reference: the rule stands down, the play passes on its merits.
    assert gate_bet(conviction, current_price=-150.0).published is True
    # The market was −150 an hour ago and is −120 now: our side got LESS
    # likely by the market's reckoning — adverse, withdrawn.
    result = gate_bet(conviction, current_price=-120.0, reference_price=-150.0)
    assert result.published is False and "against us" in result.reason
    # Moved toward us (−150 → −180): positive CLV, never a reason to withdraw.
    assert gate_bet(conviction, current_price=-180.0, reference_price=-150.0).published is True
