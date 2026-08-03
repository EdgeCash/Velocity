"""Plays-app formatting — the compact play strings and card assembly.

The app module is pure pandas (no Streamlit import), so the exact display
strings in the reference style are pinned here: "Tampa Bay ML +101",
"Tatis O1.5 TB +109", "NRFI -135", city stripping for every awkward MLB name,
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
        "away_team": ["Tampa Bay Rays", "San Diego Padres"],
        "home_team": ["Toronto Blue Jays", "Atlanta Braves"],
        "kickoff": [pd.Timestamp("2026-07-26 23:07"), pd.Timestamp("2026-07-26 23:15")],
    }
)


def test_city_strips_nicknames_of_any_length() -> None:
    assert fp.city("Tampa Bay Rays") == "Tampa Bay"
    assert fp.city("Boston Red Sox") == "Boston"
    assert fp.city("Toronto Blue Jays") == "Toronto"
    assert fp.city("St. Louis Cardinals") == "St. Louis"
    assert fp.city("Athletics") == "Athletics"
    assert fp.city("TB") == "TB"  # unknown → unchanged


def test_game_play_labels() -> None:
    names = fp.matchup_names(GAMES_MAP)
    row = {"game_id": "g1", "market": "moneyline", "side": "away", "price": 101, "point": None}
    assert fp.game_play_label(row, names) == "Tampa Bay ML +101"
    row = {"game_id": "g1", "market": "spread", "side": "away", "price": -140, "point": 1.5}
    assert fp.game_play_label(row, names) == "Tampa Bay +1.5 RL -140"
    row = {"game_id": "g1", "market": "total", "side": "over", "price": -110, "point": 8.5}
    assert fp.game_play_label(row, names) == "O8.5 -110"
    row = {"game_id": "g1", "market": "moneyline_f5", "side": "home", "price": -120, "point": None}
    assert fp.game_play_label(row, names) == "F5 ML Toronto -120"
    row = {"game_id": "g1", "market": "total_f5", "side": "under", "price": 100, "point": 4.5}
    assert fp.game_play_label(row, names) == "F5 U4.5 +100"
    row = {"game_id": "g1", "market": "total_i1", "side": "under", "price": -135, "point": 0.5}
    assert fp.game_play_label(row, names) == "NRFI -135"
    row = {"game_id": "g1", "market": "total_i1", "side": "over", "price": 115, "point": 0.5}
    assert fp.game_play_label(row, names) == "YRFI +115"


def test_prop_play_label() -> None:
    row = {"player": "Fernando Tatis Jr.", "market": "total_bases", "side": "over",
           "price": 109, "point": 1.5}
    # A generational suffix stays attached to the surname ("Mesa Jr" style).
    assert fp.prop_play_label(row) == "Tatis Jr. O1.5 TB +109"
    row = {"player": "Gerrit Cole", "market": "pitcher_strikeouts", "side": "over",
           "price": -106, "point": 6.5}
    assert fp.prop_play_label(row) == "Cole O6.5 K -106"
    row = {"player": "Luis Castillo", "market": "pitcher_outs", "side": "under",
           "price": -115, "point": 16.5}
    assert fp.prop_play_label(row) == "Castillo U16.5 OUTS -115"


def test_plays_table_orders_games_props_parlays() -> None:
    plays = pd.DataFrame(
        [{"game_id": "g1", "market": "moneyline", "side": "away", "price": 101,
          "point": None, "stake": 2.0}]
    )
    props = pd.DataFrame(
        [{"game_id": "g2", "market": "pitcher_strikeouts", "side": "over", "price": -106,
          "point": 6.5, "player": "Gerrit Cole", "stake": 1.0}]
    )
    parlays = pd.DataFrame(
        [{"legs": "A + B", "n_legs": 2, "price": 300, "stake": 0.5}]
    )
    view = fp.plays_table(plays, props, parlays, GAMES_MAP)
    assert view["matchup"].tolist() == [
        "Tampa Bay @ Toronto", "San Diego @ Atlanta", "PARLAY (2 legs)",
    ]
    assert view["play"].tolist()[0] == "Tampa Bay ML +101"
    assert view["play"].tolist()[1] == "Cole O6.5 K -106"
    assert view["play"].tolist()[2] == "A + B → +300"


def test_matchup_cards_carry_their_own_plays() -> None:
    projections = pd.DataFrame(
        [{"game_id": "g1", "away": "TB", "home": "TOR", "mu_away": 4.1, "mu_home": 4.6,
          "p_home_win": 0.55, "fair_spread": -0.5, "fair_total": 8.7,
          "f5_fair_total": 4.6, "p_home_win_f5": 0.54, "p_yrfi": 0.52}]
    )
    plays = pd.DataFrame(
        [{"game_id": "g1", "market": "moneyline", "side": "away", "price": 101,
          "point": None, "stake": 2.0}]
    )
    view = fp.plays_table(plays, None, None, GAMES_MAP)
    cards = fp.matchup_cards(projections, GAMES_MAP, view)
    assert len(cards) == 1
    card = cards[0]
    assert (card["away"], card["home"]) == ("Tampa Bay", "Toronto")
    assert card["plays"] == ["Tampa Bay ML +101"]
    assert card["p_yrfi"] == 0.52


def test_load_slate_frames_picks_newest_recursively(tmp_path: Path) -> None:
    old, new = "20260725T140000Z", "20260726T140000Z"
    nested = tmp_path / "artifact"
    nested.mkdir()
    pd.DataFrame({"a": [1]}).to_parquet(tmp_path / f"slate_mlb_{old}.parquet")
    pd.DataFrame({"a": [2]}).to_parquet(nested / f"slate_mlb_{new}.parquet")
    frames = fp.load_slate_frames(tmp_path)
    assert frames["plays"] is not None
    assert frames["plays"]["a"].tolist() == [2]  # the newer, nested file wins
    assert frames["record"] is None


def test_card_images_finds_the_newest_run(tmp_path: Path) -> None:
    old, new = "20260725T140000Z", "20260726T140000Z"
    for stamp in (old, new):
        (tmp_path / f"social_mlb_{stamp}_SF_at_LAD.png").write_bytes(b"x")
        (tmp_path / f"social_mlb_{stamp}_captions.md").write_text(f"copy {stamp}")
    (tmp_path / f"simcheck_mlb_{new}_NYY_at_BOS.png").write_bytes(b"x")
    (tmp_path / f"recordcard_mlb_{new}.png").write_bytes(b"x")

    images = fp.card_images(tmp_path)
    assert [label for label, _ in images["model"]] == ["SF @ LAD"]
    assert new in str(images["model"][0][1])  # only the newest run's cards
    assert [label for label, _ in images["simcheck"]] == ["NYY @ BOS"]
    assert images["record"] is not None
    assert images["model_captions"] == f"copy {new}"
    assert images["simcheck_captions"] is None  # no captions file written


def test_card_images_empty_folder(tmp_path: Path) -> None:
    images = fp.card_images(tmp_path)
    assert images["model"] == [] and images["simcheck"] == []
    assert images["record"] is None
