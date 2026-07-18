"""NFL game model — expected points, pricing sanity, and a golden projection.

The golden test pins the model's output on the frozen fixture at a fixed seed
and config. Any change to ratings, the scoring model, or the sim will move these
numbers and fail the test — forcing every projection change to be a conscious,
reviewed diff rather than a silent drift.
"""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.features.team import fit_ratings
from velocity.models.game_nfl import NFLGameModel, NFLModelConfig
from velocity.models.simulate import SimConfig
from velocity.util.seed import make_rng


@pytest.fixture
def model(plays: pd.DataFrame) -> NFLGameModel:
    ratings = fit_ratings(plays)
    # Smaller sim for fast, still-stable tests; golden values are pinned to it.
    return NFLGameModel(ratings, NFLModelConfig(sim=SimConfig(n_sims=20_000)))


def test_stronger_team_favored_at_home(model: NFLGameModel) -> None:
    proj = model.project("KC", "CAR", rng=make_rng())
    assert proj.mu_home > proj.mu_away
    assert proj.p_home_win() > 0.5
    assert proj.fair_spread() < 0  # home favored → negative spread


def test_home_field_advantage_applied(model: NFLGameModel) -> None:
    home = model.expected_points("KC", "DET", neutral_site=False)
    neutral = model.expected_points("KC", "DET", neutral_site=True)
    hfa = model.config.hfa_points
    assert home[0] - neutral[0] == pytest.approx(hfa / 2.0)
    assert neutral[1] - home[1] == pytest.approx(hfa / 2.0)
    # HFA shifts the margin but leaves the total unchanged.
    assert sum(home) == pytest.approx(sum(neutral))


def test_neutral_site_is_antisymmetric(model: NFLGameModel) -> None:
    a = model.expected_points("KC", "CAR", neutral_site=True)
    b = model.expected_points("CAR", "KC", neutral_site=True)
    assert (a[0] - a[1]) == pytest.approx(-(b[0] - b[1]))


def test_outputs_are_bounded_and_sane(model: NFLGameModel) -> None:
    proj = model.project("DET", "JAX", rng=make_rng())
    assert 0.0 <= proj.p_home_win() <= 1.0
    assert proj.p_home_win() + proj.p_away_win() == pytest.approx(1.0)
    assert proj.fair_total() > 0
    assert 0.0 < proj.mu_home < 70.0
    assert 0.0 < proj.mu_away < 70.0


def test_fair_lines_are_near_coin_flips(model: NFLGameModel) -> None:
    proj = model.project("BUF", "CHI", rng=make_rng())
    assert proj.prob_home_cover(proj.fair_spread()) == pytest.approx(0.5, abs=0.04)
    assert proj.prob_over(proj.fair_total()) == pytest.approx(0.5, abs=0.04)


def test_projection_is_reproducible(model: NFLGameModel) -> None:
    a = model.project("KC", "SF", rng=make_rng())
    b = model.project("KC", "SF", rng=make_rng())
    assert a.p_home_win() == b.p_home_win()
    assert a.fair_spread() == b.fair_spread()
    assert a.fair_total() == b.fair_total()


def test_golden_projection(model: NFLGameModel) -> None:
    """Pinned output for KC (home) vs DET on the frozen fixture + seed 1729."""
    proj = model.project("KC", "DET", rng=make_rng())
    assert proj.mu_home == pytest.approx(33.923641, abs=1e-4)
    assert proj.mu_away == pytest.approx(22.900267, abs=1e-4)
    assert proj.p_home_win() == pytest.approx(0.803925, abs=1e-6)
    assert proj.fair_spread() == pytest.approx(-11.0, abs=1e-9)
    assert proj.fair_total() == pytest.approx(57.0, abs=1e-9)
