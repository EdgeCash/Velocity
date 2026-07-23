"""MLB game context (velocity.ingest.mlb_context) — StatsAPI hydrate → cards.

Exercises the pure ``normalize_context`` layer against a frozen schedule-hydrate
fixture: team ids / names / records and each probable starter's id, hand, and
season line. Same discipline as every adapter — everything optional degrades to
``None`` rather than crashing, and a game with no teams is dropped. The network
``load_context`` is not touched here.
"""

from __future__ import annotations

import json
from pathlib import Path

from velocity.ingest.mlb_context import normalize_context

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _by_pk() -> dict:
    return {c.game_pk: c for c in normalize_context(_load("mlb_context.json"))}


def test_drops_game_without_teams() -> None:
    ctx = _by_pk()
    # The null-gamePk / empty-teams game is dropped; two real games survive.
    assert set(ctx) == {"745804", "745820"}


def test_team_id_name_and_record() -> None:
    game = _by_pk()["745804"]
    assert game.away.team_id == "137"
    assert game.home.name == "Los Angeles Dodgers"
    assert game.away.record == "58-44"
    assert game.home.record == "64-38"


def test_pitcher_line_and_hand() -> None:
    game = _by_pk()["745804"]
    assert game.home_sp is not None
    assert game.home_sp.player_id == "808967"
    assert game.home_sp.name == "Yoshinobu Yamamoto"
    assert game.home_sp.hand == "R"
    # W-L · ERA · WHIP, in that order, from the pitching split (fielding skipped).
    assert game.home_sp.line == "11-3 · 2.51 ERA · 0.99 WHIP"


def test_partial_record_degrades_to_none() -> None:
    """A record missing losses can't be shown as W-L, so it's dropped."""
    game = _by_pk()["745820"]
    assert game.away.record is None  # wins only
    assert game.home.record == "49-53"


def test_missing_pitcher_and_empty_stats() -> None:
    game = _by_pk()["745820"]
    # Home team has no probablePitcher at all.
    assert game.home_sp is None
    # Away starter is present but has no stat blocks — name/hand kept, line None.
    assert game.away_sp is not None
    assert game.away_sp.name == "Gerrit Cole"
    assert game.away_sp.hand == "R"
    assert game.away_sp.line is None


def test_empty_payload_yields_nothing() -> None:
    assert normalize_context({}) == []
