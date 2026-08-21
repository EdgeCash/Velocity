"""Conviction scoring: blending math, abstain renormalization, tiers, vetoes."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from velocity.intel.context import GameContext, TeamContext
from velocity.intel.score import TIER_FLAGGED, IntelConfig, assess, assess_bets
from velocity.intel.signals import SignalResult
from velocity.wagering.bet_log import Bet


@dataclass(frozen=True)
class _Stub:
    """A canned signal: always returns its fixed result (or abstains on None)."""

    name: str
    result: SignalResult | None

    def evaluate(self, bet: Bet, ctx: GameContext) -> SignalResult | None:
        return self.result


def _ctx(game_id: str = "g1") -> GameContext:
    return GameContext(
        game_id=game_id, season=2025,
        away=TeamContext(team="AWY"), home=TeamContext(team="HOM"),
    )


def _bet(p_model: float = 0.55, p_fair: float | None = 0.50) -> Bet:
    return Bet(game_id="g1", market="spread", side="home", book="book",
               price=-110, stake=1.0, p_model=p_model, p_fair=p_fair)


def test_composite_blends_edge_and_renormalized_signals_exactly() -> None:
    config = IntelConfig(signal_weights={"a": 0.3, "b": 0.1})
    signals = [
        _Stub("a", SignalResult("a", 0.5, "confirms")),
        _Stub("b", SignalResult("b", -1.0, "contradicts")),
        _Stub("c", None),  # abstains: drops out of the weighting entirely
    ]
    verdict = assess(_bet(), _ctx(), signals, config)
    # edge 0.05 at reference 0.05 → edge_score 1.0.
    assert verdict.edge_score == pytest.approx(1.0)
    # context = (0.3·0.5 + 0.1·(−1)) / 0.4 = 0.125.
    assert verdict.context_score == pytest.approx(0.125)
    # composite = 0.4·1.0 + 0.6·((0.125 + 1) / 2) = 0.7375 → tier A.
    assert verdict.score == pytest.approx(0.7375)
    assert verdict.tier == "A"
    assert len(verdict.signals) == 2


def test_edge_score_clips_and_neutralizes_missing_fair_prob() -> None:
    thin = assess(_bet(p_model=0.51, p_fair=0.50), _ctx(), [])
    assert thin.edge_score == pytest.approx(0.2)  # 0.01 / 0.05
    fat = assess(_bet(p_model=0.70, p_fair=0.50), _ctx(), [])
    assert fat.edge_score == pytest.approx(1.0)  # clipped
    unknown = assess(_bet(p_fair=None), _ctx(), [])
    assert unknown.edge_score == pytest.approx(0.5)


def test_no_active_signal_leaves_context_neutral() -> None:
    verdict = assess(_bet(), _ctx(), [_Stub("a", None)])
    assert verdict.context_score == 0.0
    # composite = 0.4·1.0 + 0.6·0.5 = 0.7.
    assert verdict.score == pytest.approx(0.7)
    assert verdict.rationale() == "no context signal spoke"


def test_contradicting_context_demotes_the_tier() -> None:
    config = IntelConfig(signal_weights={"a": 1.0})
    against = [_Stub("a", SignalResult("a", -0.9, "the football disagrees"))]
    verdict = assess(_bet(p_model=0.53), _ctx(), against, config)
    # composite = 0.4·0.6 + 0.6·(0.05/2) = 0.27 → tier C.
    assert verdict.score == pytest.approx(0.27)
    assert verdict.tier == "C"


def test_veto_flags_regardless_of_composite() -> None:
    config = IntelConfig(signal_weights={"a": 0.1, "b": 1.0})
    signals = [
        _Stub("a", SignalResult("a", -1.0, "QB out", veto=True)),
        _Stub("b", SignalResult("b", 1.0, "everything else confirms")),
    ]
    verdict = assess(_bet(p_model=0.70), _ctx(), signals, config)
    assert verdict.tier == TIER_FLAGGED
    assert verdict.vetoed
    assert verdict.rationale().startswith("VETO — QB out")


def test_veto_disabled_becomes_an_ordinary_negative_signal() -> None:
    config = IntelConfig(signal_weights={"a": 1.0}, veto_enabled=False)
    signals = [_Stub("a", SignalResult("a", -1.0, "QB out", veto=True))]
    verdict = assess(_bet(p_model=0.70), _ctx(), signals, config)
    assert not verdict.vetoed
    # composite = 0.4·1.0 + 0.6·0 = 0.4 → tier C under the defaults.
    assert verdict.score == pytest.approx(0.4)
    assert verdict.tier == "C"


def test_unweighted_signal_is_reported_but_not_scored() -> None:
    config = IntelConfig(signal_weights={})
    signals = [_Stub("mystery", SignalResult("mystery", 1.0, "novel evidence"))]
    verdict = assess(_bet(), _ctx(), signals, config)
    assert verdict.context_score == 0.0
    assert "novel evidence" in verdict.rationale()


def test_assess_bets_skips_games_without_context() -> None:
    bets = [
        _bet(),
        Bet(game_id="g9", market="spread", side="home", book="book",
            price=-110, stake=1.0, p_model=0.55, p_fair=0.50),
    ]
    verdicts = assess_bets(bets, {"g1": _ctx()}, [])
    assert len(verdicts) == 1
    assert verdicts[0].bet.game_id == "g1"
