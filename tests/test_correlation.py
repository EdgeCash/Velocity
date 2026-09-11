"""Two bets on one game: what ρ is, and what the flat 0.5 gets wrong.

``size_portfolio`` de-scales every pair inside a correlation group at a flat
``group_correlation = 0.5``. That was set when one bet per view was the rule;
an exchange ladder puts several contracts on one game's total, so the constant
now decides real stake. :mod:`velocity.eval.correlation` measures ρ instead of
assuming it — and deliberately does not feed the sizing path, so these tests
assert the measurement, not a staking change.

The bug class worth pinning is the label: a hedge (``over 44.5`` against
``under 36.5``, which cannot both win) and a middle (``over 36.5`` against
``under 44.5``, which both win on any total from 37 to 44) are different bets
that a side-only label collapses into one bucket.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.eval.correlation import (
    _LOSS,
    _REFUNDED,
    _WIN,
    HISTORICAL_NOTES,
    _two_bet_scale,
    correlation,
    correlation_ci,
    historical_correlation,
    ladder_separation_curve,
    pair_class,
    realized_correlation,
)
from velocity.wagering.ledger import SETTLED_RESULTS
from velocity.wagering.portfolio import correlation_scale

# ---------------------------------------------------------------- pair labels

def test_hedge_and_middle_are_not_the_same_class():
    """Opposite sides alone do not say whether both legs can win."""
    hedge = pair_class("total", "over", 44.5, "total", "under", 36.5)
    middle = pair_class("total", "over", 36.5, "total", "under", 44.5)
    assert hedge == "total hedge 8p"
    assert middle == "total middle 8p"
    assert hedge != middle


def test_pair_class_is_order_independent():
    """A card's pairs land in one bucket however they were enumerated."""
    for args in (
        ("total", "over", 44.5, "total", "under", 36.5),
        ("total", "over", 44.5, "total", "over", 48.5),
        ("spread", "home", -3.5, "total", "over", 44.5),
    ):
        a, b, c, d, e, f = args
        assert pair_class(a, b, c, d, e, f) == pair_class(d, e, f, a, b, c)


def test_same_side_label_carries_the_separation():
    assert pair_class("total", "over", 44.5, "total", "over", 48.5) == "total same-side 4p"
    assert pair_class("spread", "home", -3.0, "spread", "home", -7.0) == "spread same-side 4p"


def test_cross_market_label_is_sorted_not_positional():
    assert pair_class("total", "over", 44.5, "spread", "home", -3.5) == "spread x total"
    assert pair_class("moneyline", "home", None, "spread", "home", -3.5) \
        == "moneyline x spread"


def test_opposite_sides_without_numbers_stay_unqualified():
    """A moneyline has no number, so there is no middle to find."""
    assert pair_class("moneyline", "home", None, "moneyline", "away", None) \
        == "moneyline opposite-side"


def test_unknown_side_label_does_not_guess_a_shape():
    """An unrecognised side gives no direction, so the shape is left unsaid."""
    assert pair_class("total", "zig", 44.5, "total", "zag", 36.5) \
        == "total opposite-side 8p"


def test_opposite_sides_on_one_number_are_a_hedge():
    """Over and under the same number cannot both win."""
    assert pair_class("total", "over", 44.5, "total", "under", 44.5) == "total hedge 0p"


# ------------------------------------------------------------------ estimator

def test_correlation_is_nan_when_a_side_never_varies():
    """Every bet on that side winning is no evidence about joint outcomes."""
    ones = np.ones(100, dtype=bool)
    mixed = np.arange(100) % 2 == 0
    assert np.isnan(correlation(ones, mixed))


def test_correlation_is_nan_below_the_sample_floor():
    a = np.array([True, False, True, False])
    assert np.isnan(correlation(a, a))


def test_correlation_needs_equal_lengths():
    with pytest.raises(ValueError):
        correlation(np.ones(40, dtype=bool), np.ones(41, dtype=bool))


def test_perfect_agreement_and_disagreement():
    rng = np.random.default_rng(1)
    a = rng.random(400) < 0.5
    assert correlation(a, a) == pytest.approx(1.0)
    assert correlation(a, ~a) == pytest.approx(-1.0)


def test_bootstrap_interval_brackets_the_point_estimate():
    rng = np.random.default_rng(2)
    a = rng.random(600) < 0.5
    b = np.where(rng.random(600) < 0.8, a, ~a)
    rho, lo, hi = correlation_ci(a, b, reps=300)
    assert lo < rho < hi
    assert rho > 0.5


def test_independent_vectors_admit_zero():
    rng = np.random.default_rng(3)
    a, b = rng.random(2000) < 0.5, rng.random(2000) < 0.5
    _, lo, hi = correlation_ci(a, b, reps=400)
    assert lo <= 0.0 <= hi


# ------------------------------------------------------- scale reporting only

@pytest.mark.parametrize("rho", [0.0, 0.2, 0.5, 0.75, 1.0])
def test_reported_scale_matches_the_staking_formula(rho):
    """The measurement reports in the same units sizing would use."""
    assert _two_bet_scale(rho) == pytest.approx(correlation_scale(2, rho))


def test_negative_rho_is_carried_not_clamped():
    """``correlation_scale`` takes ρ in [0,1]; here a hedge is the finding."""
    assert _two_bet_scale(-0.5) == pytest.approx(2.0)
    assert _two_bet_scale(-1.0) == float("inf")


# --------------------------------------------------------------- historical

def _games(n: int = 400, seed: int = 11) -> pd.DataFrame:
    """Finals on half-point lines, so nothing pushes unless a test asks."""
    rng = np.random.default_rng(seed)
    home = rng.integers(0, 40, n)
    away = rng.integers(0, 40, n)
    return pd.DataFrame({
        "season": np.repeat([2024, 2025], n // 2),
        "home_score": home, "away_score": away,
        "spread_line": rng.integers(-7, 8, n) + 0.5,
        "total_line": rng.integers(30, 60, n) + 0.5,
    })


def test_historical_measures_the_classes_a_card_carries():
    rows = {m.pair: m for m in historical_correlation(_games(), reps=120)}
    assert "spread x total" in rows
    assert "total same-side 8p" in rows
    assert "total hedge 8p" in rows
    assert "total middle 8p" in rows


def test_same_side_rungs_are_positive_and_opposite_sides_negative():
    rows = {m.pair: m for m in historical_correlation(_games(), reps=120)}
    assert rows["total same-side 8p"].rho > 0.2
    assert rows["total hedge 8p"].rho < 0.0
    assert rows["total middle 8p"].rho < 0.0


def test_closer_rungs_are_more_correlated_than_distant_ones():
    """The curve the ladder rides: ρ falls monotonically with separation."""
    rows = {m.pair: m for m in historical_correlation(_games(800), reps=120)}
    curve = [rows[f"total same-side {g:g}p"].rho for g in (2, 4, 8, 12, 16, 20)]
    assert curve == sorted(curve, reverse=True)


def test_flat_assumption_over_bets_a_tightly_correlated_pair():
    """``stake_error`` is signed: positive means the flat 0.5 stakes too much."""
    rows = {m.pair: m for m in historical_correlation(_games(800), reps=120)}
    near = rows["total same-side 2p"]
    assert near.rho > 0.5
    assert near.stake_error > 0.0


def test_pushes_are_dropped_rather_than_counted_as_losses():
    games = _games(200)
    games.loc[:, "total_line"] = games["home_score"] + games["away_score"]  # all push
    assert historical_correlation(games, reps=50) == []


def test_empty_dataset_measures_nothing():
    empty = pd.DataFrame(columns=["home_score", "away_score", "spread_line", "total_line"])
    assert historical_correlation(empty) == []


def test_separation_curve_reports_per_season_spread():
    curve = ladder_separation_curve(_games(600))
    assert list(curve["separation"]) == [2, 4, 8, 12, 16, 20]
    assert (curve["seasons"] == 2).all()
    assert (curve["rho_min"] <= curve["rho_max"]).all()


# ------------------------------------------------------------------- realized

def _settled(rows: list[tuple[str, str, str, float, str]]) -> pd.DataFrame:
    """``(game_id, market, side, point, result)`` → settled ledger rows."""
    return pd.DataFrame(
        [{"game_id": g, "market": m, "side": s, "point": p, "result": r}
         for g, m, s, p, r in rows]
    )


def test_realized_is_empty_before_anything_settles():
    assert realized_correlation(_settled([])) == []
    assert realized_correlation(None) == []


def test_realized_needs_the_identity_columns():
    """A frame without ``game_id`` cannot be grouped by game."""
    thin = pd.DataFrame({"market": ["total"], "side": ["over"], "result": ["win"]})
    assert realized_correlation(thin) == []


def test_realized_omits_a_class_too_thin_to_mean_anything():
    rows = [(f"g{i}", "total", "over", 44.5, "win") for i in range(5)]
    rows += [(f"g{i}", "total", "over", 48.5, "win") for i in range(5)]
    assert realized_correlation(_settled(rows), min_pairs=20) == []


def test_realized_pairs_only_within_a_game():
    """Two bets on different games are not a same-game pair."""
    rows = [("g1", "total", "over", 44.5, "win"), ("g2", "total", "over", 48.5, "loss")]
    assert realized_correlation(_settled(rows), min_pairs=1) == []


def test_realized_finds_the_class_and_its_correlation():
    """Agreeing rungs on one side come back positively correlated."""
    rows = []
    for i in range(40):
        result = "win" if i % 2 == 0 else "loss"
        rows.append((f"g{i}", "total", "over", 44.5, result))
        rows.append((f"g{i}", "total", "over", 48.5, result))
    out = {m.pair: m for m in realized_correlation(_settled(rows), min_pairs=20, reps=80)}
    assert out["total same-side 4p"].rho == pytest.approx(1.0)
    assert out["total same-side 4p"].n == 40


def test_realized_separates_the_hedge_from_the_middle():
    """The two shapes our ladder can produce do not share a bucket."""
    rows = []
    for i in range(30):
        # a hedge: over the number and under a lower one, never both winning
        rows.append((f"h{i}", "total", "over", 44.5, "win" if i % 2 else "loss"))
        rows.append((f"h{i}", "total", "under", 36.5, "loss" if i % 2 else "win"))
        # a middle: over a lower number and under a higher one
        rows.append((f"m{i}", "total", "over", 36.5, "win"))
        rows.append((f"m{i}", "total", "under", 44.5, "win" if i % 3 else "loss"))
    out = {m.pair: m for m in realized_correlation(_settled(rows), min_pairs=20, reps=80)}
    assert "total hedge 8p" in out
    assert "total middle 8p" in out
    assert out["total hedge 8p"].rho == pytest.approx(-1.0)


def test_realized_drops_pushes_and_pending():
    """A push returns the stake, so it says nothing about joint outcomes."""
    rows = []
    for i in range(30):
        rows.append((f"g{i}", "total", "over", 44.5, "win"))
        rows.append((f"g{i}", "total", "over", 48.5, "push" if i < 25 else "win"))
    assert realized_correlation(_settled(rows), min_pairs=20) == []


def test_realized_reports_the_flat_assumption_alongside():
    rows = []
    for i in range(40):
        result = "win" if i % 2 == 0 else "loss"
        rows.append((f"g{i}", "spread", "home", -3.5, result))
        rows.append((f"g{i}", "total", "over", 44.5, result))
    out = {m.pair: m for m in realized_correlation(_settled(rows), min_pairs=20, reps=80)}
    measured = out["spread x total"]
    assert measured.flat_scale == pytest.approx(correlation_scale(2, 0.5))
    # perfectly correlated, so the flat 0.5 under-de-scales: it over-bets
    assert measured.stake_error > 0.0


# ------------------------------------------ the framing the finding depends on

def _fav_relative_games(n: int = 400) -> pd.DataFrame:
    """Favourite-covers and over agree perfectly, with the favourite on both sides.

    Built so the two framings disagree by construction. Half the games have a
    home favourite and half an away one; in every game the favourite covering
    and the total going over happen together. Restated from the favourite's side
    that is ρ = +1. Left as *home* covers it is ρ = 0, because the away-favourite
    half contributes the mirror image and the two cancel.

    This is the error a first pass actually made, and the reason the module
    carries ``fav_margin`` at all.
    """
    rows = []
    for i in range(n):
        home_fav = i % 2 == 0
        covers = (i // 2) % 2 == 0
        line = 10.5 if home_fav else -10.5
        if covers:  # favourite wins by 20, and the game goes over
            home, away = (35, 15) if home_fav else (15, 35)
        else:  # favourite fails to cover, and the game stays under
            home, away = 20, 20
        rows.append({"season": 2025, "home_score": home, "away_score": away,
                     "spread_line": line, "total_line": 44.5})
    return pd.DataFrame(rows)


def test_spread_total_is_measured_from_the_favourites_side():
    """Averaging home favourites with home dogs hides a real effect at zero."""
    games = _fav_relative_games()
    rows = {m.pair: m for m in historical_correlation(games, reps=80)}
    assert rows["spread x total"].rho == pytest.approx(1.0)

    # the framing the module rejects: home-relative, where the halves cancel
    margin = games["home_score"] - games["away_score"]
    total = games["home_score"] + games["away_score"]
    home_relative = correlation(margin > games["spread_line"], total > games["total_line"])
    assert home_relative == pytest.approx(0.0, abs=0.05)


def _blowout_games(n: int = 1200) -> pd.DataFrame:
    """Independent under 14 points of favourite, strongly correlated past it.

    The shape the NCAAF measurement found: a four-touchdown favourite covering
    *is* a game with points in it, while a three-point favourite covering says
    nothing about the total. Pooled, the halves dilute each other; split on the
    favourite's size, the structure is plain.
    """
    rng = np.random.default_rng(5)
    rows = []
    for i in range(n):
        big = i % 2 == 0
        line = 24.5 if big else 3.5
        covers = rng.random() < 0.5
        # a big favourite covering and the game going over happen together; a
        # small favourite covering says nothing about the total
        over = covers if big else rng.random() < 0.5
        # the margin has to straddle *this* game's number, or the cover
        # indicator is constant inside the bucket and ρ is undefined
        margin = (30 if covers else 10) if big else (10 if covers else 0)
        total = 60 if over else 40
        home = (total + margin) // 2
        rows.append({"season": 2025, "home_score": home, "away_score": total - home,
                     "spread_line": line, "total_line": 48.5})
    return pd.DataFrame(rows)


def test_favourite_buckets_reveal_what_the_pooled_number_dilutes():
    """The conditioning is the finding: independence holds except in blowouts."""
    rows = {m.pair: m for m in historical_correlation(_blowout_games(), reps=150)}
    pooled = rows["spread x total"].rho
    small = rows["spread x total, favourite 3-7"]
    big = rows["spread x total, favourite 21-28"]

    assert small.includes_zero
    assert not big.includes_zero
    assert big.rho > 0.8
    # the pooled estimate sits between the two and represents neither
    assert small.rho < pooled < big.rho


# ------------------------------------------------ the banked table is a cache

@pytest.mark.parametrize("league", ["nfl", "ncaaf"])
def test_banked_notes_match_a_fresh_measurement(league):
    """``HISTORICAL_NOTES`` caches the datasets, and caches go stale.

    Every entry, not a sample: the numbers in that table are what a reader of
    the sizing code will trust without rerunning anything, and a transcription
    slip is invisible by inspection. This test exists because one happened —
    the first pass banked home-relative numbers (+0.023 spread×total, +0.65
    spread×moneyline) after the module had moved to the favourite-relative
    framing, and nothing caught it.
    """
    games = pd.read_parquet(f"datasets/{league}/games.parquet")
    fresh = {m.pair: m.rho for m in historical_correlation(games, reps=200)}
    banked = {pair: rho for (lg, pair), rho in HISTORICAL_NOTES.items() if lg == league}
    assert banked, f"no banked notes for {league}"
    for pair, rho in banked.items():
        assert pair in fresh, f"{league} banked a pair the measurement no longer emits: {pair}"
        # loose on the bootstrap, tight on the point estimate — the point
        # estimate is deterministic given the dataset
        assert fresh[pair] == pytest.approx(rho, abs=1e-3)


@pytest.mark.parametrize("league", ["nfl", "ncaaf"])
def test_banked_notes_keys_are_real_pair_classes(league):
    """A banked label that ``pair_class`` would never produce cannot be matched."""
    for lg, pair in HISTORICAL_NOTES:
        if lg != league:
            continue
        if pair.startswith("spread x total, favourite"):
            continue  # a conditioned slice, not a bare pair class
        assert pair in {
            pair_class("spread", "home", -3.5, "total", "over", 44.5),
            pair_class("moneyline", "home", None, "spread", "home", -3.5),
            *(pair_class("total", "over", 44.5, "total", "over", 44.5 + g)
              for g in (2, 4, 8, 12, 16, 20)),
            *(pair_class("total", "over", 44.5, "total", "under", 44.5 - g)
              for g in (2, 4, 8, 12, 16, 20)),
            *(pair_class("total", "over", 44.5 - g, "total", "under", 44.5)
              for g in (2, 4, 8, 12, 16, 20)),
        }, f"{pair} is not a label pair_class emits"


def test_every_settled_result_the_ledger_can_write_is_classified():
    """A new settled result must be decided here, not silently dropped.

    The measurement reads ``win``/``loss`` and refunds ``push``/``tie``. If the
    ledger learns another outcome, it would fall out of every pair class without
    a word — so the partition is pinned against the ledger's own vocabulary.
    """
    assert _WIN | _LOSS | _REFUNDED == SETTLED_RESULTS
    assert not (_WIN & _LOSS) and not (_WIN & _REFUNDED) and not (_LOSS & _REFUNDED)
