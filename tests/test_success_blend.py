"""SP+'s efficiency and explosiveness, blended beside the EPA fit.

`success` and `down` have been banked since Round 1 and never scored. The
blend keeps the passer decomposition on the EPA side and moves only the
QB-neutral team components, so the QB is priced exactly once.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.features.team import (
    QBTeamRatings,
    TeamRatings,
    blend_team_components,
    epa_per_success,
)


def _plays() -> pd.DataFrame:
    # Four successes at +1.0 EPA, six failures at −0.5: a unit of success
    # rate is worth 1.5 EPA/play here.
    return pd.DataFrame({
        "success": [1, 1, 1, 1, 0, 0, 0, 0, 0, 0],
        "epa": [1.0, 1.0, 1.0, 1.0, -0.5, -0.5, -0.5, -0.5, -0.5, -0.5],
    })


def _qb() -> QBTeamRatings:
    return QBTeamRatings(
        offense={"A": 0.10, "B": -0.10}, defense={"A": 0.02, "B": -0.02},
        qb={"qbA": 0.05}, starters={"A": "qbA"}, pass_rate={"A": 0.6, "B": 0.55},
        league_epa=0.0, ridge_lambda=300.0, qb_lambda=300.0, n_plays=1000,
        teams=("A", "B"),
    )


def _extra(scale_hint: float = 1.0) -> TeamRatings:
    # Success-rate deviations: A +0.04, B −0.04 (a 4-point success-rate edge).
    return TeamRatings(offense={"A": 0.04, "B": -0.04}, defense={"A": 0.0, "B": 0.0},
                       league_epa=0.43, ridge_lambda=200.0, n_plays=1000, teams=("A", "B"))


def test_epa_per_success_is_the_gap_between_a_success_and_a_failure() -> None:
    assert epa_per_success(_plays()) == pytest.approx(1.5)
    # Weighted: weight the failures away and the gap is still the gap.
    w = pd.Series([1.0] * 4 + [0.5] * 6)
    assert epa_per_success(_plays(), weights=w) == pytest.approx(1.5)
    # A frame with only one class has no gap to measure.
    assert epa_per_success(_plays().iloc[:4]) == 0.0
    assert epa_per_success(_plays().iloc[:0]) == 0.0
    # NaNs in either column are dropped rather than poisoning the means.
    dirty = _plays().assign(epa=[np.nan] + [1.0] * 3 + [-0.5] * 6)
    assert epa_per_success(dirty) == pytest.approx(1.5)


def test_the_blend_moves_the_team_components_and_nothing_else() -> None:
    qb = _qb()
    out = blend_team_components(qb, _extra(), weight=0.25, scale=1.5)
    # 0.75·0.10 + 0.25·1.5·0.04 = 0.075 + 0.015
    assert out.offense["A"] == pytest.approx(0.09)
    assert out.offense["B"] == pytest.approx(-0.09)
    # Defense blends toward a zero extra: shrinks by the kept share.
    assert out.defense["A"] == pytest.approx(0.015)
    # The passer side is untouched — priced once, on the EPA fit.
    assert out.qb == qb.qb and out.starters == qb.starters and out.pass_rate == qb.pass_rate
    assert out.league_epa == qb.league_epa and out.teams == qb.teams


def test_weight_zero_is_the_same_ratings_and_the_bounds_are_enforced() -> None:
    qb = _qb()
    assert blend_team_components(qb, _extra(), weight=0.0) is qb
    with pytest.raises(ValueError):
        blend_team_components(qb, _extra(), weight=1.5)
    with pytest.raises(ValueError):
        blend_team_components(qb, _extra(), weight=-0.1)


def test_a_team_missing_from_the_extra_ratings_is_league_average_there() -> None:
    """Matches matchup_delta's own fallback: an unknown team contributes 0."""
    qb = _qb()
    partial = TeamRatings(offense={"A": 0.04}, defense={"A": 0.0}, league_epa=0.43,
                          ridge_lambda=200.0, n_plays=500, teams=("A",))
    out = blend_team_components(qb, partial, weight=0.5, scale=2.0)
    assert out.offense["A"] == pytest.approx(0.5 * 0.10 + 0.5 * 2.0 * 0.04)
    assert out.offense["B"] == pytest.approx(0.5 * -0.10)
    assert set(out.offense) == {"A", "B"}


def test_the_blend_prices_through_matchup_delta_as_a_weighted_delta() -> None:
    """Linear in the dicts, so the blended delta is the blend of the deltas."""
    qb = _qb()
    extra = _extra()
    out = blend_team_components(qb, extra, weight=0.4, scale=1.5)
    epa_delta = qb.matchup_delta("A", "B")
    extra_delta = 1.5 * (extra.offense["A"] + extra.defense["B"])
    qb_part = qb.qb["qbA"] * qb.pass_rate["A"]  # the passer, unchanged by the blend
    expected = 0.6 * (epa_delta - qb_part) + 0.4 * extra_delta + qb_part
    assert out.matchup_delta("A", "B") == pytest.approx(expected)
