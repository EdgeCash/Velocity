"""The curated list (docs/OUTPUT_AUDIT.md §3 #5–#6): tiers, by-rule posting, CLV by tier.

Offline. The slate frame carries the rule tier a play earned; the publish
gate, run by rule, posts only tiered plays ranked by tier then edge with the
conviction floors standing down and the veto kept; the settled record keeps
the tier so closing-line value groups by it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from velocity.eval.metrics import clv_by_tier
from velocity.intel.publish import publish_slate
from velocity.intel.score import TIER_FLAGGED, Conviction
from velocity.models.game_nfl import GameProjection
from velocity.models.simulate import GameSim
from velocity.report.scorecard import bets_from_slate
from velocity.wagering.bet_log import Bet, BetLog
from velocity.wagering.live import rule_tiers_for, slate_to_frame
from velocity.wagering.tiers import RuleTier


def _projection(mu_home: float, mu_away: float, sd: float = 13.0) -> GameProjection:
    rng = np.random.default_rng(5)
    n = 20_000
    margin = rng.normal(mu_home - mu_away, sd, n)
    total = rng.normal(mu_home + mu_away, sd, n)
    home = np.rint(np.clip((total + margin) / 2, 0, None))
    away = np.rint(np.clip((total - margin) / 2, 0, None))
    return GameProjection("HOME", "AWAY", mu_home, mu_away, GameSim(home, away))


def _bet(game_id: str, market: str, side: str, point: float | None, p_model: float,
         stake: float = 1.0) -> Bet:
    return Bet(game_id=game_id, market=market, side=side, book="bookA", price=-110.0,
               stake=stake, p_model=p_model, point=point, p_fair=0.5)


def test_slate_rows_carry_the_tier_the_play_earned() -> None:
    # g1: the model's total sits 6 under the number (tier A in the NFL);
    # g2: 5 over — no rule since the level round's ledger read overs 4+ at
    # 49.8%; g3: a spread — no rule.
    projections = {"g1": _projection(20.0, 20.0), "g2": _projection(25.0, 25.0),
                   "g3": _projection(30.0, 20.0)}
    log = BetLog()
    log.add(_bet("g1", "total", "under", 46.0, 0.55))
    log.add(_bet("g2", "total", "over", 45.0, 0.55))
    log.add(_bet("g3", "spread", "home", -3.0, 0.55))
    tiers = rule_tiers_for(log, projections, "nfl")
    assert tiers[("g1", "total", "under")].tier == "A"
    assert ("g2", "total", "over") not in tiers
    assert ("g3", "spread", "home") not in tiers
    frame = slate_to_frame(log, tiers)
    assert frame["rule_tier"].tolist()[0] == "A" and frame["rule_tier"].iloc[1:].isna().all()
    assert frame["rule_record"].iloc[0] == tiers[("g1", "total", "under")].record
    # Both leagues admit the under alone.
    assert set(rule_tiers_for(log, projections, "ncaaf")) == {("g1", "total", "under")}
    assert set(tiers) == {("g1", "total", "under")}
    # Without a map the frame's tier columns are null, the old shape plus two.
    assert slate_to_frame(log)["rule_tier"].isna().all()


def test_the_tier_survives_grading() -> None:
    log = BetLog()
    log.add(_bet("g1", "total", "under", 46.0, 0.55))
    tiers = {("g1", "total", "under"): RuleTier("A", "total", frozenset({"under"}), 4.0,
                                                0.556, 340, 9, 15)}
    frame = slate_to_frame(log, tiers)
    bets = bets_from_slate(frame)
    assert bets[0].rule_tier == "A"
    graded = BetLog()
    graded.add(bets[0])
    finals = pd.DataFrame({"game_id": ["g1"], "home_score": [20.0], "away_score": [17.0]})
    settled = graded.settle(finals)
    assert settled["rule_tier"].tolist() == ["A"] and settled["result"].tolist() == ["win"]
    table = clv_by_tier(settled)
    assert table["rule_tier"].tolist() == ["A"]
    assert table["n_decided"].iloc[0] == 1 and table["win_rate"].iloc[0] == 1.0
    assert table["roi"].iloc[0] > 0
    # A settled frame without the column is an empty table; rows without a
    # tier report as "none".
    assert clv_by_tier(settled.drop(columns=["rule_tier"])).empty
    assert clv_by_tier(settled.assign(rule_tier=None))["rule_tier"].tolist() == ["none"]


def _conviction(bet: Bet, tier: str = "C", score: float = 0.3, context: float = -0.2) -> Conviction:
    return Conviction(bet=bet, edge_score=0.5, context_score=context, score=score, tier=tier)


def test_the_gate_by_rule_posts_tiered_plays_ranked_by_tier_then_edge() -> None:
    a = _bet("g1", "total", "under", 46.0, 0.53)   # tier A, edge 0.03
    b_big = _bet("g2", "total", "over", 45.0, 0.58)  # tier B, edge 0.08
    b_small = _bet("g3", "total", "over", 45.0, 0.54)  # tier B, edge 0.04
    none = _bet("g4", "total", "over", 45.0, 0.60)  # no rule
    vetoed = _bet("g5", "total", "under", 46.0, 0.56)  # tier A but vetoed
    tier_a = RuleTier("A", "total", frozenset({"under"}), 4.0, 0.556, 340, 9, 15)
    tier_b = RuleTier("B", "total", frozenset({"over"}), 4.0, 0.528, 301, 9, 14)
    tiers = {("g1", "total", "under"): tier_a, ("g2", "total", "over"): tier_b,
             ("g3", "total", "over"): tier_b, ("g5", "total", "under"): tier_a}
    convictions = [_conviction(none, tier="A", score=0.9, context=0.5), _conviction(b_small),
                   _conviction(a), _conviction(b_big),
                   _conviction(vetoed, tier=TIER_FLAGGED)]
    published, audit = publish_slate(convictions, rule_tiers=tiers, max_plays=5)
    order = [(c.bet.game_id) for c in published]
    # The conviction floors stand down (every play here is a C at 0.3), the
    # veto keeps its say, the un-ruled play is held back, and the running
    # order is tier then edge.
    assert order == ["g1", "g2", "g3"]
    by_game = audit.set_index("game_id")
    assert by_game.loc["g4", "reason"] == "no rule with a record admits this play"
    assert by_game.loc["g5", "reason"] == "vetoed by the intel layer"
    assert by_game.loc["g1", "rule_tier"] == "A"
    assert by_game.loc["g1", "rule_record"] == tier_a.record
    assert pd.isna(by_game.loc["g4", "rule_tier"])
    # The cap keeps the running order.
    top, audit2 = publish_slate(convictions, rule_tiers=tiers, max_plays=2)
    assert [c.bet.game_id for c in top] == ["g1", "g2"]
    reason = audit2.set_index("game_id").loc["g3", "reason"]
    assert reason == "outside the top 2 by rule tier and edge"
    # Without a tier map the gate is the conviction gate as it was.
    conv_only, _ = publish_slate(convictions, max_plays=5)
    assert [c.bet.game_id for c in conv_only] == ["g4"]
