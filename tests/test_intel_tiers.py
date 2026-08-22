"""Tier backtest: gating, pick construction, grading, and summary math."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.backtest.intel_tiers import (
    TierBacktestConfig,
    _context_bucket,
    summarize,
    tier_backtest,
)
from velocity.models.game_nfl import GameProjection
from velocity.models.simulate import GameSim


class _FixedModel:
    """Every matchup projects the same sampled distribution.

    Margins: 70 samples of +10, 30 of −10 (median +10 → fair spread −10,
    P(home covers −3) = 0.7). Totals: 70 samples of 50, 30 of 40 (median 50,
    P(over 45) = 0.7).
    """

    def project(
        self,
        home_team: str,
        away_team: str,
        *,
        neutral_site: bool = False,
        rng: np.random.Generator | None = None,
    ) -> GameProjection:
        margin = np.array([10.0] * 70 + [-10.0] * 30)
        total = np.array([50.0] * 70 + [40.0] * 30)
        home = (total + margin) / 2.0
        away = (total - margin) / 2.0
        return GameProjection(
            home_team=home_team, away_team=away_team,
            mu_home=float(home.mean()), mu_away=float(away.mean()),
            sim=GameSim(home_score=home, away_score=away),
        )


def _games() -> pd.DataFrame:
    rows = []
    teams = [("AAA", "BBB"), ("CCC", "DDD")]
    for week in (1, 2, 3):
        for i, (home, away) in enumerate(teams):
            rows.append({
                "game_id": f"s24w{week}g{i}",
                "season": 2024,
                "week": week,
                "kickoff": pd.Timestamp(f"2024-09-{6 + 7 * week:02d} 17:00"),
                "home_team": home,
                "away_team": away,
                # Home wins by 10; totals land at 40 (under 45).
                "home_score": 25.0,
                "away_score": 15.0,
                "neutral_site": False,
                "spread_line": 3.0,  # home favored by 3 at the close
                "total_line": 45.0,
            })
    return pd.DataFrame(rows)


def _run(config: TierBacktestConfig | None = None):
    config = config or TierBacktestConfig(min_train_games=1)
    games = _games()

    def factory(train: pd.DataFrame) -> _FixedModel:
        return _FixedModel()

    return tier_backtest(games, games, factory, config)


def test_picks_gate_grade_and_align_exactly() -> None:
    result = _run()
    picks = result.picks
    # Week 1 has no training data; weeks 2–3 × 2 games × 2 markets = 8 picks.
    assert len(picks) == 8
    assert set(picks["week"]) == {2, 3}

    spreads = picks[picks["market"] == "spread"]
    # Model margin +10 vs close +3 → home side at its own handicap −3.
    assert (spreads["side"] == "home").all()
    assert (spreads["point"] == -3.0).all()
    assert spreads["p_model"].tolist() == pytest.approx([0.7] * len(spreads))
    # Actual margin +10 covers −3 → every spread pick wins 100/110 per unit.
    assert (spreads["result"] == "win").all()
    assert spreads["profit"].iloc[0] == pytest.approx(100.0 / 110.0)

    totals = picks[picks["market"] == "total"]
    # Fair total 50 vs close 45 → over; actual total 40 → every over loses.
    assert (totals["side"] == "over").all()
    assert (totals["result"] == "loss").all()
    assert (totals["profit"] == -1.0).all()

    # Tiers assigned, and the edge component reflects the 0.2 edge (capped).
    assert set(picks["tier"]) <= {"A", "B", "C"}
    assert (picks["edge_score"] == 1.0).all()


def test_backtest_is_deterministic() -> None:
    first, second = _run().picks, _run().picks
    pd.testing.assert_frame_equal(first, second)


def test_ev_gate_excludes_coin_flips() -> None:
    # Raising min_edge past the model's 0.2 edge leaves nothing.
    result = _run(TierBacktestConfig(min_train_games=1, min_edge=0.25))
    assert result.picks.empty
    assert result.by_tier.empty


def test_missing_lines_produce_no_picks() -> None:
    games = _games().assign(spread_line=np.nan, total_line=np.nan)

    def factory(train: pd.DataFrame) -> _FixedModel:
        return _FixedModel()

    result = tier_backtest(games, games, factory, TierBacktestConfig(min_train_games=1))
    assert result.picks.empty


def test_summarize_win_rate_excludes_pushes_and_roi_includes_them() -> None:
    picks = pd.DataFrame([
        {"tier": "A", "result": "win", "profit": 0.9, "context_score": 0.4,
         "conviction": 0.7},
        {"tier": "A", "result": "loss", "profit": -1.0, "context_score": 0.2,
         "conviction": 0.66},
        {"tier": "A", "result": "push", "profit": 0.0, "context_score": 0.0,
         "conviction": 0.65},
        {"tier": "C", "result": "loss", "profit": -1.0, "context_score": -0.5,
         "conviction": 0.3},
    ])
    table = summarize(picks, "tier").set_index("tier")
    assert table.loc["A", "bets"] == 3
    assert table.loc["A", "win_rate"] == pytest.approx(0.5)  # push excluded
    assert table.loc["A", "roi"] == pytest.approx(-0.1 / 3)  # push included
    assert table.loc["C", "win_rate"] == 0.0


def test_context_buckets_cut_on_the_thresholds() -> None:
    picks = pd.DataFrame({"context_score": [0.15, 0.149, -0.15, -0.149, 0.0]})
    buckets = _context_bucket(picks)
    assert list(buckets) == [
        "confirming", "neutral", "contradicting", "neutral", "neutral",
    ]


def test_injury_history_makes_the_veto_fire_point_in_time() -> None:
    games = _games()

    def factory(train: pd.DataFrame) -> _FixedModel:
        return _FixedModel()

    injuries = pd.DataFrame([
        # AAA's QB is out in week 2 only: week-2 home-side picks on AAA are
        # vetoed (tier X); week 3 is untouched.
        {"season": 2024, "week": 2, "player_name": "Al Starter", "team": "AAA",
         "position": "QB", "status": "Out", "is_out": True},
    ])
    result = tier_backtest(
        games, games, factory, TierBacktestConfig(min_train_games=1),
        injuries=injuries,
    )
    picks = result.picks
    aaa_w2 = picks[(picks["week"] == 2) & (picks["market"] == "spread")
                   & (picks["game_id"] == "s24w2g0")]
    assert (aaa_w2["tier"] == "X").all() and len(aaa_w2) == 1
    assert aaa_w2["rationale"].str.contains("Al Starter").all()
    aaa_w3 = picks[(picks["week"] == 3) & (picks["market"] == "spread")
                   & (picks["game_id"] == "s24w3g0")]
    assert (aaa_w3["tier"] != "X").all()
    # The X rows flow into the tier summary like any other tier.
    assert "X" in set(result.by_tier["tier"])
