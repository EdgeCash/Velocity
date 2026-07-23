"""Player handedness (velocity.ingest.mlb_people) — pure normalizer."""

from __future__ import annotations

import json
from pathlib import Path

from velocity.ingest.mlb_people import normalize_player_hands

FIXTURES = Path(__file__).parent / "fixtures"
HANDS = normalize_player_hands(json.loads((FIXTURES / "mlb_people.json").read_text()))


def test_bat_and_throw_sides() -> None:
    assert HANDS["660271"] == {"bat": "L", "pit": "R"}  # Ohtani
    assert HANDS["605141"]["bat"] == "R"
    assert HANDS["571448"]["bat"] == "S"  # switch hitter


def test_missing_side_degrades_to_none() -> None:
    assert HANDS["999002"]["bat"] is None
    assert HANDS["999002"]["pit"] == "L"


def test_record_without_id_is_skipped() -> None:
    # The id-less person is dropped; the four with ids survive.
    assert set(HANDS) == {"660271", "605141", "571448", "999002"}


def test_empty_payload_yields_nothing() -> None:
    assert normalize_player_hands({}) == {}
