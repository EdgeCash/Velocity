"""A DK kicker projection — the Showdown board's missing position.

The sibling of ``test_dfs_dst.py``, and the bug is the same one position over:
``DK_POINTS_PER_STAT`` carries no kicking weight and the correlated prop sim
has no kicker markets, so a kicker collapsed to 0.00 expected points and the
optimizer took whichever was cheapest. The FantasyPros stat-key census is what
surfaced it — ``fg`` / ``fga`` / ``xpt`` were the only keys in the feed nothing
read.

Everything here is pinned against the banked kicks (2020-2025) rather than
against DK's rulebook alone: the distance mix that converts a projected total
into DK points, and the underdispersion that makes a Poisson the wrong shape.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.dfs.kicker import (
    FG_ATTEMPT_P,
    FG_BAND_POINTS,
    FG_BAND_SHARE,
    LEAGUE_FG_RATE,
    POINTS_PER_MADE_FG,
    kicker_expected_points,
    kicker_samples,
    project_kickers,
)


def _fp() -> pd.DataFrame:
    rows = [
        # A starter with attempts served: rate comes from his own projection.
        ("k1", "Harrison Butker", "KC", "K", "fg", 1.70),
        ("k1", "Harrison Butker", "KC", "K", "fga", 1.97),
        ("k1", "Harrison Butker", "KC", "K", "xpt", 2.34),
        # Attempts missing: falls back to the league make rate.
        ("k2", "No Attempts", "BUF", "K", "fg", 1.50),
        ("k2", "No Attempts", "BUF", "K", "xpt", 2.00),
        # Projected for nothing — must be skipped, not priced at zero.
        ("k3", "Inactive", "NYJ", "K", "fg", 0.0),
        # A skill player, to prove the module only claims kickers.
        ("wr", "A Receiver", "KC", "WR", "rec_rec", 5.0),
    ]
    return pd.DataFrame(
        rows, columns=["player_id", "player_name", "team", "position", "stat", "value"]
    )


def test_the_distance_mix_is_what_converts_a_projected_total() -> None:
    """DK pays 3/4/5 by band; FantasyPros projects one number.

    Scoring every make at 3.0 would under-price a kicker by ~17%, and
    under-price the long-range ones most — exactly the captain plays.
    """
    assert sum(FG_BAND_SHARE) == pytest.approx(1.0, abs=1e-3)
    assert pytest.approx(3.612, abs=0.005) == POINTS_PER_MADE_FG
    assert FG_BAND_POINTS[0] < POINTS_PER_MADE_FG, "a make is worth more than 3.0"


def test_expected_points_reproduce_the_banked_kicker_game() -> None:
    """Banked DK kicker scoring is 8.366 points per active game.

    Butker's line here is the league average one (1.70 made, 2.34 extra
    points), so his expectation has to land on it.
    """
    points = kicker_expected_points(_fp()).set_index("player_name")["points"]
    assert float(points["Harrison Butker"]) == pytest.approx(8.37, abs=0.15)


def test_a_kicker_projected_for_nothing_is_skipped_not_priced_at_zero() -> None:
    """A zero is a claim, and priced-at-zero is the bug this module fixes.

    It also guards a subtler version: a pivot leaves NaN where a kicker
    carried no such stat, and ``value or 0.0`` passes NaN straight through
    because NaN is truthy — so the skip never fired and the kicker priced at
    NaN instead.
    """
    out = kicker_expected_points(_fp())
    assert "Inactive" not in set(out["player_name"])
    assert out["points"].notna().all()


def test_only_kickers_are_claimed() -> None:
    out = kicker_expected_points(_fp())
    assert set(out["position"]) == {"K"}
    assert "A Receiver" not in set(out["player_name"])


def test_the_make_rate_comes_from_the_projection_when_attempts_are_served() -> None:
    by_name = {p.name: p for p in project_kickers(_fp())}
    assert by_name["Harrison Butker"].fg_rate == pytest.approx(1.70 / 1.97, abs=1e-6)
    assert by_name["Harrison Butker"].rate_source == "projection"
    # No attempts served: the league rate, never an invented perfect kicker.
    assert by_name["No Attempts"].fg_rate == pytest.approx(LEAGUE_FG_RATE)
    assert by_name["No Attempts"].rate_source == "league"
    assert by_name["No Attempts"].fg_attempts > by_name["No Attempts"].fg_made


def test_samples_recover_the_expectation() -> None:
    samples = kicker_samples(_fp(), np.random.default_rng(11), n_sims=200_000)
    expected = kicker_expected_points(_fp()).set_index("player_name")["points"]
    for name in ("Harrison Butker", "No Attempts"):
        assert np.mean(samples[name]) == pytest.approx(float(expected[name]), rel=0.02)


def test_kicking_counts_are_underdispersed_so_a_poisson_is_wrong() -> None:
    """Within player-season the banked attempts run variance/mean 0.80.

    A kicker's workload is bounded and regular in a way a receiver's targets
    are not, so a Poisson (1.0) would price both tails far too wide. The
    binomial's trial probability is read straight off that measurement.
    """
    assert pytest.approx(1.0 - 0.8027, abs=1e-3) == FG_ATTEMPT_P
    rng = np.random.default_rng(5)
    from velocity.dfs.kicker import _binomial_count

    draws = _binomial_count(1.974, FG_ATTEMPT_P, rng, 200_000)
    assert float(np.var(draws) / np.mean(draws)) == pytest.approx(0.80, abs=0.03)
    assert np.mean(draws) == pytest.approx(1.974, rel=0.02)


def test_makes_never_exceed_attempts_and_points_are_whole() -> None:
    """Structural, so it has to hold in every draw rather than on average."""
    samples = kicker_samples(_fp(), np.random.default_rng(3), n_sims=50_000)
    points = samples["Harrison Butker"]
    assert np.all(points >= 0)
    assert np.array_equal(points, np.round(points)), "DK kicking pays whole points"


def test_the_band_draw_leaves_a_captain_tail() -> None:
    """Three makes all landing 50+ scores 15, not the 10.8 the mean implies.

    Collapsing the bands into their average would erase exactly the upside
    that makes a kicker worth a captain slot.
    """
    samples = kicker_samples(_fp(), np.random.default_rng(7), n_sims=200_000)
    points = samples["Harrison Butker"]
    assert float(np.percentile(points, 99)) >= 18.0
    assert points.max() > 25.0


def test_no_kickers_is_an_empty_frame_not_a_crash() -> None:
    fp = pd.DataFrame(
        [("wr", "A Receiver", "KC", "WR", "rec_rec", 5.0)],
        columns=["player_id", "player_name", "team", "position", "stat", "value"],
    )
    assert kicker_expected_points(fp).empty
    assert kicker_samples(fp, np.random.default_rng(1), n_sims=10) == {}
    assert kicker_expected_points(pd.DataFrame()).empty
