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
    assert runner.resolve_model_weight(None, "ncaaf") == 0.13  # the S3 staking sweep
    # MLB joined in 2026-09. It had been raw with no shrink on the largest
    # exposure, against a lab that put it at parity with the close; 0.2 is a
    # holding position until the weight sweep runs on the private closing-
    # moneyline archive (docs/STRATEGY_REVIEW.md §3).
    assert runner.resolve_model_weight(None, "mlb") == 0.2
    # The paper leagues stake nothing, so an anchor would never reach money.
    for league in ("wnba", "ncaab", "nhl"):
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
    # Backtested in 2026-09 and it failed: raw -4.8% over 2,807 bets, -36% at
    # >= +1000, the model's Brier 0.217 against the market's 0.183. Not staked
    # — but papered rather than excluded now, so the record keeps accruing
    # (docs/STRATEGY_REVIEW.md S2, amended).
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
    assert "0.13" in rows["Market anchoring"]  # the S3 staking sweep's weight
    assert "≥ 6" in rows["Selectivity"] and "moneylines sitting out" in rows["Selectivity"]
    assert "0.12 absolute" in rows["Edge ceilings"] and "50%" in rows["Edge ceilings"]
    assert rows["Paper"] == "team totals"
    nhl = runner.build_parser().parse_args(["--league", "nhl"])
    nhl_rows = dict(runner.live_config_rows(nhl, "goalie decomposition", None))
    assert "every market" in nhl_rows["Paper"]


def test_the_staking_row_is_read_from_the_config_not_written_down() -> None:
    """The Methods block's whole claim is that it cannot drift from the code.

    This row was a string literal. It hardcoded the slate cap that
    ``--max-slate-fraction`` moves, so the page kept saying 25% however the
    run was invoked, and it never mentioned the same-game correlation
    de-scaling — the term that halves a stake when a game carries three
    bets, and the most common reason a play is sized below its own Kelly.
    """
    runner = _runner()
    from velocity.wagering.portfolio import PortfolioConfig
    from velocity.wagering.staking import StakingConfig

    args = runner.build_parser().parse_args(["--league", "nfl"])
    row = dict(runner.live_config_rows(args, "fit", None))["Staking"]
    # Every number in the row is the config's, and the de-scaling is named.
    assert f"{StakingConfig().max_bet_fraction:.0%} per bet" in row
    assert f"{PortfolioConfig().group_cap_fraction:.0%} per game" in row
    assert "\u03c1=0.5" in row and "de-scaled" in row

    # The slate cap tracks the flag rather than the sentence.
    tighter = runner.build_parser().parse_args(
        ["--league", "nfl", "--max-slate-fraction", "0.15"])
    assert "15% per slate" in dict(
        runner.live_config_rows(tighter, "fit", None))["Staking"]


def test_the_sim_and_level_defaults_are_the_gated_ones() -> None:
    """The M1 round's promotions (docs/MODEL_LAB.md): flags override, defaults pin."""
    runner = _runner()
    args = runner.build_parser().parse_args(["--league", "nfl"])
    assert args.nfl_level is None and args.sim_shape is None and args.sim_dispersion is None
    assert runner.resolve_nfl_level(None) == runner.DEFAULT_NFL_LEVEL == "fit"
    assert runner.NFL_LEVEL_SEASONS == 2
    assert runner.resolve_ncaaf_level(None) == runner.DEFAULT_NCAAF_LEVEL == "fit"
    assert runner.resolve_ncaaf_level("constant") == "constant"
    assert args.ncaaf_level is None
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


def test_exchange_venues_are_staked_and_bounded() -> None:
    """The exchanges are live, and the flag that held them back still works.

    They were papered while the E8 gate ran on an absolute tolerance that could
    not answer for a rung's own distance and price — S2's rule being that money
    does not follow a market whose evidence is not in yet. E8b is that evidence,
    so the money follows; the posture is still a flag rather than a deletion,
    because a venue that has to be pulled back should not need a code change.
    """
    runner = _runner()
    parser = runner.build_parser()

    # Exchanges off: no venue is papered, because none is priced.
    off = parser.parse_args(["--league", "nfl"])
    assert off.exchanges is False
    assert runner.resolve_paper_venues(off) == frozenset()

    # Exchanges on: priced *and* staked.
    on = parser.parse_args(["--league", "nfl", "--exchanges"])
    assert on.exchange_paper is False
    assert runner.resolve_paper_venues(on) == frozenset()

    # And the way back, which needs no code change.
    papered = parser.parse_args(["--league", "nfl", "--exchanges", "--exchange-paper"])
    assert runner.resolve_paper_venues(papered) == frozenset({"kalshi", "polymarket"})


def test_the_first_live_exchange_exposure_is_bounded() -> None:
    """A venue class with no live record does not get the whole slate.

    Every exchange row on the card shares one cap, because what is untested
    about Kalshi and Polymarket is the same sim, the same ladder and the same
    shape gate. The cap is a share of the slate cap rather than a second
    absolute number, so it moves when the slate does instead of drifting.
    """
    runner = _runner()
    args = runner.build_parser().parse_args(["--league", "nfl", "--exchanges"])
    assert args.exchange_slate_share == 0.25
    assert args.max_slate_fraction * args.exchange_slate_share < 0.07, (
        "a first live exposure should be a few percent of bankroll, not a quarter"
    )
    # The Methods block says so, rather than the site claiming a stale posture.
    rows = dict(runner.live_config_rows(args, "fit", None))
    assert "live" in rows["Exchange stakes"] and "25%" in rows["Exchange stakes"]
    papered = runner.build_parser().parse_args(
        ["--league", "nfl", "--exchanges", "--exchange-paper"])
    assert "staked at zero" in dict(runner.live_config_rows(papered, "fit", None))[
        "Exchange stakes"]
    # Nothing is claimed at all when the exchanges are not priced this run.
    plain = runner.build_parser().parse_args(["--league", "nfl"])
    assert "Exchange stakes" not in dict(runner.live_config_rows(plain, "fit", None))


def test_a_papered_venue_prices_but_never_stakes() -> None:
    """The rule itself, at the one point that decides whether money follows."""
    from velocity.wagering.slate import SlateConfig

    config = SlateConfig(paper_venues=frozenset({"kalshi", "polymarket"}))
    # A sportsbook row is untouched...
    assert config.paper_reason("total", 0.04, 0.51, "draftkings") is None
    # ...the same edge on an exchange is priced and graded, not staked.
    assert config.paper_reason("total", 0.04, 0.51, "kalshi") == "paper venue (kalshi)"
    assert config.paper_reason("spread", 0.04, 0.51, "Polymarket") == "paper venue (polymarket)"
    # A missing book cannot be judged by venue, and is not papered by accident.
    assert config.paper_reason("total", 0.04, 0.51, None) is None
    # The market rule still wins where both apply, so the reason stays the
    # most specific one the operator can act on.
    both = SlateConfig(paper_markets=frozenset({"total"}),
                       paper_venues=frozenset({"kalshi"}))
    assert both.paper_reason("total", 0.04, 0.51, "kalshi") == "paper market"
