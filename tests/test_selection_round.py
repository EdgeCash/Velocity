"""The selection round (docs/OUTPUT_AUDIT.md §3 #2–#4): per-market anchoring.

A market anchored at 0 believes the market and never qualifies — the way a
market with no edge leaves the board; a market anchored at its fitted weight
claims that share of the raw disagreement.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from velocity.models.game_nfl import GameProjection
from velocity.models.simulate import GameSim
from velocity.wagering.slate import SlateConfig, build_slate

STAMP = pd.Timestamp("2026-09-12 12:00:00")
GAMES = pd.DataFrame({"game_id": ["g1"], "kickoff": [pd.Timestamp("2026-09-13 17:00:00")]})


def _projection(mu_home: float, mu_away: float, sd: float = 13.0) -> GameProjection:
    rng = np.random.default_rng(3)
    n = 20_000
    margin = rng.normal(mu_home - mu_away, sd, n)
    total = rng.normal(mu_home + mu_away, sd, n)
    home = np.rint(np.clip((total + margin) / 2, 0, None))
    away = np.rint(np.clip((total - margin) / 2, 0, None))
    return GameProjection("HOME", "AWAY", mu_home, mu_away, GameSim(home, away))


def _board() -> pd.DataFrame:
    rows = [("spread", "home", -110, -3.0), ("spread", "away", -110, 3.0),
            ("total", "over", -110, 44.5), ("total", "under", -110, 44.5),
            ("moneyline", "home", -150, None), ("moneyline", "away", 130, None)]
    return pd.DataFrame([
        {"line_id": f"g1|{m}|{s}", "game_id": "g1", "market": m, "side": s, "price": p,
         "point": pt, "book": "bookA", "timestamp": STAMP}
        for m, s, p, pt in rows
    ])


def test_a_zero_weight_takes_the_market_off_the_board_and_a_fitted_one_scales_the_claim() -> None:
    # A model that likes the home side by a lot, and the over by a lot.
    proj = _projection(32.0, 20.0)
    raw = SlateConfig(exclude_closing=False, min_edge=0.02)
    markets_raw = {b.market for b in build_slate({"g1": proj}, _board(), GAMES, raw).bets}
    assert {"spread", "total", "moneyline"} <= markets_raw
    anchored = SlateConfig(
        exclude_closing=False, min_edge=0.02, model_weight=0.2,
        model_weight_by_market={"spread": 0.0, "moneyline": 0.0, "total": 0.29})
    bets = build_slate({"g1": proj}, _board(), GAMES, anchored).bets
    assert {b.market for b in bets} == {"total"}
    over = next(b for b in bets if b.market == "total")
    # The belief is the market's 0.5 plus 0.29 of the raw departure.
    raw_over = next(b for b in build_slate({"g1": proj}, _board(), GAMES, raw).bets
                    if b.market == "total")
    assert abs((over.p_model - over.p_fair) - 0.29 * (raw_over.p_model - raw_over.p_fair)) < 1e-9
    # A market not listed falls back to the league weight.
    assert anchored.model_weight_for("team_total_home") == 0.2
    assert anchored.model_weight_for("total") == 0.29
