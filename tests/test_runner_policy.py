"""Live-runner wagering policy — the defaults the CLI ships with.

The knobs live in ``SlateConfig`` and are behavior-tested elsewhere; what CI
must pin is the *policy* the runner resolves when nobody passes a flag: NFL
market anchoring on at the Round-3 select-chosen weight, every other league
on the raw model until its own lab argues otherwise (docs/MODEL_LAB.md).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

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
    # The totals filters resolve per league from the wager lab's cuts
    # (docs/OUTPUT_AUDIT.md §2.2): 4 points on the under alone in both
    # leagues; the flags override.
    assert args.ncaaf_total_edge is None and args.nfl_total_edge is None
    runner = _runner()
    assert runner.resolve_total_edge(args, "ncaaf") == 4.0
    assert runner.resolve_total_edge(args, "nfl") == 4.0
    assert runner.resolve_total_sides(args, "ncaaf") == frozenset({"under"})
    # Unders only in the NFL too since the level round's wager lab: overs at
    # 4+ read 49.8% on the new ledger against 56.2% for the unders.
    assert runner.resolve_total_sides(args, "nfl") == frozenset({"under"})
    custom = runner.build_parser().parse_args(
        ["--league", "ncaaf", "--ncaaf-total-edge", "6", "--ncaaf-total-sides", "over,under"])
    assert runner.resolve_total_edge(custom, "ncaaf") == 6.0
    assert runner.resolve_total_sides(custom, "ncaaf") == frozenset({"over", "under"})
    # Per-market anchoring: the fitted table, with MARKET=WEIGHT overrides.
    assert runner.DEFAULT_MODEL_WEIGHT_BY_MARKET == {
        "nfl": {"spread": 0.0, "total": 0.29, "moneyline": 0.0},
        "ncaaf": {"spread": 0.0, "total": 0.21, "moneyline": 0.0},
    }
    assert runner.resolve_model_weights_by_market([], "nfl")["total"] == 0.29
    assert runner.resolve_model_weights_by_market(["spread=0.1"], "nfl")["spread"] == 0.1
    assert runner.resolve_model_weights_by_market([], "mlb") == {}


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
    # The wager lab's college cut: 4 points on the under alone, the spread and
    # moneyline off the board at a zero weight, the total anchored at its fit.
    assert "≥ 4 pts of disagreement (under)" in rows["Selectivity"]
    assert "spread off the board" in rows["Selectivity"]
    assert "total anchored at 0.21" in rows["Selectivity"]
    assert "moneylines sitting out" in rows["Selectivity"]
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
    # The level round: the window and its shrink toward the two seasons.
    assert runner.NFL_LEVEL_WEEKS == 8 and runner.NFL_LEVEL_SHRINK_GAMES == 128.0
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
    # The promotion and tail rounds: the banked margin lattice is on in both
    # leagues — with its tail bin left alone, it opens every spread side on
    # the ladder gate in each.
    assert args.sim_keys is None
    for league in ("nfl", "ncaaf"):
        assert runner.resolve_sim_keys(None, league) == "lattice"
    assert runner.resolve_sim_keys("none", "nfl") == "none"
    assert runner.resolve_sim_keys(None, "mlb") == "none"
    # The skew switch exists in both leagues; its default is the re-test's.
    assert args.sim_skew is None
    for league in ("nfl", "ncaaf"):
        assert runner.resolve_sim_skew(None, league) == runner.DEFAULT_SIM_SKEW_BY_LEAGUE[league]
    assert runner.resolve_sim_skew("fit", "nfl") == "fit"
    assert runner.resolve_sim_skew(None, "mlb") == "none"
    # The Methods row says what the sim did, in the run's own words.
    rows = dict(runner.live_config_rows(args, "QB-adjusted recency EPA", None))
    assert "Simulation" in rows and "sims" in rows["Simulation"]
    assert "σ 13 margin / 13.6 total" in rows["Simulation"]


def test_the_default_football_sim_carries_the_banked_lattice() -> None:
    """docs/MODEL_LAB.md, the promotion round: normal draw, resampled by the lattice."""
    from velocity.models.keynumbers import load_lattice_weights

    runner = _runner()
    for league in ("nfl", "ncaaf"):
        banked = load_lattice_weights(league)
        if banked is None:
            pytest.skip(f"no {league} lattice committed")
        args = runner.build_parser().parse_args(["--league", league])
        cfg = runner.football_sim_config(league, args)
        assert cfg.residuals is None and cfg.lattice == banked
        assert "key numbers" in runner.describe_sim(cfg, league)
        # The switch still switches.
        off = runner.build_parser().parse_args(["--league", league, "--sim-keys", "none"])
        assert runner.football_sim_config(league, off).lattice is None


def test_the_totals_skew_is_fitted_on_the_bank_and_switches() -> None:
    from velocity.models.residuals import load_residual_frame
    from velocity.models.skew import fit_epsilon

    bank = load_residual_frame("nfl")
    if bank is None:
        pytest.skip("no NFL residual bank committed")
    runner = _runner()
    on = runner.build_parser().parse_args(["--league", "nfl", "--sim-skew", "fit"])
    cfg = runner.football_sim_config("nfl", on)
    assert cfg.total_skew == pytest.approx(fit_epsilon(bank["resid_total"].to_numpy()))
    assert 0.05 < cfg.total_skew < 0.4  # right-skewed, and by less than the close's +0.33
    assert "totals skew" in runner.describe_sim(cfg, "nfl")
    off = runner.build_parser().parse_args(["--league", "nfl", "--sim-skew", "none"])
    assert runner.football_sim_config("nfl", off).total_skew == 0.0
    # The pool carries its own skew, so the empirical path never stacks it.
    both = runner.build_parser().parse_args(
        ["--league", "nfl", "--sim-shape", "empirical", "--sim-skew", "fit"])
    assert runner.football_sim_config("nfl", both).total_skew == 0.0


def test_a_skew_without_a_bank_simulates_without_it(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    from velocity.models import residuals

    monkeypatch.setattr(residuals, "DATASETS", tmp_path)
    runner = _runner()
    args = runner.build_parser().parse_args(["--league", "nfl", "--sim-skew", "fit"])
    cfg = runner.football_sim_config("nfl", args)
    assert cfg.total_skew == 0.0
    assert "no residual bank" in capsys.readouterr().out


def test_a_lattice_without_a_bank_simulates_without_it(tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    from velocity.models import keynumbers

    monkeypatch.setattr(keynumbers, "DATASETS", tmp_path)
    runner = _runner()
    args = runner.build_parser().parse_args(["--league", "nfl", "--sim-keys", "lattice"])
    cfg = runner.football_sim_config("nfl", args)
    assert cfg.lattice is None
    assert "no margin lattice banked" in capsys.readouterr().out
    assert "key numbers" not in runner.describe_sim(cfg, "nfl")


def test_an_empirical_sim_never_stacks_the_lattice_on_the_pool() -> None:
    """The pool carries the league's own lattice; the config would refuse the pair."""
    from velocity.models.residuals import load_residual_pool

    if load_residual_pool("nfl") is None:
        pytest.skip("no NFL residual bank committed")
    runner = _runner()
    args = runner.build_parser().parse_args(
        ["--league", "nfl", "--sim-shape", "empirical", "--sim-keys", "lattice"])
    cfg = runner.football_sim_config("nfl", args)
    assert cfg.residuals is not None and cfg.lattice is None


def test_an_empirical_sim_without_a_bank_falls_back_to_the_normal(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from velocity.models import residuals

    monkeypatch.setattr(residuals, "DATASETS", tmp_path)
    runner = _runner()
    args = runner.build_parser().parse_args(["--league", "ncaaf", "--sim-shape", "empirical"])
    cfg = runner.football_sim_config("ncaaf", args)
    assert cfg.residuals is None and cfg.sd_margin == 16.2
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


def test_the_plays_and_scale_defaults_are_the_gated_ones() -> None:
    """The scrimmage filter and the scale are flags whose defaults the lab sets."""
    runner = _runner()
    args = runner.build_parser().parse_args(["--league", "nfl"])
    assert args.nfl_plays is None and args.ncaaf_plays is None
    assert args.nfl_scale is None and args.ncaaf_scale is None
    for league in ("nfl", "ncaaf"):
        assert runner.resolve_plays(None, league) == runner.DEFAULT_PLAYS_BY_LEAGUE[league]
        assert runner.resolve_scale(None, league) == runner.DEFAULT_SCALE_BY_LEAGUE[league]
        assert runner.resolve_plays("scrimmage", league) == "scrimmage"
        assert runner.resolve_scale("fit", league) == "fit"
        assert runner.resolve_scale("phase", league) == "phase"
    # The promotions as they stand (docs/MODEL_LAB.md): the scale on in both
    # leagues, college's fitted on the phase of the season being projected.
    assert runner.DEFAULT_SCALE_BY_LEAGUE == {"nfl": "fit", "ncaaf": "phase"}
    assert runner.DEFAULT_PLAYS_BY_LEAGUE == {"nfl": "all", "ncaaf": "all"}
    # Rain on NFL totals: a point a side at 0.25 in, off on request.
    assert args.nfl_precip_points is None
    assert runner.resolve_precip_points(None) == runner.DEFAULT_NFL_PRECIP_POINTS == 1.0
    assert runner.resolve_precip_points(0.0) == 0.0
    assert runner.resolve_precip_points(-2.0) == 0.0
    # The injury burden: 4 points a unit, off on request.
    assert args.nfl_injury_points is None
    assert runner.resolve_injury_points(None) == runner.DEFAULT_NFL_INJURY_POINTS == 4.0
    assert runner.resolve_injury_points(0.0) == 0.0
    assert runner.resolve_plays(None, "mlb") == "all"
    assert runner.resolve_scale(None, "mlb") == "off"
    # The joint phase ridge: a ridge the lab sets, 0 = the all-plays fit.
    assert args.nfl_phase_lambda is None
    assert runner.resolve_phase_lambda(None) == runner.DEFAULT_NFL_PHASE_LAMBDA
    assert runner.resolve_phase_lambda(1000.0) == 1000.0
    assert runner.resolve_phase_lambda(-5.0) == 0.0
    # The turnover-EPA shrink: a factor in [0, 1] the lab sets, 1 = as recorded.
    assert args.nfl_turnover_shrink is None
    assert runner.resolve_turnover_shrink(None) == runner.DEFAULT_NFL_TURNOVER_SHRINK == 0.5
    assert runner.resolve_turnover_shrink(0.5) == 0.5
    assert runner.resolve_turnover_shrink(3.0) == 1.0
    assert runner.resolve_turnover_shrink(-1.0) == 0.0
    # The college QB term: a QB ridge the lab sets, 0 keeps the team fit.
    assert args.ncaaf_qb_lambda is None
    assert runner.resolve_ncaaf_qb_lambda(None) == runner.DEFAULT_NCAAF_QB_LAMBDA
    assert runner.resolve_ncaaf_qb_lambda(300.0) == 300.0
    assert runner.resolve_ncaaf_qb_lambda(-1.0) == 0.0
    # Recency on the college EPA half: a half-life the lab sets, 0 = flat.
    assert args.ncaaf_epa_half_life is None
    assert runner.resolve_ncaaf_epa_half_life(None) == runner.DEFAULT_NCAAF_EPA_HALF_LIFE == 6.0
    assert runner.DEFAULT_NCAAF_QB_LAMBDA == 0.0
    assert runner.resolve_ncaaf_epa_half_life(8.0) == 8.0
    assert runner.resolve_ncaaf_epa_half_life(-3.0) == 0.0
    # The recency round's knobs: the offseason gaps, the college scores
    # half's recency and the special-teams prior, each the lab's pick.
    for flag in ("nfl_offseason_weeks", "ncaaf_epa_offseason_weeks",
                 "ncaaf_scores_half_life", "ncaaf_st_prior"):
        assert getattr(args, flag) is None
    assert runner.resolve_offseason_weeks(None) == runner.DEFAULT_NFL_OFFSEASON_WEEKS == 8.0
    assert runner.resolve_offseason_weeks(8.0) == 8.0
    assert runner.resolve_offseason_weeks(-1.0) == 0.0
    assert (runner.resolve_ncaaf_epa_offseason_weeks(None)
            == runner.DEFAULT_NCAAF_EPA_OFFSEASON_WEEKS == 6.0)
    assert runner.resolve_ncaaf_epa_offseason_weeks(0.0) == 0.0
    assert (runner.resolve_ncaaf_scores_half_life(None)
            == runner.DEFAULT_NCAAF_SCORES_HALF_LIFE == 34.0)
    assert runner.resolve_ncaaf_scores_half_life(0.0) == 0.0
    assert runner.resolve_ncaaf_st_prior(None) is runner.DEFAULT_NCAAF_ST_PRIOR is True
    assert runner.resolve_ncaaf_st_prior("on") is True
    assert runner.resolve_ncaaf_st_prior("off") is False
    # The publish gate runs by rule tier unless told otherwise.
    assert args.publish_by_rule is None
    assert runner.resolve_publish_by_rule(None) is runner.DEFAULT_PUBLISH_BY_RULE is True
    assert runner.resolve_publish_by_rule("off") is False
    # The scale's home-margin shift, per league, the lab's pick.
    assert args.nfl_scale_shift is None and args.ncaaf_scale_shift is None
    for league in ("nfl", "ncaaf"):
        assert (runner.resolve_scale_shift(None, league)
                is runner.DEFAULT_SCALE_SHIFT_BY_LEAGUE[league])
        assert runner.resolve_scale_shift("on", league) is True
        assert runner.resolve_scale_shift("off", league) is False
    assert runner.DEFAULT_SCALE_SHIFT_BY_LEAGUE == {"nfl": False, "ncaaf": True}
    # The college blend's early-season weight: the lab's pick, clipped to [0, 1].
    assert args.ncaaf_early_weight is None
    assert runner.resolve_ncaaf_early_weight(None) == runner.DEFAULT_NCAAF_EARLY_WEIGHT == 0.4
    assert runner.resolve_ncaaf_early_weight(0.3) == 0.3
    assert runner.resolve_ncaaf_early_weight(2.0) == 1.0


def test_a_papered_game_prices_but_never_stakes() -> None:
    """The per-game rule, at the point that decides whether money follows."""
    from velocity.wagering.slate import SlateConfig

    config = SlateConfig(paper_games={"g1": "FCS side Towson"})
    assert config.paper_reason("total", 0.04, 0.51, "draftkings", game_id="g1") == (
        "paper game (FCS side Towson)")
    assert config.paper_reason("total", 0.04, 0.51, "draftkings", game_id="g2") is None
    assert config.paper_reason("total", 0.04, 0.51, "draftkings") is None


def test_fcs_games_are_papered_by_default_on_the_college_slate(tmp_path) -> None:
    import numpy as np
    import pandas as pd
    from velocity.models.game_nfl import GameProjection
    from velocity.models.simulate import GameSim

    runner = _runner()
    pd.DataFrame({"season": [2024, 2025, 2025], "team": ["Old", "Georgia", "Clemson"]}).to_parquet(
        tmp_path / "sp_ratings.parquet", index=False)
    sim = GameSim(home_score=np.array([28.0]), away_score=np.array([21.0]))
    projections = {
        "g1": GameProjection("Georgia", "Clemson", 28.0, 21.0, sim),
        "g2": GameProjection("Georgia", "Towson", 42.0, 10.0, sim),
        "g3": GameProjection("Old", "Clemson", 20.0, 30.0, sim),  # rated last season only
    }
    args = runner.build_parser().parse_args(["--league", "ncaaf", "--data", str(tmp_path)])
    assert args.ncaaf_fcs is False
    papered = runner.fcs_paper_games(args, projections)
    assert set(papered) == {"g2", "g3"}
    assert "Towson" in papered["g2"] and "Old" in papered["g3"]
    # Staked on request; never on another league; nothing without a ratings file.
    staked = runner.build_parser().parse_args(
        ["--league", "ncaaf", "--data", str(tmp_path), "--ncaaf-fcs"])
    assert runner.fcs_paper_games(staked, projections) == {}
    nfl = runner.build_parser().parse_args(["--league", "nfl", "--data", str(tmp_path)])
    assert runner.fcs_paper_games(nfl, projections) == {}
    bare = runner.build_parser().parse_args(["--league", "ncaaf", "--data", str(tmp_path / "x")])
    assert runner.fcs_paper_games(bare, projections) == {}
