"""Slate — end-to-end projection → bet → logged CLV on a fixture market.

This is the Phase 2 acceptance path in miniature: a real projection priced
against a real two-sided line archive, producing a staked bet whose closing-line
value is logged and whose bankroll impact is graded — all deterministically.
"""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.features.team import fit_ratings
from velocity.models.game_nfl import NFLGameModel, NFLModelConfig
from velocity.models.simulate import SimConfig
from velocity.util.seed import make_rng
from velocity.wagering.slate import SlateConfig, build_slate, model_probability

GAME_ID = "2023_01_KC_DET"  # home DET, away KC in the games fixture


@pytest.fixture
def projection(plays: pd.DataFrame):
    model = NFLGameModel(fit_ratings(plays), NFLModelConfig(sim=SimConfig(n_sims=40_000)))
    return model.project("DET", "KC", rng=make_rng())


def test_model_probability_reads_the_sim(projection) -> None:
    p_over = model_probability(projection, "total", "over", 48.5)
    p_under = model_probability(projection, "total", "under", 48.5)
    assert p_over + p_under == pytest.approx(1.0, abs=1e-9)
    assert p_over > 0.7  # fair total ~57, so the over at 48.5 is very likely
    p_home = model_probability(projection, "moneyline", "home", None)
    assert p_home == pytest.approx(projection.p_home_win())


def test_slate_finds_the_soft_total(projection, market, games) -> None:
    log = build_slate({GAME_ID: projection}, market, games, SlateConfig())
    totals = [b for b in log.bets if b.market == "total"]
    assert len(totals) == 1  # exactly one total bet — the soft over
    bet = totals[0]
    assert bet.side == "over"
    assert bet.point == 48.5
    assert bet.price == -110  # shopped to the best opening number
    assert bet.book == "bookA"
    assert bet.p_model > 0.7


def test_slate_bet_has_positive_clv(projection, market, games) -> None:
    log = build_slate({GAME_ID: projection}, market, games, SlateConfig())
    over = next(b for b in log.bets if b.market == "total")
    # Market moved 48.5 → 50.5 toward our over: better number and better price.
    assert over.line_clv() == pytest.approx(2.0)
    assert over.price_clv() > 0


def test_slate_respects_bet_cap(projection, market, games) -> None:
    cfg = SlateConfig(starting_bankroll=100.0)
    log = build_slate({GAME_ID: projection}, market, games, cfg)
    bet = log.bets[0]
    # Even a monster edge is capped at max_bet_fraction of bankroll.
    assert bet.stake <= cfg.staking.max_bet_fraction * cfg.starting_bankroll + 1e-9
    assert bet.stake == pytest.approx(5.0)


def test_slate_settlement_grades_and_rolls_bankroll(projection, market, games) -> None:
    log = build_slate({GAME_ID: projection}, market, games, SlateConfig())
    out = log.settle(games, starting_bankroll=100.0)
    # DET 21, KC 20 → total 41 < 48.5, so the over loses despite the +CLV.
    assert out.loc[0, "result"] == "loss"
    assert out.loc[0, "bankroll"] == pytest.approx(95.0)


def test_slate_is_deterministic(projection, market, games) -> None:
    a = build_slate({GAME_ID: projection}, market, games, SlateConfig())
    b = build_slate({GAME_ID: projection}, market, games, SlateConfig())
    pd.testing.assert_frame_equal(a.settle(games), b.settle(games))


def test_slate_never_enters_on_the_closing_line(projection, market, games) -> None:
    # Entry timestamps must be strictly before the closing observation (20:00),
    # so CLV stays a meaningful measure rather than ~0.
    log = build_slate({GAME_ID: projection}, market, games, SlateConfig())
    entry_ts = log.bets[0].timestamp
    assert entry_ts == pd.Timestamp("2023-09-05 12:00:00")


def _segment_projection():
    """A hand-built segment-aware projection with exactly known probabilities.

    F5 margins [2, 0, −1, 1] → home wins 2, tie 1, loss 1 (tie-conditioned
    p_home = 2/3); first-inning totals [1, 0, 0, 0] → NRFI (under 0.5) = 3/4.
    """
    from types import SimpleNamespace

    import numpy as np
    from velocity.models.game_nfl import GameProjection
    from velocity.models.simulate import GameSim

    f5 = GameProjection(
        "H", "A", 2.0, 1.5,
        GameSim(np.array([3.0, 2.0, 1.0, 2.0]), np.array([1.0, 2.0, 2.0, 1.0])),
    )
    i1 = GameProjection(
        "H", "A", 0.25, 0.0,
        GameSim(np.array([1.0, 0.0, 0.0, 0.0]), np.array([0.0, 0.0, 0.0, 0.0])),
    )
    return SimpleNamespace(f5=f5, i1=i1)


def test_model_probability_prices_segments_exactly() -> None:
    proj = _segment_projection()
    # F5 moneyline conditions on no tie: 2 wins / (2 wins + 1 loss).
    assert model_probability(proj, "moneyline_f5", "home", None) == pytest.approx(2 / 3)
    assert model_probability(proj, "moneyline_f5", "away", None) == pytest.approx(1 / 3)
    # F5 run line and total read the segment sim.
    assert model_probability(proj, "spread_f5", "home", -0.5) == pytest.approx(0.5)
    assert model_probability(proj, "total_f5", "over", 3.5) == pytest.approx(0.5)
    # NRFI/YRFI at 0.5 partition the first inning.
    assert model_probability(proj, "total_i1", "under", 0.5) == pytest.approx(0.75)
    assert model_probability(proj, "total_i1", "over", 0.5) == pytest.approx(0.25)


def test_football_projection_cannot_price_segments(projection) -> None:
    # A projection without segment sims returns None — skipped, never mis-priced
    # off the full-game distribution.
    assert model_probability(projection, "total_i1", "under", 0.5) is None
    assert model_probability(projection, "moneyline_f5", "home", None) is None


def _segment_lines(game_id: str, ts: pd.Timestamp) -> pd.DataFrame:
    rows = [
        {"line_id": f"i1-{side}", "game_id": game_id, "book": "dk", "market": "total_i1",
         "side": side, "price": -110, "point": 0.5, "timestamp": ts, "is_closing": False}
        for side in ("over", "under")
    ] + [
        {"line_id": f"f5ml-{side}", "game_id": game_id, "book": "dk", "market": "moneyline_f5",
         "side": side, "price": price, "point": None, "timestamp": ts, "is_closing": False}
        for side, price in (("home", -120), ("away", 100))
    ]
    return pd.DataFrame(rows)


def test_slate_stakes_nrfi_and_f5_edges() -> None:
    games = pd.DataFrame({"game_id": ["g1"], "kickoff": [pd.Timestamp("2026-07-24 23:00")]})
    lines = _segment_lines("g1", pd.Timestamp("2026-07-24 12:00"))
    log = build_slate(
        {"g1": _segment_projection()}, lines, games, SlateConfig(exclude_closing=False)
    )
    by_market = {b.market: b for b in log.bets}
    # NRFI: model 75% vs a de-vigged 50% coin — a clear under.
    assert by_market["total_i1"].side == "under"
    assert by_market["total_i1"].p_model == pytest.approx(0.75)
    # F5 moneyline: tie-conditioned 2/3 home beats the 52% implied at −120.
    assert by_market["moneyline_f5"].side == "home"
    assert by_market["moneyline_f5"].p_model == pytest.approx(2 / 3)


def test_segment_lines_skipped_for_football(projection, games) -> None:
    lines = _segment_lines(GAME_ID, pd.Timestamp("2023-09-05 12:00:00"))
    log = build_slate({GAME_ID: projection}, lines, games, SlateConfig(exclude_closing=False))
    assert len(log) == 0  # no segment sims on a football projection → no bets


def test_no_lines_no_bets(projection, games) -> None:
    empty = pd.DataFrame(
        columns=[
            "line_id", "game_id", "book", "market", "side",
            "price", "point", "timestamp", "is_closing",
        ]
    )
    empty["timestamp"] = pd.to_datetime(empty["timestamp"])
    log = build_slate({GAME_ID: projection}, empty, games, SlateConfig())
    assert len(log) == 0
