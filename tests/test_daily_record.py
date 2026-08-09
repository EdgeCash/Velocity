"""Yesterday's record — parlay settlement, record building, and the headline.

The model-status section is only worth trusting if it grades exactly the way a
book does, so every case here is hand-checkable: parlays settle leg by leg with
pushes dropping out, a play that can't be graded stays pending, and the headline
states the record and units verbatim.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest
from velocity.report.daily_record import (
    build_daily_record,
    empty_record,
    grade_parlay_frame,
    record_headline,
)

REPO = Path(__file__).parent.parent


# --- parlay settlement --------------------------------------------------------


def _parlay_row(legs: list[dict], stake: float = 2.0) -> pd.DataFrame:
    return pd.DataFrame(
        [{"legs": "x", "stake": stake, "price": 300, "legs_json": json.dumps(legs)}]
    )


# g1 finishes 27-20 home (total 47); g2 finishes 17-24 away.
FINALS = pd.DataFrame(
    {
        "game_id": ["g1", "g2"],
        "home_score": [27.0, 17.0],
        "away_score": [20.0, 24.0],
    }
)
BOX = pd.DataFrame(
    {"game_id": ["g1"], "player_id": ["p9"], "player_name": ["Josh Allen"],
     "pass_yards": [287.0]}
)


def test_parlay_wins_with_a_push_dropping_out() -> None:
    legs = [
        {"game_id": "g1", "market": "moneyline", "side": "home", "price": 100, "point": None},
        # The total lands exactly on 47 → this leg pushes and drops out.
        {"game_id": "g1", "market": "total", "side": "over", "price": 120, "point": 47.0},
        {"game_id": "g1", "market": "pass_yards", "side": "over", "price": -110,
         "point": 249.5, "player": "p9"},
    ]
    graded = grade_parlay_frame(_parlay_row(legs), FINALS, BOX)
    assert graded.loc[0, "result"] == "win"
    # Stake 2 at (2.0 × 1.909…) − 1 → the push contributed nothing.
    assert graded.loc[0, "profit"] == pytest.approx(2.0 * (2.0 * (1 + 100 / 110) - 1.0))


def test_parlay_single_lost_leg_loses_the_ticket() -> None:
    legs = [
        {"game_id": "g1", "market": "moneyline", "side": "home", "price": 100, "point": None},
        {"game_id": "g2", "market": "moneyline", "side": "home", "price": -130, "point": None},
    ]
    graded = grade_parlay_frame(_parlay_row(legs), FINALS)  # g2 went to the road team
    assert graded.loc[0, "result"] == "loss"
    assert graded.loc[0, "profit"] == pytest.approx(-2.0)


def test_parlay_with_ungradable_leg_is_pending() -> None:
    legs = [
        {"game_id": "g1", "market": "moneyline", "side": "home", "price": 100, "point": None},
        {"game_id": "gX", "market": "moneyline", "side": "home", "price": 100, "point": None},
    ]
    graded = grade_parlay_frame(_parlay_row(legs), FINALS)
    assert graded.loc[0, "result"] == "pending"


def test_parlay_all_push_pushes() -> None:
    legs = [
        {"game_id": "g1", "market": "total", "side": "over", "price": 100, "point": 47.0},
    ]
    graded = grade_parlay_frame(_parlay_row(legs), FINALS)
    assert graded.loc[0, "result"] == "push"
    assert graded.loc[0, "profit"] == 0.0


# --- record + headline --------------------------------------------------------


def _record() -> pd.DataFrame:
    games = pd.DataFrame(
        {
            "game_id": ["g1", "g2"],
            "market": ["total", "moneyline"],
            "side": ["under", "home"],
            "point": [47.5, None],
            "price": [-110, 120],
            "stake": [2.0, 3.0],
            "result": ["win", "loss"],
            "profit": [2.0 * 100 / 110, -3.0],
        }
    )
    props = pd.DataFrame(
        {
            "player": ["Josh Allen"],
            "market": ["pass_yards"],
            "side": ["over"],
            "point": [249.5],
            "price": [-110],
            "stake": [1.0],
            "result": ["win"],
            "profit": [100 / 110],
        }
    )
    parlays = pd.DataFrame(
        {"legs": ["A + B"], "price": [300], "stake": [1.0],
         "result": ["pending"], "profit": [float("nan")]}
    )
    return build_daily_record(
        games, props, parlays,
        matchups={"g1": "BUF @ KC", "g2": "DAL @ PHI"},
        slate_date="2026-09-13",
    )


def test_record_rows_and_labels() -> None:
    record = _record()
    assert record["section"].tolist() == ["games", "games", "props", "parlays"]
    assert record.loc[0, "play"] == "BUF @ KC"
    assert record.loc[2, "play"] == "Josh Allen"
    assert record.loc[3, "market"] == "parlay"


def test_record_headline_states_the_record() -> None:
    headline = record_headline(_record())
    assert headline == (
        "Yesterday (Sep 13): games 1-1 (-1.2u) · props 1-0 (+0.9u) · "
        "parlays 0-0 (+0.0u) [1 pending] · total -0.3u"
    )


def test_empty_record_headline() -> None:
    record = empty_record()
    record["slate_date"] = pd.NaT
    assert record_headline(record) == "Yesterday: no plays."


# --- grade_yesterday stamp selection ------------------------------------------


def _grade_yesterday_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "grade_yesterday", REPO / "scripts" / "grade_yesterday.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_prior_stamp_picks_latest_previous_central_date(tmp_path: Path) -> None:
    gy = _grade_yesterday_module()
    frame = pd.DataFrame({"a": [1]})
    # Two runs yesterday (Central), one today: 2026-09-13 14:00Z and 21:00Z are
    # both 09-13 Central; 2026-09-14 14:00Z is 09-14 Central ("today").
    for stamp in ("20260913T140000Z", "20260913T210000Z", "20260914T140000Z"):
        run_dir = tmp_path / stamp
        run_dir.mkdir()
        frame.to_parquet(run_dir / f"slate_nfl_{stamp}.parquet", index=False)
    stamps = gy._stamps(tmp_path, "nfl")
    assert set(stamps) == {"20260913T140000Z", "20260913T210000Z", "20260914T140000Z"}
    from datetime import UTC, datetime

    now = datetime(2026, 9, 14, 21, 0, tzinfo=UTC)
    # The latest slate from the most recent *prior* Central date — the 4pm run.
    assert gy._pick_prior_stamp(stamps, now) == "20260913T210000Z"


def test_prior_stamp_none_without_prior_dates(tmp_path: Path) -> None:
    gy = _grade_yesterday_module()
    from datetime import UTC, datetime

    now = datetime(2026, 9, 13, 21, 0, tzinfo=UTC)
    stamps = {"20260913T140000Z": {}}
    assert gy._pick_prior_stamp(stamps, now) is None


def test_newest_cumulative_picks_latest_stamp(tmp_path: Path) -> None:
    gy = _grade_yesterday_module()
    for stamp, marker in (("20260913T140000Z", 1), ("20260914T140000Z", 2)):
        run_dir = tmp_path / stamp
        run_dir.mkdir()
        pd.DataFrame({"marker": [marker]}).to_parquet(
            run_dir / f"cumulative_record_nfl_{stamp}.parquet", index=False
        )
    chain = gy._newest_cumulative(tmp_path, "nfl")
    assert chain is not None
    assert chain["marker"].tolist() == [2]
    assert gy._newest_cumulative(tmp_path, "ncaaf") is None
