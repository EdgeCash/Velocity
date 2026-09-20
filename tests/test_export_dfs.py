"""dfs.csv / dfs_optimizer.csv — one pool, four contest reads of it."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from velocity.export.dfs import (
    CONTEST_QUANTILES,
    DFS_COLUMNS,
    DFS_OPTIMIZER_COLUMNS,
    build_dfs,
    build_dfs_optimizer,
    dfs_distribution_frame,
    leverage_score,
    stack_rating,
)


def _pool() -> pd.DataFrame:
    return pd.DataFrame([
        {"player_name": "QB1", "team": "KC", "position": "QB",
         "salary": 7800, "points": 20.1},
        {"player_name": "WR1", "team": "KC", "position": "WR",
         "salary": 6900, "points": 12.4},
        {"player_name": "WR2", "team": "KC", "position": "WR",
         "salary": 4200, "points": 8.1},
        {"player_name": "RB1", "team": "BUF", "position": "RB",
         "salary": 5600, "points": 11.9},
    ])


def _samples() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(3)
    return {
        "QB1": rng.gamma(4.0, 5.0, 20_000),
        "WR1": rng.gamma(3.0, 4.0, 20_000),
        "WR2": rng.gamma(2.0, 4.0, 20_000),
        "RB1": rng.gamma(3.0, 4.0, 20_000),
    }


def test_distribution_frame_quantiles_are_ordered_and_exact() -> None:
    samples = _samples()
    frame = dfs_distribution_frame(samples).set_index("player")
    row = frame.loc["QB1"]
    assert row["projection"] == pytest.approx(float(np.mean(samples["QB1"])), abs=1e-2)
    assert row["p90"] == pytest.approx(float(np.percentile(samples["QB1"], 90)), abs=1e-2)
    assert row["median"] < row["p75"] < row["p90"] < row["p99"]


def test_distribution_frame_drops_an_empty_array() -> None:
    frame = dfs_distribution_frame({"nobody": np.array([]), "x": np.ones(4)})
    assert list(frame["player"]) == ["x"]


def test_contest_columns_read_the_documented_quantiles() -> None:
    dist = dfs_distribution_frame(_samples())
    row = build_dfs_optimizer(_pool(), dist).set_index("player").loc["QB1"]
    lookup = dist.set_index("player").loc["QB1"]
    for column, quantile in CONTEST_QUANTILES:
        assert row[column] == pytest.approx(float(lookup[quantile]), abs=1e-6)
    # Cash is the median and a GPP is the tail; they must not be the same cell.
    assert row["cash"] < row["gpp"] < row["ceiling"]


def test_value_score_is_points_per_thousand_and_refuses_a_free_board() -> None:
    pool = _pool()
    pool.loc[len(pool)] = {"player_name": "TIER1", "team": "KC", "position": "FLEX",
                           "salary": 0, "points": 15.0}
    out = build_dfs(pool).set_index("player")
    assert out.loc["QB1", "value_score"] == pytest.approx(20.1 / 7.8, abs=1e-2)
    # A salary-free board has no value per dollar, and says so with a null.
    assert pd.isna(out.loc["TIER1", "value_score"])


def test_stack_rating_scores_the_teammates_not_the_player() -> None:
    rating = stack_rating(_pool()).tolist()
    # WR2's partners (QB1 + WR1) are the slate's strongest; QB1's are the weakest.
    assert rating[2] == pytest.approx(10.0)
    assert rating[0] == pytest.approx(0.0)
    # RB1 is alone on his team — no stack to rate, so nothing is claimed.
    assert pd.isna(rating[3])


def test_stack_rating_abstains_when_every_player_is_the_same() -> None:
    flat = pd.DataFrame([
        {"team": "KC", "points": 10.0}, {"team": "KC", "points": 10.0},
    ])
    assert stack_rating(flat).isna().all()


def test_ownership_and_leverage_are_empty_without_a_projection() -> None:
    out = build_dfs(_pool(), dfs_distribution_frame(_samples()))
    assert out["ownership"].isna().all()
    assert out["leverage_score"].isna().all()


def test_leverage_rewards_a_strong_projection_at_low_ownership() -> None:
    # Ranked by projection: 0 > 1 > 2 > 3. Ranked by ownership: 0 > 1 > 3 > 2.
    projection = pd.Series([20.0, 12.0, 11.9, 8.0])
    ownership = pd.Series([0.40, 0.25, 0.02, 0.11])
    scores = leverage_score(projection, ownership).tolist()
    # Players the field has priced exactly right sit at the neutral 5.
    assert scores[0] == pytest.approx(5.0)
    assert scores[1] == pytest.approx(5.0)
    # Third-best projection at the lowest ownership is the leverage play...
    assert scores[2] > 5.0
    # ...and the worst projection at higher ownership is the opposite.
    assert scores[3] < 5.0


def test_leverage_abstains_without_enough_to_rank() -> None:
    one = pd.Series([12.0])
    assert leverage_score(one, pd.Series([0.2])).isna().all()
    assert leverage_score(pd.Series([12.0, 8.0]), pd.Series([None, None])).isna().all()


def test_both_exports_keep_their_column_contract_on_nothing() -> None:
    for builder, columns in (
        (build_dfs, DFS_COLUMNS), (build_dfs_optimizer, DFS_OPTIMIZER_COLUMNS)
    ):
        out = builder(None)
        expected = [c for c in columns if c not in ("generated_at", "season", "week")]
        assert list(out.columns) == expected
        assert out.empty
