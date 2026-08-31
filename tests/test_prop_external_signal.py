"""BettingPros prop corroboration — the outside projection judging our props.

Pins the join (normalized player name × slug-mapped market), the magnitude
ladder (star rating, then probability distance in either API scale, then a
modest bare-recommendation default), sign alignment with the bet side, and
the abstain paths (unmapped slug, unknown player, no recommendation).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.intel.context import GameContext, TeamContext
from velocity.intel.score import DEFAULT_SIGNAL_WEIGHTS
from velocity.intel.signals import PropExternalSignal
from velocity.wagering.bet_log import Bet

CTX = GameContext(
    game_id="g1", season=2026,
    away=TeamContext(team="BUF"), home=TeamContext(team="KC"),
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame([
        # Full premium row: over recommended at 4/5 stars.
        {"market_slug": "passing-yards", "player_name": "Josh Allen",
         "recommended_side": "over", "bet_rating": 4.0, "probability": 61.0,
         "projection": 268.4},
        # No rating → probability (0–1 scale here) sets the magnitude.
        {"market_slug": "receptions", "player_name": "Travis Kelce",
         "recommended_side": "under", "bet_rating": np.nan, "probability": 0.70,
         "projection": 5.1},
        # Bare recommendation: no rating, no probability.
        {"market_slug": "rushing-yards", "player_name": "James Cook",
         "recommended_side": "over", "bet_rating": np.nan, "probability": np.nan,
         "projection": np.nan},
        # Unmapped slug and missing recommendation → never indexed.
        {"market_slug": "longest-completion", "player_name": "Josh Allen",
         "recommended_side": "over", "bet_rating": 5.0, "probability": 80.0,
         "projection": 45.0},
        {"market_slug": "passing-yards", "player_name": "Patrick Mahomes",
         "recommended_side": None, "bet_rating": np.nan, "probability": np.nan,
         "projection": 285.0},
    ])


SIGNAL = PropExternalSignal.from_frame(_frame())


def _bet(player: str, market: str, side: str) -> Bet:
    return Bet(game_id="g1", market=market, side=side, book="b", price=-110.0,
               stake=1.0, p_model=0.55, point=249.5, player=player)


def test_agreement_scores_by_their_own_conviction() -> None:
    agree = SIGNAL.evaluate(_bet("Josh Allen", "pass_yards", "over"), CTX)
    assert agree is not None and agree.score == pytest.approx(0.8)  # 4/5 stars
    assert "268.4" in agree.rationale and "over" in agree.rationale
    disagree = SIGNAL.evaluate(_bet("Josh Allen", "pass_yards", "under"), CTX)
    assert disagree is not None and disagree.score == pytest.approx(-0.8)


def test_probability_and_bare_fallbacks() -> None:
    by_prob = SIGNAL.evaluate(_bet("Travis Kelce", "receptions", "under"), CTX)
    assert by_prob is not None and by_prob.score == pytest.approx(0.4)  # 2·|0.70−0.5|
    bare = SIGNAL.evaluate(_bet("James Cook", "rush_yards", "over"), CTX)
    assert bare is not None and bare.score == pytest.approx(0.35)


def test_abstains_when_the_snapshot_has_nothing_to_say() -> None:
    # Unmapped slug rows and rows without a recommendation never index …
    assert ("joshallen", "longest-completion") not in SIGNAL.index
    assert SIGNAL.evaluate(_bet("Patrick Mahomes", "pass_yards", "over"), CTX) is None
    # … and unknown players or non-total sides abstain outright.
    assert SIGNAL.evaluate(_bet("Nobody", "pass_yards", "over"), CTX) is None
    assert SIGNAL.evaluate(_bet("Josh Allen", "pass_yards", "home"), CTX) is None
    # A pre-slug snapshot (no market_slug column values) indexes nothing.
    old = _frame().assign(market_slug="")
    assert PropExternalSignal.from_frame(old).index == {}
    assert DEFAULT_SIGNAL_WEIGHTS["prop_external"] > 0
