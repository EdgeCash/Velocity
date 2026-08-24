"""Engagement ledger — normalized comparison, not raw like-counting."""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.report.engagement import (
    cadence,
    compare_by_context,
    compare_styles,
    empty_ledger,
    engagement_rate,
    minimum_posts_for_signal,
)


def _ledger() -> pd.DataFrame:
    rows = []
    # DFS posts daily at a steady rate; wager posts are rarer but bigger.
    for i in range(14):
        rows.append({
            "post_id": f"d{i}", "style": "dfs",
            "posted_at": pd.Timestamp("2026-08-01") + pd.Timedelta(days=i),
            "league": "mlb", "reference": f"sheet{i}.png", "n_plays": 3.0,
            "followers": 1000.0,
            "prior_day_result": "red" if i % 2 else "green",
            "impressions": 2000.0, "likes": 20.0, "reposts": 3.0,
            "replies": 2.0, "profile_clicks": 5.0,
        })
    for i in range(4):
        rows.append({
            "post_id": f"w{i}", "style": "wager",
            "posted_at": pd.Timestamp("2026-08-01") + pd.Timedelta(days=i * 3),
            "league": "mlb", "reference": f"publish{i}", "n_plays": 2.0,
            "followers": 1000.0,
            # Wager posts collapse after a red night, hold after green.
            "prior_day_result": "red" if i % 2 else "green",
            "impressions": 2000.0, "likes": 8.0 if i % 2 else 60.0,
            "reposts": 1.0, "replies": 1.0, "profile_clicks": 2.0,
        })
    return pd.DataFrame(rows)


def test_engagement_rate_normalizes_per_follower() -> None:
    frame = engagement_rate(_ledger())
    first = frame.iloc[0]
    assert first["engagement"] == 25.0  # 20 likes + 3 reposts + 2 replies
    assert first["per_follower"] == pytest.approx(0.025)
    assert first["per_impression"] == pytest.approx(0.0125)


def test_engagement_rate_survives_missing_metrics() -> None:
    lean = pd.DataFrame([{"post_id": "x", "style": "dfs", "likes": 10.0,
                          "followers": 100.0}])
    frame = engagement_rate(lean)
    assert frame.iloc[0]["engagement"] == 10.0
    assert frame.iloc[0]["per_follower"] == pytest.approx(0.1)
    # No impressions recorded → the rate is unknown, never zero.
    assert pd.isna(frame.iloc[0]["per_impression"])
    # A zero follower count must not divide.
    zero = engagement_rate(pd.DataFrame([{"post_id": "y", "style": "dfs",
                                          "likes": 5.0, "followers": 0.0}]))
    assert pd.isna(zero.iloc[0]["per_follower"])


def test_compare_styles_uses_medians_so_one_viral_post_cannot_decide() -> None:
    ledger = _ledger()
    viral = ledger.iloc[[0]].copy()
    viral["post_id"] = "viral"
    viral["likes"] = 100000.0
    table = compare_styles(pd.concat([ledger, viral], ignore_index=True))
    dfs = table[table["style"] == "dfs"].iloc[0]
    # The median shrugs off the outlier — a mean would not.
    assert dfs["median_engagement"] == 25.0


def test_compare_by_context_exposes_the_forgiveness_gap() -> None:
    table = compare_by_context(_ledger())
    wager = table[table["style"] == "wager"].set_index("prior_day_result")
    dfs = table[table["style"] == "dfs"].set_index("prior_day_result")
    # The fixture encodes the hypothesis: wager posts crater after a red
    # night while DFS holds. The table has to surface exactly that.
    assert wager.loc["red", "median_per_follower"] < wager.loc["green", "median_per_follower"]
    assert dfs.loc["red", "median_per_follower"] == dfs.loc["green", "median_per_follower"]


def test_cadence_states_the_confound_out_loud() -> None:
    table = cadence(_ledger()).set_index("style")
    # DFS posts far more often; comparing summed engagement would just
    # measure this. The table exists so the confound is visible.
    assert table.loc["dfs", "posts"] == 14
    assert table.loc["wager", "posts"] == 4
    assert table.loc["dfs", "posts_per_week"] > table.loc["wager", "posts_per_week"]


def test_minimum_posts_guards_against_calling_it_early() -> None:
    table = minimum_posts_for_signal(_ledger()).set_index("style")
    # The wager fixture is noisy (8 vs 60 likes), so it needs MORE posts
    # before a difference means anything than the steady DFS stream does.
    assert table.loc["wager", "needed_per_style"] > table.loc["dfs", "needed_per_style"]
    assert table.loc["dfs", "needed_per_style"] >= 8  # never claim "2 posts is enough"


def test_empty_ledger_is_typed_and_comparisons_degrade() -> None:
    empty = empty_ledger()
    assert list(empty.columns)[:3] == ["post_id", "style", "posted_at"]
    assert compare_styles(empty).empty
    assert compare_by_context(empty).empty
    assert cadence(empty).empty
