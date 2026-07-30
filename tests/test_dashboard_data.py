"""Dashboard data layer (velocity.report.dashboard_data) — pure, offline.

Pins the run discovery/grouping (game vs props vs games-map vs cards, by
league+stamp, newest first, props not mistaken for the game slate) and the plays
shaping (matchup labels, American price, %s, stake %, empty-safe). The Streamlit
app itself is a thin viewer over this and isn't imported here.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from velocity.report.dashboard_data import (
    american,
    discover_slates,
    shape_plays,
    slate_summary,
)


def _write_run(d: Path, stamp: str, *, props: bool = True, cards: bool = True) -> None:
    games = pd.DataFrame({
        "game_id": ["g1"], "market": ["moneyline"], "side": ["home"], "point": [None],
        "book": ["dk"], "price": [-120.0], "p_model": [0.56], "p_fair": [0.52],
        "edge": [0.04], "stake": [2.5],
    })
    games.to_parquet(d / f"slate_mlb_{stamp}.parquet", index=False)
    pd.DataFrame({
        "game_id": ["g1"], "home_team": ["Los Angeles Dodgers"],
        "away_team": ["San Francisco Giants"], "kickoff": pd.to_datetime(["2026-07-26T23:10"]),
    }).to_parquet(d / f"games_mlb_{stamp}.parquet", index=False)
    if props:
        pd.DataFrame({
            "game_id": ["g1"], "player": ["Clayton Kershaw"], "market": ["pitcher_strikeouts"],
            "side": ["over"], "point": [5.5], "book": ["dk"], "price": [-110.0],
            "p_model": [0.6], "p_fair": [0.54], "edge": [0.06], "stake": [1.5],
        }).to_parquet(d / f"slate_mlb_props_{stamp}.parquet", index=False)
    if cards:
        (d / f"cards_mlb_{stamp}.html").write_text("<h1>cards</h1>")


def test_discover_groups_runs_newest_first(tmp_path: Path) -> None:
    _write_run(tmp_path, "20260726T140000Z")
    _write_run(tmp_path, "20260727T140000Z")
    slates = discover_slates(tmp_path)

    assert [s.stamp for s in slates] == ["20260727T140000Z", "20260726T140000Z"]  # newest first
    top = slates[0]
    assert top.league == "mlb"
    # all four files grouped into the one run — props not mistaken for the game slate
    assert top.game_path is not None and top.props_path is not None
    assert top.games_path is not None and top.cards_path is not None
    assert top.game_path.name == "slate_mlb_20260727T140000Z.parquet"
    assert top.props_path.name == "slate_mlb_props_20260727T140000Z.parquet"


def test_discover_handles_missing_pieces_and_subdirs(tmp_path: Path) -> None:
    sub = tmp_path / "hist-props-123"  # an extracted artifact subfolder
    sub.mkdir()
    _write_run(sub, "20260726T140000Z", props=False, cards=False)
    slates = discover_slates(tmp_path)
    assert len(slates) == 1
    assert slates[0].props_path is None and slates[0].cards_path is None  # degrade, don't crash
    assert slates[0].game_path is not None  # found one level down


def test_discover_empty_dir_is_empty(tmp_path: Path) -> None:
    assert discover_slates(tmp_path / "nope") == []
    assert discover_slates(tmp_path) == []


def test_american_formatting() -> None:
    assert american(-110.0) == "-110"
    assert american(120.0) == "+120"
    assert american(None) == "—"
    assert american(float("nan")) == "—"


def test_shape_plays_labels_and_percentages(tmp_path: Path) -> None:
    _write_run(tmp_path, "20260726T140000Z")
    slate = discover_slates(tmp_path)[0]
    games = pd.read_parquet(slate.game_path)
    gmap = pd.read_parquet(slate.games_path)

    disp = shape_plays(games, gmap, bankroll=100.0)
    row = disp.iloc[0]
    assert row["matchup"] == "San Francisco Giants @ Los Angeles Dodgers"
    assert row["price"] == "-120"
    assert row["model_%"] == 56.0
    assert row["edge_%"] == 4.0
    assert row["stake_%"] == 2.5  # 2.5 / 100 * 100


def test_shape_plays_player_and_empty() -> None:
    empty = shape_plays(None, None, bankroll=100.0, player=True)
    assert empty.empty and "player" in empty.columns

    props = pd.DataFrame({
        "game_id": ["g1"], "player": ["Kershaw"], "market": ["pitcher_strikeouts"],
        "side": ["over"], "point": [5.5], "book": ["dk"], "price": [-110.0],
        "p_model": [0.6], "p_fair": [0.54], "edge": [0.06], "stake": [1.5],
    })
    disp = shape_plays(props, None, bankroll=100.0, player=True)
    assert disp.iloc[0]["player"] == "Kershaw"
    assert disp.iloc[0]["matchup"] == "g1"  # no games map → falls back to id


def test_slate_summary_counts_and_stake() -> None:
    games = pd.DataFrame({"stake": [2.0, 3.0]})
    props = pd.DataFrame({"stake": [1.0]})
    s = slate_summary(games, props)
    assert s == {"game_plays": 2, "prop_plays": 1, "total_staked": 6.0}
    assert slate_summary(None, None) == {"game_plays": 0, "prop_plays": 0, "total_staked": 0.0}
