"""Walk-forward MLB backtest driver (velocity.backtest.mlb) — pure, offline.

Prices a tiny two-day archive with a stub model (home is a strong favorite), so no
network. Pins the whole chain: entry board priced → bet staged where the price
offers an edge → graded against the closing board (CLV) and the final (win/loss),
one point-in-time model per game-day.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from velocity.backtest.mlb import BacktestReport, run_archive_backtest
from velocity.models.game_nfl import GameProjection
from velocity.models.simulate import GameSim

# Home wins ~65% of samples and by a few runs — a clear favorite the market underpriced.
_RNG = np.random.default_rng(0)
_HOME = _RNG.normal(5.2, 3.0, 20_000)
_AWAY = _RNG.normal(4.0, 3.0, 20_000)


def _favorite_home(home_key: str, away_key: str) -> GameProjection:
    sim = GameSim(home_score=_HOME, away_score=_AWAY)
    return GameProjection(home_team=home_key, away_team=away_key,
                          mu_home=5.2, mu_away=4.0, sim=sim)


def _ml(game_id, snapshot, side, price, kickoff_book="dk"):
    return {
        "line_id": f"{game_id}-{snapshot}-{side}", "game_id": game_id, "book": kickoff_book,
        "market": "moneyline", "side": side, "price": price, "point": None,
        "timestamp": snapshot.replace("Z", ""), "is_closing": True, "snapshot": snapshot,
    }


# Two game-days. Each game: an entry snapshot >3h before first pitch where home is a
# near-even underdog price (edge for our favorite model), and a closing snapshot just
# before first pitch where home has shortened (favorable move → positive CLV).
EVENTS = pd.DataFrame({
    "game_id": ["g1", "g2"],
    "home_team": ["Los Angeles Dodgers", "New York Yankees"],
    "away_team": ["San Francisco Giants", "Boston Red Sox"],
    "kickoff": pd.to_datetime(["2026-07-20T23:10:00", "2026-07-21T23:05:00"]),
})

LINES = pd.DataFrame([
    # g1 entry (18:00, >3h before 23:10): home +105 / away -125
    _ml("g1", "2026-07-20T18:00:00Z", "Los Angeles Dodgers", 105),
    _ml("g1", "2026-07-20T18:00:00Z", "San Francisco Giants", -125),
    # g1 close (22:00): home -140 / away +120 — home shortened
    _ml("g1", "2026-07-20T22:00:00Z", "Los Angeles Dodgers", -140),
    _ml("g1", "2026-07-20T22:00:00Z", "San Francisco Giants", 120),
    # g2 entry (18:00, >3h before 23:05): home +100 / away -120
    _ml("g2", "2026-07-21T18:00:00Z", "New York Yankees", 100),
    _ml("g2", "2026-07-21T18:00:00Z", "Boston Red Sox", -120),
    # g2 close (22:00): home -135 / away +115
    _ml("g2", "2026-07-21T22:00:00Z", "New York Yankees", -135),
    _ml("g2", "2026-07-21T22:00:00Z", "Boston Red Sox", 115),
])

# Both home favorites win.
FINALS = pd.DataFrame({
    "game_id": ["g1", "g2"],
    "home_score": [5.0, 6.0],
    "away_score": [3.0, 2.0],
})


def test_backtest_prices_grades_and_scores_clv() -> None:
    seen: list[str] = []

    def factory(date: str):
        seen.append(date)
        return _favorite_home, ["LAD", "SF", "NYY", "BOS"]

    report = run_archive_backtest(LINES, EVENTS, FINALS, factory)

    assert isinstance(report, BacktestReport)
    # One point-in-time model built per game-day (US-Eastern date, not UTC).
    assert set(seen) == {"2026-07-20", "2026-07-21"}

    ledger = report.ledger
    ml = ledger[(ledger["market"] == "moneyline") & (ledger["side"] == "home")]
    assert not ml.empty  # the favorite home moneyline cleared the edge on both days
    assert (ml["result"] == "win").all()  # both favorites won
    # We took +100/+105 and the home side shortened into the close → positive price CLV.
    assert (ml["price_clv"] > 0).all()

    summary = report.summary
    assert summary["n_bets"] == len(ledger)
    assert summary["wins"] >= 2
    assert summary["mean_price_clv"] > 0


def test_empty_archive_yields_empty_report() -> None:
    report = run_archive_backtest(
        LINES.iloc[:0], EVENTS.iloc[:0], FINALS.iloc[:0], lambda d: (_favorite_home, [])
    )
    assert report.ledger.empty
    assert report.summary["n_bets"] == 0
    assert report.clv_by_market.empty
