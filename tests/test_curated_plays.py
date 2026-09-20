"""The curated card — A+/A/B/Watch, and the argument beside each call."""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.wagering.plays import (
    TIER_A,
    TIER_A_PLUS,
    TIER_B,
    TIER_WATCH,
    PlaysConfig,
    assign_tier,
    build_plays,
    confidence_score,
    edge_score,
    explain,
    reason_text,
    rule_score,
    selection_text,
    tier_rank,
)
from velocity.wagering.tiers import RULE_TIERS, RuleTier, rule_named

NFL_UNDER = RULE_TIERS["nfl"][0]


def test_rule_named_finds_the_numbers_behind_a_banked_letter() -> None:
    rule = rule_named("nfl", "total", "A")
    assert rule is not None and rule.win_rate == pytest.approx(NFL_UNDER.win_rate)
    assert rule_named("nfl", "spread", "A") is None  # no promoted spread rule
    assert rule_named("mlb", "total", "A") is None


def test_edge_score_caps_and_floors() -> None:
    config = PlaysConfig()
    assert edge_score(0.0, config) == 0.0
    assert edge_score(-0.05, config) == 0.0  # a negative edge is not a small one
    assert edge_score(0.05, config) == pytest.approx(0.5)
    assert edge_score(0.40, config) == 1.0  # a 40% edge is a data error, not 4x
    assert edge_score(None, config) is None


def test_rule_score_scales_the_win_rate_by_seasons_cleared() -> None:
    config = PlaysConfig()
    strong = RuleTier("A", "total", frozenset({"under"}), 4.0, 0.60, 300, 15, 15)
    thin = RuleTier("A", "total", frozenset({"under"}), 4.0, 0.60, 300, 5, 15)
    assert rule_score(strong, config) == pytest.approx(1.0)
    assert rule_score(thin, config) == pytest.approx(1 / 3, abs=1e-6)
    assert rule_score(None, config) is None


def test_confidence_renormalizes_around_a_missing_component() -> None:
    # No rule and no intel: confidence is the edge component alone, not a
    # score penalized for silence.
    assert confidence_score(0.05, None, None) == pytest.approx(5.0)
    # A perfect everything is a ten.
    perfect = RuleTier("A", "total", frozenset({"under"}), 4.0, 0.60, 300, 15, 15)
    assert confidence_score(0.20, perfect, 1.0) == pytest.approx(10.0)


def test_confidence_is_zero_when_nothing_can_be_scored() -> None:
    assert confidence_score(None, None, None) == 0.0


def test_tiers_need_a_rule_for_the_top_shelf() -> None:
    config = PlaysConfig()
    high = 9.0
    assert assign_tier(confidence=high, stake=2.0, edge=0.06,
                       rule=NFL_UNDER, vetoed=False, config=config) == TIER_A_PLUS
    # Same confidence, no walk-forward record behind it: one shelf down.
    assert assign_tier(confidence=high, stake=2.0, edge=0.06,
                       rule=None, vetoed=False, config=config) == TIER_A
    assert assign_tier(confidence=3.0, stake=2.0, edge=0.02,
                       rule=None, vetoed=False, config=config) == TIER_B


def test_watch_covers_vetoed_unstaked_and_edgeless() -> None:
    config = PlaysConfig()
    common = {"confidence": 9.0, "rule": NFL_UNDER, "config": config}
    assert assign_tier(stake=2.0, edge=0.06, vetoed=True, **common) == TIER_WATCH
    assert assign_tier(stake=0.0, edge=0.06, vetoed=False, **common) == TIER_WATCH
    assert assign_tier(stake=2.0, edge=0.0, vetoed=False, **common) == TIER_WATCH
    assert assign_tier(stake=2.0, edge=None, vetoed=False, **common) == TIER_WATCH


def test_tier_rank_orders_the_card() -> None:
    assert tier_rank(TIER_A_PLUS) < tier_rank(TIER_A) < tier_rank(TIER_B)
    assert tier_rank(TIER_B) < tier_rank(TIER_WATCH)


def test_selection_text_names_the_team_not_the_side() -> None:
    assert selection_text("spread", "away", 2.5, home_team="Carolina",
                          away_team="Atlanta") == "Atlanta +2.5"
    assert selection_text("spread", "home", -6.0, home_team="Carolina",
                          away_team="Atlanta") == "Carolina -6"
    assert selection_text("total", "under", 44.5) == "Under 44.5"
    assert selection_text("moneyline", "home", None, home_team="Carolina",
                          away_team="Atlanta") == "Carolina ML"
    assert selection_text("pass_yds", "over", 265.5,
                          player="Patrick Mahomes") == "Patrick Mahomes Over 265.5 pass_yds"


def test_reason_states_the_case_and_flags_a_veto() -> None:
    text = reason_text(selection="Atlanta +2.5", market="spread", model_line=-2.2,
                       disagreement=4.7, probability=0.578, kelly_fraction=0.018,
                       confidence=8.2, rule=NFL_UNDER)
    assert "Model line -2.2" in text
    assert "+4.7 points" in text
    assert "57.8%" in text and "1.80%" in text and "confidence 8.2" in text
    assert "\n" not in text  # one line: Excel renders a wrapped cell badly
    vetoed = reason_text(selection="X", market="total", veto=True)
    assert vetoed.startswith("X — VETOED")


def test_explain_renders_the_same_argument_as_a_block() -> None:
    text = reason_text(selection="Atlanta +2.5", market="spread",
                       model_line=-2.2, disagreement=4.7)
    block = explain({"selection": "Atlanta +2.5", "reason": text}).splitlines()
    assert block[0] == "Atlanta +2.5"
    assert block[1] == "Model line -2.2"
    assert block[2] == "+4.7 points of disagreement"


def _slate() -> pd.DataFrame:
    return pd.DataFrame([
        {"game_id": "g1", "market": "total", "side": "under", "point": 48.5,
         "book": "dk", "price": -110, "p_model": 0.62, "p_fair": 0.52,
         "edge": 0.10, "stake": 2.4, "note": None, "rule_tier": "A", "league": "nfl"},
        {"game_id": "g1", "market": "spread", "side": "away", "point": 2.5,
         "book": "dk", "price": -108, "p_model": 0.545, "p_fair": 0.51,
         "edge": 0.035, "stake": 1.1, "note": None, "rule_tier": None, "league": "nfl"},
    ])


def _games() -> pd.DataFrame:
    return pd.DataFrame([{"game_id": "g1", "home_team": "Carolina",
                          "away_team": "Atlanta", "league": "nfl"}])


def _projections() -> pd.DataFrame:
    return pd.DataFrame([{"game_id": "g1", "fair_spread": 2.2, "fair_total": 44.0}])


def test_build_plays_ranks_and_argues() -> None:
    plays = build_plays(_slate(), games=_games(), projections=_projections())
    assert list(plays["tier"]) == [TIER_A_PLUS, TIER_B]
    top = plays.iloc[0]
    assert top["selection"] == "Under 48.5"
    assert top["rule_tier"] == "A"
    assert "rule A (unders 4+)" in top["reason"]
    # The spread play gets the team name and the model's line for its side.
    assert plays.iloc[1]["selection"] == "Atlanta +2.5"
    assert "Model line -2.2" in plays.iloc[1]["reason"]
    assert "+4.7 points" in plays.iloc[1]["reason"]


def test_disagreement_sign_always_favours_the_side_taken() -> None:
    slate = _slate()
    # Flip the total to the over at the same number: the model is BELOW it,
    # so the over's disagreement must go negative.
    slate.loc[0, "side"] = "over"
    plays = build_plays(slate, games=_games(), projections=_projections())
    under = plays[plays["market"] == "total"].iloc[0]
    assert "-4.5 points" in under["reason"]


def test_a_vetoed_play_is_watched_and_says_why() -> None:
    intel = pd.DataFrame([{
        "game_id": "g1", "player": None, "market": "total", "side": "under",
        "conviction": 0.8, "tier": "flagged", "recommended": False,
        "rationale": "VETO — starting QB ruled out",
    }])
    plays = build_plays(_slate(), games=_games(), projections=_projections(), intel=intel)
    row = plays[plays["market"] == "total"].iloc[0]
    assert row["tier"] == TIER_WATCH
    assert "VETOED" in row["reason"] and "QB ruled out" in row["reason"]


def test_a_paper_row_is_never_a_recommendation() -> None:
    slate = _slate()
    slate.loc[0, "note"] = "paper: market excluded"
    plays = build_plays(slate, games=_games(), projections=_projections())
    row = plays[plays["market"] == "total"].iloc[0]
    assert row["tier"] == TIER_WATCH
    assert "paper: market excluded" in row["reason"]


def test_props_ride_the_same_card() -> None:
    props = pd.DataFrame([{
        "game_id": "g1", "player": "Bijan Robinson", "market": "rush_yds",
        "side": "over", "point": 64.5, "book": "dk", "price": -115,
        "p_model": 0.60, "p_fair": 0.53, "edge": 0.07, "stake": 1.4, "note": None,
    }])
    plays = build_plays(None, props=props, games=_games(), projections=_projections())
    row = plays.iloc[0]
    assert row["bet_type"] == "prop"
    assert row["selection"] == "Bijan Robinson Over 64.5 rush_yds"


def test_build_plays_is_empty_but_shaped_on_nothing() -> None:
    assert build_plays(None).empty
    assert "tier" in build_plays(None).columns


def test_intel_conviction_lifts_confidence() -> None:
    intel = pd.DataFrame([{
        "game_id": "g1", "player": None, "market": "total", "side": "under",
        "conviction": 0.95, "tier": "A", "recommended": True,
        "rationale": "rest edge, both units trending",
    }])
    without = build_plays(_slate(), games=_games(), projections=_projections())
    with_intel = build_plays(_slate(), games=_games(),
                             projections=_projections(), intel=intel)
    a = without[without["market"] == "total"].iloc[0]["confidence"]
    b = with_intel[with_intel["market"] == "total"].iloc[0]["confidence"]
    assert b > a
    assert "rest edge" in with_intel[with_intel["market"] == "total"].iloc[0]["reason"]
