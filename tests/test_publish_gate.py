"""The publish gate — a much higher bar than bettable, and quiet by design."""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.intel.publish import (
    DEFAULT_MAX_EDGE,
    adverse_drift,
    current_prices,
    gate_bet,
    gate_summary,
    publish_slate,
)
from velocity.intel.score import Conviction
from velocity.wagering.bet_log import Bet


def _bet(**over) -> Bet:
    base = {"game_id": "g1", "market": "spread", "side": "home", "book": "dk",
            "price": -110.0, "stake": 1.5, "p_model": 0.58, "point": -2.5,
            "p_fair": 0.52}
    base.update(over)
    return Bet(**base)  # type: ignore[arg-type]


def _conv(tier="A", score=0.8, **bet_over) -> Conviction:
    return Conviction(bet=_bet(**bet_over), edge_score=0.6, context_score=0.3,
                      score=score, tier=tier)


def test_a_clean_high_conviction_play_publishes() -> None:
    result = gate_bet(_conv())
    assert result.published and result.reason == ""
    assert result.edge == pytest.approx(0.06)
    assert result.tier == "A"


def test_edge_ceiling_rejects_the_adverse_selection_zone() -> None:
    # The finding this gate exists for: our biggest edges had the WORST CLV,
    # so an outsized edge is a data alarm, not an opportunity.
    huge = _conv(p_model=0.90, p_fair=0.52)
    result = gate_bet(huge)
    assert not result.published
    assert "ceiling" in result.reason and "adverse-selection" in result.reason
    assert result.edge > DEFAULT_MAX_EDGE


def test_edge_floor_and_missing_fair_price_reject() -> None:
    assert not gate_bet(_conv(p_model=0.53, p_fair=0.52)).published
    thin = gate_bet(_conv(p_model=0.53, p_fair=0.52))
    assert "below floor" in thin.reason
    no_fair = gate_bet(_conv(p_fair=None))
    assert not no_fair.published and "fair price" in no_fair.reason


def test_tier_rules_and_vetoes() -> None:
    assert not gate_bet(_conv(tier="B")).published
    assert "below publishable" in gate_bet(_conv(tier="B")).reason
    vetoed = gate_bet(_conv(tier="X"))
    assert not vetoed.published and "vetoed" in vetoed.reason
    # A veto outranks even a perfect edge.
    assert not gate_bet(_conv(tier="X", p_model=0.60)).published


def test_adverse_drift_math_and_direction() -> None:
    # We took -110 (52.4% implied); the market is now -140 (58.3%) on our
    # side, so the market moved TOWARD us — favorable, negative drift.
    assert adverse_drift(-110.0, -140.0) < 0
    # Market drifting to +120 (45.5%) means it moved AGAINST our side.
    assert adverse_drift(-110.0, 120.0) > 0
    # Unreadable prices never reject on their own.
    assert adverse_drift(-110.0, None) is None
    assert adverse_drift(-110.0, -50.0) is None  # no valid American price there


def test_a_play_the_market_ran_away_from_is_withdrawn() -> None:
    moved = gate_bet(_conv(), current_price=200.0)  # our side got much longer
    assert not moved.published and "moved" in moved.reason
    # A small drift is noise, not a message.
    assert gate_bet(_conv(), current_price=-108.0).published


def test_publish_slate_splits_and_audits_every_candidate() -> None:
    convictions = [
        _conv(score=0.9),                                    # publishes
        _conv(tier="B", game_id="g2"),                       # tier
        _conv(p_model=0.95, p_fair=0.52, game_id="g3"),      # ceiling
        _conv(tier="X", game_id="g4"),                       # veto
    ]
    published, audit = publish_slate(convictions)
    assert len(published) == 1
    assert len(audit) == 4  # every candidate is explained, not just the winner
    assert int(audit["published"].sum()) == 1
    assert set(audit["game_id"]) == {"g1", "g2", "g3", "g4"}
    assert audit[audit.game_id == "g3"].iloc[0]["reason"].startswith("edge")


def test_publish_slate_orders_by_conviction() -> None:
    published, _ = publish_slate([
        _conv(score=0.80, game_id="g1"),
        _conv(score=0.95, game_id="g2"),
    ])
    assert [c.bet.game_id for c in published] == ["g2", "g1"]


def test_conviction_floor_is_stricter_than_tier_A() -> None:
    # Tier A only needs a 0.65 composite, which the blend lets a bet reach on
    # EDGE ALONE. Publishing demands more than merely bettable.
    marginal = gate_bet(_conv(score=0.66))
    assert not marginal.published and "conviction" in marginal.reason
    assert gate_bet(_conv(score=0.80)).published


def test_context_must_corroborate_not_merely_fail_to_object() -> None:
    # A big edge that no signal supports is the shape of a line we have
    # mispriced — the adverse-selection profile the record flagged.
    uncorroborated = Conviction(bet=_bet(), edge_score=0.95,
                                context_score=0.0, score=0.80, tier="A")
    result = gate_bet(uncorroborated)
    assert not result.published and "corroborate" in result.reason
    # Actively negative context is likewise refused.
    against = Conviction(bet=_bet(), edge_score=0.95, context_score=-0.4,
                         score=0.80, tier="A")
    assert not gate_bet(against).published


def test_max_plays_caps_a_freak_board_but_is_not_a_quota() -> None:
    many = [_conv(score=0.80 + i * 0.01, game_id=f"g{i}") for i in range(12)]
    published, audit = publish_slate(many, max_plays=5)
    assert len(published) == 5
    # The cap keeps the HIGHEST conviction, and says so in the audit.
    assert [c.bet.game_id for c in published] == ["g11", "g10", "g9", "g8", "g7"]
    capped = audit[audit["reason"].str.contains("outside the top", na=False)]
    assert len(capped) == 7
    assert not capped["published"].any()
    # A quiet board still returns nothing — the cap is a ceiling, not a floor.
    assert publish_slate([_conv(tier="C")], max_plays=5)[0] == []


def test_audit_banks_conviction_and_context_for_calibration() -> None:
    _published, audit = publish_slate([_conv(score=0.83)])
    row = audit.iloc[0]
    assert row["conviction"] == pytest.approx(0.83)
    assert row["context"] == pytest.approx(0.3)


def test_no_picks_is_a_pick() -> None:
    # A board where nothing qualifies returns an empty set and SAYS WHY.
    published, audit = publish_slate([_conv(tier="B"), _conv(tier="C")])
    assert published == []
    summary = gate_summary(audit)
    assert "no plays cleared" in summary and "tier" in summary
    assert gate_summary(pd.DataFrame()) == "no bets on the board"


def test_current_prices_takes_the_newest_quote() -> None:
    lines = pd.DataFrame([
        {"game_id": "g1", "market": "spread", "side": "home", "price": -110.0,
         "timestamp": pd.Timestamp("2026-08-24 10:00")},
        {"game_id": "g1", "market": "spread", "side": "home", "price": -125.0,
         "timestamp": pd.Timestamp("2026-08-24 17:00")},
    ])
    assert current_prices(lines)[("g1", "spread", "home")] == -125.0
    assert current_prices(None) == {}
    assert current_prices(pd.DataFrame()) == {}
