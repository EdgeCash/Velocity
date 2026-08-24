"""Home-run model — rate decomposition, shrinkage, and the prop protocol."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.models.props_hr import (
    MARKET,
    HomeRunModel,
    _park_factors,
    _pitcher_factors,
    _slot_plate_appearances,
    _statcast_prior,
)

RNG = np.random.default_rng(7)


def _bank(n_games: int = 400) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """A synthetic season: two parks, two pitchers, three batter archetypes."""
    parks = {"Slugger Park": 0.030, "Cavern Field": 0.012}  # true HR/PA
    batters = {"masher": 1.8, "average": 1.0, "slappy": 0.35}  # rate multipliers
    game_rows, bat_rows, sp_rows = [], [], []
    for g in range(n_games):
        home = list(parks)[g % 2]
        away = list(parks)[(g + 1) % 2]
        gid = f"g{g}"
        game_rows.append({"game_id": gid, "season": 2026, "home_team": home,
                          "away_team": away})
        # One starter per side; the "gopher" arm allows twice the league rate.
        for side, pid, mult in (("home", "ace", 0.6), ("away", "gopher", 1.7)):
            sp_rows.append({"game_id": gid, "side": side, "starter_id": pid,
                            "hr": RNG.poisson(0.9 * mult),
                            "batters_faced": 24})
        for slot, (name, mult) in enumerate(batters.items(), start=1):
            for side in ("home", "away"):
                pa = 5 if slot == 1 else 4
                p = parks[home] * mult
                bat_rows.append({
                    "game_id": gid, "team": home if side == "home" else away,
                    "side": side, "batter_id": f"{name}_{side}",
                    "batter_name": f"{name} {side}", "lineup_slot": slot,
                    "started": True, "pa": pa, "ab": pa,
                    "hr": int(RNG.binomial(pa, min(p, 1.0))),
                })
    return pd.DataFrame(bat_rows), pd.DataFrame(game_rows), pd.DataFrame(sp_rows)


def test_fit_recovers_batter_ordering_and_park_effect() -> None:
    bat, games, sp = _bank()
    model = HomeRunModel.fit(bat, games, sp)
    assert 0.005 < model.league_rate < 0.06
    masher = model.batter_rate["masher_home"]
    average = model.batter_rate["average_home"]
    slappy = model.batter_rate["slappy_home"]
    assert masher > average > slappy  # the ordering survives shrinkage
    # Slugger Park really is the better park; the factor must say so.
    assert model.park_factor["Slugger Park"] > model.park_factor["Cavern Field"]
    # Multipliers stay inside their published clamps.
    assert all(0.80 <= v <= 1.30 for v in model.park_factor.values())


def test_pitcher_factor_separates_the_gopher_arm() -> None:
    _bat, _games, sp = _bank()
    factors = _pitcher_factors(sp, league_rate=0.03)
    assert factors["gopher"] > 1.0 > factors["ace"]
    assert all(0.65 <= v <= 1.55 for v in factors.values())
    # No starters frame, or one missing the HR columns → no claim at all.
    assert _pitcher_factors(None, 0.03) == {}
    assert _pitcher_factors(pd.DataFrame({"starter_id": ["x"]}), 0.03) == {}


def test_slot_plate_appearances_prices_the_top_of_the_order() -> None:
    bat, _games, _sp = _bank()
    slots = _slot_plate_appearances(bat)
    assert slots[1] > slots[2]  # leadoff bats more
    # Substitutes carry no projectable workload and never enter the table.
    with_sub = pd.concat([bat, pd.DataFrame([{
        "game_id": "g0", "batter_id": "sub", "lineup_slot": 0,
        "started": False, "pa": 1, "hr": 0}])], ignore_index=True)
    assert 0 not in _slot_plate_appearances(with_sub)


def test_probability_compounds_over_plate_appearances() -> None:
    bat, games, sp = _bank()
    model = HomeRunModel.fit(bat, games, sp)
    per_pa = model.rate("masher_home")
    assert per_pa is not None
    p4 = model.probability("masher_home", pa=4)
    p5 = model.probability("masher_home", pa=5)
    assert p4 is not None and p5 is not None
    assert p5 > p4 > per_pa  # more trips, more chances
    assert p4 == pytest.approx(1 - (1 - per_pa) ** 4)
    # An unknown batter is never priced.
    assert model.rate("nobody") is None
    assert model.probability("nobody") is None


def test_context_multipliers_move_the_rate_the_right_way() -> None:
    bat, games, sp = _bank()
    model = HomeRunModel.fit(bat, games, sp)
    base = model.rate("average_home")
    vs_gopher = model.rate("average_home", opposing_starter="gopher")
    vs_ace = model.rate("average_home", opposing_starter="ace")
    assert vs_gopher is not None and vs_ace is not None and base is not None
    assert vs_gopher > base > vs_ace
    in_slugger = model.rate("average_home", venue="Slugger Park")
    in_cavern = model.rate("average_home", venue="Cavern Field")
    assert in_slugger is not None and in_cavern is not None
    assert in_slugger > in_cavern
    # An unknown pitcher or park is neutral, never a guess.
    assert model.rate("average_home", opposing_starter="unknown") == base


def _wide_bank(n_batters: int = 40, games: int = 60):
    """A population big enough to FIT the Statcast regression (needs 30+).

    Each batter's barrel rate is tied to his true HR/PA, which is exactly the
    relationship the prior is supposed to learn. Seeded locally so the draws
    do not shift with test-execution order.
    """
    rng = np.random.default_rng(11)
    game_rows, bat_rows, cast_rows = [], [], []
    for g in range(games):
        gid = f"w{g}"
        game_rows.append({"game_id": gid, "season": 2026,
                          "home_team": "Neutral Park", "away_team": "Other Park"})
    for b in range(n_batters):
        barrel = 2.0 + 18.0 * (b / max(n_batters - 1, 1))  # 2% .. 20%
        true_rate = 0.004 + 0.0018 * barrel  # the line the prior must recover
        pid = f"b{b}"
        cast_rows.append({"player_id": pid, "side": "batter",
                          "barrel_rate": barrel})
        for g in range(games):
            bat_rows.append({
                "game_id": f"w{g}", "team": "Neutral Park", "side": "home",
                "batter_id": pid, "batter_name": f"Batter {b}",
                "lineup_slot": (b % 9) + 1, "started": True,
                "pa": 4, "ab": 4,
                "hr": int(rng.binomial(4, min(true_rate, 1.0))),
            })
    return (pd.DataFrame(bat_rows), pd.DataFrame(game_rows),
            pd.DataFrame(cast_rows))


def test_statcast_prior_lifts_a_thin_sample_masher() -> None:
    bat, games, statcast = _wide_bank()
    # A callup: 20 PA, no homers yet, but elite barrel quality.
    rookie = pd.DataFrame([{
        "game_id": f"w{i}", "team": "Neutral Park", "side": "home",
        "batter_id": "rookie", "batter_name": "Elite Rookie",
        "lineup_slot": 4, "started": True, "pa": 4, "ab": 4, "hr": 0}
        for i in range(5)])
    bat = pd.concat([bat, rookie], ignore_index=True)
    statcast = pd.concat([statcast, pd.DataFrame([
        {"player_id": "rookie", "side": "batter", "barrel_rate": 20.0}])],
        ignore_index=True)

    plain = HomeRunModel.fit(bat, games)
    informed = HomeRunModel.fit(bat, games, statcast=statcast)
    # Without Statcast he shrinks to the league mean; with it, his
    # batted-ball quality carries him above it. That gap is the model's edge.
    assert informed.batter_rate["rookie"] > plain.batter_rate["rookie"]
    assert informed.batter_rate["rookie"] > informed.league_rate
    # And a prior is a starting point, not a claim: with 20 PA of nothing on
    # the board he lands just BELOW the rate his barrels alone would imply —
    # the zero counts for something, it just cannot count for much yet.
    merged = bat.merge(games[["game_id", "home_team"]], on="game_id")
    prior = _statcast_prior(merged, statcast, informed.league_rate)
    assert informed.batter_rate["rookie"] < prior["rookie"]
    assert informed.batter_rate["rookie"] > 0.9 * prior["rookie"]


def test_statcast_prior_recovers_the_underlying_line() -> None:
    bat, games, statcast = _wide_bank()
    merged = bat.merge(games[["game_id", "home_team"]], on="game_id")
    league = merged["hr"].sum() / merged["pa"].sum()
    prior = _statcast_prior(merged, statcast, league)
    assert len(prior) == 40
    # The fitted prior must rank batters the way their barrel rates do.
    assert prior["b39"] > prior["b20"] > prior["b0"]


def test_statcast_prior_degrades_without_overlap() -> None:
    bat, _games, _sp = _bank()
    merged = bat.assign(home_team="Slugger Park")
    assert _statcast_prior(merged, None, 0.03) == {}
    assert _statcast_prior(merged, pd.DataFrame(), 0.03) == {}
    # Too few joinable batters to fit a line → no prior, plain shrinkage.
    thin = pd.DataFrame([{"player_id": "masher_home", "side": "batter",
                          "barrel_rate": 19.0}])
    assert _statcast_prior(merged, thin, 0.03) == {}


def test_park_factors_shrink_a_tiny_sample_toward_neutral() -> None:
    # One game at a freak park: the raw rate is absurd, the factor is calm.
    frame = pd.DataFrame([{"home_team": "Freak Park", "pa": 8, "hr": 6}])
    factors = _park_factors(frame, league_rate=0.03)
    # Raw rate is 0.75 HR/PA — 25x league. Shrinkage pulls it to ~1.03.
    assert 1.0 < factors["Freak Park"] < 1.05


def test_game_props_satisfy_the_prop_slate_protocol() -> None:
    bat, games, sp = _bank()
    model = HomeRunModel.fit(bat, games, sp)
    props = model.for_game(
        opposing_starter={"masher_home": "gopher"},
        venue="Slugger Park",
        lineup_slot={"masher_home": 1},
    )
    assert props.has("masher_home", MARKET)
    assert not props.has("masher_home", "pitcher_strikeouts")
    assert not props.has("nobody", MARKET)
    over = props.prob_over("masher_home", MARKET, 0.5)
    assert 0.0 < over < 1.0
    assert props.prob_under("masher_home", MARKET, 0.5) == pytest.approx(1 - over)
    # A 1.5 line (two homers) must be far longer than "goes deep".
    assert props.prob_over("masher_home", MARKET, 1.5) < over
    # Unknown players price at the coin flip the protocol expects.
    assert props.prob_over("nobody", MARKET, 0.5) == 0.5


def test_expected_home_runs_ranks_the_single_stat_contest() -> None:
    bat, games, sp = _bank()
    model = HomeRunModel.fit(bat, games, sp)
    masher = model.expected_home_runs("masher_home", lineup_slot=1)
    slappy = model.expected_home_runs("slappy_home", lineup_slot=3)
    assert masher is not None and slappy is not None and masher > slappy
    assert model.expected_home_runs("nobody") is None


def test_empty_bank_fits_without_raising() -> None:
    empty = pd.DataFrame(columns=["game_id", "batter_id", "pa", "hr",
                                  "lineup_slot", "started"])
    games = pd.DataFrame(columns=["game_id", "season", "home_team", "away_team"])
    model = HomeRunModel.fit(empty, games)
    assert model.league_rate == 0.0 and model.batter_rate == {}
    assert model.rate("anyone") is None
