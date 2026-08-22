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


def _team_total_lines(point_home: float) -> pd.DataFrame:
    ts = pd.Timestamp("2023-09-05 12:00")  # pre-kickoff (fixture kicks 09-07 20:20)
    rows = []
    for side, price in (("over", -110), ("under", -110)):
        rows.append({
            "line_id": f"{GAME_ID}|team_total_home|{side}|bookA|{point_home:g}",
            "game_id": GAME_ID, "book": "bookA", "market": "team_total_home",
            "side": side, "price": price, "point": point_home,
            "timestamp": ts, "is_closing": False,
        })
    return pd.DataFrame(rows)


def test_model_probability_prices_team_totals(projection) -> None:
    import numpy as np

    point = float(np.floor(np.median(projection.sim.home_score))) - 5.5  # half point: no pushes
    p_over = model_probability(projection, "team_total_home", "over", point)
    p_under = model_probability(projection, "team_total_home", "under", point)
    assert p_over + p_under == pytest.approx(1.0, abs=1e-9)
    assert p_over > 0.6  # ~six points below the fair number: the over is likely


def test_slate_bets_the_soft_team_total(projection, games) -> None:
    import numpy as np

    fair = float(np.median(projection.sim.home_score))
    lines = _team_total_lines(fair - 6.0)
    log = build_slate(
        {GAME_ID: projection}, lines, games, SlateConfig(exclude_closing=False)
    )
    bets = [b for b in log.bets if b.market == "team_total_home"]
    assert len(bets) == 1
    assert bets[0].side == "over"
    assert bets[0].point == pytest.approx(fair - 6.0)


def test_team_total_disagreement_gate_blocks_thin_edges(projection, games) -> None:
    import numpy as np

    fair = float(np.median(projection.sim.home_score))
    lines = _team_total_lines(fair - 6.0)
    cfg = SlateConfig(exclude_closing=False, min_team_total_disagreement=10.0)
    log = build_slate({GAME_ID: projection}, lines, games, cfg)
    assert not [b for b in log.bets if b.market.startswith("team_total")]
    # The full-game totals gate does not leak onto team totals and vice versa.
    cfg_totals_only = SlateConfig(exclude_closing=False, min_total_disagreement=99.0)
    log = build_slate({GAME_ID: projection}, lines, games, cfg_totals_only)
    assert [b for b in log.bets if b.market == "team_total_home"]
