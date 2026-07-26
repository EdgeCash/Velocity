"""Yesterday's record — linescore segments, parlay settlement, and the headline.

The model-status section is only worth trusting if it grades exactly the way a
book does, so every case here is hand-checkable: linescore innings sum to the F5
and first-inning segments (and refuse partial segments), parlays settle leg by
leg with pushes dropping out, a play that can't be graded stays pending, and the
headline states the record and units verbatim.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest
from velocity.ingest.mlb import normalize_linescore
from velocity.report.daily_record import (
    build_daily_record,
    empty_record,
    grade_parlay_frame,
    record_headline,
)
from velocity.report.results import attach_segment_scores

REPO = Path(__file__).parent.parent


# --- linescore normalizer -----------------------------------------------------


def _linescore_payload(n_innings: int = 9) -> dict:
    # Away scores 1 in the 1st and 2 in the 6th; home scores 1 in the 5th.
    away = {1: 1, 6: 2}
    home = {5: 1}
    return {
        "innings": [
            {"num": n, "away": {"runs": away.get(n, 0)}, "home": {"runs": home.get(n, 0)}}
            for n in range(1, n_innings + 1)
        ]
    }


def test_linescore_segments_sum_correctly() -> None:
    seg = normalize_linescore(_linescore_payload())
    assert seg == {
        "home_score_f5": 1.0, "away_score_f5": 1.0,  # the 6th-inning runs are excluded
        "home_score_i1": 0.0, "away_score_i1": 1.0,
    }


def test_linescore_partial_segment_is_ungradable() -> None:
    # Only 3 innings recorded (shortened/in progress): F5 must not grade, i1 can.
    seg = normalize_linescore(_linescore_payload(n_innings=3))
    assert seg["home_score_f5"] is None
    assert seg["away_score_f5"] is None
    assert seg["away_score_i1"] == 1.0


def test_attach_segment_scores_joins_by_gamepk() -> None:
    finals = pd.DataFrame({"game_id": ["evt1"], "home_score": [3.0], "away_score": [3.0]})
    pk_map = pd.DataFrame({"game_id": ["evt1"], "game_pk": ["777"]})
    out = attach_segment_scores(finals, pk_map, {"777": normalize_linescore(_linescore_payload())})
    assert out.loc[0, "home_score_f5"] == 1.0
    assert out.loc[0, "away_score_i1"] == 1.0
    # A game with no linescore keeps null segments (its segment bets stay pending).
    out2 = attach_segment_scores(finals, pk_map, {})
    assert out2["home_score_f5"].isna().all()


# --- parlay settlement --------------------------------------------------------


def _parlay_row(legs: list[dict], stake: float = 2.0) -> pd.DataFrame:
    return pd.DataFrame(
        [{"legs": "x", "stake": stake, "price": 300, "legs_json": json.dumps(legs)}]
    )


FINALS = pd.DataFrame(
    {
        "game_id": ["g1", "g2"],
        "home_score": [5.0, 2.0],
        "away_score": [3.0, 6.0],
        "home_score_f5": [2.0, 1.0],
        "away_score_f5": [2.0, 4.0],
        "home_score_i1": [0.0, 0.0],
        "away_score_i1": [0.0, 2.0],
    }
)
BOX = pd.DataFrame(
    {"game_id": ["g1"], "player_id": ["p9"], "player_name": ["Some Ace"],
     "pitcher_strikeouts": [7.0]}
)


def test_parlay_wins_with_a_push_dropping_out() -> None:
    legs = [
        {"game_id": "g1", "market": "moneyline", "side": "home", "price": 100, "point": None},
        # F5 tied 2-2 → this leg pushes and drops out of the multiplier.
        {"game_id": "g1", "market": "moneyline_f5", "side": "home", "price": 120, "point": None},
        {"game_id": "g1", "market": "pitcher_strikeouts", "side": "over", "price": -110,
         "point": 6.5, "player": "p9"},
    ]
    graded = grade_parlay_frame(_parlay_row(legs), FINALS, BOX)
    assert graded.loc[0, "result"] == "win"
    # Stake 2 at (2.0 × 1.909…) − 1 → the push contributed nothing.
    assert graded.loc[0, "profit"] == pytest.approx(2.0 * (2.0 * (1 + 100 / 110) - 1.0))


def test_parlay_single_lost_leg_loses_the_ticket() -> None:
    legs = [
        {"game_id": "g1", "market": "moneyline", "side": "home", "price": 100, "point": None},
        {"game_id": "g2", "market": "total_i1", "side": "under", "price": -130, "point": 0.5},
    ]
    graded = grade_parlay_frame(_parlay_row(legs), FINALS)  # g2 had a 1st-inning run
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
        {"game_id": "g1", "market": "moneyline_f5", "side": "home", "price": 100, "point": None},
    ]
    graded = grade_parlay_frame(_parlay_row(legs), FINALS)
    assert graded.loc[0, "result"] == "push"
    assert graded.loc[0, "profit"] == 0.0


# --- record + headline --------------------------------------------------------


def _record() -> pd.DataFrame:
    games = pd.DataFrame(
        {
            "game_id": ["g1", "g2"],
            "market": ["total_i1", "moneyline"],
            "side": ["under", "home"],
            "point": [0.5, None],
            "price": [-110, 120],
            "stake": [2.0, 3.0],
            "result": ["win", "loss"],
            "profit": [2.0 * 100 / 110, -3.0],
        }
    )
    props = pd.DataFrame(
        {
            "player": ["Some Ace"],
            "market": ["pitcher_strikeouts"],
            "side": ["over"],
            "point": [6.5],
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
        matchups={"g1": "SF @ LAD", "g2": "NYY @ BOS"},
        slate_date="2026-07-25",
    )


def test_record_rows_and_labels() -> None:
    record = _record()
    assert record["section"].tolist() == ["games", "games", "props", "parlays"]
    assert record.loc[0, "play"] == "SF @ LAD"
    assert record.loc[2, "play"] == "Some Ace"
    assert record.loc[3, "market"] == "parlay"


def test_record_headline_states_the_record() -> None:
    headline = record_headline(_record())
    assert headline == (
        "Yesterday (Jul 25): games 1-1 (-1.2u) · props 1-0 (+0.9u) · "
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
    # Two runs yesterday (Central), one today: 2026-07-25 14:00Z and 21:00Z are
    # both 07-25 Central; 2026-07-26 14:00Z is 07-26 Central ("today").
    for stamp in ("20260725T140000Z", "20260725T210000Z", "20260726T140000Z"):
        run_dir = tmp_path / stamp
        run_dir.mkdir()
        frame.to_parquet(run_dir / f"slate_mlb_{stamp}.parquet", index=False)
    stamps = gy._stamps(tmp_path, "mlb")
    assert set(stamps) == {"20260725T140000Z", "20260725T210000Z", "20260726T140000Z"}
    from datetime import UTC, datetime

    now = datetime(2026, 7, 26, 21, 0, tzinfo=UTC)
    # The latest slate from the most recent *prior* Central date — the 4pm run.
    assert gy._pick_prior_stamp(stamps, now) == "20260725T210000Z"


def test_prior_stamp_none_without_prior_dates(tmp_path: Path) -> None:
    gy = _grade_yesterday_module()
    from datetime import UTC, datetime

    now = datetime(2026, 7, 25, 21, 0, tzinfo=UTC)
    stamps = {"20260725T140000Z": {}}
    assert gy._pick_prior_stamp(stamps, now) is None
