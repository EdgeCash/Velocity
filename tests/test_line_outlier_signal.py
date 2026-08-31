"""Consensus-outlier demotion — a too-friendly number is a red flag, not a gift.

Pins the demote-only contract: no score is ever positive, demotion starts
only beyond the market's normal cross-book dispersion and saturates at twice
it, the favorable direction flips with the side, and missing consensus (or
an unmapped market) abstains. Consensus lines are free-tier fields, so the
signal must work on rows without any premium projection.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.intel.context import GameContext, TeamContext
from velocity.intel.score import DEFAULT_SIGNAL_WEIGHTS
from velocity.intel.signals import PropLineOutlierSignal
from velocity.wagering.bet_log import Bet

CTX = GameContext(
    game_id="g1", season=2026,
    away=TeamContext(team="BUF"), home=TeamContext(team="KC"),
)

# Free-tier rows on purpose: consensus present, premium projection absent.
SIGNAL = PropLineOutlierSignal.from_frame(pd.DataFrame([
    {"market_slug": "passing-yards", "player_name": "Josh Allen",
     "consensus_over_line": 267.5, "consensus_under_line": 267.5,
     "recommended_side": None, "bet_rating": np.nan},
    {"market_slug": "receptions", "player_name": "Travis Kelce",
     "consensus_over_line": np.nan, "consensus_under_line": 6.5,
     "recommended_side": None, "bet_rating": np.nan},
]))


def _bet(player: str, market: str, side: str, point: float) -> Bet:
    return Bet(game_id="g1", market=market, side=side, book="b", price=-110.0,
               stake=1.0, p_model=0.55, point=point, player=player)


def test_friendly_outlier_is_demoted_and_saturates() -> None:
    # Over at 245 vs 267.5 consensus: 22.5 friendly, threshold 15 → −0.5.
    halfway = SIGNAL.evaluate(_bet("Josh Allen", "pass_yards", "over", 245.0), CTX)
    assert halfway is not None and halfway.score == pytest.approx(-0.5)
    assert "stale-line shape" in halfway.rationale
    # 2× the threshold saturates at −1 and never overshoots.
    floor = SIGNAL.evaluate(_bet("Josh Allen", "pass_yards", "over", 230.0), CTX)
    assert floor is not None and floor.score == -1.0
    # The mirror image: an under HIGHER than consensus is the friendly side.
    under = SIGNAL.evaluate(_bet("Josh Allen", "pass_yards", "under", 290.0), CTX)
    assert under is not None and under.score == pytest.approx(-0.5)


def test_normal_dispersion_and_unfriendly_numbers_abstain() -> None:
    # Inside the 15-point dispersion — nothing to flag, and never a reward.
    assert SIGNAL.evaluate(_bet("Josh Allen", "pass_yards", "over", 260.0), CTX) is None
    # An UNfriendly outlier (over at a higher number) is not this signal's job.
    assert SIGNAL.evaluate(_bet("Josh Allen", "pass_yards", "over", 290.0), CTX) is None


def test_side_fallback_missing_data_and_unknown_markets() -> None:
    # The over consensus is NaN — the paired under consensus (same number) serves.
    kelce = SIGNAL.evaluate(_bet("Travis Kelce", "receptions", "over", 5.0), CTX)
    assert kelce is not None and kelce.score == pytest.approx(-0.5)  # 1.5 vs t=1.0
    assert SIGNAL.evaluate(_bet("Nobody", "pass_yards", "over", 200.0), CTX) is None
    assert SIGNAL.evaluate(  # a market with no dispersion threshold abstains
        _bet("Josh Allen", "anytime_td", "over", 0.5), CTX) is None
    assert DEFAULT_SIGNAL_WEIGHTS["line_outlier"] > 0
