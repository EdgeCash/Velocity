"""props.csv — the staked prop board with the simulation's shape behind it."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.export.props import (
    PROPS_COLUMNS,
    build_props,
    prop_distribution_frame,
)


class _Sim:
    """The prop-sim surface the export duck-types on."""

    def __init__(self, samples: dict[tuple[str, str], np.ndarray]) -> None:
        self.samples = samples

    def has(self, player: str, market: str) -> bool:
        return (player, market) in self.samples

    def player_samples(self, player: str, market: str) -> np.ndarray:
        return self.samples[(player, market)]


def _sim() -> _Sim:
    rng = np.random.default_rng(5)
    return _Sim({
        ("mahomes-p", "pass_yds"): rng.normal(280.0, 55.0, 30_000),
        ("kelce-t", "rec_yds"): rng.normal(70.0, 30.0, 30_000),
    })


def _roster() -> pd.DataFrame:
    return pd.DataFrame([
        {"player_key": "mahomes-p", "player_name": "Patrick Mahomes",
         "position": "QB", "team": "KC"},
        {"player_key": "kelce-t", "player_name": "Travis Kelce",
         "position": "TE", "team": "KC"},
    ])


def test_distribution_frame_carries_the_sims_own_quantiles() -> None:
    sim = _sim()
    frame = prop_distribution_frame({"g1": sim}, _roster()).set_index("player")
    row = frame.loc["Patrick Mahomes"]
    samples = sim.player_samples("mahomes-p", "pass_yds")
    assert row["projection"] == pytest.approx(float(np.mean(samples)), abs=1e-3)
    assert row["p90"] == pytest.approx(float(np.percentile(samples, 90)), abs=1e-3)
    assert row["p99"] == pytest.approx(float(np.percentile(samples, 99)), abs=1e-3)
    # The quantiles have to be ordered, or something upstream is mislabelled.
    assert row["median"] < row["p75"] < row["p90"] < row["p99"]
    assert int(row["n_sims"]) == 30_000


def test_distribution_frame_builds_without_a_roster() -> None:
    frame = prop_distribution_frame({"g1": _sim()})
    assert len(frame) == 2
    assert (frame["team"] == "").all()
    assert set(frame["player_key"]) == {"mahomes-p", "kelce-t"}


def test_distribution_frame_skips_a_market_the_sim_does_not_price() -> None:
    class _Silent(_Sim):
        def has(self, player: str, market: str) -> bool:
            return False

    assert prop_distribution_frame({"g1": _Silent({("x", "y"): np.ones(5)})}).empty


def test_build_props_joins_the_distribution_by_folded_name() -> None:
    dist = prop_distribution_frame({"g1": _sim()}, _roster())
    props = pd.DataFrame([{
        "game_id": "g1", "player": "Patrick Mahomes", "market": "pass_yds",
        "side": "over", "point": 265.5, "book": "dk", "price": -115,
        "p_model": 0.58, "p_fair": 0.52, "edge": 0.06, "stake": 2.4, "note": None,
    }])
    row = build_props(props, dist).iloc[0]
    assert row["team"] == "KC"
    assert row["line"] == pytest.approx(265.5)
    assert row["hit_probability"] == pytest.approx(0.58)
    assert row["99th_percentile"] > row["90th_percentile"] > row["median"]
    # 52% de-vigged is roughly -108 in American terms.
    assert row["fair_price"] == pytest.approx(-108.0, abs=1.0)


def test_build_props_exports_without_a_distribution() -> None:
    props = pd.DataFrame([{
        "game_id": "g1", "player": "Someone Unmapped", "market": "rec_yds",
        "side": "under", "point": 44.5, "book": "fd", "price": -120,
        "p_model": 0.6, "p_fair": 0.55, "edge": 0.05, "stake": 1.0, "note": None,
    }])
    row = build_props(props, None).iloc[0]
    assert pd.isna(row["projection"]) and pd.isna(row["90th_percentile"])
    assert row["team"] == ""
    assert row["recommended_stake"] == pytest.approx(1.0)


def test_build_props_is_empty_but_shaped_on_nothing() -> None:
    out = build_props(None)
    expected = [c for c in PROPS_COLUMNS if c not in ("generated_at", "season", "week")]
    assert list(out.columns) == expected
    assert out.empty


def test_fair_price_abstains_on_an_impossible_probability() -> None:
    props = pd.DataFrame([{
        "game_id": "g1", "player": "X", "market": "m", "side": "over",
        "point": 1.5, "book": "dk", "price": -110, "p_model": 0.6,
        "p_fair": None, "edge": None, "stake": 0.0, "note": None,
    }])
    assert pd.isna(build_props(props).loc[0, "fair_price"])


def test_the_props_board_names_the_game_and_the_kickoff() -> None:
    """``team`` alone says GB; it does not say GB against whom, or when.

    The game_id rides on the distribution frame, so the board can be joined
    to the games frame for the two facts a prop row cannot state by itself.
    """
    props = pd.DataFrame([{
        "game_id": "g1", "player": "Matthew Golden", "market": "receptions",
        "side": "under", "point": 4.5, "edge": 0.0895, "stake": 1.35,
        "price": -143, "p_model": 0.6446, "p_fair": 0.5551,
    }])
    dist = pd.DataFrame([{
        "game_id": "g1", "player_key": "mg", "player": "Matthew Golden",
        "team": "GB", "position": "WR", "market": "receptions",
        "projection": 3.9, "median": 4.0, "p75": 5.0, "p90": 7.0,
        "p99": 10.0, "n_sims": 100,
    }])
    games = pd.DataFrame([{
        "game_id": "g1", "home_team": "Atlanta Falcons",
        "away_team": "Green Bay Packers", "kickoff": "2026-09-22T00:15:00Z",
    }])

    frame = build_props(props, dist, games)

    assert frame["matchup"].iloc[0] == "Green Bay Packers @ Atlanta Falcons"
    assert frame["kickoff"].iloc[0] == "2026-09-22T00:15:00Z"


def test_the_props_board_still_builds_with_no_games_frame() -> None:
    """The games frame is an enrichment, never a requirement.

    A run that banked props but no games frame exports the board it has, with
    the two cells it cannot fill left empty.
    """
    props = pd.DataFrame([{
        "game_id": "g1", "player": "Matthew Golden", "market": "receptions",
        "side": "under", "point": 4.5, "edge": 0.0895, "stake": 1.35,
        "price": -143, "p_model": 0.6446, "p_fair": 0.5551,
    }])

    frame = build_props(props, None, None)

    assert len(frame) == 1
    assert frame["matchup"].iloc[0] is None
    assert frame["kickoff"].iloc[0] is None
