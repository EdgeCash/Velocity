"""BettingPros' batting order, layered UNDER statsapi's.

Audit finding 1, as corrected. The original claim — that a benched hitter is
priced as a starter — is wrong wherever statsapi has posted: ``apply_confirmed_
cards`` already folds the confirmed order in and drops an announced bench.

What is true is a timing gap. statsapi publishes "a couple of hours before
first pitch"; the live slate runs at 16:53 and 22:53 UTC, and on 2026-09-16
twenty-six of thirty games started at 22:00 UTC or later. Measured that day:

    statsapi       8 of 60 sides   at 18:55 UTC
    BettingPros   60 of 60 sides   at 16:53 UTC  (38 confirmed, 22 projected)

So at the run that matters, most of the slate fell back to each batter's most
recent PRIOR game — the wrong slot, and bats who are not playing at all. On
that slate the fallback corrected 208 slots and excluded 554 bats who were on a
carded team but not in its lineup.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location(
        "build_dfs_lineup_cards", REPO / "scripts" / "build_dfs_lineup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lineups(confirmed: bool = True) -> pd.DataFrame:
    """Two carded teams, three bats each."""
    rows = []
    for team, names in (("CIN", ["Carlos Jorge", "Elly De La Cruz", "Sal Stewart"]),
                        ("LAD", ["Josue De Paula", "Mookie Betts", "Will Smith"])):
        for slot, name in enumerate(names, start=1):
            rows.append({"game_id": "1", "league": "mlb", "side": "home",
                         "slot": slot, "player_name": name, "bp_player_id": "x",
                         "team": team, "position": "CF",
                         "lineup_type": "confirmed" if confirmed else "projected",
                         "is_confirmed": confirmed})
    return pd.DataFrame(rows)


def _index() -> dict[str, str]:
    from velocity.util.names import fold_name

    names = ["Carlos Jorge", "Elly De La Cruz", "Sal Stewart",
             "Josue De Paula", "Mookie Betts", "Will Smith", "Benched Guy"]
    return {fold_name(n): f"id-{i}" for i, n in enumerate(names)}


def test_todays_slot_replaces_last_games() -> None:
    mod = _module()
    slot_of = {"id-0": 7}          # batted seventh last time
    team_of = {"id-0": "Cincinnati Reds"}
    mod.apply_projected_cards(slot_of, team_of, _lineups(), _index())
    assert slot_of["id-0"] == 1, "today's card must win over the previous game"


def test_a_bat_not_in_todays_lineup_is_excluded() -> None:
    """The one that matters: he is not playing, so he is not a pick."""
    mod = _module()
    slot_of = {"id-6": 4}
    team_of = {"id-6": "Cincinnati Reds", "id-0": "Cincinnati Reds"}
    eligible = mod.apply_projected_cards(slot_of, team_of, _lineups(), _index())
    assert "id-0" in eligible
    assert "id-6" not in eligible, "a carded team's bench must leave the board"


def test_a_team_nobody_carded_stays_unrestricted() -> None:
    """Both layers express "no opinion" the same way — that is what composes."""
    mod = _module()
    slot_of: dict[str, int] = {}
    team_of = {"id-6": "Miami Marlins"}   # not in the BP cards
    eligible = mod.apply_projected_cards(slot_of, team_of, _lineups(), _index())
    assert "id-6" in eligible


def test_the_team_is_resolved_from_the_abbreviation() -> None:
    """BettingPros sends ARI; the banks say Arizona Diamondbacks."""
    mod = _module()
    slot_of: dict[str, int] = {}
    team_of: dict[str, str] = {}
    mod.apply_projected_cards(slot_of, team_of, _lineups(), _index())
    assert team_of["id-0"] == "Cincinnati Reds"
    assert team_of["id-3"] == "Los Angeles Dodgers"


def test_an_unresolvable_name_does_not_become_a_phantom_id() -> None:
    mod = _module()
    frame = _lineups()
    frame.loc[0, "player_name"] = "Nobody At All"
    slot_of: dict[str, int] = {}
    team_of: dict[str, str] = {}
    eligible = mod.apply_projected_cards(slot_of, team_of, frame, _index())
    assert len(eligible) == 5, "the unmatched bat is skipped, not invented"


def test_nothing_usable_returns_none_so_the_caller_is_unchanged() -> None:
    mod = _module()
    assert mod.apply_projected_cards({}, {}, pd.DataFrame(), {}) is None
    assert mod.apply_projected_cards({}, {}, None, {}) is None
    # Resolvable against an empty index: no ids, so no opinion.
    assert mod.apply_projected_cards({}, {}, _lineups(), {}) is None


def test_statsapi_cannot_re_admit_a_bat_the_book_benched() -> None:
    """The composition rule, and the reason the board intersects.

    ``apply_confirmed_cards`` calls every bat on an UNPOSTED team eligible.
    Taken as a replacement that would undo the restriction BettingPros already
    has for that team, so the board intersects the two.
    """
    mod = _module()
    slot_of: dict[str, int] = {}
    team_of = {"id-6": "Cincinnati Reds", "id-0": "Cincinnati Reds"}
    projected = mod.apply_projected_cards(slot_of, team_of, _lineups(), _index())
    # statsapi has posted nothing for the Reds, so it leaves everyone in.
    confirmed = {"id-0", "id-6"}
    assert "id-6" in confirmed
    assert "id-6" not in (projected & confirmed), "the intersection must hold"


@pytest.mark.parametrize("confirmed", [True, False])
def test_projected_and_confirmed_cards_are_both_used(confirmed: bool) -> None:
    """A projected card still beats last night's box score.

    ``is_confirmed`` rides along so a consumer can weigh them differently; this
    layer uses both, because the alternative is a stale slot.
    """
    mod = _module()
    slot_of = {"id-0": 9}
    team_of = {"id-0": "Cincinnati Reds"}
    mod.apply_projected_cards(slot_of, team_of, _lineups(confirmed), _index())
    assert slot_of["id-0"] == 1
