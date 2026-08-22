"""sbro odds archives — pair parsing, spread/total demux, and the game join."""

from __future__ import annotations

import pandas as pd
import pytest
from velocity.ingest.sbro import (
    SBRO_COLUMNS,
    join_sbro_closes,
    normalize_sbro_season,
    parse_sbro_html,
    resolve_sbro_team,
    sbro_season_url,
    sbro_team_lookup,
)


def _wire(date: object, vh: str, team: str, final: object, open_: object,
          close: object, ml: object) -> dict[str, object]:
    return {"Date": date, "Rot": 601, "VH": vh, "Team": team, "1st": 30,
            "2nd": 30, "Final": final, "Open": open_, "Close": close,
            "ML": ml, "2H": 70}


def test_normalize_pairs_and_demuxes_spread_total() -> None:
    raw = pd.DataFrame([
        # Home favorite: spread on the home row (smaller number).
        _wire(1105, "V", "Princeton", 67, 144.5, 142.5, 190),
        _wire(1105, "H", "Duquesne", 94, 5.5, 5.5, -240),
        # Away favorite: spread on the visitor row → negative home spread.
        _wire(1105, "V", "Louisville", 87, 7, 6, -280),
        _wire(1105, "H", "MiamiFlorida", 74, 146.5, 145, 230),
        # Neutral pair, pick'em close, January date (year rolls forward).
        _wire(104, "N", "Kansas", 72, 3, "pk", -110),
        _wire(104, "N", "Villanova", 70, 140, 141.5, -110),
        # NL close on the spread side → no spread, total survives.
        _wire(1106, "V", "Wofford", 64, 3.5, "NL", -150),
        _wire(1106, "H", "Mercer", 53, "NL", 139.5, 130),
        _wire(1107, "V", "StrayRow", 1, 1, 1, 1),  # unpaired → resynced away
    ])
    out = normalize_sbro_season(raw, season=2020)
    assert len(out) == 4
    duq = out.iloc[0]
    assert duq["home_team"] == "Duquesne" and not duq["neutral_site"]
    assert duq["spread_close"] == pytest.approx(5.5)
    assert duq["total_close"] == pytest.approx(142.5)
    mia = out.iloc[1]
    assert mia["spread_close"] == pytest.approx(-6.0)  # home dog
    assert mia["total_close"] == pytest.approx(145.0)
    neutral = out.iloc[2]
    assert neutral["neutral_site"]
    assert neutral["date"] == pd.Timestamp("2020-01-04")  # season 2020 = 2019-20
    assert neutral["spread_close"] == pytest.approx(0.0)  # pk
    assert neutral["total_close"] == pytest.approx(141.5)
    nl = out.iloc[3]
    assert pd.isna(nl["spread_close"])
    assert nl["total_close"] == pytest.approx(139.5)
    # November dates stay in the season's first calendar year.
    assert out.iloc[0]["date"] == pd.Timestamp("2019-11-05")


def test_parse_sbro_html_reads_the_wire_table() -> None:
    html = """
    <table><tr><th>Date</th><th>Rot</th><th>VH</th><th>Team</th><th>1st</th>
    <th>2nd</th><th>Final</th><th>Open</th><th>Close</th><th>ML</th><th>2H</th></tr>
    <tr><td>1109</td><td>601</td><td>V</td><td>UCSanDiego</td><td>33</td>
    <td>47</td><td>80</td><td>141.5</td><td>140</td><td>700</td><td>74</td></tr>
    </table>"""
    out = parse_sbro_html(html)
    assert list(out.columns) == list(SBRO_COLUMNS)
    assert len(out) == 1
    assert out.loc[0, "Team"] == "UCSanDiego"


def test_sbro_season_url_switches_transport() -> None:
    assert sbro_season_url(2020).endswith("ncaa-basketball-2019-20.xlsx")
    assert sbro_season_url(2022).endswith("ncaa-basketball-2021-22/")


def test_resolve_sbro_team_never_guesses() -> None:
    lookup = sbro_team_lookup({"Michigan State", "UConn", "Kansas City",
                               "Green Bay", "Duke"})
    assert resolve_sbro_team("MichiganSt", lookup) == "Michigan State"
    assert resolve_sbro_team("Connecticut", lookup) == "UConn"
    assert resolve_sbro_team("UMKC", lookup) == "Kansas City"
    assert resolve_sbro_team("WiscGreenBay", lookup) == "Green Bay"
    assert resolve_sbro_team("Duke", lookup) == "Duke"
    assert resolve_sbro_team("Hogwarts", lookup) is None


def _games_frame() -> pd.DataFrame:
    return pd.DataFrame([
        {"game_id": "g1", "season": 2020, "kickoff": pd.Timestamp("2019-11-06 00:30"),
         "home_team": "Duquesne", "away_team": "Princeton",
         "home_score": 94.0, "away_score": 67.0},
        # hoopR lists Kansas as home; sbro's neutral designation is flipped.
        {"game_id": "g2", "season": 2020, "kickoff": pd.Timestamp("2020-01-04 21:00"),
         "home_team": "Kansas", "away_team": "Villanova",
         "home_score": 72.0, "away_score": 70.0},
        # Scores that disagree with sbro → the join must drop it.
        {"game_id": "g3", "season": 2020, "kickoff": pd.Timestamp("2019-11-06 01:00"),
         "home_team": "Mercer", "away_team": "Wofford",
         "home_score": 53.0, "away_score": 99.0},
    ])


def test_join_sbro_closes_orients_flips_and_gates() -> None:
    closes = pd.DataFrame([
        # Late tip: ET date one day before the UTC-derived kickoff date.
        {"season": 2020, "date": pd.Timestamp("2019-11-05"),
         "home_team": "Duquesne", "away_team": "Princeton", "neutral_site": False,
         "home_score": 94.0, "away_score": 67.0, "spread_open": 5.5,
         "spread_close": 5.5, "total_open": 144.5, "total_close": 142.5,
         "ml_home": -240.0, "ml_away": 190.0},
        # sbro has Villanova as designated home; hoopR says Kansas → flip.
        {"season": 2020, "date": pd.Timestamp("2020-01-04"),
         "home_team": "Villanova", "away_team": "Kansas", "neutral_site": True,
         "home_score": 70.0, "away_score": 72.0, "spread_open": 2.0,
         "spread_close": 3.0, "total_open": 140.0, "total_close": 141.5,
         "ml_home": 120.0, "ml_away": -140.0},
        # Score mismatch vs hoopR finals → dropped.
        {"season": 2020, "date": pd.Timestamp("2019-11-05"),
         "home_team": "Mercer", "away_team": "Wofford", "neutral_site": False,
         "home_score": 53.0, "away_score": 64.0, "spread_open": 3.5,
         "spread_close": 3.5, "total_open": 139.5, "total_close": 139.5,
         "ml_home": 130.0, "ml_away": -150.0},
    ])
    out = join_sbro_closes(closes, _games_frame())
    assert sorted(out["game_id"]) == ["g1", "g2"]
    by_id = out.set_index("game_id")
    assert by_id.loc["g1", "spread_close"] == pytest.approx(5.5)
    # The flip negates the spread and swaps the moneylines.
    assert by_id.loc["g2", "spread_close"] == pytest.approx(-3.0)
    assert by_id.loc["g2", "ml_home"] == pytest.approx(-140.0)
    assert by_id.loc["g2", "ml_away"] == pytest.approx(120.0)
    assert by_id.loc["g2", "total_close"] == pytest.approx(141.5)
