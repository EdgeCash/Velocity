"""Pick sets: partition, ordering, the intel frame, and the console card."""

from __future__ import annotations

from velocity.intel.picks import build_pick_sets, intel_frame, render_pick_sets
from velocity.intel.score import TIER_FLAGGED, Conviction
from velocity.intel.signals import SignalResult
from velocity.wagering.bet_log import Bet


def _conviction(
    tier: str,
    score: float,
    *,
    game_id: str = "g1",
    player: str | None = None,
    signals: tuple[SignalResult, ...] = (),
) -> Conviction:
    bet = Bet(game_id=game_id, market="spread", side="home", book="book",
              price=-110, stake=2.5, p_model=0.56, p_fair=0.51, point=-3.0,
              player=player)
    return Conviction(bet=bet, edge_score=1.0, context_score=0.2,
                      score=score, tier=tier, signals=signals)


def test_pick_sets_partition_and_rank_by_conviction() -> None:
    convictions = [
        _conviction("B", 0.50, game_id="g2"),
        _conviction("A", 0.70, game_id="g1"),
        _conviction("A", 0.90, game_id="g3"),
        _conviction("C", 0.30, game_id="g4"),
        _conviction(TIER_FLAGGED, 0.80, game_id="g5"),
    ]
    sets = {s.key: s for s in build_pick_sets(convictions)}
    assert [c.bet.game_id for c in sets["prime"].picks] == ["g3", "g1"]
    assert [c.bet.game_id for c in sets["solid"].picks] == ["g2"]
    assert [c.bet.game_id for c in sets["model_only"].picks] == ["g4"]
    assert [c.bet.game_id for c in sets["flagged"].picks] == ["g5"]
    assert not sets["flagged"].recommended
    assert sets["prime"].recommended


def test_intel_frame_carries_the_verdict_columns() -> None:
    veto = SignalResult("injury", -1.0, "QB out", veto=True)
    frame = intel_frame([
        _conviction("A", 0.72),
        _conviction(TIER_FLAGGED, 0.6, player="Pat Quick", signals=(veto,)),
    ])
    assert list(frame["tier"]) == ["A", TIER_FLAGGED]
    assert list(frame["recommended"]) == [True, False]
    assert frame.loc[1, "player"] == "Pat Quick"
    assert frame.loc[1, "rationale"] == "VETO — QB out"
    assert frame.loc[0, "conviction"] == 0.72
    assert {"game_id", "market", "side", "book", "price", "stake",
            "context_score", "edge_score"} <= set(frame.columns)


def test_intel_frame_empty_keeps_schema() -> None:
    frame = intel_frame([])
    assert frame.empty
    assert "conviction" in frame.columns and "tier" in frame.columns


def test_render_pick_sets_reads_as_an_argued_card() -> None:
    confirm = SignalResult("matchup", 0.6, "unit EPA edge +0.6σ toward HOM")
    text = render_pick_sets(build_pick_sets([
        _conviction("A", 0.72, signals=(confirm,)),
        _conviction(TIER_FLAGGED, 0.6, game_id="g5",
                    signals=(SignalResult("injury", -1.0, "QB out", veto=True),)),
    ]))
    assert "Prime — model edge + confirming context (1):" in text
    assert "unit EPA edge +0.6σ toward HOM" in text
    assert "Flagged — vetoed on information the model can't see (1):" in text
    assert "VETO — QB out" in text
    assert "✗" in text  # flagged picks are visually distinct


def test_render_pick_sets_empty() -> None:
    assert "no assessed bets" in render_pick_sets(build_pick_sets([]))
