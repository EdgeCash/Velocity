"""Prop grading + shrink sweep — hand-checkable settlement and re-pricing.

Grading matches the book: over/under against the actual, exact line pushes,
missing player (inactive) stays pending. The sweep re-applies the live slate's
qualify rule at each shrink, so its per-market ROI table is exactly what the
calibration decision reads.
"""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.backtest.props_football import (
    actuals_index,
    grade_prop_ledger,
    sweep_shrink,
)
from velocity.ingest.nfl import normalize_weekly_stats

WEEKLY_RAW = pd.DataFrame(
    {
        "player_id": ["00-001", "00-002", "00-003"],
        "player_display_name": ["Josh Allen", "Travis Kelce", "Isiah Pacheco"],
        "position": ["QB", "TE", "RB"],
        "recent_team": ["BUF", "KC", "KC"],
        "season": [2026, 2026, 2026],
        "week": [1, 1, 1],
        "passing_yards": [287.0, 0.0, 0.0],
        "passing_tds": [2.0, 0.0, 0.0],
        "rushing_yards": [34.0, 0.0, 71.0],
        "rushing_tds": [1.0, 0.0, 0.0],
        "receptions": [0.0, 7.0, 3.0],
        "receiving_yards": [0.0, 81.0, 22.0],
        "receiving_tds": [0.0, 1.0, 0.0],
    }
)


def test_normalize_weekly_stats_maps_markets_and_atd() -> None:
    weekly = normalize_weekly_stats(WEEKLY_RAW)
    allen = weekly[weekly["player_name"] == "Josh Allen"].iloc[0]
    assert allen["pass_yards"] == 287.0
    assert allen["rush_yards"] == 34.0
    assert allen["anytime_td"] == 1.0  # rushing TD counts; passing TDs don't
    kelce = weekly[weekly["player_name"] == "Travis Kelce"].iloc[0]
    assert kelce["receptions"] == 7.0
    assert kelce["anytime_td"] == 1.0  # receiving TD


def test_actuals_index_is_name_normalized() -> None:
    index = actuals_index(normalize_weekly_stats(WEEKLY_RAW))
    assert index["joshallen"]["pass_yards"] == 287.0
    assert "traviskelce" in index


def _props() -> pd.DataFrame:
    return pd.DataFrame(
        [
            # Allen over 249.5 pass yards: actual 287 → win.
            {"player": "Josh Allen", "market": "pass_yards", "side": "over",
             "point": 249.5, "price": -110, "p_model": 0.62, "p_fair": 0.52},
            # Kelce under 7 receptions: actual exactly 7 → push.
            {"player": "Travis Kelce", "market": "receptions", "side": "under",
             "point": 7.0, "price": 100, "p_model": 0.55, "p_fair": 0.50},
            # Pacheco over 80.5 rush yards: actual 71 → loss.
            {"player": "Isiah Pacheco", "market": "rush_yards", "side": "over",
             "point": 80.5, "price": -110, "p_model": 0.58, "p_fair": 0.50},
            # A player with no stat line that week: pending, never guessed.
            {"player": "Mystery Man", "market": "receptions", "side": "over",
             "point": 3.5, "price": -110, "p_model": 0.60, "p_fair": 0.51},
        ]
    )


def test_grade_prop_ledger_settles_like_a_book() -> None:
    graded = grade_prop_ledger(_props(), normalize_weekly_stats(WEEKLY_RAW))
    assert graded["result"].tolist() == ["win", "push", "loss", "pending"]
    assert graded.loc[0, "profit"] == pytest.approx(100 / 110)
    assert graded.loc[1, "profit"] == 0.0
    assert graded.loc[2, "profit"] == -1.0
    assert pd.isna(graded.loc[3, "profit"])
    assert graded.loc[0, "actual"] == 287.0


def test_sweep_shrink_gates_on_the_shrunk_probability() -> None:
    graded = grade_prop_ledger(_props(), normalize_weekly_stats(WEEKLY_RAW))
    table = sweep_shrink(graded, [1.0, 0.1], min_edge=0.02)
    raw_all = table[(table["shrink"] == 1.0) & (table["market"] == "ALL")].iloc[0]
    # At shrink 1.0 all three settled bets clear their edges (pending never counts).
    assert raw_all["n_bets"] == 3
    # 1 win, 1 loss decided → 50% hit; profit (0.909 + 0 − 1)/3.
    assert raw_all["hit_rate"] == pytest.approx(0.5)
    assert raw_all["roi"] == pytest.approx((100 / 110 - 1.0) / 3)
    # At a crushing shrink nothing clears a 2% edge anymore.
    tiny = table[(table["shrink"] == 0.1) & (table["market"] == "ALL")].iloc[0]
    assert tiny["n_bets"] == 0
    # Per-market rows exist for every market that qualified at shrink 1.0.
    assert set(table[table["shrink"] == 1.0]["market"]) == {
        "ALL", "pass_yards", "receptions", "rush_yards",
    }


def test_sweep_requires_fair_probability() -> None:
    graded = grade_prop_ledger(_props(), normalize_weekly_stats(WEEKLY_RAW))
    graded.loc[:, "p_fair"] = float("nan")
    table = sweep_shrink(graded, [1.0])
    assert (table["n_bets"] == 0).all()  # no de-vigged pair → no qualified bet
