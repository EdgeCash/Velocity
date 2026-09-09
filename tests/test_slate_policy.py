"""Paper markets and the edge ceilings — where the money stops and the evidence
keeps flowing (docs/STRATEGY_REVIEW.md S2).

A paper row is priced, logged and graded for CLV like any other bet, at stake
zero with the reason on the ticket. Nothing downstream may turn it back into
money: not the group cap, not the portfolio, not a parlay leg, not the
publish gate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.models.game_nfl import GameProjection
from velocity.models.simulate import GameSim
from velocity.wagering.bet_log import Bet
from velocity.wagering.live import slate_to_frame
from velocity.wagering.parlay import legs_from_bets
from velocity.wagering.slate import SlateConfig, build_slate

STAMP = pd.Timestamp("2026-09-12 12:00:00")
KICK = pd.Timestamp("2026-09-13 17:00:00")


def _projection(mu_home: float, mu_away: float, sd: float = 13.0) -> GameProjection:
    rng = np.random.default_rng(7)
    n = 20_000
    margin = rng.normal(mu_home - mu_away, sd, n)
    total = rng.normal(mu_home + mu_away, sd, n)
    home = np.rint(np.clip((total + margin) / 2, 0, None))
    away = np.rint(np.clip((total - margin) / 2, 0, None))
    return GameProjection("HOME", "AWAY", mu_home, mu_away, GameSim(home, away))


def _lines(rows: list[tuple[str, str, float, float | None, str]]) -> pd.DataFrame:
    return pd.DataFrame([
        {"line_id": f"g1|{m}|{s}|{b}|{i}", "game_id": "g1", "market": m, "side": s,
         "price": p, "point": pt, "book": b, "timestamp": STAMP}
        for i, (m, s, p, pt, b) in enumerate(rows)
    ])


GAMES = pd.DataFrame({"game_id": ["g1"], "kickoff": [KICK]})


def _board() -> pd.DataFrame:
    # A total the model likes modestly, a team total, and a moneyline dog the
    # model likes far too much.
    return _lines([
        ("total", "over", -110, 44.5, "bookA"),
        ("total", "under", -110, 44.5, "bookA"),
        ("team_total_home", "over", -110, 24.5, "bookA"),
        ("team_total_home", "under", -110, 24.5, "bookA"),
        ("moneyline", "home", -150, None, "bookA"),
        ("moneyline", "away", 130, None, "bookA"),
    ])


def _slate(config: SlateConfig, proj: GameProjection) -> pd.DataFrame:
    return slate_to_frame(build_slate({"g1": proj}, _board(), GAMES, config))


def test_a_paper_market_is_logged_at_stake_zero_with_the_reason() -> None:
    # Home 30 / away 20: the over 44.5 and the home team total 24.5 both clear.
    proj = _projection(30.0, 20.0)
    plain = _slate(SlateConfig(exclude_closing=False, max_edge=None, max_relative_edge=None),
                   proj)
    paper = _slate(SlateConfig(exclude_closing=False, max_edge=None, max_relative_edge=None,
                               paper_markets=frozenset({"team_total_home"})), proj)
    assert set(plain[plain["market"] == "team_total_home"]["stake"] > 0) == {True}
    tt = paper[paper["market"] == "team_total_home"]
    assert len(tt) == 1
    assert tt.iloc[0]["stake"] == 0.0
    assert tt.iloc[0]["note"] == "paper market"
    # Every other row is still staked with no note. Their stakes may even rise:
    # a paper row takes no share of the game's group cap, so the cap is split
    # among fewer real bets.
    others = paper[paper["market"] != "team_total_home"]
    assert set(others["market"]) == set(plain[plain["market"] != "team_total_home"]["market"])
    assert (others["stake"] > 0).all()
    assert others["note"].isna().all()


def test_the_absolute_ceiling_turns_an_outsized_edge_into_paper() -> None:
    # Home 34 / away 12 against a 44.5 total and a −150 home price: the model's
    # win probability is far above the market's — the shape the adverse-
    # selection study calls an alarm, not an opportunity.
    proj = _projection(34.0, 12.0)
    frame = _slate(SlateConfig(exclude_closing=False, max_edge=0.12, max_relative_edge=None),
                   proj)
    ml = frame[(frame["market"] == "moneyline") & (frame["side"] == "home")]
    assert len(ml) == 1
    assert ml.iloc[0]["edge"] > 0.12
    assert ml.iloc[0]["stake"] == 0.0
    assert ml.iloc[0]["note"].startswith("edge ") and "above ceiling 0.12" in ml.iloc[0]["note"]
    # With the ceiling off the same row is staked.
    frame = _slate(SlateConfig(exclude_closing=False, max_edge=None, max_relative_edge=None),
                   proj)
    assert frame[(frame["market"] == "moneyline") & (frame["side"] == "home")].iloc[0]["stake"] > 0


def test_the_relative_ceiling_bites_on_the_longshot_the_absolute_one_misses() -> None:
    # A +600 dog: fair ≈ 0.135. A 0.09 edge is under the absolute ceiling but
    # two-thirds of the fair probability — exactly the shape that filled the
    # first NCAAF card with +1000 dogs.
    board = _lines([
        ("moneyline", "home", -900, None, "bookA"),
        ("moneyline", "away", 600, None, "bookA"),
    ])
    proj = _projection(30.0, 20.0, sd=13.0)  # away wins ~22%: edge ≈ 0.08 on the dog
    log = build_slate({"g1": proj}, board, GAMES,
                      SlateConfig(exclude_closing=False, max_edge=0.12, max_relative_edge=0.5))
    rows = slate_to_frame(log)
    dog = rows[(rows["market"] == "moneyline") & (rows["side"] == "away")]
    assert len(dog) == 1
    assert 0.02 < dog.iloc[0]["edge"] < 0.12  # the absolute ceiling would let it through
    assert dog.iloc[0]["stake"] == 0.0
    assert "of fair" in dog.iloc[0]["note"] and "ceiling 50%" in dog.iloc[0]["note"]


def test_paper_rows_never_become_parlay_legs_and_neither_do_exchange_rungs() -> None:
    proj = _projection(30.0, 20.0)
    bets = [
        Bet("g1", "total", "over", "bookA", -110, 1.0, 0.58, point=44.5, p_fair=0.5),
        Bet("g1", "team_total_home", "over", "bookA", -110, 0.0, 0.6, point=24.5,
            p_fair=0.5, note="paper market"),
        Bet("g1", "spread", "home", "kalshi", -120, 1.0, 0.6, point=-6.5, p_fair=0.5),
    ]
    legs = legs_from_bets(bets, {"g1": proj.sim})
    described = [leg.describe() for leg, _ in legs]
    assert len(legs) == 1
    assert described[0].startswith("total over 44.5")


def test_the_publish_gate_never_posts_a_paper_row() -> None:
    from velocity.intel.publish import gate_bet
    from velocity.intel.score import Conviction

    bet = Bet("g1", "team_total_home", "over", "bookA", -110, 0.0, 0.6, point=24.5,
              p_fair=0.5, note="paper market")
    conviction = Conviction(bet=bet, edge_score=1.0, context_score=0.5, score=0.9,
                            tier="A", signals=())
    result = gate_bet(conviction)
    assert result.published is False
    assert result.reason == "paper — priced, not staked (paper market)"
