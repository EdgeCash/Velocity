"""Parlays — sim-exact correlated pricing, push handling, and the builder.

Every case here has a closed-form answer by construction: tiny hand-built sim
results whose joint outcomes are known exactly, so the expected parlay EV is
arithmetic, not simulation noise. The properties pinned: independence multiplies
expectations across games, same-game correlation is priced off the joint samples
(not a naive product), a pushed leg reduces the parlay instead of killing it,
conflicting legs never share a ticket, and staking respects the parlay cap.
"""

from __future__ import annotations

import numpy as np
import pytest
from velocity.models.simulate import GameSim
from velocity.models.simulate_baseball import BaseballSimResult
from velocity.wagering.bet_log import Bet
from velocity.wagering.parlay import (
    ParlayConfig,
    ParlayLeg,
    build_parlays,
    evaluate_parlay,
    leg_outcomes,
    legs_from_bets,
    parlay_slate_to_frame,
)


def _sim(home: list[float], away: list[float]) -> GameSim:
    return GameSim(home_score=np.array(home, float), away_score=np.array(away, float))


def _result(
    home: list[float],
    away: list[float],
    *,
    f5_home: list[float] | None = None,
    f5_away: list[float] | None = None,
    i1_home: list[float] | None = None,
    i1_away: list[float] | None = None,
    pitcher_ks: dict[str, list[float]] | None = None,
) -> BaseballSimResult:
    n = len(home)
    zeros = [0.0] * n
    return BaseballSimResult(
        full=_sim(home, away),
        f5=_sim(f5_home or home, f5_away or away),
        i1=_sim(i1_home or zeros, i1_away or zeros),
        pitcher_strikeouts={
            pid: np.array(v, float) for pid, v in (pitcher_ks or {}).items()
        },
    )


# A coin-flip game: home wins sims 0-1, away wins sims 2-3. At +100 (decimal 2.0)
# a moneyline leg on either side is exactly fair.
COIN = _result([5, 4, 2, 1], [3, 2, 4, 3])


def _ml(game_id: str, side: str = "home", price: float = 100) -> ParlayLeg:
    return ParlayLeg(game_id=game_id, market="moneyline", side=side, price=price)


# --- leg outcomes -------------------------------------------------------------


def test_leg_outcomes_game_markets_read_the_right_segment() -> None:
    result = _result(
        [5, 0], [3, 2],
        f5_home=[2, 0], f5_away=[2, 1],
        i1_home=[1, 0], i1_away=[0, 0],
    )
    ml = leg_outcomes(_ml("g"), result)
    assert ml.tolist() == [1, -1]  # home wins sim 0, loses sim 1
    f5_ml = leg_outcomes(
        ParlayLeg(game_id="g", market="moneyline_f5", side="home", price=100), result
    )
    assert f5_ml.tolist() == [0, -1]  # F5 tied in sim 0 → push
    nrfi = leg_outcomes(
        ParlayLeg(game_id="g", market="total_i1", side="under", price=-135, point=0.5), result
    )
    assert nrfi.tolist() == [-1, 1]  # a first-inning run in sim 0 only


def test_leg_outcomes_props_and_pushes() -> None:
    result = _result([1, 1, 1], [0, 0, 0], pitcher_ks={"p1": [4, 6, 8]})
    leg = ParlayLeg(
        game_id="g", market="pitcher_strikeouts", side="over", price=-110, point=6.0,
        player="p1",
    )
    assert leg_outcomes(leg, result).tolist() == [-1, 0, 1]  # under / push / over


def test_leg_outcomes_unknown_market_raises() -> None:
    with pytest.raises(ValueError, match="unknown parlay market"):
        leg_outcomes(ParlayLeg(game_id="g", market="team_total", side="over", price=100), COIN)


# --- evaluate_parlay ----------------------------------------------------------


def test_independent_fair_legs_are_fair() -> None:
    results = {"a": COIN, "b": COIN}
    ev = evaluate_parlay([_ml("a"), _ml("b")], results)
    assert ev.p_all_win == pytest.approx(0.25)
    assert ev.combined_decimal == pytest.approx(4.0)
    assert ev.ev == pytest.approx(0.0)  # fair legs multiply to a fair parlay
    assert ev.same_game is False


def test_same_game_correlation_beats_the_naive_product() -> None:
    # The over hits exactly when home wins → perfectly correlated legs.
    result = _result([6, 1], [3, 2])  # totals 9, 3; home wins sim 0 only
    legs = [
        _ml("g"),
        ParlayLeg(game_id="g", market="total", side="over", price=100, point=5.5),
    ]
    ev = evaluate_parlay(legs, {"g": result})
    # Joint P(win both) is 0.5, not the naive 0.25 — E[mult] = 0.5·2·2 = 2.
    assert ev.p_all_win == pytest.approx(0.5)
    assert ev.ev == pytest.approx(1.0)
    assert ev.same_game is True


def test_push_reduces_the_parlay() -> None:
    # A total pinned exactly on the number pushes in every sim: the parlay
    # reduces to the other leg alone (book convention).
    result = _result([3, 2], [2, 3])  # totals always 5
    legs = [
        _ml("g"),
        ParlayLeg(game_id="g", market="total", side="over", price=-110, point=5.0),
    ]
    ev = evaluate_parlay(legs, {"g": result})
    assert ev.ev == pytest.approx(0.0)  # a fair coin at +100, pushes aside
    assert ev.p_all_win == pytest.approx(0.0)  # "all win outright" never happens


def test_cross_game_independence_multiplies_expectations() -> None:
    # Legs with different win rates: 3/4 at +100 (EV .5) and 1/4 at +300 (EV 0).
    juiced = _result([5, 4, 3, 1], [1, 2, 1, 3])  # home wins 3 of 4
    dog = _result([2, 1, 0, 0], [1, 2, 1, 3])  # home wins 1 of 4
    legs = [_ml("a", price=100), _ml("b", price=300)]
    ev = evaluate_parlay(legs, {"a": juiced, "b": dog})
    assert ev.p_all_win == pytest.approx(0.75 * 0.25)
    # E[mult] = (0.75·2)·(0.25·4) = 1.5 → EV +0.5, the product of leg EVs' growth.
    assert ev.ev == pytest.approx(0.5)


def test_empty_parlay_raises() -> None:
    with pytest.raises(ValueError, match="at least one leg"):
        evaluate_parlay([], {})


# --- legs_from_bets -----------------------------------------------------------


def _bet(game_id: str, market: str, side: str, price: float, *, point: float | None = None,
         player: str | None = None, p_model: float = 0.6) -> Bet:
    return Bet(
        game_id=game_id, market=market, side=side, book="dk", price=price,
        stake=1.0, p_model=p_model, point=point, player=player,
    )


def test_legs_from_bets_resolves_props_and_drops_unknowns() -> None:
    results = {"g": _result([1, 0], [0, 1], pitcher_ks={"p1": [5, 7]})}
    names = {"gerritcole": "p1"}
    bets = [
        _bet("g", "moneyline", "home", 100),
        _bet("g", "pitcher_strikeouts", "over", -110, point=5.5, player="Gerrit Cole"),
        _bet("g", "pitcher_strikeouts", "over", -110, point=5.5, player="Nobody Known"),
        _bet("other", "moneyline", "home", 100),  # no sim for this game
    ]
    pairs = legs_from_bets(bets, results, names)
    assert [leg.player for leg, _ in pairs] == [None, "p1"]
    assert pairs[1][0].label == "Gerrit Cole"  # display keeps the provider name


def test_legs_from_bets_labels_game_legs_with_the_matchup() -> None:
    results = {"g": _result([1, 0], [0, 1], pitcher_ks={"p1": [5, 7]})}
    bets = [
        _bet("g", "moneyline", "home", 100),
        _bet("g", "pitcher_strikeouts", "over", -110, point=5.5, player="Gerrit Cole"),
    ]
    pairs = legs_from_bets(bets, results, {"gerritcole": "p1"}, game_labels={"g": "SF@LAD"})
    game_leg, prop_leg = pairs[0][0], pairs[1][0]
    # A game leg carries the matchup so a cross-game parlay reads unambiguously;
    # a prop leg keeps the player's name.
    assert game_leg.describe().startswith("SF@LAD moneyline home")
    assert prop_leg.describe().startswith("Gerrit Cole pitcher_strikeouts over")


def test_legs_from_bets_drops_unsimulated_stat() -> None:
    results = {"g": _result([1, 0], [0, 1])}  # no pitcher arrays at all
    bets = [_bet("g", "pitcher_strikeouts", "over", -110, point=5.5, player="Gerrit Cole")]
    assert legs_from_bets(bets, results, {"gerritcole": "p1"}) == []


# --- build_parlays ------------------------------------------------------------


def test_builder_combines_positive_ev_cross_game_legs() -> None:
    # Two 3-of-4 favorites at +100: each leg EV +0.5, parlay EV (1.5·1.5)−1 = 1.25.
    juiced = _result([5, 4, 3, 1], [1, 2, 1, 3])
    results = {"a": juiced, "b": juiced}
    bets = [_bet("a", "moneyline", "home", 100, p_model=0.75),
            _bet("b", "moneyline", "home", 100, p_model=0.75)]
    tickets = build_parlays(bets, results, bankroll=100.0)
    assert len(tickets) == 1
    t = tickets[0]
    assert t.evaluation.ev == pytest.approx(1.25)
    assert t.evaluation.same_game is False
    assert len(t.legs) == 2


def test_builder_rejects_conflicting_legs() -> None:
    results = {"g": COIN}
    bets = [_bet("g", "moneyline", "home", 150, p_model=0.6),
            _bet("g", "moneyline", "away", 150, p_model=0.6)]
    # Both sides of one market can never share a ticket, and there is no other
    # combination — so no parlays.
    assert build_parlays(bets, results, bankroll=100.0, config=ParlayConfig(min_ev=-1.0)) == []


def test_builder_respects_ev_bar_and_stake_cap() -> None:
    juiced = _result([5, 4, 3, 1], [1, 2, 1, 3])
    results = {"a": juiced, "b": juiced}
    bets = [_bet("a", "moneyline", "home", 100, p_model=0.75),
            _bet("b", "moneyline", "home", 100, p_model=0.75)]
    # An impossible EV bar yields nothing.
    assert build_parlays(bets, results, bankroll=100.0,
                         config=ParlayConfig(min_ev=10.0)) == []
    # A monster edge is still capped at max_stake_fraction of bankroll.
    tickets = build_parlays(bets, results, bankroll=100.0)
    cfg = ParlayConfig()
    assert tickets[0].stake <= cfg.max_stake_fraction * 100.0 + 1e-9
    assert tickets[0].stake == pytest.approx(1.0)  # kelly is huge here → the cap binds


def test_builder_same_game_flagged_and_optional() -> None:
    result = _result([6, 1], [3, 2])
    results = {"g": result}
    bets = [_bet("g", "moneyline", "home", 100, p_model=0.55),
            _bet("g", "total", "over", 100, point=5.5, p_model=0.55)]
    tickets = build_parlays(bets, results, bankroll=100.0)
    assert len(tickets) == 1
    assert tickets[0].evaluation.same_game is True
    assert build_parlays(
        bets, results, bankroll=100.0, config=ParlayConfig(allow_same_game=False)
    ) == []


def test_builder_is_deterministic() -> None:
    juiced = _result([5, 4, 3, 1], [1, 2, 1, 3])
    results = {"a": juiced, "b": juiced}
    bets = [_bet("a", "moneyline", "home", 100, p_model=0.75),
            _bet("b", "moneyline", "home", 100, p_model=0.75)]
    a = parlay_slate_to_frame(build_parlays(bets, results, bankroll=100.0))
    b = parlay_slate_to_frame(build_parlays(bets, results, bankroll=100.0))
    assert a.equals(b)


def test_parlay_frame_shape() -> None:
    juiced = _result([5, 4, 3, 1], [1, 2, 1, 3])
    tickets = build_parlays(
        [_bet("a", "moneyline", "home", 100, p_model=0.75),
         _bet("b", "moneyline", "home", 100, p_model=0.75)],
        {"a": juiced, "b": juiced},
        bankroll=100.0,
    )
    frame = parlay_slate_to_frame(tickets)
    assert list(frame.columns) == [
        "legs", "n_legs", "price", "decimal", "p_win", "ev", "same_game", "stake",
        "legs_json",
    ]
    assert frame.loc[0, "price"] == 300  # two +100 legs → +300 parlay
    assert "moneyline home" in frame.loc[0, "legs"]
    # legs_json carries the structured legs so a grader can settle the ticket.
    import json

    legs = json.loads(frame.loc[0, "legs_json"])
    assert [leg["game_id"] for leg in legs] == ["a", "b"]
    assert legs[0]["market"] == "moneyline"
    assert legs[0]["price"] == 100


def test_config_validation() -> None:
    with pytest.raises(ValueError, match="min_legs"):
        ParlayConfig(min_legs=1)
    with pytest.raises(ValueError, match="kelly_fraction"):
        ParlayConfig(kelly_fraction=0.0)
    with pytest.raises(ValueError, match="max_stake_fraction"):
        ParlayConfig(max_stake_fraction=1.5)
