"""Historical close joining — consensus extraction and doubleheader matching.

The lab's market benchmarks are only as honest as this join: the close must be
the last pre-kickoff snapshot's median (one stale book never becomes "the
close"), the sign convention must match the datasets (positive = home
favored), and an MLB doubleheader's two games must each get their own number.
"""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.backtest.lab import inseason_variants

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "join_historical_closes",
    Path(__file__).resolve().parents[1] / "scripts" / "join_historical_closes.py",
)
jc = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(jc)


def _lines() -> pd.DataFrame:
    rows = []
    # Two snapshots; the later one (still pre-kickoff) must win. Two books at
    # the close → median.
    for ts, points in (("2026-08-18 17:00", (-1.5, 8.5)), ("2026-08-18 22:00", (1.5, 9.0))):
        for book, jitter in (("dk", 0.0), ("fd", 1.0)):
            spread, total = points
            rows += [
                {"game_id": "g1", "book": book, "market": "spread", "side": "home",
                 "point": spread + (jitter if book == "fd" else 0.0),
                 "price": -110, "timestamp": pd.Timestamp(ts)},
                {"game_id": "g1", "book": book, "market": "total", "side": "over",
                 "point": total + (jitter if book == "fd" else 0.0),
                 "price": -110, "timestamp": pd.Timestamp(ts)},
            ]
    # A post-kickoff snapshot that must be ignored entirely.
    rows.append({"game_id": "g1", "book": "dk", "market": "spread", "side": "home",
                 "point": 9.9, "price": -110,
                 "timestamp": pd.Timestamp("2026-08-19 01:00")})
    return pd.DataFrame(rows)


def _events() -> pd.DataFrame:
    return pd.DataFrame([{
        "game_id": "g1", "home_team": "Baltimore Orioles",
        "away_team": "New York Yankees",
        "kickoff": pd.Timestamp("2026-08-18 23:05"),
    }])


def test_closing_consensus_last_snapshot_median_and_sign() -> None:
    closes = jc.closing_consensus(_lines(), _events())
    assert len(closes) == 1
    row = closes.iloc[0]
    # Median of home points (1.5, 2.5) = 2.0 → spread_line = −2.0 (home dog).
    assert row["spread_line"] == pytest.approx(-2.0)
    assert row["total_line"] == pytest.approx(9.5)


def test_join_closes_handles_doubleheaders() -> None:
    games = pd.DataFrame([
        {"game_id": "a", "home_team": "Baltimore Orioles",
         "away_team": "New York Yankees",
         "kickoff": pd.Timestamp("2026-08-18 17:05"), "season": 2026},
        {"game_id": "b", "home_team": "Baltimore Orioles",
         "away_team": "New York Yankees",
         "kickoff": pd.Timestamp("2026-08-18 23:05"), "season": 2026},
    ])
    closes = pd.DataFrame([
        {"game_id": "p1", "home_team": "Baltimore Orioles",
         "away_team": "New York Yankees",
         "kickoff": pd.Timestamp("2026-08-18 17:00"),
         "spread_line": 1.0, "total_line": 8.0},
        {"game_id": "p2", "home_team": "Baltimore Orioles",
         "away_team": "New York Yankees",
         "kickoff": pd.Timestamp("2026-08-18 23:00"),
         "spread_line": -2.0, "total_line": 9.5},
    ])
    joined = jc.join_closes(games, closes).set_index("game_id")
    # Each game of the doubleheader gets its own close, not a shared one.
    assert joined.loc["a", "spread_line"] == pytest.approx(1.0)
    assert joined.loc["b", "spread_line"] == pytest.approx(-2.0)
    # A game with no candidate within tolerance stays null.
    lonely = games.assign(kickoff=pd.Timestamp("2026-09-01 17:00"))
    assert jc.join_closes(lonely, closes)["spread_line"].isna().all()


def test_inseason_variants_shapes() -> None:
    for league, bar in (("mlb", 5.0), ("wnba", 10.0)):
        variants = inseason_variants(league, 64)
        assert "scores" in variants and "recency-4" in variants
        assert all(kind == "games" for kind, _ in variants.values())
        # The factory prices end-to-end on a tiny frame.
        games = pd.DataFrame([
            {"season": 2026, "week": w, "home_team": "A", "away_team": "B",
             "home_score": 5 + w % 3, "away_score": 3, "neutral_site": False}
            for w in range(1, 7)
        ])
        model = variants["scores"][1](games)
        proj = model.project("A", "B")
        assert proj.mu_margin > 0
