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


# --------------------------------------------------------------------------
# rush_rec_yards and interceptions must SETTLE, not just price (2026-09-16)
# --------------------------------------------------------------------------
#
# grade_prop_ledger looks the market name up as a COLUMN of the weekly
# actuals, so a market added to PROP_MARKETS and left out of
# normalize_weekly_stats would price, stake, and then sit `pending` forever —
# a bet the record can never close. These pin the settlement path.


def test_the_two_new_markets_are_columns_of_the_weekly_actuals() -> None:
    weekly = normalize_weekly_stats(WEEKLY_RAW)
    assert "rush_rec_yards" in weekly.columns
    assert "interceptions" in weekly.columns


def test_combined_yards_actual_is_the_sum_of_its_own_legs() -> None:
    weekly = normalize_weekly_stats(WEEKLY_RAW)
    pacheco = weekly[weekly["player_name"] == "Isiah Pacheco"].iloc[0]
    assert pacheco["rush_rec_yards"] == 71.0 + 22.0
    # It can never disagree with the legs it is made of.
    for row in weekly.to_dict("records"):
        assert row["rush_rec_yards"] == row["rush_yards"] + row["receiving_yards"]


def test_the_new_markets_actually_settle() -> None:
    weekly = normalize_weekly_stats(WEEKLY_RAW)
    raw = WEEKLY_RAW.assign(passing_interceptions=[1.0, 0.0, 0.0])
    ledger = pd.DataFrame(
        [
            # Pacheco 71 + 22 = 93 against 88.5 → the over wins.
            {"player": "Isiah Pacheco", "market": "rush_rec_yards", "side": "over",
             "point": 88.5, "price": -110},
            {"player": "Isiah Pacheco", "market": "rush_rec_yards", "side": "under",
             "point": 88.5, "price": -110},
            # Allen threw 1 against 0.5 → the over wins.
            {"player": "Josh Allen", "market": "interceptions", "side": "over",
             "point": 0.5, "price": 120},
        ]
    )
    graded = grade_prop_ledger(ledger, normalize_weekly_stats(raw))
    assert list(graded["result"]) == ["win", "loss", "win"]
    assert list(graded["actual"]) == [93.0, 93.0, 1.0]
    assert weekly["rush_rec_yards"].notna().all()


def test_a_missing_interceptions_column_stays_pending_not_a_free_under() -> None:
    """nflverse has already moved this spelling once.

    Defaulting an absent column to 0.0 — the convention the older markets in
    this normalizer use — would settle every interception UNDER as a winner on
    a feed change. "Nobody threw a pick" is a claim, and the record does not
    guess (inactive is not under).
    """
    weekly = normalize_weekly_stats(WEEKLY_RAW)  # carries neither spelling
    graded = grade_prop_ledger(
        pd.DataFrame(
            [{"player": "Josh Allen", "market": "interceptions", "side": "under",
              "point": 0.5, "price": -110}]
        ),
        weekly,
    )
    assert graded["result"].iloc[0] == "pending"


def test_either_interception_spelling_is_read() -> None:
    for column in ("passing_interceptions", "interceptions"):
        raw = WEEKLY_RAW.assign(**{column: [2.0, 0.0, 0.0]})
        weekly = normalize_weekly_stats(raw)
        allen = weekly[weekly["player_name"] == "Josh Allen"].iloc[0]
        assert allen["interceptions"] == 2.0, column


def test_a_player_with_no_stat_line_grades_neither_new_market() -> None:
    raw = pd.concat(
        [
            WEEKLY_RAW,
            pd.DataFrame([{
                "player_id": "00-004", "player_display_name": "Inactive Back",
                "position": "RB", "recent_team": "KC", "season": 2026, "week": 1,
            }]),
        ],
        ignore_index=True,
    )
    index = actuals_index(normalize_weekly_stats(raw))
    assert "rush_rec_yards" not in index["inactiveback"]
    assert "interceptions" not in index["inactiveback"]


# --------------------------------------------------------------------------
# Attempt counts must settle too (2026-09-16)
# --------------------------------------------------------------------------


def test_the_attempt_markets_are_columns_of_the_weekly_actuals() -> None:
    weekly = normalize_weekly_stats(
        WEEKLY_RAW.assign(attempts=[38.0, 0.0, 0.0], carries=[5.0, 0.0, 17.0])
    )
    assert "rush_attempts" in weekly.columns
    assert "pass_attempts" in weekly.columns


def test_the_attempt_markets_actually_settle() -> None:
    weekly = normalize_weekly_stats(
        WEEKLY_RAW.assign(attempts=[38.0, 0.0, 0.0], carries=[5.0, 0.0, 17.0])
    )
    graded = grade_prop_ledger(
        pd.DataFrame([
            {"player": "Josh Allen", "market": "pass_attempts", "side": "over",
             "point": 35.5, "price": -110},
            {"player": "Isiah Pacheco", "market": "rush_attempts", "side": "under",
             "point": 15.5, "price": -110},
        ]),
        weekly,
    )
    assert list(graded["result"]) == ["win", "loss"]
    assert list(graded["actual"]) == [38.0, 17.0]


def test_a_missing_attempts_column_stays_pending_not_a_free_under() -> None:
    """nflverse has moved these spellings before.

    Defaulting an absent column to 0.0 would settle every attempts UNDER as a
    winner on a feed rename. "He had no carries" is a graded result; "the
    column is gone" is not.
    """
    weekly = normalize_weekly_stats(WEEKLY_RAW)  # carries neither column
    graded = grade_prop_ledger(
        pd.DataFrame([
            {"player": "Josh Allen", "market": "pass_attempts", "side": "under",
             "point": 35.5, "price": -110},
            {"player": "Isiah Pacheco", "market": "rush_attempts", "side": "under",
             "point": 15.5, "price": -110},
        ]),
        weekly,
    )
    assert list(graded["result"]) == ["pending", "pending"]


def test_alternate_attempt_spellings_are_read() -> None:
    for column, market in (("rushing_attempts", "rush_attempts"),
                           ("passing_attempts", "pass_attempts")):
        weekly = normalize_weekly_stats(WEEKLY_RAW.assign(**{column: [9.0, 0.0, 0.0]}))
        allen = weekly[weekly["player_name"] == "Josh Allen"].iloc[0]
        assert allen[market] == 9.0, column


def test_completions_settle_against_the_newly_banked_column() -> None:
    """The column this market waited on — nflverse served it, we never kept it."""
    weekly = normalize_weekly_stats(
        WEEKLY_RAW.assign(attempts=[38.0, 0.0, 0.0], completions=[23.0, 0.0, 0.0])
    )
    assert "pass_completions" in weekly.columns
    graded = grade_prop_ledger(
        pd.DataFrame([
            {"player": "Josh Allen", "market": "pass_completions", "side": "over",
             "point": 21.5, "price": -120},
        ]),
        weekly,
    )
    assert graded["result"].iloc[0] == "win"
    assert graded["actual"].iloc[0] == 23.0


def test_a_missing_completions_column_stays_pending() -> None:
    graded = grade_prop_ledger(
        pd.DataFrame([
            {"player": "Josh Allen", "market": "pass_completions", "side": "under",
             "point": 21.5, "price": -110},
        ]),
        normalize_weekly_stats(WEEKLY_RAW),
    )
    assert graded["result"].iloc[0] == "pending"
