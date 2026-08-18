"""Season outlook — the builder's record math and the portrait render.

The card is only trustworthy if the expected-wins arithmetic is exact:
banked wins count 1, ties count ½, remaining games count their win
probability, and home/away perspective flips the projector's number
correctly.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from velocity.report.outlook import build_outlook
from velocity.report.outlook_png import render_outlook


def _schedule() -> pd.DataFrame:
    return pd.DataFrame([
        # Played: a home win, a road loss, a tie.
        {"season": 2026, "week": 1, "home_team": "KC", "away_team": "BUF",
         "home_score": 27, "away_score": 20},
        {"season": 2026, "week": 2, "home_team": "DEN", "away_team": "KC",
         "home_score": 24, "away_score": 17},
        {"season": 2026, "week": 3, "home_team": "KC", "away_team": "LV",
         "home_score": 20, "away_score": 20},
        # Ahead: one home, one road.
        {"season": 2026, "week": 4, "home_team": "KC", "away_team": "LAC",
         "home_score": None, "away_score": None},
        {"season": 2026, "week": 5, "home_team": "JAX", "away_team": "KC",
         "home_score": None, "away_score": None},
        # Another season — must be excluded.
        {"season": 2025, "week": 1, "home_team": "KC", "away_team": "BUF",
         "home_score": 10, "away_score": 40},
    ])


def _project(home: str, away: str) -> float:
    # KC wins 70% at home; 60% for the road side when KC visits.
    return {"KC": 0.70, "JAX": 0.40}[home]


def test_build_outlook_record_math_and_perspective() -> None:
    outlook = build_outlook(_schedule(), "KC", _project, season=2026)
    assert (outlook.wins, outlook.losses, outlook.ties) == (1, 1, 1)
    # 1 banked + 0.5 tie + 0.70 home + (1 − 0.40) road = 2.8
    assert outlook.expected_wins == pytest.approx(2.8)
    assert outlook.expected_losses == pytest.approx(5 - 2.8 - 0.5)
    assert outlook.projected_line() == "PROJECTED 2.8–1.7"
    weeks = [r.week for r in outlook.rows]
    assert weeks == sorted(weeks)
    ahead = [r for r in outlook.rows if not r.played]
    assert [round(r.p_win, 2) for r in ahead] == [0.70, 0.60]  # type: ignore[arg-type]
    assert ahead[0].at_home and not ahead[1].at_home
    played = outlook.rows[0]
    assert (played.result, played.score) == ("W", "27-20")


def test_build_outlook_unknown_team_raises() -> None:
    with pytest.raises(ValueError):
        build_outlook(_schedule(), "SEA", _project, season=2026)


def test_render_outlook_portrait_png(tmp_path: Path) -> None:
    from PIL import Image

    outlook = build_outlook(_schedule(), "KC", _project, season=2026)
    dest = render_outlook(outlook, tmp_path / "outlook.png", team_name="Kansas City")
    assert dest.exists() and dest.stat().st_size > 10_000
    assert Image.open(dest).size == (1080, 1350)
