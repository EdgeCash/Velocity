"""team_totals.csv — a market the owner bets, given a board of its own.

Team totals previously reached only plays.csv, and only when a play was
staked: the market appeared exactly when it had already been bet and nowhere
when it had not (docs/DECISIONS.md D2). These tests pin the board, and above
all that its numbers are the engine's own rather than a second derivation.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from velocity.export.meta import ExportMeta
from velocity.export.team_totals import (
    TEAM_TOTALS_COLUMNS,
    build_team_totals,
    export_team_totals,
    market_team_totals,
)
from velocity.models.simulate import SimConfig, simulate_game
from velocity.report.social import distributions_frame


class _Proj:
    def __init__(self, sim: object) -> None:
        self.sim = sim


def _sim(seed: int = 5):  # type: ignore[no-untyped-def]
    return simulate_game(3.0, 45.0, np.random.default_rng(seed),
                         SimConfig(n_sims=20_000))


def _games() -> pd.DataFrame:
    return pd.DataFrame([{"game_id": "g1", "league": "nfl",
                          "home_team": "Carolina", "away_team": "Atlanta"}])


def _board(home: float = 24.5, away: float = 21.5) -> pd.DataFrame:
    return pd.DataFrame([
        {"game_id": "g1", "market": "team_total_home", "side": "over",
         "point": home, "price": -110},
        {"game_id": "g1", "market": "team_total_home", "side": "under",
         "point": home, "price": -110},
        {"game_id": "g1", "market": "team_total_away", "side": "over",
         "point": away, "price": -115},
    ])


def test_the_distribution_carries_each_side_s_own_score() -> None:
    """Without the per-team pmf nothing downstream can price a team total."""
    dists = distributions_frame({"g1": _Proj(_sim())})
    assert set(dists["kind"]) == {"total", "margin", "home_score", "away_score"}
    for kind in ("home_score", "away_score"):
        assert dists[dists["kind"] == kind]["prob"].sum() == pytest.approx(1.0)


def test_over_probability_is_the_sims_own_number() -> None:
    """The same comparison slate.model_probability makes, on the same samples.

    Exported values carry the export's documented 4-dp rounding, so the
    engine's number is rounded the same way before comparing — the agreement
    being tested is the pricing, not the precision.
    """
    sim = _sim()
    out = build_team_totals(_games(), board=_board(),
                            distributions=distributions_frame({"g1": _Proj(sim)}))
    home = out[out["side"] == "home"].iloc[0]
    away = out[out["side"] == "away"].iloc[0]
    assert home["over_probability"] == pytest.approx(
        round(float(np.mean(sim.home_score > 24.5)), 4), abs=1e-12)
    assert away["over_probability"] == pytest.approx(
        round(float(np.mean(sim.away_score > 21.5)), 4), abs=1e-12)


def test_the_pmf_route_and_the_engine_agree_before_rounding() -> None:
    """The guard that matters: no second pricing model inside the export."""
    from velocity.export.games import prob_above

    sim = _sim()
    dists = distributions_frame({"g1": _Proj(sim)})
    pmf = dists[dists["kind"] == "home_score"][["value", "prob"]]
    for point in (13.5, 17.5, 20.5, 24.5, 31.5):
        assert prob_above(pmf, point) == pytest.approx(
            float(np.mean(sim.home_score > point)), abs=1e-12)


def test_the_model_number_is_the_median_the_slate_prices_against() -> None:
    """Not the mean: the median is the 50/50 over point the engine uses."""
    sim = _sim()
    out = build_team_totals(_games(), board=_board(),
                            distributions=distributions_frame({"g1": _Proj(sim)}))
    home = out[out["side"] == "home"].iloc[0]
    assert home["model_team_total"] == pytest.approx(float(np.median(sim.home_score)))


def test_edge_is_positive_toward_the_over() -> None:
    """The same direction games.csv's total_edge points."""
    sim = _sim()
    dists = distributions_frame({"g1": _Proj(sim)})
    low = build_team_totals(_games(), board=_board(home=17.5), distributions=dists)
    high = build_team_totals(_games(), board=_board(home=34.5), distributions=dists)
    assert low[low["side"] == "home"].iloc[0]["team_total_edge"] > 0
    assert high[high["side"] == "home"].iloc[0]["team_total_edge"] < 0


def test_one_row_per_side_naming_team_and_opponent() -> None:
    out = build_team_totals(_games(), board=_board(),
                            distributions=distributions_frame({"g1": _Proj(_sim())}))
    assert len(out) == 2
    home = out[out["side"] == "home"].iloc[0]
    assert (home["team"], home["opponent"]) == ("Carolina", "Atlanta")
    away = out[out["side"] == "away"].iloc[0]
    assert (away["team"], away["opponent"]) == ("Atlanta", "Carolina")


def test_an_unquoted_game_still_exports_the_models_number() -> None:
    """"Nobody is quoting it" and "it was never simulated" must read differently."""
    out = build_team_totals(_games(), board=None,
                            distributions=distributions_frame({"g1": _Proj(_sim())}))
    assert len(out) == 2
    row = out.iloc[0]
    assert row["model_team_total"] is not None
    assert row["market_team_total"] is None
    assert row["team_total_edge"] is None
    assert row["over_probability"] is None


def test_without_a_distribution_the_market_still_exports() -> None:
    out = build_team_totals(_games(), board=_board(), distributions=None)
    home = out[out["side"] == "home"].iloc[0]
    assert home["market_team_total"] == pytest.approx(24.5)
    assert home["model_team_total"] is None


def test_market_consensus_reads_the_over_rows_only() -> None:
    """Over and under share a number; counting both double-weights a book."""
    board = pd.DataFrame([
        {"game_id": "g1", "market": "team_total_home", "side": "over",
         "point": 24.5, "price": -110},
        {"game_id": "g1", "market": "team_total_home", "side": "under",
         "point": 24.5, "price": -110},
        {"game_id": "g1", "market": "team_total_home", "side": "over",
         "point": 25.5, "price": -108},
    ])
    quotes = market_team_totals(board)
    assert quotes[("g1", "team_total_home")] == pytest.approx(25.0)


def test_the_board_s_other_markets_are_ignored() -> None:
    board = pd.DataFrame([
        {"game_id": "g1", "market": "total", "side": "over", "point": 44.5,
         "price": -110},
        {"game_id": "g1", "market": "spread", "side": "home", "point": -3.0,
         "price": -110},
    ])
    assert market_team_totals(board) == {}


def test_empty_but_shaped_on_nothing(tmp_path: Path) -> None:
    out = build_team_totals(None)
    expected = [c for c in TEAM_TOTALS_COLUMNS
                if c not in ("generated_at", "season", "week")]
    assert list(out.columns) == expected
    assert out.empty
    meta = ExportMeta("2026-09-20T17:53:00Z", 2026, 3)
    path = export_team_totals(meta, None, out_dir=tmp_path)
    assert list(pd.read_csv(path).columns) == list(TEAM_TOTALS_COLUMNS)
