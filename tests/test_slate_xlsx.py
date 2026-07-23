"""Slate workbook export (velocity.report.slate_xlsx).

Covers the pure display-frame builders (team-name join, derived columns) and that
the workbook writes valid, readable sheets with the right values — offline, no
model or network.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from velocity.report.slate_xlsx import (
    export_slate_workbook,
    plays_display,
    projections_display,
    props_display,
)

EVENTS = pd.DataFrame({
    "game_id": ["g1", "g2"],
    "away_team": ["San Francisco Giants", "New York Mets"],
    "home_team": ["Los Angeles Dodgers", "Philadelphia Phillies"],
    "kickoff": pd.to_datetime(["2026-07-24T02:10:00", "2026-07-24T23:05:00"]),
})


class _FakeProj:
    def __init__(self, mu_away: float, mu_home: float, ft: float, fs: float, ph: float) -> None:
        self.mu_away, self.mu_home = mu_away, mu_home
        self._ft, self._fs, self._ph = ft, fs, ph

    def fair_total(self) -> float:
        return self._ft

    def fair_spread(self) -> float:
        return self._fs

    def p_home_win(self) -> float:
        return self._ph

    def p_away_win(self) -> float:
        return 1.0 - self._ph


def _plays() -> pd.DataFrame:
    return pd.DataFrame({
        "game_id": ["g1", "g2"], "market": ["total", "moneyline"], "side": ["over", "home"],
        "point": [8.5, None], "book": ["dk", "fd"], "price": [-110, 120],
        "p_model": [0.57, 0.55], "p_fair": [0.54, 0.52], "edge": [0.03, 0.03],
        "stake": [3.5, 2.0],
    })


def test_projections_display_builds_expected_columns() -> None:
    projections = {"g1": _FakeProj(3.9, 5.1, 9.0, -1.0, 0.56)}
    df = projections_display(projections, EVENTS)
    row = df.iloc[0]
    assert row["Matchup"] == "San Francisco Giants @ Los Angeles Dodgers"
    assert row["Proj Total"] == pytest.approx(9.0)  # away + home
    assert row["Away Win %"] == pytest.approx(0.44)  # 1 - home
    assert row["Fair Total"] == 9.0


def test_plays_display_joins_teams_and_derives_stake() -> None:
    disp = plays_display(_plays(), EVENTS, bankroll=100.0)
    assert list(disp.columns)[:3] == ["Matchup", "Market", "Side"]
    assert disp.iloc[0]["Matchup"] == "San Francisco Giants @ Los Angeles Dodgers"
    assert disp.iloc[0]["Stake %"] == pytest.approx(0.035)  # stake / bankroll
    assert disp.iloc[0]["Stake $"] == pytest.approx(3.5)


def test_empty_frames_yield_empty_display() -> None:
    assert plays_display(pd.DataFrame(), EVENTS, 100.0).empty
    assert props_display(pd.DataFrame(), EVENTS, 100.0).empty


def test_export_workbook_writes_readable_sheets(tmp_path: Path) -> None:
    projections = {"g1": _FakeProj(3.9, 5.1, 9.0, -1.0, 0.56),
                   "g2": _FakeProj(4.6, 5.4, 10.0, -0.5, 0.58)}
    dest = tmp_path / "slate.xlsx"
    out = export_slate_workbook(
        dest,
        projections_display(projections, EVENTS),
        plays_display(_plays(), EVENTS, 100.0),
        None,
        league="mlb", generated_at="2026-07-24 00:00:00", bankroll=100.0,
    )
    assert out == dest and dest.exists()
    sheets = pd.read_excel(dest, sheet_name=None, header=3)
    assert set(sheets) == {"Read Me", "Game Projections", "Play Suggestions"}
    plays = sheets["Play Suggestions"].dropna(how="all")
    # 2 plays + a TOTAL row; a real Model % value survived the round-trip.
    assert "Los Angeles Dodgers" in plays["Matchup"].to_string()
    assert (plays["Model %"].dropna() > 0).all()


def test_export_includes_prop_sheet_when_present(tmp_path: Path) -> None:
    props = pd.DataFrame({
        "game_id": ["g1"], "player": ["Yamamoto"], "market": ["pitcher_strikeouts"],
        "side": ["over"], "point": [5.5], "book": ["dk"], "price": [-115],
        "p_model": [0.58], "p_fair": [0.53], "edge": [0.05], "stake": [2.5],
    })
    dest = tmp_path / "slate.xlsx"
    export_slate_workbook(
        dest, projections_display({}, EVENTS), plays_display(pd.DataFrame(), EVENTS, 100.0),
        props_display(props, EVENTS, 100.0),
        league="mlb", generated_at="2026-07-24 00:00:00", bankroll=100.0,
    )
    sheets = pd.read_excel(dest, sheet_name=None, header=3)
    assert "Prop Suggestions" in sheets
    assert "Yamamoto" in sheets["Prop Suggestions"]["Player"].to_string()
