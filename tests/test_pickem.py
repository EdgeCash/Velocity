"""Pick'em slip EV — exact math, correlated path, published breakevens.

The engine is only trustworthy if its plumbing is exact: the Poisson-binomial
DP must match brute-force enumeration, the sampled path must agree with the
independent path when legs really are independent, the breakevens must land
on the published figures (2-pick power ≈ 57.7%), and correlation must move
power-play EV in the direction the math says it does.
"""

from __future__ import annotations

from itertools import product

import numpy as np
import pytest
from velocity.wagering.pickem import (
    PAYOUTS,
    Leg,
    PayoutTable,
    best_slips,
    breakeven_leg_prob,
    fair_leg_prob,
    hit_distribution,
    hit_matrix,
    reverted_table,
    slip_ev,
    slip_ev_from_hits,
)


def test_official_tables_frozen() -> None:
    # The official structures (help-center, 2026-08). If PrizePicks changes
    # them, this test is the tripwire — update PAYOUTS deliberately.
    assert PAYOUTS["power-2"].payout(2) == 3.0
    assert PAYOUTS["power-3"].payout(3) == 6.0
    assert PAYOUTS["power-4"].payout(4) == 10.0
    assert PAYOUTS["power-5"].payout(5) == 20.0
    assert PAYOUTS["power-6"].payout(6) == 37.5
    assert PAYOUTS["flex-6"].multipliers == {6: 25.0, 5: 2.0, 4: 0.4}
    assert PAYOUTS["flex-5"].multipliers == {5: 10.0, 4: 2.0, 3: 0.4}
    assert PAYOUTS["flex-4"].multipliers == {4: 6.0, 3: 1.5}
    assert PAYOUTS["flex-3"].multipliers == {3: 3.0, 2: 1.0}
    assert PAYOUTS["flex-2"].multipliers == {2: 2.0, 1: 0.5}
    assert PAYOUTS["power-4"].payout(3) == 0.0  # power pays nothing partial


def test_hit_distribution_matches_brute_force() -> None:
    probs = [0.62, 0.55, 0.71, 0.48]
    dist = hit_distribution(probs)
    brute = np.zeros(5)
    for outcome in product([0, 1], repeat=4):
        p = np.prod([pr if o else 1 - pr for pr, o in zip(probs, outcome, strict=True)])
        brute[sum(outcome)] += p
    assert dist == pytest.approx(brute, abs=1e-12)
    assert dist.sum() == pytest.approx(1.0)


def test_sampled_path_agrees_with_independent_path() -> None:
    rng = np.random.default_rng(7)
    probs = [0.60, 0.55, 0.65, 0.50, 0.58]
    hits = np.column_stack([rng.random(200_000) < p for p in probs])
    table = PAYOUTS["flex-5"]
    assert slip_ev_from_hits(hits, table) == pytest.approx(
        slip_ev(probs, table), abs=0.01
    )


def test_breakevens_match_published_figures() -> None:
    # 2-pick power at 3x: p² = 1/3 → p = 1/√3.
    assert breakeven_leg_prob(PAYOUTS["power-2"]) == pytest.approx(
        1 / np.sqrt(3), abs=1e-6
    )
    # Larger flexes break even in the low-to-mid 50s; power slips need more.
    for name, lo, hi in (("flex-5", 0.50, 0.60), ("flex-6", 0.50, 0.60),
                         ("power-6", 0.50, 0.70)):
        p = breakeven_leg_prob(PAYOUTS[name])
        assert lo < p < hi, (name, p)
        assert slip_ev([p] * PAYOUTS[name].n_picks, PAYOUTS[name]) == pytest.approx(
            1.0, abs=1e-6
        )


def test_correlation_lifts_power_ev_above_independence() -> None:
    # Two perfectly correlated legs at p=0.6: all-hit probability is 0.6, not
    # 0.36 — the whole point of same-game stacks against fixed payouts.
    n = 100_000
    rng = np.random.default_rng(11)
    shared = rng.random(n) < 0.6
    correlated = np.column_stack([shared, shared])
    table = PAYOUTS["power-2"]
    ev_corr = slip_ev_from_hits(correlated, table)
    ev_ind = slip_ev([0.6, 0.6], table)
    assert ev_corr == pytest.approx(0.6 * 3.0, abs=0.02)
    assert ev_corr > ev_ind + 0.5


def test_fair_leg_prob_devigs_two_sided_prices() -> None:
    # Symmetric -110/-110 devigs to a coin flip.
    assert fair_leg_prob(-110, -110) == pytest.approx(0.5, abs=1e-9)
    assert fair_leg_prob(-150, +120) > 0.5


def test_reverted_table_follows_the_dnp_rule() -> None:
    assert reverted_table(PAYOUTS["flex-5"]).name == "flex-4"
    assert reverted_table(PAYOUTS["power-4"]).name == "power-3"
    assert reverted_table(PAYOUTS["power-6"], voids=2).name == "power-4"
    assert reverted_table(PAYOUTS["power-2"]) is None  # 2-pick → refund
    assert reverted_table(PAYOUTS["flex-3"], voids=0) is PAYOUTS["flex-3"]


def test_leg_validation() -> None:
    with pytest.raises(ValueError):
        Leg("A", "Pass Yards", 249.5, "middle", 0.6)
    with pytest.raises(ValueError):
        Leg("A", "Pass Yards", 249.5, "over", 1.2)
    leg = Leg("Justin Fields", "Pass Yards", 249.5, "over", 0.61)
    assert leg.label == "Justin Fields over 249.5 Pass Yards"


def test_hit_matrix_reads_samples_with_push_as_miss() -> None:
    samples = {"A": np.array([250.0, 249.5, 300.0]), "B": np.array([4.0, 6.0, 5.5])}
    legs = [Leg("A", "yds", 249.5, "over", 0.6), Leg("B", "rec", 5.5, "under", 0.55)]
    hits = hit_matrix(legs, lambda player, stat: samples[player])
    # A: 250 > 249.5 hit; 249.5 push → miss; 300 hit. B: 4 < 5.5 hit; 6 miss;
    # 5.5 push → miss.
    assert hits.tolist() == [[True, True], [False, False], [True, False]]


def test_best_slips_ranks_correlated_stacks_higher() -> None:
    # Four legs, identical marginals (p≈0.6): two are perfectly correlated,
    # two are independent. Correlation-aware search must put the correlated
    # pair on top of the power-2 board; the independent path can't see it.
    n = 60_000
    rng = np.random.default_rng(3)
    shared = rng.random(n) < 0.6
    cols = {
        ("QB1", "Pass Yards"): np.where(shared, 300.0, 200.0),
        ("WR1", "Rec Yards"): np.where(shared, 90.0, 50.0),
        ("RB9", "Rush Yards"): np.where(rng.random(n) < 0.6, 80.0, 40.0),
        ("TE7", "Receptions"): np.where(rng.random(n) < 0.6, 6.0, 3.0),
    }
    legs = [
        Leg("QB1", "Pass Yards", 250.0, "over", 0.6),
        Leg("WR1", "Rec Yards", 70.0, "over", 0.6),
        Leg("RB9", "Rush Yards", 60.0, "over", 0.6),
        Leg("TE7", "Receptions", 4.5, "over", 0.6),
    ]
    board = best_slips(
        legs, samples_fn=lambda p, s: cols[(p, s)], tables=("power-2",), top=6
    )
    assert board.iloc[0]["legs"] == "QB1 over 250 Pass Yards + WR1 over 70 Rec Yards"
    assert board.iloc[0]["ev"] == pytest.approx(1.8, abs=0.03)
    # And the same search without samples is blind to the stack: every pair
    # prices identically.
    flat = best_slips(legs, tables=("power-2",), top=6)
    assert flat["ev"].nunique() == 1


def test_best_slips_respects_min_ev_and_sizes() -> None:
    legs = [Leg(f"P{i}", "st", 1.0, "over", 0.52) for i in range(4)]
    board = best_slips(legs, tables=("power-2", "flex-4"), min_ev=5.0)
    assert board.empty  # nothing at p=0.52 clears EV 5.0
    board = best_slips(legs, tables=("power-6",))
    assert board.empty  # not enough legs for a 6-pick


def test_payout_table_validation() -> None:
    with pytest.raises(ValueError):
        PayoutTable("bad", 2, {})
    with pytest.raises(ValueError):
        PayoutTable("bad", 2, {3: 2.0})
    with pytest.raises(ValueError):
        slip_ev([0.5], PAYOUTS["power-2"])  # wrong leg count
    with pytest.raises(ValueError):
        slip_ev_from_hits(np.ones((10, 3), dtype=bool), PAYOUTS["power-2"])
