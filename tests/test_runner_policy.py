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
    # NCAAF joined the anchor: the ≥6-point totals filter claims ~0.14 of
    # edge at sd 16.7 while the backtest realizes ~0.03; 0.2 maps one onto
    # the other and the points filter stays the selector.
    assert runner.resolve_model_weight(None, "ncaaf") == 0.2
    for league in ("mlb", "wnba", "ncaab", "nhl"):
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


def test_ncaaf_moneylines_sit_out_by_default() -> None:
    # Never backtested, and 60% of the first live card's solo-Kelly exposure
    # (docs/STRATEGY_REVIEW.md §1.2). Off until the backtest says otherwise.
    args = _runner().build_parser().parse_args(["--league", "ncaaf"])
    assert args.ncaaf_moneylines is False
    on = _runner().build_parser().parse_args(["--league", "ncaaf", "--ncaaf-moneylines"])
    assert on.ncaaf_moneylines is True


def test_paper_posture_resolves_per_league() -> None:
    runner = _runner()
    # The content + CLV leagues stake nothing; football stakes.
    for league in ("ncaab", "nhl", "wnba"):
        assert runner.resolve_paper(None, league) is True
    for league in ("nfl", "ncaaf", "mlb"):
        assert runner.resolve_paper(None, league) is False
    # The flag wins either way.
    assert runner.resolve_paper(True, "nfl") is True
    assert runner.resolve_paper(False, "nhl") is False
    # Team totals are paper on a staking league until their gate is calibrated.
    args = runner.build_parser().parse_args(["--league", "nfl"])
    assert runner.resolve_paper_markets(args) == frozenset({"team_total_home", "team_total_away"})
    args = runner.build_parser().parse_args(["--league", "nfl", "--no-team-totals-paper"])
    assert runner.resolve_paper_markets(args) == frozenset()
    args = runner.build_parser().parse_args(["--league", "nhl"])
    assert "__all__" in runner.resolve_paper_markets(args)


def test_edge_ceilings_ship_on() -> None:
    # The adverse-selection guard where the money is: 0.12 absolute (the
    # publish gate's ceiling) and 50% of the fair probability.
    args = _runner().build_parser().parse_args(["--league", "nfl"])
    assert args.max_edge == 0.12
    assert args.max_relative_edge == 0.50


def test_the_live_config_block_describes_the_run_not_a_hand_table() -> None:
    runner = _runner()
    args = runner.build_parser().parse_args(["--league", "ncaaf"])
    rows = dict(runner.live_config_rows(args, "EPA×scores blend", None))
    assert rows["Ratings"] == "EPA×scores blend"
    assert "0.2" in rows["Market anchoring"]
    assert "≥ 6" in rows["Selectivity"] and "moneylines sitting out" in rows["Selectivity"]
    assert "0.12 absolute" in rows["Edge ceilings"] and "50%" in rows["Edge ceilings"]
    assert rows["Paper"] == "team totals"
    nhl = runner.build_parser().parse_args(["--league", "nhl"])
    nhl_rows = dict(runner.live_config_rows(nhl, "goalie decomposition", None))
    assert "every market" in nhl_rows["Paper"]


def test_the_sim_and_level_defaults_are_the_gated_ones() -> None:
    """The M1 round's promotions (docs/MODEL_LAB.md): flags override, defaults pin."""
    runner = _runner()
    args = runner.build_parser().parse_args(["--league", "nfl"])
    assert args.nfl_level is None and args.sim_shape is None and args.sim_dispersion is None
    assert runner.resolve_nfl_level(None) == runner.DEFAULT_NFL_LEVEL == "fit"
    assert runner.NFL_LEVEL_SEASONS == 2
    assert runner.resolve_nfl_level("constant") == "constant"
    for league in ("nfl", "ncaaf"):
        assert runner.resolve_sim_shape(None, league) == runner.DEFAULT_SIM_SHAPE_BY_LEAGUE[league]
        assert (runner.resolve_sim_dispersion(None, league)
                == runner.DEFAULT_SIM_DISPERSION_BY_LEAGUE[league])
    assert runner.resolve_sim_shape("empirical", "nfl") == "empirical"
    assert runner.resolve_sim_shape(None, "mlb") == "normal"
    # The Methods row says what the sim did, in the run's own words.
    rows = dict(runner.live_config_rows(args, "QB-adjusted recency EPA", None))
    assert "Simulation" in rows and "sims" in rows["Simulation"]
    assert "σ 13 margin / 13.6 total" in rows["Simulation"]


def test_an_empirical_sim_without_a_bank_falls_back_to_the_normal(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from velocity.models import residuals

    monkeypatch.setattr(residuals, "DATASETS", tmp_path)
    runner = _runner()
    args = runner.build_parser().parse_args(["--league", "ncaaf", "--sim-shape", "empirical"])
    cfg = runner.football_sim_config("ncaaf", args)
    assert cfg.residuals is None and cfg.sd_margin == 18.2
    assert runner.describe_sim(cfg, "ncaaf").startswith("normal")
