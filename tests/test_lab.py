"""Model lab — weighted fits, phase splits, and the disagreement sweeps.

The lab is only trustworthy if its plumbing is exact: weights=None is
bit-identical to the unweighted fit, recency decay halves at the half-life,
the phase blend is the linear combination it claims, and the spread sweep's
sign convention matches nflverse (positive spread_line = home favored).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.backtest.lab import (
    ats_ou_vs_close,
    combine_ratings,
    disagreement_sweep,
    fit_split_ratings,
    recency_weights,
)
from velocity.features.team import TeamRatings, fit_ratings


def _plays(n_per_pair: int = 40, seed: int = 5) -> pd.DataFrame:
    """Synthetic two-team schedule where A's offense is clearly better."""
    rng = np.random.default_rng(seed)
    rows = []
    for season, week in ((2024, 1), (2024, 2), (2025, 1)):
        for _ in range(n_per_pair):
            rows.append({"season": season, "week": week, "posteam": "A", "defteam": "B",
                         "play_type": "pass", "epa": rng.normal(0.15, 0.3)})
            rows.append({"season": season, "week": week, "posteam": "B", "defteam": "A",
                         "play_type": "run", "epa": rng.normal(-0.10, 0.3)})
    return pd.DataFrame(rows)


def test_weights_none_is_bit_identical_to_unweighted() -> None:
    plays = _plays()
    a = fit_ratings(plays)
    b = fit_ratings(plays, weights=None)
    assert a.offense == b.offense and a.defense == b.defense


def test_uniform_weights_match_unweighted() -> None:
    plays = _plays()
    a = fit_ratings(plays)
    w = pd.Series(1.0, index=plays.index)
    b = fit_ratings(plays, weights=w)
    for team in a.offense:
        assert b.offense[team] == pytest.approx(a.offense[team])


def test_recency_weights_halve_at_the_half_life() -> None:
    plays = _plays()
    w = recency_weights(plays, half_life_weeks=24.0)
    newest = w[(plays["season"] == 2025) & (plays["week"] == 1)].iloc[0]
    oldest = w[(plays["season"] == 2024) & (plays["week"] == 1)].iloc[0]
    assert newest == 1.0
    # 2024w1 → 2025w1 is 25 key-steps... on the contiguous key: (2025*25+1)-(2024*25+1)=25.
    assert oldest == pytest.approx(0.5 ** (25 / 24))
    # More recent plays always weigh at least as much.
    mid = w[(plays["season"] == 2024) & (plays["week"] == 2)].iloc[0]
    assert oldest < mid < newest


def test_recency_weighted_fit_leans_toward_recent_form() -> None:
    plays = _plays().copy()
    # Team A collapses in the most recent slice.
    recent = (plays["season"] == 2025) & (plays["posteam"] == "A")
    plays.loc[recent, "epa"] = -0.5
    flat = fit_ratings(plays)
    recency = fit_ratings(plays, weights=recency_weights(plays, half_life_weeks=4.0))
    assert recency.offense["A"] < flat.offense["A"]  # the collapse counts more


def test_combine_ratings_is_the_stated_linear_blend() -> None:
    p = TeamRatings(offense={"A": 0.2}, defense={"A": -0.1}, league_epa=0.05,
                    ridge_lambda=200.0, n_plays=100, teams=("A",))
    r = TeamRatings(offense={"A": -0.05, "B": 0.02}, defense={"B": 0.03}, league_epa=0.01,
                    ridge_lambda=200.0, n_plays=50, teams=("A", "B"))
    combined = combine_ratings(p, r, pass_weight=0.75)
    assert combined.offense["A"] == pytest.approx(0.75 * 0.2 + 0.25 * -0.05)
    assert combined.offense["B"] == pytest.approx(0.25 * 0.02)  # missing → league avg 0
    assert combined.defense["A"] == pytest.approx(0.75 * -0.1)
    assert combined.league_epa == pytest.approx(0.75 * 0.05 + 0.25 * 0.01)
    with pytest.raises(ValueError, match="pass_weight"):
        combine_ratings(p, r, pass_weight=1.5)


def test_fit_split_ratings_separates_the_phases() -> None:
    plays = _plays()
    # All of A's plays are passes; at pass_weight=1.0 the blend IS the pass fit.
    split_all_pass = fit_split_ratings(plays, pass_weight=1.0)
    pass_only = fit_ratings(plays[plays["play_type"] == "pass"])
    assert split_all_pass.offense["A"] == pytest.approx(pass_only.offense["A"])


GAMES = pd.DataFrame({
    "game_id": ["g1", "g2", "g3"],
    "home_score": [27.0, 20.0, 30.0],
    "away_score": [20.0, 24.0, 27.0],
    "spread_line": [3.0, 7.0, 3.0],   # nflverse: positive = home favored
    "total_line": [44.5, 47.5, 51.5],
})
PROJECTIONS = pd.DataFrame({
    "game_id": ["g1", "g2", "g3"],
    # fair_spread is the fair HOME spread: negative = home favored.
    "fair_spread": [-6.0, -3.0, -4.0],
    "fair_total": [49.5, 44.5, 52.5],
})


def test_spread_sweep_signs_match_nflverse() -> None:
    sweep = disagreement_sweep(PROJECTIONS, GAMES, market="spread", thresholds=(0.0, 3.5))
    flat = sweep.iloc[0]
    # g1: model home −6 vs line home −3 → pick home; margin 7 > 3 → WIN.
    # g2: model home −3 vs line home −7 → pick away; margin −4 < 7 → WIN.
    # g3: model home −4 vs line −3 → pick home; margin 3 = line 3 → push, excluded.
    assert flat["bets"] == 2
    assert flat["win_rate"] == pytest.approx(1.0)
    tight = sweep.iloc[1]
    # Gaps: g1 |6−3| = 3 (below 3.5), g2 |3−7| = 4 (kept) → one bet survives.
    assert tight["bets"] == 1
    # A threshold beyond every gap leaves nothing.
    empty = disagreement_sweep(PROJECTIONS, GAMES, market="spread", thresholds=(10.0,))
    assert empty.iloc[0]["bets"] == 0


def test_total_sweep_and_flat_summary() -> None:
    sweep = disagreement_sweep(PROJECTIONS, GAMES, market="total", thresholds=(0.0,))
    # g1: model 49.5 > 44.5 → over; realized 47 > 44.5 → WIN.
    # g2: model 44.5 < 47.5 → under; realized 44 < 47.5 → WIN.
    # g3: model 52.5 > 51.5 → over; realized 57 > 51.5 → WIN.
    assert sweep.iloc[0]["bets"] == 3
    assert sweep.iloc[0]["win_rate"] == pytest.approx(1.0)
    summary = ats_ou_vs_close(PROJECTIONS, GAMES)
    assert summary["ats_bets"] == 2.0
    assert summary["ou_win_rate"] == pytest.approx(1.0)


def test_nickname_aliases_prefix_matches_schools() -> None:
    from velocity.wagering.live import nickname_aliases

    known = ["Georgia", "Georgia Southern", "Ole Miss", "Miami", "Miami (OH)", "Texas",
             "Texas A&M"]
    aliases = nickname_aliases(
        ["Georgia Bulldogs", "Georgia Southern Eagles", "Ole Miss Rebels",
         "Miami Hurricanes", "Texas A&M Aggies", "Fresno State Bulldogs"],
        known,
    )
    assert aliases["Georgia Bulldogs"] == "Georgia"
    # Several schools prefix-match; the longest school name wins.
    assert aliases["Georgia Southern Eagles"] == "Georgia Southern"
    assert aliases["Ole Miss Rebels"] == "Ole Miss"
    assert aliases["Miami Hurricanes"] == "Miami"  # "Miami (OH)" is not a prefix
    assert aliases["Texas A&M Aggies"] == "Texas A&M"
    assert "Fresno State Bulldogs" not in aliases  # no match → absent, never guessed


def test_market_blend_sweep_scores_select_and_holdout() -> None:
    import numpy as np
    import pandas as pd
    from velocity.backtest.lab import market_blend_sweep

    rng = np.random.default_rng(3)
    n = 400
    seasons = np.where(np.arange(n) < 200, 2018, 2022)  # half select, half holdout
    # The market is well calibrated; the model is pure noise → the sweep must
    # prefer w=0 (all market) on both windows.
    spread = rng.normal(0, 6, n)
    import math
    p_true = 0.5 * (1.0 + np.vectorize(math.erf)(spread / (13.45 * math.sqrt(2))))
    outcome = (rng.random(n) < p_true).astype(float)
    projections = pd.DataFrame({
        "season": seasons, "week": 1, "game_id": [f"g{i}" for i in range(n)],
        "p_home_win": rng.random(n), "home_win": outcome,
        "fair_spread": 0.0, "fair_total": 44.0,
    })
    games = pd.DataFrame({"game_id": [f"g{i}" for i in range(n)],
                          "spread_line": spread})
    sweep = market_blend_sweep(projections, games, select_through=2019)
    assert list(sweep["weight"]) == sorted(sweep["weight"])
    assert (sweep["n_select"] == 200).all() and (sweep["n_holdout"] == 200).all()
    best = sweep.loc[sweep["brier_select"].idxmin()]
    assert best["weight"] <= 0.2  # a noise model should be regressed to the market
    # And the pure-model endpoint must be the worst of all weights here.
    w1 = sweep.loc[sweep["weight"] == 1.0].iloc[0]
    assert w1["brier_select"] == sweep["brier_select"].max()


def test_market_blend_sweep_empty_when_no_lines() -> None:
    import pandas as pd
    from velocity.backtest.lab import market_blend_sweep

    projections = pd.DataFrame({"season": [2020], "week": [1], "game_id": ["g1"],
                                "p_home_win": [0.5], "home_win": [1.0],
                                "fair_spread": [0.0], "fair_total": [44.0]})
    games = pd.DataFrame({"game_id": ["g1"], "spread_line": [None]})
    assert market_blend_sweep(projections, games).empty


def test_rest_adjusted_model_applies_bye_and_short_week() -> None:
    import pandas as pd
    from velocity.backtest.lab import RestAdjustedModel

    captured = {}

    class _Inner:
        def project(self, home, away, *, neutral_site=False, rng=None,
                    home_bonus=0.0, away_bonus=0.0):
            captured.update(home_bonus=home_bonus, away_bonus=away_bonus)
            return "proj"

    schedule = pd.DataFrame({
        "home_team": ["KC", "BUF", "KC"],
        "away_team": ["DEN", "KC", "BUF"],
        "kickoff": pd.to_datetime([
            "2025-09-07",  # KC and DEN play
            "2025-09-14",  # BUF hosts KC (KC on 7 days rest)
            "2025-09-28",  # KC hosts BUF: KC off 14 days (bye), BUF off 14 too
        ]),
    })
    model = RestAdjustedModel(_Inner(), schedule, bye_points=1.5, short_points=1.0)

    # Week 3 game: both teams coming off 14 days rest → both get the bye bonus.
    assert model.project("KC", "BUF", kickoff=pd.Timestamp("2025-09-28")) == "proj"
    assert captured == {"home_bonus": 1.5, "away_bonus": 1.5}

    # A Thursday game 4 days after playing → short-week penalty; opponent
    # with no prior game gets nothing.
    model.project("KC", "NYJ", kickoff=pd.Timestamp("2025-09-11"))
    assert captured == {"home_bonus": -1.0, "away_bonus": 0.0}

    # No kickoff supplied → no adjustment either way.
    model.project("KC", "BUF", kickoff=None)
    assert captured == {"home_bonus": 0.0, "away_bonus": 0.0}


def test_wind_total_bonus_and_weather_wrapper() -> None:
    import pandas as pd
    from velocity.backtest.lab import WeatherAdjustedModel
    from velocity.features.weather import wind_total_bonus

    assert wind_total_bonus(None) == 0.0
    assert wind_total_bonus(float("nan")) == 0.0
    assert wind_total_bonus(10.0) == 0.0  # below threshold
    assert wind_total_bonus(25.0) == pytest.approx(-1.5)  # (25-15)*0.15
    assert wind_total_bonus(60.0) == pytest.approx(-3.0)  # capped

    captured = {}

    class _Inner:
        def project(self, home, away, *, neutral_site=False, rng=None,
                    home_bonus=0.0, away_bonus=0.0):
            captured.update(home_bonus=home_bonus, away_bonus=away_bonus)
            return "proj"

    weather = pd.DataFrame({
        "home_team": ["GB", "MIA"],
        "kickoff": pd.to_datetime(["2025-12-14", "2025-10-05"]),
        "roof": ["outdoors", "outdoors"],
        "wind_max": [25.0, 8.0],
        "temp_mean": [20.0, 85.0],
        "precip": [0.0, 0.0],
    })
    model = WeatherAdjustedModel(_Inner(), weather)
    model.project("GB", "CHI", kickoff=pd.Timestamp("2025-12-14"))
    assert captured == {"home_bonus": -1.5, "away_bonus": -1.5}  # symmetric
    model.project("MIA", "NYJ", kickoff=pd.Timestamp("2025-10-05"))
    assert captured == {"home_bonus": 0.0, "away_bonus": 0.0}  # calm day
    model.project("GB", "CHI", kickoff=None)  # no kickoff → no adjustment
    assert captured == {"home_bonus": 0.0, "away_bonus": 0.0}


def test_join_weather_leaves_domes_unmeasured() -> None:
    import pandas as pd
    from velocity.features.weather import join_weather, stadium_coords

    games = pd.DataFrame({
        "game_id": ["g1", "g2"],
        "home_team": ["GB", "MIN"],
        "roof": ["outdoors", "dome"],
        "kickoff": pd.to_datetime(["2025-12-14", "2025-12-14"]),
    })
    daily = pd.DataFrame({
        "team": ["GB", "MIN"],
        "date": pd.to_datetime(["2025-12-14", "2025-12-14"]),
        "wind_max": [22.0, 30.0],
        "temp_mean": [15.0, 10.0],
        "precip": [0.1, 0.0],
    })
    joined = join_weather(games, daily)
    assert joined.loc[joined.game_id == "g1", "wind_max"].iloc[0] == 22.0
    # A dome has no wind *measurement* — NaN, never a fake calm zero.
    assert pd.isna(joined.loc[joined.game_id == "g2", "wind_max"].iloc[0])
    # Relocation eras resolve: St. Louis before 2016, LA after.
    assert stadium_coords("LA", 2014) != stadium_coords("LA", 2020)
    assert stadium_coords("XXX", 2020) is None


def test_compress_plays_reproduces_the_play_level_fit() -> None:
    from velocity.backtest.lab import compress_plays

    plays = _plays()
    cells = compress_plays(plays)
    assert len(cells) < len(plays)
    full = fit_ratings(plays, ridge_lambda=150.0)
    cell = fit_ratings(cells, ridge_lambda=150.0, weights=cells["n"].astype(float))
    for team in ("A", "B"):
        assert cell.offense[team] == pytest.approx(full.offense[team], abs=1e-12)
        assert cell.defense[team] == pytest.approx(full.defense[team], abs=1e-12)
    assert cell.league_epa == pytest.approx(full.league_epa, abs=1e-12)

    # Recency composes: plays in a cell share (season, week), hence the decay,
    # so count × cell-decay equals the summed play weights.
    w_full = fit_ratings(plays, ridge_lambda=150.0,
                         weights=recency_weights(plays, 17.0))
    w_cell = fit_ratings(cells, ridge_lambda=150.0,
                         weights=cells["n"].astype(float) * recency_weights(cells, 17.0))
    for team in ("A", "B"):
        assert w_cell.offense[team] == pytest.approx(w_full.offense[team], abs=1e-12)


def test_ncaaf_walk_order_moves_bowls_after_the_regular_season() -> None:
    from velocity.backtest.lab import ncaaf_walk_order

    games = pd.DataFrame({
        "season": [2023] * 6,
        "week": [1, 16, 1, 13, 14, 15],
        "season_type": ["REG", "REG", "POST", "POST", "POST", "POST"],
    })
    out = ncaaf_walk_order(games)
    # Regular weeks untouched; POST weeks 1/13/14/15 → 18/19/20/21 (dense rank).
    assert list(out["week"]) == [1, 16, 18, 19, 20, 21]
    # The leak this closes: every bowl now sorts after every regular week.
    assert out[out["season_type"] == "POST"]["week"].min() > 16


def test_ncaaf_variants_gate_epa_on_plays() -> None:
    from velocity.backtest.lab import ncaaf_variants

    without = ncaaf_variants(64)
    assert not any(name.startswith(("epa", "blend")) for name in without)
    with_plays = ncaaf_variants(64, plays=_plays().assign(game_id="g1"))
    assert {"epa-r200", "epa-r400", "epa-r800", "epa-recency-17",
            "blend-epa50"} <= set(with_plays)
    # The factory prices a matchup end-to-end from a plays frame.
    kind, factory = with_plays["epa-r400"]
    assert kind == "plays"
    model = factory(_plays())
    proj = model.project("A", "B")
    assert proj.mu_margin > 0  # A's offense is clearly better in the fixture
    assert 40 < proj.mu_total < 80  # college scoring shape, not NFL's


def test_blended_model_is_the_linear_blend_of_its_parts() -> None:
    from velocity.backtest.lab import BlendedGameModel
    from velocity.features.team import fit_ratings
    from velocity.models.game_nfl import NFLGameModel, NFLModelConfig
    from velocity.models.simulate import SimConfig

    sim = SimConfig(sd_margin=17.0, sd_total=16.0, n_sims=64)
    cfg_a = NFLModelConfig(base_points=28.5, plays_per_game=65.0, hfa_points=2.5, sim=sim)
    cfg_b = NFLModelConfig(base_points=24.0, plays_per_game=60.0, hfa_points=3.0, sim=sim)
    ratings = fit_ratings(_plays())
    a = NFLGameModel(ratings, cfg_a)
    b = NFLGameModel(ratings, cfg_b)

    blend = BlendedGameModel(a, b, 0.30, sim)
    mu_a = a.expected_points("A", "B")
    mu_b = b.expected_points("A", "B")
    mu = blend.expected_points("A", "B")
    assert mu[0] == pytest.approx(0.30 * mu_a[0] + 0.70 * mu_b[0])
    assert mu[1] == pytest.approx(0.30 * mu_a[1] + 0.70 * mu_b[1])
    # Weight 1 recovers the primary exactly; a projection carries the blend μs.
    assert BlendedGameModel(a, b, 1.0, sim).expected_points("A", "B") == mu_a
    proj = blend.project("A", "B")
    assert proj.mu_margin == pytest.approx(mu[0] - mu[1])
    with pytest.raises(ValueError):
        BlendedGameModel(a, b, 1.5, sim)


def _mlb_games(n: int = 30) -> pd.DataFrame:
    rng = np.random.default_rng(4)
    rows = []
    for i in range(n):
        # B hosts A half the time; ace (SP9) starts A's even games.
        home, away = ("A", "B") if i % 2 else ("B", "A")
        rows.append({
            "game_id": f"m{i}", "season": 2026, "week": 1 + i // 4,
            "home_team": home, "away_team": away,
            "home_score": float(rng.poisson(4.5)), "away_score": float(rng.poisson(4.2)),
            "neutral_site": False,
            "kickoff": pd.Timestamp("2026-06-01") + pd.Timedelta(days=i),
        })
    return pd.DataFrame(rows)


def _mlb_starters(games: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for i, g in enumerate(games.to_dict("records")):
        for side, team in (("home", g["home_team"]), ("away", g["away_team"])):
            sp = "SP9" if (team == "A" and i % 2 == 0) else f"SP-{team}"
            rows.append({"game_id": g["game_id"], "team": team, "side": side,
                         "starter_id": sp, "starter_name": sp})
    return pd.DataFrame(rows)


def test_mlb_starter_frame_attaches_opposing_starter() -> None:
    from velocity.backtest.lab import mlb_starter_frame

    games = _mlb_games(2)
    frame = mlb_starter_frame(games, _mlb_starters(games))
    assert len(frame) == 4
    g0 = games.iloc[0]
    home_row = frame[(frame["game_id"] == g0["game_id"])
                     & (frame["posteam"] == g0["home_team"])].iloc[0]
    away_starters = _mlb_starters(games)
    expected = away_starters[(away_starters["game_id"] == g0["game_id"])
                             & (away_starters["side"] == "away")]["starter_id"].iloc[0]
    assert home_row["passer_player_id"] == expected  # batting vs the AWAY starter
    assert home_row["epa"] == g0["home_score"]


def test_starter_aware_model_prices_the_probable() -> None:
    from velocity.backtest.lab import StarterAwareModel
    from velocity.models.simulate import SimConfig

    class Ratings:
        league_epa = 4.4

        def matchup_delta(self, off, deft, qb_id=None):
            return (-0.8 if qb_id == "ACE" else 0.0)

    kick = pd.Timestamp("2026-08-18 23:00")
    lookup = {("H", "A", kick): ("ACE", None)}
    model = StarterAwareModel(Ratings(), lookup, SimConfig(sd_margin=3.2, sd_total=4.6, n_sims=64))
    with_ace = model.project("H", "A", kickoff=kick)
    # The away offense faces the ACE → its mu drops by 0.8.
    assert with_ace.mu_away == pytest.approx(4.4 - 0.8 - 0.075)
    unknown = model.project("H", "A", kickoff=None)  # outside the lookup
    assert unknown.mu_away == pytest.approx(4.4 - 0.075)


def test_bonus_adjusted_model_applies_bonuses() -> None:
    from velocity.backtest.lab import BonusAdjustedModel
    from velocity.models.simulate import SimConfig

    class Inner:
        def expected_points(self, home, away, *, neutral_site=False):
            return 80.0, 78.0

    model = BonusAdjustedModel(
        Inner(), lambda h, a, k: (0.0, -3.5),
        SimConfig(sd_margin=12.5, sd_total=15.0, n_sims=64),
    )
    proj = model.project("H", "A", kickoff=pd.Timestamp("2026-08-18"))
    assert (proj.mu_home, proj.mu_away) == (80.0, 74.5)


def test_inseason_situational_variants_price() -> None:
    from velocity.backtest.lab import inseason_variants, mlb_sp_variants

    mlb = inseason_variants("mlb", 64)
    assert "park-fit" in mlb and "b2b-2" not in mlb
    games = _mlb_games()
    park_model = mlb["park-fit"][1](games)
    assert park_model.project("A", "B").mu_total > 0

    wnba = inseason_variants("wnba", 64)
    assert "b2b-3.5" in wnba and "park-fit" not in wnba
    # Back-to-back: same team played the previous night → penalized mu.
    w_games = _mlb_games().assign(
        home_score=lambda d: d.home_score + 75, away_score=lambda d: d.away_score + 75
    )
    b2b_model = wnba["b2b-3.5"][1](w_games)
    last_kick = w_games["kickoff"].max()
    fresh = b2b_model.project("A", "B", kickoff=last_kick + pd.Timedelta(days=10))
    tired = b2b_model.project("A", "B", kickoff=last_kick + pd.Timedelta(days=1))
    assert tired.mu_total == pytest.approx(fresh.mu_total - 7.0, abs=1e-6)

    sp = mlb_sp_variants(64, games, _mlb_starters(games))
    assert {"sp-q5", "sp-q40", "sp-q160", "sp-q640"} <= set(sp)
    model = sp["sp-q15"][1](games)
    proj = model.project(games.iloc[0]["home_team"], games.iloc[0]["away_team"],
                         kickoff=games.iloc[0]["kickoff"])
    assert 5 < proj.mu_total < 14


def test_mlb_fip_priors_sign_scale_and_shrink() -> None:
    from velocity.backtest.lab import mlb_fip_priors

    rows = []
    for i in range(20):  # ace: 6 IP, 8 K, 1 BB, 0 HR per start
        rows.append({"game_id": f"a{i}", "starter_id": "ACE", "outs": 18.0,
                     "k": 8, "bb": 1, "hbp": 0, "hr": 0})
    for i in range(20):  # bum: 5 IP, 3 K, 4 BB, 1.5 HR per start
        rows.append({"game_id": f"b{i}", "starter_id": "BUM", "outs": 15.0,
                     "k": 3, "bb": 4, "hbp": 0, "hr": 1 + i % 2})
    starters = pd.DataFrame(rows)
    priors = mlb_fip_priors(starters, shrink_innings=60.0)
    assert priors["ACE"] < 0 < priors["BUM"]  # negative = fewer runs allowed
    # Runs-per-start scale: a full season of elite/awful peripherals stays
    # within a couple of runs, never the raw FIP-numerator scale.
    assert -2.5 < priors["ACE"] and priors["BUM"] < 2.5
    # More shrinkage → smaller magnitudes; empty input → no priors.
    tighter = mlb_fip_priors(starters, shrink_innings=600.0)
    assert abs(tighter["ACE"]) < abs(priors["ACE"])
    assert mlb_fip_priors(starters.iloc[:0], 60.0) == {}


def test_starter_aware_model_unknown_starter_prices_neutral() -> None:
    from velocity.backtest.lab import (
        SimConfig,
        StarterAwareModel,
        mlb_starter_frame,
    )
    from velocity.features.team import fit_qb_ratings

    games = _mlb_games()
    frame = mlb_starter_frame(games, _mlb_starters(games))
    ratings = fit_qb_ratings(frame, ridge_lambda=100.0, qb_lambda=5.0,
                             min_dropbacks=1)
    assert ratings.starters  # the stale fallback the model must NOT use
    sim = SimConfig(sd_margin=3.2, sd_total=4.6, n_sims=64)
    model = StarterAwareModel(ratings, {("A", "B", None): (None, None)}, sim)
    known = StarterAwareModel(
        ratings, {("A", "B", None): ("SP-B", "SP9")}, sim
    )
    neutral = model.project("A", "B")
    priced = known.project("A", "B")
    # No probable → both mus are exactly base ± hfa/2 (league-average SPs),
    # never the starter one team most recently faced.
    base = ratings.league_epa
    exp_home = base + ratings.matchup_delta("A", "B", qb_id="zzz-unknown")
    assert neutral.mu_home == pytest.approx(exp_home + model.hfa_points / 2.0)
    assert neutral.mu_home != pytest.approx(priced.mu_home)
