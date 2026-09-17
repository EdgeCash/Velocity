"""The wager lab (velocity.backtest.wagers): rules scored against the close, offline.

Synthetic projections and games with known outcomes, so every number the
lab prints — the bets a rule makes, the side it takes, pushes, the price it
pays, the per-season robustness, the anchoring weight and the calibration
table — is checked against arithmetic done by hand.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from velocity.backtest.wagers import (
    BREAK_EVEN_110,
    WagerRule,
    anchoring_weight,
    calibration_table,
    grade_frame,
    records_frame,
    rule_weight,
    score_rule,
    select,
    side_probabilities,
    standard_rules,
)

REPO = Path(__file__).parent.parent


def _fixture() -> tuple[pd.DataFrame, pd.DataFrame]:
    # Six games: the model's total sits above the close by 5, 5, 1, −5, −7, 0;
    # the margin above the spread by 7, −7, 1, 0, 3, −2.
    projections = pd.DataFrame({
        "game_id": [f"g{i}" for i in range(6)],
        "season": [2024, 2024, 2024, 2025, 2025, 2025],
        "week": [1, 2, 3, 1, 2, 3],
        "p_home_win": [0.7, 0.3, 0.55, 0.5, 0.6, 0.45],
        "home_win": [1, 0, 1, 0, 1, 0],
        "mu_home": [30.0, 20.0, 25.0, 22.0, 24.0, 21.0],
        "mu_away": [20.0, 30.0, 25.0, 22.0, 24.0, 23.0],
        "sd_margin": 13.0, "sd_total": 13.0,
    })
    games = pd.DataFrame({
        "game_id": projections["game_id"],
        "home_score": [31.0, 17.0, 27.0, 20.0, 30.0, 24.0],
        "away_score": [14.0, 27.0, 24.0, 20.0, 24.0, 24.0],
        "spread_line": [3.0, -3.0, -1.0, 0.0, -3.0, 0.0],
        "total_line": [45.0, 45.0, 49.0, 49.0, 55.0, 44.0],
        "over_odds": [-110.0, -105.0, -110.0, -110.0, -110.0, -110.0],
        "under_odds": [-110.0, -115.0, -110.0, -110.0, -120.0, -110.0],
        "home_spread_odds": -110.0, "away_spread_odds": -110.0,
        "home_moneyline": [-200.0, 150.0, -120.0, 100.0, -150.0, 110.0],
        "away_moneyline": [170.0, -180.0, 100.0, -120.0, 130.0, -130.0],
    })
    return projections, games


def test_grade_frame_lines_prices_and_outcomes() -> None:
    projections, games = _fixture()
    frame = grade_frame(projections, games)
    assert len(frame) == 6
    assert frame["mu_total"].tolist() == [50.0, 50.0, 50.0, 44.0, 48.0, 44.0]
    # Totals: 45 > 45 push (g0), 44 < 45 under, 51 > 49 over, 40 < 49 under,
    # 54 < 55 under, 48 > 44 over.
    assert frame["over_hit"].tolist()[1:] == [0.0, 1.0, 0.0, 0.0, 1.0]
    assert pd.isna(frame["over_hit"].iloc[0])
    # Spreads: 17 > 3 cover, −10 < −3 no, 3 > −1 cover, 0 == 0 push, 6 > −3 cover, 0 == 0 push.
    assert frame["home_cover"].tolist()[:3] == [1.0, 0.0, 1.0]
    assert pd.isna(frame["home_cover"].iloc[3]) and pd.isna(frame["home_cover"].iloc[5])
    # Real prices kept; the de-vigged market probability of the home side.
    assert frame["under_odds"].tolist()[1] == -115.0
    q = frame["q_home"].iloc[0]
    assert q == pytest.approx((200 / 300) / ((200 / 300) + (100 / 270)))
    # A frame with no price columns is priced at −110 and has no moneyline.
    bare = grade_frame(projections, games[["game_id", "home_score", "away_score",
                                           "spread_line", "total_line"]])
    assert (bare["over_odds"] == -110.0).all() and bare["q_home"].isna().all()


def test_select_takes_the_side_the_points_say_and_pays_its_price() -> None:
    frame = grade_frame(*_fixture())
    bets = select(frame, WagerRule("total", "either", min_points=4.0))
    # |diff| ≥ 4: g0 (+5, push → dropped), g1 (+5 over, lost), g3 (−5 under, won),
    # g4 (−7 under, won at −120).
    assert bets["game_id"].tolist() == ["g1", "g3", "g4"]
    assert bets["side"].tolist() == ["over", "under", "under"]
    assert bets["won"].tolist() == [0.0, 1.0, 1.0]
    assert bets["price"].tolist() == [-105.0, -110.0, -120.0]
    assert bets["profit"].tolist() == pytest.approx([-1.0, 100 / 110, 100 / 120])
    unders = select(frame, WagerRule("total", "under", min_points=4.0))
    assert unders["game_id"].tolist() == ["g3", "g4"]
    overs = select(frame, WagerRule("total", "over", min_points=4.0))
    assert overs["game_id"].tolist() == ["g1"]
    # Spreads: |d| ≥ 6 → g0 (+7 home, covered), g1 (−7 away, won: home failed to cover).
    spreads = select(frame, WagerRule("spread", "either", min_points=6.0))
    assert spreads["side"].tolist() == ["home", "away"] and spreads["won"].tolist() == [1.0, 1.0]
    # Away only.
    away = select(frame, WagerRule("spread", "away", min_points=6.0))
    assert away["game_id"].tolist() == ["g1"]


def test_score_rule_counts_seasons_with_enough_bets() -> None:
    frame = grade_frame(*_fixture())
    record = score_rule(frame, WagerRule("total", "either", min_points=4.0), min_season_bets=1)
    assert record.n == 3 and record.win_rate == pytest.approx(2 / 3)
    assert record.roi == pytest.approx((-1.0 + 100 / 110 + 100 / 120) / 3)
    assert record.seasons == 2 and record.seasons_above_break_even == 1  # 2024: 0/1, 2025: 2/2
    assert record.per_season["n"].tolist() == [1, 2]
    strict = score_rule(frame, WagerRule("total", "either", min_points=4.0), min_season_bets=2)
    assert strict.seasons == 1 and strict.seasons_above_break_even == 1
    row = records_frame([record]).iloc[0]
    assert row["rule"] == "total-either-4pt" and row["worst_season"] == 0.0
    assert pytest.approx(110 / 210) == BREAK_EVEN_110


def test_anchoring_weight_recovers_a_planted_weight() -> None:
    rng = np.random.default_rng(1)
    n = 4000
    close = rng.normal(45.0, 5.0, n)
    model = close + rng.normal(0.0, 4.0, n)
    actual = close + 0.3 * (model - close) + rng.normal(0.0, 10.0, n)
    frame = pd.DataFrame({
        "total": actual, "total_line": close, "mu_total": model,
        "margin": 0.0, "spread_line": 0.0, "mu_margin": 0.0, "q_home": np.nan,
    })
    assert anchoring_weight(frame, "total") == pytest.approx(0.3, abs=0.05)
    assert anchoring_weight(frame, "spread") == 0.0  # no variation → 0
    assert np.isnan(anchoring_weight(frame, "moneyline"))  # no moneyline closes


def test_side_probabilities_and_calibration_table() -> None:
    frame = grade_frame(*_fixture())
    raw = side_probabilities(frame, "total")
    # g0: total 50 vs 45 at sd 13 → P(over) = Φ(5/13) ≈ 0.65; g3: 44 vs 49 → ≈ 0.35.
    assert raw.iloc[0] == pytest.approx(0.6496, abs=1e-3)
    assert raw.iloc[3] == pytest.approx(0.3504, abs=1e-3)
    anchored = side_probabilities(frame, "total", weight=0.1)
    assert anchored.iloc[0] == pytest.approx(0.5 + 0.1 * (raw.iloc[0] - 0.5))
    table = calibration_table(frame, "total", bins=(0.5, 0.6, 1.0))
    # Five graded games (the push drops), four with a lean — g5 sits at
    # exactly 0.5 and falls outside the left-open bins: g2 (0.53 over, won)
    # in the lower bin; g4 (under 0.70, won), g1 (over 0.65, lost) and g3
    # (under 0.65, won) in the upper.
    by_bin = table.set_index("bin")
    assert int(by_bin["n"].sum()) == 4
    assert by_bin.loc["(0.6, 1.0]", "n"] == 3
    assert by_bin.loc["(0.6, 1.0]", "actual"] == pytest.approx(2 / 3)


def test_standard_rules_cover_both_markets_and_fitted_weights_only() -> None:
    rules = standard_rules("nfl", {"spread": 0.03, "total": 0.07, "moneyline": float("nan")})
    names = {r.name for r in rules}
    assert {"total-under-4pt", "spread-away-6pt", "total-either-edge0.02@w0.07",
            "spread-either-edge0.01@w0.03"} <= names
    assert not any(r.market == "moneyline" for r in rules)  # nan weight → no edge rules
    assert all(r.min_edge is None for r in rules if r.market == "spread" and r.min_points > 0)


def test_the_cli_parses_rules_and_runs_on_the_committed_projections() -> None:
    spec = importlib.util.spec_from_file_location("wager_lab", REPO / "scripts" / "wager_lab.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    rule = mod.parse_rule("total-under-4pt", {"total": 0.07})
    assert rule == WagerRule("total", "under", min_points=4.0, weight=0.07)
    rule = mod.parse_rule("spread-either-edge0.02@w0.05", {})
    assert rule.min_edge == 0.02 and rule.weight == 0.05 and rule.side == "either"
    for league in ("nfl", "ncaaf"):
        path = REPO / "datasets" / league / "projections_promoted.parquet"
        assert path.exists(), "the promoted chain's projections are committed for the lab"
        games = pd.read_parquet(REPO / "datasets" / league / "games.parquet")
        frame = grade_frame(pd.read_parquet(path), games)
        assert len(frame) > 3000
        assert 0.0 < anchoring_weight(frame, "total") < 0.2


def test_rule_weight_maps_the_claim_onto_the_record() -> None:
    frame = grade_frame(*_fixture())
    rule = WagerRule("total", "either", min_points=4.0)
    w = rule_weight(frame, rule)
    # Three bets (g1 over lost, g3 under won, g4 under won): realized 2/3;
    # the picked sides' raw excess probabilities average over the same bets.
    bets = select(frame, rule)
    p_over = side_probabilities(frame.set_index("game_id").loc[bets["game_id"]].reset_index(),
                                "total").to_numpy()
    excess = np.where(bets["side"].to_numpy() == "over", p_over - 0.5, 0.5 - p_over).mean()
    assert w == pytest.approx((2 / 3 - 0.5) / excess)
    assert np.isnan(rule_weight(frame, WagerRule("total", "either", min_points=99.0)))

