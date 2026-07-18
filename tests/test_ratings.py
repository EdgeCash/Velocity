"""Opponent-adjusted ratings — recovery, determinism, and shrinkage.

The fixture is generated from *known* true team strengths under the same model
the fitter assumes (see ``tests/fixtures/_generate.py``), so the sharpest test is
whether the fit recovers that truth. Offense is well-identified and must recover
strongly; defense carries a weaker signal (smaller true spread, more shrinkage)
and is tested with an honest, looser bar.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tests.fixtures._generate import LEAGUE_MEAN_EPA, TRUE_STRENGTHS
from velocity.features.team import fit_ratings


def _spearman(a: list[float], b: list[float]) -> float:
    ra = pd.Series(a).rank()
    rb = pd.Series(b).rank()
    return float(np.corrcoef(ra, rb)[0, 1])


def test_recovers_offense_ranking(plays: pd.DataFrame) -> None:
    ratings = fit_ratings(plays)
    teams = list(TRUE_STRENGTHS)
    true_off = [TRUE_STRENGTHS[t][0] for t in teams]
    fit_off = [ratings.offense[t] for t in teams]
    assert _spearman(true_off, fit_off) >= 0.9
    # The extremes must be correctly placed, not just correlated on average.
    assert max(ratings.offense, key=ratings.offense.get) == "KC"
    assert min(ratings.offense, key=ratings.offense.get) == "CAR"


def test_recovers_defense_signal(plays: pd.DataFrame) -> None:
    ratings = fit_ratings(plays)
    teams = list(TRUE_STRENGTHS)
    true_def = [TRUE_STRENGTHS[t][1] for t in teams]
    fit_def = [ratings.defense[t] for t in teams]
    # Defense is genuinely harder to estimate — assert a real but weaker signal.
    assert _spearman(true_def, fit_def) >= 0.5


def test_intercept_matches_league_mean(plays: pd.DataFrame) -> None:
    ratings = fit_ratings(plays)
    assert ratings.league_epa == pytest.approx(LEAGUE_MEAN_EPA, abs=0.02)


def test_fit_is_deterministic(plays: pd.DataFrame) -> None:
    a = fit_ratings(plays)
    b = fit_ratings(plays)
    assert a.offense == b.offense
    assert a.defense == b.defense
    assert a.league_epa == b.league_epa


def test_stronger_ridge_shrinks_ratings(plays: pd.DataFrame) -> None:
    teams = list(TRUE_STRENGTHS)
    spreads = [
        float(np.std([fit_ratings(plays, ridge_lambda=lam).offense[t] for t in teams]))
        for lam in (50.0, 200.0, 800.0)
    ]
    # More shrinkage → ratings pulled toward the league mean → smaller spread.
    assert spreads[0] > spreads[1] > spreads[2]


def test_matchup_delta_excludes_intercept(plays: pd.DataFrame) -> None:
    ratings = fit_ratings(plays)
    delta = ratings.matchup_delta("KC", "CAR")
    expected = ratings.expected_epa("KC", "CAR")
    assert expected == pytest.approx(ratings.league_epa + delta)
    # KC offense vs a weak defense should be an above-average matchup.
    assert delta > 0


def test_empty_plays_raise(plays: pd.DataFrame) -> None:
    empty = plays.iloc[0:0]
    with pytest.raises(ValueError, match="no usable plays"):
        fit_ratings(empty)


def test_nonpositive_lambda_rejected(plays: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="ridge_lambda"):
        fit_ratings(plays, ridge_lambda=0.0)
