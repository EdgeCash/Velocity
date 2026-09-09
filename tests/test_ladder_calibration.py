"""The E8 gate: refuse ladder rungs where the sim's shape is measurably wrong.

The sim draws a rounded normal; real football residuals are leptokurtic, so a
normal overstates the chance of landing past any threshold — the direction that
invents edges rather than hiding them. These pin the measurement, the committed
table, and the gate's deliberately narrow scope (docs/BUILD_EXCHANGES.md E8).
"""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.eval.ladders import (
    DEFAULT_TOLERANCE,
    OFFSET_ERROR,
    offset_error,
    offset_is_honest,
    residual_calibration,
)
from velocity.models.game_nfl import GameProjection
from velocity.models.simulate import SimConfig, simulate_game
from velocity.util.seed import make_rng
from velocity.wagering.slate import SlateConfig, _ladder_gate_blocks


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


def test_committed_table_matches_a_fresh_measurement() -> None:
    # The constants are generated from the committed datasets; regenerating
    # them must reproduce what is checked in, or the table has gone stale.
    games = pd.read_parquet("datasets/nfl/games.parquet")
    fresh = residual_calibration(games, "spread", max_offset=6.5)
    committed = OFFSET_ERROR[("nfl", "spread")]
    for row in fresh.itertuples():
        assert row.error == pytest.approx(committed[row.offset], abs=5e-4)


def test_nfl_spreads_fail_the_gate_where_ncaaf_spreads_pass() -> None:
    # The headline finding: a normal misses NFL spread shape by 3+ points of
    # probability in the shoulders, which swamps the 2-point edge we bet on.
    assert offset_error("nfl", "spread", 4.5) > 0.03
    assert not offset_is_honest("nfl", "spread", 4.5)
    # NCAAF spreads are comfortably inside tolerance at the same distance.
    assert offset_error("ncaaf", "spread", 4.5) < 0.02
    assert offset_is_honest("ncaaf", "spread", 4.5)
    # Deep out, a normal's own mass is small and the absolute miss recovers.
    assert offset_is_honest("nfl", "spread", 17.5)


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
    # The gate's original table stopped at 20.5 and waved everything past it
    # through, on the argument that the error out there was small and
    # shrinking. It is not shrinking: NFL totals plateau around a point of
    # probability all the way out, more than half the tolerance, and the sign
    # has flipped by then — the real tail is FATTER than the fitted normal, so
    # the miss is no longer in the direction the shoulders taught us to expect.
    deep = [OFFSET_ERROR[("nfl", "total")][off] for off in (20.5, 22.5, 24.5, 26.5, 28.5)]
    assert min(deep) > 0.006
    assert max(deep) < DEFAULT_TOLERANCE
    # NCAAF totals actually get WORSE past the old table's end.
    table = OFFSET_ERROR[("ncaaf", "total")]
    assert table[25.5] > table[15.5]
    # And past the end of the measurement there is no opinion to have, so the
    # rung is refused — an EV maximizer finds an ungated region precisely
    # because it is ungated.
    assert offset_error("nfl", "total", 40.5) is None
    assert not offset_is_honest("nfl", "total", 40.5)


def test_offset_is_measured_from_the_fair_line_on_either_side(projection) -> None:
    config = SlateConfig(ladder_tolerance=0.02, league="nfl")
    assert projection.sim.fair_spread() == pytest.approx(-6.0, abs=0.5)
    # Home -6 and away +6 are the same contract at zero offset; both are gated
    # because NFL spread shape is already 3pp off at the line itself.
    assert _ladder_gate_blocks(projection, "spread", "home", -6.0, "kalshi", config)
    assert _ladder_gate_blocks(projection, "spread", "away", 6.0, "kalshi", config)
    # Far out, the sim recovers and the rung is bettable again.
    assert not _ladder_gate_blocks(projection, "spread", "home", -24.5, "kalshi", config)


def test_the_gate_only_touches_ladder_venues(projection) -> None:
    # A sportsbook posts one main number its own backtests validate; gating it
    # would silently switch off ordinary spread betting.
    config = SlateConfig(ladder_tolerance=0.02, league="nfl")
    assert not _ladder_gate_blocks(projection, "spread", "home", -6.0, "bookA", config)
    assert _ladder_gate_blocks(projection, "spread", "home", -6.0, "polymarket", config)


def test_the_gate_is_off_unless_configured(projection) -> None:
    # Default config leaves every rung alone, so nothing that worked before
    # this phase changes behaviour.
    assert not _ladder_gate_blocks(projection, "spread", "home", -6.0, "kalshi", SlateConfig())
    # A league is required too: without one there is no table to consult.
    no_league = SlateConfig(ladder_tolerance=0.02)
    assert not _ladder_gate_blocks(projection, "spread", "home", -6.0, "kalshi", no_league)


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

    They must stay wider than the market-anchored residual (15.5) that first
    drew attention to them: a sim's dispersion answers to its *own* residuals,
    and the market is far sharper than the model. Calibrating to the market
    would shrink these ~15% and make the sim overconfident
    (docs/MODEL_LAB.md "NCAAF Round 3").
    """
    from velocity.models.simulate import NCAAF_SD_MARGIN, NCAAF_SD_TOTAL

    assert pytest.approx(18.2) == NCAAF_SD_MARGIN
    assert pytest.approx(16.7) == NCAAF_SD_TOTAL
    # Comfortably above the market-residual figure, and above the 17.0/16.0
    # the sim shipped with, which every measured season exceeded.
    assert NCAAF_SD_MARGIN > 17.0
    assert NCAAF_SD_TOTAL > 16.0
