"""Cross-venue closing comparison — de-vigged, like-for-like, or not at all.

The question the exchange build exists to answer is whether an exchange's
close is sharper or softer than a sportsbook's (docs/BUILD_EXCHANGES.md E7).
Getting it wrong is easy in two specific ways, both pinned here: comparing raw
implied probabilities (which still carry vig and spread), and comparing an
executable ask to a mid sample (biased by half the spread, venue-asymmetrically).
"""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.eval.venues import compare_closes, fair_closing_probabilities, venue_sharpness


def _close(
    book: str, side: str, price: int, point: float | None = None, basis: str = "ask"
) -> dict:
    return {
        "game_id": "g1",
        "market": "moneyline" if point is None else "spread",
        "book": book,
        "side": side,
        "price": price,
        "point": point,
        "price_basis": basis,
    }


def test_devig_strips_each_venues_margin_before_comparing() -> None:
    closes = pd.DataFrame(
        [
            # Sportsbook: -110 both ways is a 4.8% hold.
            _close("bookA", "home", -110),
            _close("bookA", "away", -110),
            # Exchange: two asks summing just over $1 — the spread as overround.
            _close("kalshi", "home", -104),
            _close("kalshi", "away", -104),
        ]
    )
    fair = fair_closing_probabilities(closes)
    # Both venues price a coin flip; the raw implied probabilities differ
    # (0.524 vs 0.510) purely by margin, and de-vig removes it.
    assert set(fair["book"]) == {"bookA", "kalshi"}
    for value in fair["p_fair"]:
        assert value == pytest.approx(0.5, abs=1e-9)


def test_one_sided_quotes_are_dropped_not_treated_as_fair() -> None:
    fair = fair_closing_probabilities(pd.DataFrame([_close("kalshi", "home", 150)]))
    assert fair.empty


def test_ladder_rungs_are_paired_within_their_own_contract() -> None:
    closes = pd.DataFrame(
        [
            _close("kalshi", "home", -110, -1.5),
            _close("kalshi", "away", -110, 1.5),
            _close("kalshi", "home", 400, -14.5),
            _close("kalshi", "away", -500, 14.5),
        ]
    )
    fair = fair_closing_probabilities(closes)
    near = fair[fair["point"] == -1.5].iloc[0]
    far = fair[fair["point"] == -14.5].iloc[0]
    assert near["p_fair"] == pytest.approx(0.5, abs=1e-9)
    # The far rung keeps its own long price rather than borrowing the near
    # rung's — the whole point of keying on the contract.
    assert far["p_fair"] < 0.25


def test_a_sharper_close_scores_a_lower_brier() -> None:
    fair = pd.DataFrame(
        {
            "book": ["bookA", "bookA", "kalshi", "kalshi"],
            "p_fair": [0.5, 0.5, 0.9, 0.9],
            "price_basis": ["ask"] * 4,
        }
    )
    # Both home sides won; the venue that said 0.9 was closer to the truth.
    scored = venue_sharpness(fair, pd.Series([1.0, 1.0, 1.0, 1.0]))
    assert list(scored["book"]) == ["kalshi", "bookA"]
    assert scored.iloc[0]["brier"] < scored.iloc[1]["brier"]


def test_mixed_price_bases_are_excluded_not_averaged() -> None:
    # An ask close and a mid close are different measurements; letting them
    # into one comparison shows a sharpness gap that is really half a spread.
    fair = pd.DataFrame(
        {
            "book": ["bookA", "polymarket"],
            "p_fair": [0.5, 0.9],
            "price_basis": ["ask", "mid"],
        }
    )
    scored = venue_sharpness(fair, pd.Series([1.0, 1.0]))
    assert list(scored["book"]) == ["bookA"]
    # Opting out of the guard is possible, but must be explicit.
    both = venue_sharpness(fair, pd.Series([1.0, 1.0]), require_basis=None)
    assert set(both["book"]) == {"bookA", "polymarket"}


def test_compare_closes_reports_the_disagreement_per_contract() -> None:
    book = fair_closing_probabilities(
        pd.DataFrame([_close("bookA", "home", -110), _close("bookA", "away", -110)])
    )
    exchange = fair_closing_probabilities(
        pd.DataFrame([_close("kalshi", "home", -200), _close("kalshi", "away", 180)])
    )
    merged = compare_closes(book, exchange, on=("game_id", "market", "side", "point"))
    home = merged[merged["side"] == "home"].iloc[0]
    assert home["p_book"] == pytest.approx(0.5, abs=1e-9)
    # The exchange closed the home side much higher — where an origination
    # edge would have to live.
    assert home["disagreement"] > 0.1


def test_basis_is_reported_per_side_not_consumed_by_the_first() -> None:
    """Both sides of a contract report the contract's basis.

    The basis is computed once per contract but a row is emitted per side, so
    reading it destructively labels the second side "mixed" — which silently
    halves the comparison set, since mixed rows are excluded by default.
    """
    closes = pd.DataFrame([_close("kalshi", "home", -110), _close("kalshi", "away", -110)])
    fair = fair_closing_probabilities(closes)
    assert len(fair) == 2
    assert set(fair["price_basis"]) == {"ask"}
    # And a genuinely mixed contract is still caught.
    mixed = pd.DataFrame(
        [_close("kalshi", "home", -110), _close("kalshi", "away", -110, basis="mid")]
    )
    assert set(fair_closing_probabilities(mixed)["price_basis"]) == {"mixed"}
