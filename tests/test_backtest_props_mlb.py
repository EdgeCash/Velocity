"""Prop CLV backtest (velocity.backtest.props_mlb) — pure, offline.

Two levels: the closing-attach + CLV math on a hand-built prop BetLog, and the
day-walk driver end to end on a two-snapshot prop archive with a real (tiny) model.
No network, no box scores — this is the grade-free CLV read.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from velocity.backtest.props_mlb import (
    PropBacktestReport,
    grade_prop_ledger,
    prop_clv_ledger,
    run_prop_archive_backtest,
    run_prop_shrink_sweep,
)
from velocity.ingest.mlb import normalize_boxscore
from velocity.ingest.theoddsapi import normalize_player_props
from velocity.models.game_mlb import MLBGameModel
from velocity.models.simulate_baseball import (
    BaseballSimConfig,
    Team,
    batter_from_rates,
    pitcher_from_rates,
)
from velocity.wagering.bet_log import Bet, BetLog
from velocity.wagering.props_slate import build_name_index
from velocity.wagering.slate import SlateConfig

FIXTURES = Path(__file__).parent / "fixtures"
AVG_BAT = {"k": 0.225, "bb": 0.085, "hbp": 0.011, "hr": 0.035, "in_play": 0.644}
AVG_BIP = {"single": 0.222, "double": 0.068, "triple": 0.008, "out_bip": 0.702}
AVG_PIT = {"k": 0.225, "bb": 0.080, "hbp": 0.011, "hr": 0.035, "in_play": 0.649}
HIGH_K_PIT = {"k": 0.32, "bb": 0.06, "hbp": 0.01, "hr": 0.03, "in_play": 0.58}


def _props_json() -> list[dict]:
    return json.loads((FIXTURES / "theoddsapi_mlb_props.json").read_text())


# --- the CLV math (pure) ----------------------------------------------------


def test_prop_clv_ledger_attaches_close_and_scores() -> None:
    log = BetLog()
    log.add(Bet(game_id="g1", market="pitcher_strikeouts", side="over", book="dk",
                price=-110, stake=2.0, p_model=0.6, point=6.5, player="Kershaw"))
    log.add(Bet(game_id="g1", market="total_bases", side="under", book="dk",
                price=100, stake=1.0, p_model=0.55, point=1.5, player="Ohtani"))
    # Kershaw's over closed at a higher number and a worse price → we beat both;
    # Ohtani has no closing row → CLV is undefined, not zero.
    closing = pd.DataFrame([{
        "game_id": "g1", "market": "pitcher_strikeouts", "player": "Kershaw",
        "side": "over", "price": -130, "point": 8.5,
    }])
    led = prop_clv_ledger(log, closing)

    k = led[led["player"] == "Kershaw"].iloc[0]
    assert k["line_clv"] == 2.0          # over 6.5 vs close 8.5 → +2 points in our favor
    assert k["price_clv"] > 0            # -110 beats a -130 close
    o = led[led["player"] == "Ohtani"].iloc[0]
    assert pd.isna(o["price_clv"]) and pd.isna(o["line_clv"])  # unmatched → undefined


# --- the day-walk driver (real tiny model, two-snapshot archive) ------------


def _model_and_names() -> tuple[MLBGameModel, dict[str, str]]:
    lad_lineup = [batter_from_rates("660271", AVG_BAT, AVG_BIP)]
    lad_lineup += [batter_from_rates(f"lad{i}", AVG_BAT, AVG_BIP) for i in range(8)]
    lad = Team(lineup=lad_lineup, pitcher=pitcher_from_rates("477132", HIGH_K_PIT))
    sf = Team(
        lineup=[batter_from_rates(f"sf{i}", AVG_BAT, AVG_BIP) for i in range(9)],
        pitcher=pitcher_from_rates("sf_p", AVG_PIT),
    )
    model = MLBGameModel(
        teams={"LAD": lad, "SF": sf},
        config=BaseballSimConfig(n_sims=1500, starter_outs=18),
        seed=3,
    )
    stats = pd.DataFrame(
        {"player_id": ["477132", "660271"], "player_name": ["Clayton Kershaw", "Shohei Ohtani"]}
    )
    return model, build_name_index(stats)


EVENTS = pd.DataFrame({
    "game_id": ["evt-mlb-001"],  # the id the prop fixture carries
    "home_team": ["Los Angeles Dodgers"],
    "away_team": ["San Francisco Giants"],
    "kickoff": pd.to_datetime(["2026-07-24T23:10:00"]),
})


def _archive() -> pd.DataFrame:
    base = normalize_player_props(_props_json())
    entry = base.assign(snapshot="2026-07-24T18:00:00Z")          # >3h pre → entry
    close = base.assign(snapshot="2026-07-24T22:00:00Z", price=base["price"] - 12)  # near-close
    return pd.concat([entry, close], ignore_index=True)


def test_prop_archive_backtest_prices_and_scores_clv() -> None:
    seen: list[str] = []

    def factory(date: str):
        seen.append(date)
        return _model_and_names()

    report = run_prop_archive_backtest(
        _archive(), EVENTS, factory, config=SlateConfig(exclude_closing=False, min_edge=0.0)
    )

    assert isinstance(report, PropBacktestReport)
    assert seen == ["2026-07-24"]  # one point-in-time model on the game's ET date
    led = report.ledger
    assert not led.empty  # beatable prop lines cleared the 0% edge
    assert set(led["market"]).issubset({"pitcher_strikeouts", "total_bases", "hits"})
    assert {"price_clv", "line_clv"}.issubset(led.columns)
    assert report.summary["n_bets"] == len(led)
    assert not report.clv_by_market.empty


def _boxscores() -> pd.DataFrame:
    box = json.loads((FIXTURES / "mlb_boxscore.json").read_text())
    return normalize_boxscore(box, game_id="evt-mlb-001")


def test_grade_prop_ledger_scores_over_under_and_pending() -> None:
    # Box score: Kershaw 7 K (pitching), Ohtani 7 total bases.
    box = _boxscores()
    _, name_to_id = _model_and_names()  # "Clayton Kershaw"→477132, "Shohei Ohtani"→660271
    ledger = pd.DataFrame([
        {"game_id": "evt-mlb-001", "market": "pitcher_strikeouts", "player": "Clayton Kershaw",
         "side": "over", "point": 6.5, "price": -110, "stake": 2.0, "p_model": 0.6,
         "price_clv": 0.05, "line_clv": 1.0},
        {"game_id": "evt-mlb-001", "market": "total_bases", "player": "Shohei Ohtani",
         "side": "under", "point": 1.5, "price": 100, "stake": 1.0, "p_model": 0.55,
         "price_clv": -0.02, "line_clv": -0.5},
        {"game_id": "evt-mlb-001", "market": "hits", "player": "Ghost Runner",
         "side": "over", "point": 0.5, "price": -120, "stake": 1.0, "p_model": 0.7,
         "price_clv": 0.0, "line_clv": 0.0},
    ])
    graded = grade_prop_ledger(ledger, box, name_to_id)

    assert graded["result"].tolist() == ["win", "loss", "pending"]
    assert graded.loc[0, "profit"] == pytest.approx(2.0 * (100 / 110))  # win at -110
    assert graded.loc[1, "profit"] == -1.0  # under 1.5 vs 7 total bases → loss
    assert pd.isna(graded.loc[2, "profit"])  # unresolved player → pending


def test_prop_archive_backtest_graded_gives_full_scorecard() -> None:
    report = run_prop_archive_backtest(
        _archive(), EVENTS, lambda d: _model_and_names(),
        boxscores=_boxscores(), config=SlateConfig(exclude_closing=False, min_edge=0.0),
    )
    assert "result" in report.ledger.columns
    assert (report.ledger["result"] != "pending").any()  # box scores actually graded bets
    # graded → the scorecard summary (record + ROI + ECE), not just CLV
    assert {"wins", "losses", "roi", "ece"} <= set(report.summary)
    assert not report.calibration.empty


def test_prop_shrink_sweep_prices_once_and_scales_confidence() -> None:
    cfg = SlateConfig(exclude_closing=False, min_edge=0.0)
    reports = run_prop_shrink_sweep(
        _archive(), EVENTS, lambda d: _model_and_names(), [1.0, 0.5], base_config=cfg
    )
    assert set(reports) == {1.0, 0.5}
    raw, shrunk = reports[1.0].ledger, reports[0.5].ledger
    assert not raw.empty

    # shrink=1.0 is the raw model — bit-identical to the plain archive backtest.
    plain = run_prop_archive_backtest(
        _archive(), EVENTS, lambda d: _model_and_names(), config=cfg
    )
    assert raw["p_model"].tolist() == plain.ledger["p_model"].tolist()

    # 0.5 pulls every retained bet's confidence halfway to 0.5 (p → 0.5 + 0.5·(p−0.5)).
    keys = ["game_id", "market", "player", "side"]
    merged = raw.merge(shrunk, on=keys, suffixes=("_raw", "_s"))
    assert not merged.empty
    assert np.allclose(merged["p_model_s"], 0.5 + 0.5 * (merged["p_model_raw"] - 0.5))
    assert len(shrunk) <= len(raw)  # shrinking edge can only drop bets, never add


def test_empty_prop_archive_yields_empty_report() -> None:
    report = run_prop_archive_backtest(
        _archive().iloc[:0], EVENTS.iloc[:0], lambda d: _model_and_names()
    )
    assert report.ledger.empty
    assert report.summary["n_bets"] == 0
    assert report.clv_by_market.empty
