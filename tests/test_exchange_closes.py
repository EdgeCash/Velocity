"""An exchange contract's own close, rebuilt from the banked snapshots.

A ladder rung cannot be graded against the sportsbook's consensus close on the
main number — a different strike is a different contract, and scoring one
against the other booked the whole distance between them as line value earned.
The honest benchmark is the venue's own last quote before the game started,
which the hourly collector already banks; this is the reader that finds it.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd

_SCRIPT = Path(__file__).parent.parent / "scripts" / "grade_yesterday.py"
FIX = Path(__file__).resolve().parent / "fixtures"


def _grader():
    spec = importlib.util.spec_from_file_location("grade_yesterday", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GAMES = pd.DataFrame(
    {
        "game_id": ["evt-phi-atl"],
        "kickoff": [pd.Timestamp("2026-09-11 23:15:00")],
        "home_team": ["Atlanta Braves"],
        "away_team": ["Philadelphia Phillies"],
    }
)


def _bank(root: Path, stamp: str, markets: list[dict]) -> None:
    raw = root / f"run-{stamp}" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / f"kalshi_KXMLBGAME_{stamp}.json").write_text(json.dumps({"markets": markets}))


def _mlb_markets(home_ask: str, away_ask: str) -> list[dict]:
    """Two winner contracts on one captured game, quoted at the given asks."""
    base = json.loads((FIX / "kalshi_mlb.json").read_text())["markets"]
    winners = [m for m in base if m["ticker"].startswith("KXMLBGAME")][:2]
    out = []
    for market, ask in zip(winners, (away_ask, home_ask), strict=False):
        out.append({**market, "status": "active", "yes_ask_dollars": ask})
    return out


def test_the_last_snapshot_before_first_pitch_is_the_close(tmp_path: Path) -> None:
    grader = _grader()
    _bank(tmp_path, "20260911T180000Z", _mlb_markets("0.5000", "0.5000"))
    _bank(tmp_path, "20260911T220000Z", _mlb_markets("0.5800", "0.4400"))
    closes = grader.exchange_closing_for_slate(tmp_path, GAMES, "mlb")
    assert closes is not None and not closes.empty
    assert set(closes["book"]) == {"kalshi"}
    assert set(closes["game_id"]) == {"evt-phi-atl"}
    # The 22:00 quote is the last one before the 23:15 first pitch, so it wins.
    priced = closes.set_index("side")["price"]
    assert priced.loc["home"] != priced.loc["away"], "the later, moved quote is the close"


def test_a_snapshot_taken_after_first_pitch_is_not_a_close(tmp_path: Path) -> None:
    """An in-play price is not the number anyone could have closed at."""
    grader = _grader()
    _bank(tmp_path, "20260912T020000Z", _mlb_markets("0.9000", "0.1200"))
    assert grader.exchange_closing_for_slate(tmp_path, GAMES, "mlb") is None


def test_an_empty_or_missing_bank_returns_nothing(tmp_path: Path) -> None:
    grader = _grader()
    assert grader.exchange_closing_for_slate(tmp_path / "absent", GAMES, "mlb") is None
    (tmp_path / "empty").mkdir()
    assert grader.exchange_closing_for_slate(tmp_path / "empty", GAMES, "mlb") is None


def test_the_close_carries_the_ladder_key_the_scorecard_matches_on(tmp_path: Path) -> None:
    # book and point are what separate a rung from the main line; without them
    # the scorecard would fall back to the sportsbook index again.
    grader = _grader()
    _bank(tmp_path, "20260911T220000Z", _mlb_markets("0.5800", "0.4400"))
    closes = grader.exchange_closing_for_slate(tmp_path, GAMES, "mlb")
    assert closes is not None
    assert set(closes.columns) == {"game_id", "market", "side", "book", "point", "price"}


def test_an_exchange_bet_now_scores_against_its_own_close(tmp_path: Path) -> None:
    """End to end: the reader's output is what gives a rung real CLV."""
    from velocity.report.scorecard import bets_from_slate

    grader = _grader()
    _bank(tmp_path, "20260911T220000Z", _mlb_markets("0.5800", "0.4400"))
    closes = grader.exchange_closing_for_slate(tmp_path, GAMES, "mlb")
    assert closes is not None
    row = closes[closes["side"] == "home"].iloc[0]

    slate = pd.DataFrame(
        {
            "game_id": ["evt-phi-atl"],
            "market": ["moneyline"],
            "side": ["home"],
            "point": [None],
            "book": ["kalshi"],
            # A clearly better number than the close, on the same contract.
            "price": [150.0],
            "stake": [1.0],
            "p_model": [0.55],
        }
    )
    bet = bets_from_slate(slate, closes)[0]
    assert bet.closing_price == float(row["price"])
    # Bought at +150 against a close the venue itself quoted: real, earned CLV.
    assert bet.price_clv() is not None and bet.price_clv() > 0
