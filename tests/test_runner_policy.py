"""Live-runner wagering policy — the defaults the CLI ships with.

The knobs live in ``SlateConfig`` and are behavior-tested elsewhere; what CI
must pin is the *policy* the runner resolves when nobody passes a flag: NFL
market anchoring on at the Round-3 select-chosen weight, every other league
on the raw model until its own lab argues otherwise (docs/MODEL_LAB.md).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "scripts" / "run_live_slate.py"


def _runner():
    spec = importlib.util.spec_from_file_location("run_live_slate", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_model_weight_resolves_per_league() -> None:
    runner = _runner()
    # Unset → the league policy: NFL anchors at 0.2, everyone else raw.
    assert runner.resolve_model_weight(None, "nfl") == 0.2
    for league in ("ncaaf", "mlb", "wnba", "ncaab", "nhl"):
        assert runner.resolve_model_weight(None, league) == 1.0
    # An explicit flag always wins, 1.0 (raw) included.
    assert runner.resolve_model_weight(0.5, "nfl") == 0.5
    assert runner.resolve_model_weight(1.0, "nfl") == 1.0


def test_cli_default_leaves_weight_to_league_policy() -> None:
    args = _runner().build_parser().parse_args(["--league", "nfl"])
    assert args.model_weight is None  # sentinel — resolved by league, not argparse
    assert args.min_edge == 0.02
    # The 2025 extension left ≥4 pts of totals disagreement at break-even
    # (52.3% on 5,657) while ≥6 still clears (53.0%) — the default moved.
    assert args.ncaaf_total_edge == 6.0


def test_prop_min_edge_defaults_to_double_the_game_bar() -> None:
    runner = _runner()
    # Unset → 2× the game threshold (wider for the noisiest markets,
    # DESIGN §6.2); explicit always wins; a zero game bar stays zero.
    assert runner.resolve_prop_min_edge(None, 0.02) == 0.04
    assert runner.resolve_prop_min_edge(None, 0.0) == 0.0
    assert runner.resolve_prop_min_edge(0.03, 0.02) == 0.03


def test_market_edge_pairs_parse_exactly() -> None:
    import pytest

    runner = _runner()
    assert runner.parse_market_edges([]) == {}
    parsed = runner.parse_market_edges(["total=0.03", " pass_yards =0.05"])
    assert parsed == {"total": 0.03, "pass_yards": 0.05}
    with pytest.raises(SystemExit):
        runner.parse_market_edges(["total"])  # no '='
    with pytest.raises(SystemExit):
        runner.parse_market_edges(["total=lots"])  # not a number


def test_ncaaf_base_points_tracks_the_current_scoring_regime() -> None:
    import numpy as np
    import pandas as pd

    runner = _runner()
    games = pd.DataFrame({
        # Old regime 60-point totals, current regime 50 — the trailing two
        # seasons (2024–25) set the level; 2015 must not drag it up.
        "season": [2015, 2015, 2024, 2025],
        "home_score": [35, 33, 27, 24],
        "away_score": [25, 27, 23, 26],
    })
    assert runner.ncaaf_base_points(games) == 25.0  # (50+50)/2 totals → 25/team
    empty = games.assign(home_score=np.nan, away_score=np.nan)
    assert runner.ncaaf_base_points(empty) == 28.5  # degenerate → old constant


def test_ncaaf_spreads_sit_out_by_default() -> None:
    args = _runner().build_parser().parse_args(["--league", "ncaaf"])
    # The backtested college posture: no ATS edge at any threshold, so
    # spreads are off unless deliberately re-enabled.
    assert args.ncaaf_spreads is False
    on = _runner().build_parser().parse_args(["--league", "ncaaf", "--ncaaf-spreads"])
    assert on.ncaaf_spreads is True
