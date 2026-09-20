"""The E8 gate: refuse ladder rungs where the sim's shape is measurably wrong.

The sim draws a rounded normal; real football residuals are leptokurtic, so a
normal overstates the chance of landing past any threshold — the direction that
invents edges rather than hiding them. These pin the measurement, the committed
table, and the gate's deliberately narrow scope (docs/BUILD_EXCHANGES.md E8).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest
from velocity.eval.ladders import (
    DEFAULT_TOLERANCE,
    OFFSET_BIAS,
    OFFSET_ERROR,
    default_relative_tolerance,
    offset_error,
    offset_is_honest,
    residual_calibration,
    residual_threshold,
    rung_bias,
    rung_is_honest,
)
from velocity.models.game_nfl import GameProjection
from velocity.models.simulate import SimConfig, simulate_game
from velocity.util.seed import make_rng
from velocity.wagering.slate import SlateConfig, _ladder_gate_blocks

_CALIBRATE_SPEC = importlib.util.spec_from_file_location(
    "calibrate_ladders",
    Path(__file__).resolve().parents[1] / "scripts" / "calibrate_ladders.py",
)
_CALIBRATE = importlib.util.module_from_spec(_CALIBRATE_SPEC)
_CALIBRATE_SPEC.loader.exec_module(_CALIBRATE)


@pytest.fixture
def projection() -> GameProjection:
    # Home favoured by 6 with a 45-point total, so the fair spread is -6.
    sim = simulate_game(6.0, 45.0, make_rng(), SimConfig(n_sims=40_000))
    return GameProjection(home_team="H", away_team="A", mu_home=25.5, mu_away=19.5, sim=sim)


def test_calibration_measures_both_tails_against_the_normal() -> None:
    # A deliberately peaked sample: far more mass at the line than a normal
    # with the same spread would put there.
    games = pd.DataFrame(
        {
            "home_score": [21] * 40 + [45, 0, 38, 3],
            "away_score": [21] * 40 + [0, 45, 3, 38],
            "spread_line": [0.0] * 44,
            "total_line": [42.0] * 44,
        }
    )
    table = residual_calibration(games, "spread", max_offset=5.5)
    assert list(table["offset"]) == [0.5, 1.5, 2.5, 3.5, 4.5, 5.5]
    # The normal expects mass out at ±5.5 that this sample does not have.
    assert table.set_index("offset").loc[5.5, "error"] > 0.05
    assert (table["error"] >= 0).all()


@pytest.mark.parametrize(
    ("league", "dataset"),
    [("nfl", "datasets/nfl/games.parquet"), ("ncaaf", "datasets/ncaaf/games.parquet")],
)
@pytest.mark.parametrize("market", ["spread", "total"])
def test_committed_table_matches_a_fresh_measurement(league, dataset, market) -> None:
    """The banked table is a cache of the datasets, and caches go stale.

    Every offset of every table, signed per tail — not a sample of the near
    ones — because the deep rungs are where the gate's bar is tightest and a
    transcription slip there is invisible by inspection.
    ``scripts/calibrate_ladders.py`` prints the literal this compares against.

    The reference comes from that same script rather than being restated
    here: the table is a statement about a PARTICULAR sim, so a test that
    named its own config could pass while the banked numbers described
    something else. It also means a league whose promoted sd moves fails
    this test until the table is regenerated, which is the point.
    """
    fresh = residual_calibration(
        pd.read_parquet(dataset), market, max_offset=28.5,
        reference=_CALIBRATE.SIMS[league])
    committed = OFFSET_BIAS[(league, market)]
    assert set(fresh["offset"]) == set(committed)
    for row in fresh.itertuples():
        over, under = committed[row.offset]
        assert row.over_bias == pytest.approx(over, abs=5e-4)
        assert row.under_bias == pytest.approx(under, abs=5e-4)


def test_the_lattice_opened_the_nfl_spread_shoulders() -> None:
    """The headline finding, twice revised, and the second revision is a promotion.

    Measuring the sim rather than a continuous normal took NFL spreads from
    3.7 points of probability in the shoulders to 2.9 — still past the
    two-point edge the slate bets on, so the rungs stayed refused. The
    promotion round (docs/MODEL_LAB.md) then gave the NFL sim football's own
    margin lattice, and the same measurement reads 1.9: the shoulder error
    was the normal smearing mass over 9, 11, 12 and 15 that football puts on
    3, 7 and 14, and putting it back opens every NFL spread side (50 → 58 of
    58). Still a real error, still measured, and now inside the bar.
    """
    assert 0.015 < offset_error("nfl", "spread", 4.5) < 0.02
    assert offset_is_honest("nfl", "spread", 4.5)
    # NCAAF spreads were inside tolerance before the lattice and are measured
    # without it: college's lattice closed sides here rather than opening them.
    assert offset_error("ncaaf", "spread", 4.5) < 0.02
    assert offset_is_honest("ncaaf", "spread", 4.5)
    # Deep out, the sim's own mass is small and the absolute miss recovers.
    assert offset_is_honest("nfl", "spread", 17.5)
    # The lattice is a margin correction and the totals table is untouched by
    # it beyond parity: NFL totals still refuse the over shoulder.
    assert offset_error("nfl", "total", 4.5) > 0.025
    assert not offset_is_honest("nfl", "total", 4.5)


def test_markets_without_a_number_are_never_gated() -> None:
    assert offset_error("nfl", "moneyline", 0.0) == 0.0
    assert offset_is_honest("nfl", "moneyline", 0.0)
    # Team totals borrow the game total's shape.
    assert offset_error("nfl", "team_total_home", 3.5) == offset_error("nfl", "total", 3.5)
    # An unmeasured league says so rather than guessing, and unknown is not
    # safe: nothing has ever checked this shape, so the rung is refused.
    assert offset_error("mlb", "spread", 3.5) is None
    assert not offset_is_honest("mlb", "spread", 3.5)
    assert offset_is_honest("mlb", "spread", 3.5, unmeasured_is_honest=True)


def test_the_deep_tail_is_measured_rather_than_assumed_innocent() -> None:
    """The gate's original table stopped at 20.5 and waved everything past it
    through, on the argument that the error out there was small and shrinking.

    It is small and it does shrink — but not to nothing, and not fast. NFL
    totals still carry 1.3 points of probability at 20.5, two-thirds of the
    tolerance, and 0.6 at 28.5. Waving that through is how the first live
    exchange board came back entirely deep tail: the gate had blocked
    everything nearer the line, so an EV maximizer went where nothing was
    checked.
    """
    deep = [OFFSET_ERROR[("nfl", "total")][off] for off in (20.5, 22.5, 24.5, 26.5, 28.5)]
    assert min(deep) > 0.005
    assert max(deep) > 0.012
    assert max(deep) < DEFAULT_TOLERANCE
    # NCAAF totals no longer get WORSE past the old table's end, and that
    # reversal is worth recording rather than quietly dropping: under the old
    # continuous-normal stand-in the error at 25.5 was 0.0142 against 0.0080
    # at 15.5, and measuring the sim itself makes it 0.0062 against 0.0073.
    # The deep-tail blow-up was the stand-in's, not the sim's.
    table = OFFSET_ERROR[("ncaaf", "total")]
    assert table[25.5] < table[15.5]
    assert table[25.5] < 0.01
    # And past the end of the measurement there is no opinion to have, so the
    # rung is refused — an EV maximizer finds an ungated region precisely
    # because it is ungated.
    assert offset_error("nfl", "total", 40.5) is None
    assert not offset_is_honest("nfl", "total", 40.5)


def test_offset_is_measured_from_the_fair_line_on_either_side(projection) -> None:
    config = SlateConfig(ladder_tolerance=0.02, league="nfl")
    assert projection.sim.fair_spread() == pytest.approx(-6.0, abs=0.5)
    assert projection.sim.fair_total() == pytest.approx(45.0, abs=1.0)
    # Home -10.5 against a -6 fair line is 4.5 points of residual out, where
    # the normal overstated the favourite's tail by 3.7pp (2.9 measured as a
    # sim) and the lattice-corrected sim by 1.9 — inside the 2pp bar, so the
    # rung now stands. Away +10.5 is the same contract from the other side,
    # which the sim *understates*, so it always stood.
    assert not _ladder_gate_blocks(projection, "spread", "home", -10.5, "kalshi", config)
    assert not _ladder_gate_blocks(projection, "spread", "away", 10.5, "kalshi", config)
    # Totals keep the asymmetry on show: 4.5 over the fair total is where the
    # symmetric sim overstates the OVER tail by 2.8pp and understates the
    # under by the same, so the over is refused however good the price looks
    # and the under — the same number, the other side — stands. That is the
    # asymmetry the symmetric gate used to flatten.
    over = round(projection.sim.fair_total()) + 4.5
    assert _ladder_gate_blocks(projection, "total", "over", over, "kalshi", config)
    assert not _ladder_gate_blocks(projection, "total", "under", over, "kalshi", config)
    # At the fair line itself there is no threshold to be wrong about, so
    # neither side is charged the half-point-out error.
    assert not _ladder_gate_blocks(projection, "spread", "home", -6.0, "kalshi", config)
    assert not _ladder_gate_blocks(projection, "spread", "away", 6.0, "kalshi", config)


def test_the_gate_only_touches_ladder_venues(projection) -> None:
    # A sportsbook posts one main number its own backtests validate; gating it
    # would silently switch off ordinary totals betting.
    config = SlateConfig(ladder_tolerance=0.02, league="nfl")
    over = round(projection.sim.fair_total()) + 4.5
    assert not _ladder_gate_blocks(projection, "total", "over", over, "bookA", config)
    assert _ladder_gate_blocks(projection, "total", "over", over, "polymarket", config)


def test_the_gate_is_off_unless_configured(projection) -> None:
    # Default config leaves every rung alone, so nothing that worked before
    # this phase changes behaviour.
    assert not _ladder_gate_blocks(projection, "spread", "home", -10.5, "kalshi", SlateConfig())
    # A league is required too: without one there is no table to consult.
    no_league = SlateConfig(ladder_tolerance=0.02)
    assert not _ladder_gate_blocks(projection, "spread", "home", -10.5, "kalshi", no_league)


def test_calibration_reports_relative_error_too() -> None:
    """A rung's EV moves by error/price, so the deep tail needs both columns.

    An absolute miss of half a point is nothing at even money and a tenth of
    stake on a six-cent contract. The gate uses the absolute measure — it is
    in the same units as ``min_edge``, which is what qualifies a bet — but the
    relative one has to be visible, because it is what the far rungs the gate
    admits are actually exposed to.
    """
    games = pd.read_parquet("datasets/nfl/games.parquet")
    table = residual_calibration(games, "spread", max_offset=20.5).set_index("offset")
    near, far = table.loc[3.5], table.loc[20.5]
    # Absolute error says the far rung is the safe one...
    assert far["error"] < near["error"]
    # ...while relative error says its EV is at least as exposed.
    assert far["relative_error"] > 0.05
    assert far["relative_error"] >= near["relative_error"]


def test_ncaaf_sim_dispersion_matches_its_measured_residual() -> None:
    """The college sim's noise constants are walk-forward measured, not guessed.

    A sim's dispersion answers to its *own* residuals, and is re-measured
    when the projections sharpen: Round 3 measured 18.2 / 16.7 on the flat
    ``ridge-10`` fit (docs/MODEL_LAB.md "NCAAF Round 3"); the home-margin
    round measured 16.2 / 16.2 on the promoted chain (recency on both halves,
    the scale's home-margin intercept kept). The market-anchored residual
    that first drew attention to them (15.5) is still the wrong yardstick —
    the close is sharper than the model — and the constants stay above it.
    """
    from velocity.models.simulate import NCAAF_SD_MARGIN, NCAAF_SD_TOTAL

    assert pytest.approx(16.2) == NCAAF_SD_MARGIN
    assert pytest.approx(16.2) == NCAAF_SD_TOTAL
    assert NCAAF_SD_MARGIN > 15.5
    assert NCAAF_SD_TOTAL > 15.5


def test_the_two_tails_are_not_the_same_error() -> None:
    """The signed table exists because the shape error has a side.

    A probability the sim *overstates* is an edge it can invent; one it
    understates it can only hide. The old symmetric ``max(|over|, |under|)``
    charged both sides the worse tail's error, which blocked near-the-line rungs
    the bias runs in favour of and said nothing about which deep rung was the
    trap. These are the splits that make that distinction worth having.
    """
    spread = OFFSET_BIAS[("nfl", "spread")]
    # NFL spreads: the favourite's tail flips to an understatement past 15.5
    # while the dog's tail stays overstated through 22.5 — opposite signs at
    # the same offset, so one number for both sides cannot be right.
    assert spread[20.5][0] < 0 < spread[20.5][1]
    # Totals are the starker case, and measuring the sim rather than a
    # continuous normal identified WHY: total residuals are right-skewed
    # (+0.33 NFL, +0.34 college, against +0.10 and +0.01 on the spreads) and
    # the sim is symmetric. So near the line it overstates the OVER tail and
    # understates the under, in both leagues, by comparable amounts.
    for league in ("nfl", "ncaaf"):
        over, under = OFFSET_BIAS[(league, "total")][4.5]
        assert over > 0.015 > 0 > under
    # Deep out the two leagues part company, which one number for both would
    # hide: NFL totals leave the under tail as the only overstated side worth
    # gating, while college's deep tails are both inside a cent. Under the old
    # stand-in college looked like the NFL here (+0.0105 at 20.5); it does not.
    assert OFFSET_BIAS[("nfl", "total")][20.5][1] > 0.010 > abs(
        OFFSET_BIAS[("nfl", "total")][20.5][0])
    assert max(abs(v) for v in OFFSET_BIAS[("ncaaf", "total")][20.5]) < 0.010
    # And the symmetric view is derived from the signed one rather than banked
    # beside it, so the two can never drift apart.
    for key, table in OFFSET_BIAS.items():
        for offset, (over, under) in table.items():
            assert OFFSET_ERROR[key][offset] == max(abs(over), abs(under))


def test_residual_threshold_undoes_the_home_line_negation() -> None:
    """The sign trap: a home line is a negated margin, a total line is not.

    Get this backwards and every favourite's rung reads as its own mirror — the
    gate would charge the safe tail's error on the dangerous side, which is
    exactly the direction that lets an invented edge through.
    """
    # Fair spread -6 is a 6-point margin expectation. The -10.5 rung asks for
    # 4.5 points MORE than expected, so it sits at +4.5 of residual.
    assert residual_threshold("spread", -10.5, -6.0) == 4.5
    # The -3.5 rung asks for 2.5 points less: -2.5 of residual, and the home
    # side wins anywhere above that.
    assert residual_threshold("spread", -3.5, -6.0) == -2.5
    # Totals need no inversion: 24.5 against a 45 projection is -20.5 out.
    assert residual_threshold("total", 24.5, 45.0) == -20.5
    assert residual_threshold("total", 65.5, 45.0) == 20.5


def test_rung_bias_charges_each_side_only_its_own_tail() -> None:
    over, under = OFFSET_BIAS[("nfl", "total")][20.5]
    deep_under = residual_threshold("total", 24.5, 45.0)  # -20.5
    # Buying the deep under buys the under tail, which the sim overstates.
    assert rung_bias("nfl", "total", "under", deep_under) == under
    # Buying over that same number is the complement, so the same miss runs the
    # other way: the sim understates it and cannot have invented the edge.
    assert rung_bias("nfl", "total", "over", deep_under) == -under
    deep_over = residual_threshold("total", 65.5, 45.0)  # +20.5
    assert rung_bias("nfl", "total", "over", deep_over) == over
    assert rung_bias("nfl", "total", "under", deep_over) == -over
    # Spread sides mirror the same way once the contract is home-normalized.
    # Read from the table rather than pinned as a literal: this test is about
    # the MIRRORING, and a hand-copied 0.0368 makes it fail on every dataset
    # refresh for a reason that has nothing to do with what it checks.
    threshold = residual_threshold("spread", -10.5, -6.0)
    spread_over = OFFSET_BIAS[("nfl", "spread")][4.5][0]
    assert rung_bias("nfl", "spread", "home", threshold) == pytest.approx(spread_over)
    assert rung_bias("nfl", "spread", "away", threshold) == pytest.approx(-spread_over)
    # Team totals still borrow the game total's shape, sides and all.
    assert rung_bias("nfl", "team_total_home", "under", deep_under) == under
    # And the unmeasured answers are unchanged: a moneyline has no number to be
    # wrong about, an unknown league has nothing to say.
    assert rung_bias("nfl", "moneyline", "home", 0.0) == 0.0
    assert rung_bias("mlb", "spread", "home", 3.5) is None
    assert rung_bias("nfl", "total", "under", 40.5) is None


def test_the_bar_scales_with_the_rung_s_own_price() -> None:
    """The refinement the absolute gate was missing, and why it was backwards.

    A rung's EV per unit staked moves by its probability error divided by its
    price, so the 1.3pp miss on NFL's deep under tail is half a point of EV at
    even money and a fifth of stake on a 6-cent contract. Measured on the
    committed datasets that ratio *grows* with distance rather than shrinking —
    NFL totals run 0.06 of stake at half a point to 0.47 at 28.5 — so an
    absolute bar admitted precisely the rungs where a shape error costs the
    most. That is why the first live exchange board came back all deep tail.
    """
    deep_under = residual_threshold("total", 24.5, 45.0)
    # The absolute bar alone passes it: 1.3pp is well inside a 2pp tolerance.
    assert rung_is_honest("nfl", "total", "under", deep_under)
    # Priced as the ~6-cent contract an exchange actually lists it at, the same
    # miss is a fifth of stake and the rung is refused.
    assert not rung_is_honest("nfl", "total", "under", deep_under, price=0.058)
    # The identical error at even money is what the tolerance was written for,
    # so nothing changes there.
    assert rung_is_honest("nfl", "total", "under", deep_under, price=0.5)
    # The scaling is monotone in the price, with no cliff of its own.
    bar = [rung_is_honest("nfl", "total", "under", deep_under, price=p)
           for p in (0.02, 0.10, 0.25, 0.40, 0.60)]
    assert bar == sorted(bar)


def test_the_relative_bar_is_derived_not_a_second_knob() -> None:
    """Two tolerances that can be set apart are two tolerances that will drift.

    The relative bar is the EV distortion the absolute bar already accepts at
    even money, held constant across the price range. So the two tests agree
    exactly at 0.5 by construction, and tightening ``min_edge`` tightens both.
    """
    assert default_relative_tolerance(0.02) == pytest.approx(0.04)
    assert default_relative_tolerance(0.01) == pytest.approx(0.02)
    # At even money the relative bar is the absolute one, to the last decimal:
    # a bias just inside tolerance passes, one just outside does not.
    threshold = residual_threshold("total", 24.5, 45.0)
    bias = rung_bias("nfl", "total", "under", threshold)
    assert rung_is_honest("nfl", "total", "under", threshold, price=0.5, tolerance=bias)
    assert not rung_is_honest(
        "nfl", "total", "under", threshold, price=0.5, tolerance=bias - 1e-9
    )


def test_the_safe_tail_is_no_longer_charged_for_the_dangerous_one() -> None:
    """The unlock, and the reason it is safe to take.

    Everything the old gate let through on NFL was 15+ points out, because it
    charged near-the-line rungs the shoulder error — an error that, on the side
    those rungs actually buy, runs the *other* way. The residual measured here
    is around the market's close, which is sharper than the model's own
    projection, so the model's true bias is this one diluted toward zero; and
    dilution cannot flip a sign. A measured understatement stays one.
    """
    near = residual_threshold("spread", -3.5, -6.0)  # -2.5: buying home short
    assert rung_bias("nfl", "spread", "home", near) < 0
    assert rung_is_honest("nfl", "spread", "home", near, price=0.64)
    # The symmetric gate refused it, charging the favourite-tail error to a
    # bet that is not exposed to it. The lattice has since brought that
    # favourite-tail error inside the bar on NFL spreads, so the sharper
    # case is now a total: the under at 4.5 over the fair number buys the
    # tail the sim UNDERSTATES, while the symmetric view would have charged
    # it the 2.8pp the sim overstates the over by.
    shoulder = residual_threshold("total", 49.5, 45.0)  # +4.5
    assert rung_bias("nfl", "total", "under", shoulder) < 0
    assert rung_is_honest("nfl", "total", "under", shoulder, price=0.35)
    assert not offset_is_honest("nfl", "total", 4.5)
    # Past the table's end nothing has been checked, and an EV maximizer finds
    # an ungated region precisely because it is ungated — unchanged.
    assert not rung_is_honest("nfl", "spread", "home", 40.5, price=0.5)
    assert rung_is_honest("nfl", "spread", "home", 40.5, price=0.5, unmeasured_is_honest=True)


def test_the_slate_gate_refuses_the_cheap_deep_rung_it_used_to_take(projection) -> None:
    """End to end, on the shape of bet the first live exchange board filled with.

    Every qualifying rung on that board was 15 to 22 points out, priced in
    single-digit cents — the region the absolute gate waved through and where a
    1.3pp shape error is a fifth of stake. The same call without a price keeps
    the old absolute-only answer, which is what makes this the price's doing
    rather than a coincidence of the table.
    """
    config = SlateConfig(ladder_tolerance=0.02, league="nfl")
    total = projection.sim.fair_total()
    deep_under = round(total) - 20.5  # ~24.5 on a 45-point game
    assert not _ladder_gate_blocks(projection, "total", "under", deep_under, "kalshi", config)
    assert _ladder_gate_blocks(
        projection, "total", "under", deep_under, "kalshi", config, p_fair=0.058
    )
    # The over at that same number is the side the sim understates; it stays
    # bettable at its own (near-certain) price.
    assert not _ladder_gate_blocks(
        projection, "total", "over", deep_under, "kalshi", config, p_fair=0.94
    )
