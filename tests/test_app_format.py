"""Plays-app formatting — the compact play strings and card assembly.

The app module is pure pandas (no Streamlit import), so the exact display
strings in the reference style are pinned here: "Kansas City ML -145",
"Allen O249.5 PASS YDS +105", city stripping for every awkward NFL name,
and the per-game card carrying its own plays.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

REPO = Path(__file__).parent.parent

_spec = importlib.util.spec_from_file_location("format_plays", REPO / "app" / "format_plays.py")
fp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fp)  # type: ignore[union-attr]


GAMES_MAP = pd.DataFrame(
    {
        "game_id": ["g1", "g2"],
        "away_team": ["Buffalo Bills", "Dallas Cowboys"],
        "home_team": ["Kansas City Chiefs", "Philadelphia Eagles"],
        "kickoff": [pd.Timestamp("2026-09-10 00:20"), pd.Timestamp("2026-09-13 17:00")],
    }
)


def test_city_strips_nicknames_of_any_length() -> None:
    assert fp.city("Buffalo Bills") == "Buffalo"
    assert fp.city("Kansas City Chiefs") == "Kansas City"
    assert fp.city("New England Patriots") == "New England"
    assert fp.city("Green Bay Packers") == "Green Bay"
    assert fp.city("Ohio State Buckeyes") == "Ohio State Buckeyes"  # NCAAF → unchanged
    assert fp.city("KC") == "KC"  # unknown → unchanged


def test_game_play_labels() -> None:
    names = fp.matchup_names(GAMES_MAP)
    row = {"game_id": "g1", "market": "moneyline", "side": "away", "price": 105, "point": None}
    assert fp.game_play_label(row, names) == "Buffalo ML +105"
    row = {"game_id": "g1", "market": "spread", "side": "away", "price": -110, "point": 2.5}
    assert fp.game_play_label(row, names) == "Buffalo +2.5 -110"
    row = {"game_id": "g1", "market": "total", "side": "over", "price": -110, "point": 47.5}
    assert fp.game_play_label(row, names) == "O47.5 -110"
    row = {"game_id": "g1", "market": "total", "side": "under", "price": 100, "point": 47.5}
    assert fp.game_play_label(row, names) == "U47.5 +100"


def test_prop_play_label() -> None:
    row = {"player": "Marvin Harrison Jr.", "market": "receiving_yards", "side": "over",
           "price": 109, "point": 74.5}
    # A generational suffix stays attached to the surname ("Harrison Jr." style).
    assert fp.prop_play_label(row) == "Harrison Jr. O74.5 REC YDS +109"
    row = {"player": "Josh Allen", "market": "pass_yards", "side": "over",
           "price": -106, "point": 249.5}
    assert fp.prop_play_label(row) == "Allen O249.5 PASS YDS -106"
    row = {"player": "Travis Kelce", "market": "receptions", "side": "under",
           "price": -115, "point": 6.5}
    assert fp.prop_play_label(row) == "Kelce U6.5 REC -115"


def test_plays_table_orders_games_props_parlays() -> None:
    plays = pd.DataFrame(
        [{"game_id": "g1", "market": "moneyline", "side": "away", "price": 105,
          "point": None, "stake": 2.0}]
    )
    props = pd.DataFrame(
        [{"game_id": "g2", "market": "pass_yards", "side": "over", "price": -106,
          "point": 249.5, "player": "Dak Prescott", "stake": 1.0}]
    )
    parlays = pd.DataFrame(
        [{"legs": "A + B", "n_legs": 2, "price": 300, "stake": 0.5}]
    )
    view = fp.plays_table(plays, props, parlays, GAMES_MAP)
    assert view["matchup"].tolist() == [
        "Buffalo @ Kansas City", "Dallas @ Philadelphia", "PARLAY (2 legs)",
    ]
    assert view["play"].tolist()[0] == "Buffalo ML +105"
    assert view["play"].tolist()[1] == "Prescott O249.5 PASS YDS -106"
    assert view["play"].tolist()[2] == "A + B → +300"


def test_matchup_cards_carry_their_own_plays() -> None:
    projections = pd.DataFrame(
        [{"game_id": "g1", "away": "BUF", "home": "KC", "mu_away": 22.4, "mu_home": 25.1,
          "p_home_win": 0.58, "fair_spread": -2.7, "fair_total": 47.5}]
    )
    plays = pd.DataFrame(
        [{"game_id": "g1", "market": "moneyline", "side": "away", "price": 105,
          "point": None, "stake": 2.0}]
    )
    view = fp.plays_table(plays, None, None, GAMES_MAP)
    cards = fp.matchup_cards(projections, GAMES_MAP, view)
    assert len(cards) == 1
    card = cards[0]
    assert (card["away"], card["home"]) == ("Buffalo", "Kansas City")
    assert card["plays"] == ["Buffalo ML +105"]
    assert card["fair_total"] == 47.5


def test_load_slate_frames_picks_newest_recursively(tmp_path: Path) -> None:
    old, new = "20260913T140000Z", "20260914T140000Z"
    nested = tmp_path / "artifact"
    nested.mkdir()
    pd.DataFrame({"a": [1]}).to_parquet(tmp_path / f"slate_nfl_{old}.parquet")
    pd.DataFrame({"a": [2]}).to_parquet(nested / f"slate_nfl_{new}.parquet")
    frames = fp.load_slate_frames(tmp_path)
    assert frames["plays"] is not None
    assert frames["plays"]["a"].tolist() == [2]  # the newer, nested file wins
    assert frames["record"] is None


def test_card_images_finds_the_newest_run(tmp_path: Path) -> None:
    old, new = "20260913T140000Z", "20260914T140000Z"
    for stamp in (old, new):
        (tmp_path / f"social_nfl_{stamp}_BUF_at_KC.png").write_bytes(b"x")
        (tmp_path / f"social_nfl_{stamp}_captions.md").write_text(f"copy {stamp}")
    (tmp_path / f"simcheck_nfl_{new}_DAL_at_PHI.png").write_bytes(b"x")
    (tmp_path / f"recordcard_nfl_{new}.png").write_bytes(b"x")

    images = fp.card_images(tmp_path)
    assert [label for label, _ in images["model"]] == ["BUF @ KC"]
    assert new in str(images["model"][0][1])  # only the newest run's cards
    assert [label for label, _ in images["simcheck"]] == ["DAL @ PHI"]
    assert images["record"] is not None
    assert images["model_captions"] == f"copy {new}"
    assert images["simcheck_captions"] is None  # no captions file written


def test_card_images_empty_folder(tmp_path: Path) -> None:
    images = fp.card_images(tmp_path)
    assert images["model"] == [] and images["simcheck"] == []
    assert images["record"] is None
