"""Plays-app smoke test — the Streamlit script runs headless without exceptions.

Runs the real app via Streamlit's AppTest against a local slate folder built
from small frames, and asserts it renders (no uncaught exception, the play
strings present). Skipped when streamlit isn't installed — the app is an
optional surface and CI doesn't carry the dependency; the pure formatting
logic is covered dependency-free in test_app_format.py.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

st = pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

REPO = Path(__file__).parent.parent
STAMP = "20260726T140000Z"


def _slate_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "slate"
    folder.mkdir()
    pd.DataFrame(
        [{"game_id": "g1", "market": "moneyline", "side": "away", "price": 101,
          "point": None, "stake": 2.0, "book": "dk", "p_model": 0.52, "p_fair": 0.5,
          "edge": 0.02, "league": "mlb",
          "generated_at": pd.Timestamp("2026-07-26 14:00")}]
    ).to_parquet(folder / f"slate_mlb_{STAMP}.parquet", index=False)
    pd.DataFrame(
        [{"game_id": "g1", "away_team": "Tampa Bay Rays", "home_team": "Toronto Blue Jays",
          "kickoff": pd.Timestamp("2026-07-26 23:07"), "league": "mlb"}]
    ).to_parquet(folder / f"games_mlb_{STAMP}.parquet", index=False)
    pd.DataFrame(
        [{"game_id": "g1", "away": "TB", "home": "TOR", "mu_away": 4.1, "mu_home": 4.6,
          "p_home_win": 0.55, "fair_spread": -0.5, "fair_total": 8.7,
          "f5_fair_total": 4.6, "p_home_win_f5": 0.54, "p_yrfi": 0.52}]
    ).to_parquet(folder / f"projections_mlb_{STAMP}.parquet", index=False)
    pd.DataFrame(
        [{"section": "games", "play": "Tampa Bay @ Toronto", "market": "moneyline",
          "side": "away", "point": None, "price": 101, "stake": 2.0, "result": "win",
          "profit": 2.02, "slate_date": pd.Timestamp("2026-07-25")}]
    ).to_parquet(folder / f"record_mlb_{STAMP}.parquet", index=False)
    return folder


def test_app_renders_local_slate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VELOCITY_SLATE_DIR", str(_slate_dir(tmp_path)))
    at = AppTest.from_file(str(REPO / "app" / "streamlit_app.py"), default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    body = " ".join(str(m.value) for m in at.markdown)
    assert "Tampa Bay ML +101" in body  # the plays board rendered
    assert "Tampa Bay @ Toronto" in body
    assert "Yesterday: 1-0" in body  # the record tab rendered


def test_app_warns_without_a_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VELOCITY_SLATE_DIR", raising=False)
    at = AppTest.from_file(str(REPO / "app" / "streamlit_app.py"), default_timeout=30)
    at.run()
    assert not at.exception
    assert at.warning  # "no data source configured" guidance, not a crash
