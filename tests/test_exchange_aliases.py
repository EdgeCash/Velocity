"""Exchange team codes resolve to rating keys — per venue, never merged.

An exchange labels teams with opaque codes. Their meaning is venue-specific:
``sdst`` is South Dakota State on one exchange and San Diego State on the
other, so a table built from one venue's payload must never be applied to the
other's rows. These pin the resolution ladder and the per-venue discipline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from velocity.ingest.kalshi import NFL_CODE_FIXUPS, extract_kalshi_events, normalize_kalshi_markets
from velocity.ingest.kalshi import team_names_by_code as kalshi_names
from velocity.ingest.polymarket import extract_polymarket_events, normalize_polymarket_events
from velocity.ingest.polymarket import team_names_by_code as polymarket_names
from velocity.wagering.live import apply_team_aliases, canonicalize_sides, exchange_aliases

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures"
KALSHI = json.loads((FIX / "kalshi_nfl.json").read_text())
PM_EVENTS = json.loads((FIX / "polymarket_nfl_events.json").read_text())
PM_BOOKS = json.loads((FIX / "polymarket_nfl_books.json").read_text())
NFL_TEAMS = ["ARI", "BUF", "DEN", "HOU", "KC", "LA", "LAC", "NYG", "SF", "JAX"]
STAMP = "2026-09-09T01:00:00Z"


def test_kalshi_names_come_from_winner_markets() -> None:
    names = kalshi_names(KALSHI)
    # Only the winner series names teams; ladders carry strikes, not names.
    assert names == {"NYG": "New York G", "LAR": "Los Angeles R"}


def test_polymarket_names_come_from_moneyline_outcomes() -> None:
    names = polymarket_names(PM_EVENTS)
    assert names["den"] == "Broncos"
    assert names["kc"] == "Chiefs"
    # Away-then-home outcome order, taken per event.
    assert names["buf"] == "Bills"
    assert names["hou"] == "Texans"


def test_codes_resolve_by_code_then_name_then_fixup() -> None:
    aliases = exchange_aliases(kalshi_names(KALSHI), NFL_TEAMS, NFL_CODE_FIXUPS)
    # NYG is already a rating key, so the code resolves directly.
    assert aliases["NYG"] == "NYG"
    # LAR is not, and its display name is truncated to "Los Angeles R" — too
    # mangled to resolve — so the explicit fixup carries it.
    assert aliases["LAR"] == "LA"


def test_college_style_names_resolve_by_school_prefix() -> None:
    # The model keys colleges by school; the venue writes a nickname or an
    # abbreviated "St." that has to expand before it can match.
    known = ["San Jose State", "Georgia", "Appalachian State"]
    aliases = exchange_aliases(
        {"SJSU": "San Jose St.", "UGA": "Georgia Bulldogs", "APP": "Appalachian St."}, known
    )
    assert aliases == {
        "SJSU": "San Jose State",
        "UGA": "Georgia",
        "APP": "Appalachian State",
    }


def test_unresolvable_codes_are_absent_not_guessed() -> None:
    aliases = exchange_aliases({"ZZZ": "Some Unknown School"}, NFL_TEAMS)
    assert aliases == {}


def test_apply_rewrites_both_frames_and_drops_unresolved() -> None:
    lines = normalize_kalshi_markets(KALSHI, STAMP)
    events = extract_kalshi_events(KALSHI)
    aliases = exchange_aliases(kalshi_names(KALSHI), NFL_TEAMS, NFL_CODE_FIXUPS)
    mapped_lines, mapped_events = apply_team_aliases(lines, events, aliases)

    assert set(mapped_events["home_team"]) == {"LA"}
    assert set(mapped_events["away_team"]) == {"NYG"}
    # Team sides are rewritten: LAR becomes LA, while NYG is already a rating
    # key and maps to itself.
    sides = set(mapped_lines["side"])
    assert "LAR" not in sides
    assert {"NYG", "LA"} <= sides
    # Only the game the events frame covers survives. The fixture's ladder
    # rungs belong to games whose winner markets it does not include, and a
    # game with no event cannot have its sides canonicalized.
    assert set(mapped_lines["game_id"]) == {"26SEP21NYGLAR"}
    # Rows for a team that did not resolve are dropped, never guessed.
    partial_lines, partial_events = apply_team_aliases(lines, events, {"NYG": "NYG"})
    assert partial_events.empty
    assert partial_lines.empty


def test_canonicalized_board_survives_side_mapping(games: pd.DataFrame) -> None:
    # The point of doing this per venue: afterwards both boards speak the same
    # team language, so canonicalize_sides maps every team row to home/away.
    lines = normalize_polymarket_events(PM_EVENTS, PM_BOOKS, STAMP)
    events = extract_polymarket_events(PM_EVENTS)
    aliases = exchange_aliases(polymarket_names(PM_EVENTS), NFL_TEAMS)
    mapped_lines, mapped_events = apply_team_aliases(lines, events, aliases)
    assert set(mapped_events["home_team"]) == {"KC", "HOU"}

    # Over/Under sides pass through the alias step untouched, then map like
    # any other side.
    assert {"Over", "Under"} <= set(mapped_lines["side"])
    canonical = canonicalize_sides(mapped_lines, mapped_events)
    assert len(canonical) == len(mapped_lines)
    assert set(canonical["side"]) == {"home", "away", "over", "under"}


def test_two_venues_can_share_a_board_after_canonicalization() -> None:
    # Codes collide across venues, so the tables stay separate and each is
    # applied to its own rows; the concatenated board is then unambiguous.
    k_lines, k_events = apply_team_aliases(
        normalize_kalshi_markets(KALSHI, STAMP),
        extract_kalshi_events(KALSHI),
        exchange_aliases(kalshi_names(KALSHI), NFL_TEAMS, NFL_CODE_FIXUPS),
    )
    p_lines, p_events = apply_team_aliases(
        normalize_polymarket_events(PM_EVENTS, PM_BOOKS, STAMP),
        extract_polymarket_events(PM_EVENTS),
        exchange_aliases(polymarket_names(PM_EVENTS), NFL_TEAMS),
    )
    board = pd.concat([k_lines, p_lines], ignore_index=True)
    events = pd.concat([k_events, p_events], ignore_index=True)
    assert set(board["book"]) == {"kalshi", "polymarket"}
    # Every team label on the shared board is a rating key.
    teams = set(events["home_team"]) | set(events["away_team"])
    assert teams <= set(NFL_TEAMS)
