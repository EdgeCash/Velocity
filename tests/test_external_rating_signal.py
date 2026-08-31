"""The external-rating corroboration signal (SP+) — agreement in points.

Pins the sign conventions exactly: a spread side is corroborated when the
implied margin covers its number (slate.py's ``margin + point > 0``), a
moneyline when its side simply wins, a total by implied-vs-line in the
side's direction; unknown teams abstain, and team totals are out of scope.
"""

from __future__ import annotations

import pytest
from velocity.intel.context import GameContext, TeamContext
from velocity.intel.score import DEFAULT_SIGNAL_WEIGHTS
from velocity.intel.signals import ExternalRatingSignal
from velocity.wagering.bet_log import Bet

# Georgia: strong (36 scored, 16 allowed); Vandy: weak (24 scored, 30 allowed).
# Implied (Georgia home): GA (36+30)/2 = 33, Vandy (24+16)/2 = 20 →
# margin +13 + 2.5 HFA = +15.5, total 53.
SIGNAL = ExternalRatingSignal(
    ratings={"Georgia": (36.0, 16.0), "Vanderbilt": (24.0, 30.0)}, label="SP+"
)
CTX = GameContext(
    game_id="g1", season=2026,
    away=TeamContext(team="Vanderbilt"), home=TeamContext(team="Georgia"),
)


def _bet(market: str, side: str, point: float | None) -> Bet:
    return Bet(game_id="g1", market=market, side=side, book="b", price=-110.0,
               stake=1.0, p_model=0.55, point=point)


def test_spread_agreement_covers_the_number() -> None:
    covered = SIGNAL.evaluate(_bet("spread", "home", -10.5), CTX)
    assert covered is not None and covered.score == pytest.approx(5.0 / 7.0)
    # The same game, laying more than the implied margin → the signal argues.
    too_many = SIGNAL.evaluate(_bet("spread", "home", -20.5), CTX)
    assert too_many is not None and too_many.score == pytest.approx(-5.0 / 7.0)
    # The dog side of a covered number is the mirror image.
    dog = SIGNAL.evaluate(_bet("spread", "away", 10.5), CTX)
    assert dog is not None and dog.score == pytest.approx(-5.0 / 7.0)


def test_moneyline_and_total_directions() -> None:
    ml = SIGNAL.evaluate(_bet("moneyline", "away", None), CTX)
    assert ml is not None and ml.score == -1.0  # -15.5 clipped at the scale
    over = SIGNAL.evaluate(_bet("total", "over", 49.5), CTX)
    assert over is not None and over.score == pytest.approx(3.5 / 7.0)
    under = SIGNAL.evaluate(_bet("total", "under", 49.5), CTX)
    assert under is not None and under.score == pytest.approx(-3.5 / 7.0)
    assert "SP+" in over.rationale


def test_abstains_without_a_rating_or_a_comparable_market() -> None:
    stranger = GameContext(
        game_id="g2", season=2026,
        away=TeamContext(team="Nobody State"), home=TeamContext(team="Georgia"),
    )
    assert SIGNAL.evaluate(_bet("spread", "home", -3.5), stranger) is None
    assert SIGNAL.evaluate(_bet("team_total_home", "over", 27.5), CTX) is None
    assert DEFAULT_SIGNAL_WEIGHTS["external_rating"] > 0  # it counts when it speaks
