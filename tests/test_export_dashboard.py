"""dashboard.csv — one long table a workbook pivots, computed from eval.metrics."""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.export.dashboard import (
    DASHBOARD_COLUMNS,
    SECTION_CLV,
    SECTION_PERFORMANCE,
    SECTION_ROI,
    SECTION_TOP_DFS,
    SECTION_TOP_PLAYS,
    SECTION_TOP_PROPS,
    build_dashboard,
)


def _record() -> pd.DataFrame:
    return pd.DataFrame([
        {"market": "total", "result": "win", "profit": 0.91, "stake": 1.0,
         "price_clv": 0.02, "line_clv": 0.5, "rule_tier": "A"},
        {"market": "total", "result": "loss", "profit": -1.0, "stake": 1.0,
         "price_clv": -0.01, "line_clv": -0.5, "rule_tier": "A"},
        {"market": "spread", "result": "win", "profit": 0.95, "stake": 1.0,
         "price_clv": 0.03, "line_clv": 1.0, "rule_tier": None},
        {"market": "spread", "result": "push", "profit": 0.0, "stake": 1.0,
         "price_clv": 0.0, "line_clv": 0.0, "rule_tier": None},
    ])


def _value(frame: pd.DataFrame, section: str, metric: str) -> float:
    rows = frame[(frame["section"] == section) & (frame["metric"] == metric)]
    assert len(rows) == 1, f"{section}/{metric} appears {len(rows)} times"
    return float(rows.iloc[0]["value"])


def test_performance_counts_decided_bets_only_for_the_win_rate() -> None:
    out = build_dashboard(_record())
    assert _value(out, SECTION_PERFORMANCE, "graded_bets") == 4
    assert _value(out, SECTION_PERFORMANCE, "pushes") == 1
    # 2-1 on decided bets; the push is neither a win nor a loss.
    assert _value(out, SECTION_PERFORMANCE, "win_rate") == pytest.approx(2 / 3, abs=1e-4)


def test_roi_is_profit_per_unit_staked() -> None:
    out = build_dashboard(_record())
    assert _value(out, SECTION_ROI, "roi_overall") == pytest.approx(0.86 / 4, abs=1e-4)
    assert _value(out, SECTION_ROI, "roi_total") == pytest.approx(-0.09 / 2, abs=1e-4)


def test_clv_reports_overall_and_per_tier() -> None:
    out = build_dashboard(_record())
    assert _value(out, SECTION_CLV, "pct_beat_close") == pytest.approx(0.5)
    assert _value(out, SECTION_CLV, "mean_price_clv_tier_A") == pytest.approx(0.005)
    assert _value(out, SECTION_CLV, "mean_price_clv_tier_none") == pytest.approx(0.015)


def test_empty_record_says_so_instead_of_reporting_zeros() -> None:
    out = build_dashboard(None)
    assert _value(out, SECTION_PERFORMANCE, "graded_bets") == 0
    clv = out[(out["section"] == SECTION_CLV) & (out["metric"] == "mean_price_clv")]
    assert pd.isna(clv.iloc[0]["value"])
    assert "no settled bets" in clv.iloc[0]["detail"]


def test_top_sections_carry_the_tier_and_the_reason() -> None:
    plays = pd.DataFrame([{"tier": "A+", "selection": "Under 48.5", "edge": 0.10,
                           "reason": "Model total 44 · confidence 8.1"}])
    props = pd.DataFrame([{"player": "Bijan Robinson", "market": "rush_yds",
                           "line": 64.5, "edge": 0.07, "hit_probability": 0.6,
                           "projection": 71.2}])
    dfs = pd.DataFrame([{"player": "WR2", "position": "WR", "team": "KC",
                         "salary": 4200, "projection": 8.1, "ceiling": 26.1,
                         "value_score": 1.93}])
    out = build_dashboard(_record(), plays=plays, props=props, dfs=dfs)
    play = out[out["section"] == SECTION_TOP_PLAYS].iloc[0]
    assert "[A+] Under 48.5" in play["detail"]
    assert _value(out, SECTION_TOP_PROPS, "prop_1") == pytest.approx(0.07)
    assert "Bijan Robinson" in out[out["section"] == SECTION_TOP_PROPS].iloc[0]["detail"]
    assert "ceiling 26.1" in out[out["section"] == SECTION_TOP_DFS].iloc[0]["detail"]


def test_top_sections_say_the_board_is_empty_rather_than_vanish() -> None:
    out = build_dashboard(_record())
    for section in (SECTION_TOP_PLAYS, SECTION_TOP_PROPS, SECTION_TOP_DFS):
        rows = out[out["section"] == section]
        assert len(rows) == 1 and rows.iloc[0]["metric"] == "none"


def test_the_shape_is_the_contract() -> None:
    out = build_dashboard(_record())
    expected = [c for c in DASHBOARD_COLUMNS if c not in
                ("generated_at", "season", "week")]
    assert list(out.columns) == expected
