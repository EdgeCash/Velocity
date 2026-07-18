"""NCAAF game model — pace, prior shrinkage, and a golden projection.

Uses the shared plays fixture for the ratings machinery; the college-specific
behavior under test is pace-scaling and preseason-prior shrinkage.
"""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.features.team import fit_ratings, matchup_pace, team_pace
from velocity.models.game_ncaaf import NCAAFGameModel, NCAAFModelConfig
from velocity.models.simulate import SimConfig
from velocity.util.seed import make_rng

_SIM = SimConfig(sd_margin=16.0, sd_total=12.0, n_sims=20_000)


@pytest.fixture
def ratings_and_pace(plays: pd.DataFrame):
    return fit_ratings(plays), team_pace(plays)


def test_team_pace_counts_plays_per_game(plays: pd.DataFrame) -> None:
    pace = team_pace(plays)
    # The fixture has 40 offensive plays per team per game.
    assert pace["KC"] == pytest.approx(40.0)


def test_matchup_pace_averages_and_falls_back() -> None:
    pace = {"A": 80.0, "B": 60.0}
    assert matchup_pace(pace, "A", "B", 70.0) == pytest.approx(70.0)
    assert matchup_pace(pace, "A", "UNKNOWN", 70.0) == pytest.approx(75.0)  # 80 & league 70


def test_priors_regress_early_season_toward_even(ratings_and_pace) -> None:
    ratings, pace = ratings_and_pace
    cfg = NCAAFModelConfig(sim=_SIM)
    priors = pd.Series(dict.fromkeys(ratings.teams, 0.0))
    week2 = pd.Series(dict.fromkeys(ratings.teams, 2.0))
    early = NCAAFGameModel(ratings, pace, cfg, priors=priors, games_played=week2)
    late = NCAAFGameModel(ratings, pace, cfg)  # no priors → full data

    early_spread = abs(early.project("KC", "CAR", rng=make_rng()).fair_spread())
    late_spread = abs(late.project("KC", "CAR", rng=make_rng()).fair_spread())
    # With flat priors and only two games, the strong team's edge is pulled in.
    assert early_spread < late_spread


def test_hfa_and_neutral_site(ratings_and_pace) -> None:
    ratings, pace = ratings_and_pace
    model = NCAAFGameModel(ratings, pace, NCAAFModelConfig(sim=_SIM))
    home = model.expected_points("KC", "CAR", neutral_site=False)
    neutral = model.expected_points("KC", "CAR", neutral_site=True)
    assert home[0] - neutral[0] == pytest.approx(model.config.hfa_points / 2.0)
    a = model.expected_points("KC", "CAR", neutral_site=True)
    b = model.expected_points("CAR", "KC", neutral_site=True)
    assert (a[0] - a[1]) == pytest.approx(-(b[0] - b[1]))


def test_outputs_bounded(ratings_and_pace) -> None:
    ratings, pace = ratings_and_pace
    model = NCAAFGameModel(ratings, pace, NCAAFModelConfig(sim=_SIM))
    proj = model.project("DET", "JAX", rng=make_rng())
    assert 0.0 <= proj.p_home_win() <= 1.0
    assert proj.fair_total() > 0
    assert 0.0 < proj.mu_home < 90.0  # college scores run higher, still bounded


def test_golden_projection(ratings_and_pace) -> None:
    """Pinned full-data projection for KC vs CAR at seed 1729."""
    ratings, pace = ratings_and_pace
    model = NCAAFGameModel(ratings, pace, NCAAFModelConfig(sim=_SIM))
    proj = model.project("KC", "CAR", rng=make_rng())
    assert proj.mu_home == pytest.approx(32.77677, abs=1e-4)
    assert proj.mu_away == pytest.approx(18.479019, abs=1e-4)
    assert proj.p_home_win() == pytest.approx(0.813775, abs=1e-6)
    assert proj.fair_spread() == pytest.approx(-15.0, abs=1e-9)
    assert proj.fair_total() == pytest.approx(51.0, abs=1e-9)
