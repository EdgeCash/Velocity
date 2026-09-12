"""The exchange board beyond football.

Kalshi and Polymarket list all four in-season sports under the same shapes the
football build already reads, but three details differ and each one failed
silently rather than loudly: baseball tickers carry a start time the pattern
did not admit, Gamma's slug alternation named only the football leagues, and
the team-name resolution ladder only ever matched in the direction college
needs. Fixtures are trimmed live payloads captured 2026-09-12.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import velocity.ingest.kalshi as kalshi
from velocity.ingest.exchanges import (
    EXCHANGE_LEAGUES,
    KALSHI_SERIES_BY_LEAGUE,
    POLYMARKET_LEAGUE,
    kalshi_board,
)
from velocity.ingest.polymarket import TAG_IDS, parse_event_slug
from velocity.wagering.live import exchange_aliases, known_by_prefix

FIX = Path(__file__).resolve().parent / "fixtures"
MLB = json.loads((FIX / "kalshi_mlb.json").read_text())
WNBA = json.loads((FIX / "kalshi_wnba.json").read_text())
STAMP = pd.Timestamp("2026-09-12 18:00:00")


def test_a_baseball_ticker_carries_a_start_time_and_still_parses() -> None:
    """Two teams meet twice on a date, so baseball names the hour as well.

    The pattern demanded letters straight after the date, so every baseball
    ticker failed to parse and the whole sport was dropped without a word.
    """
    parsed = kalshi.parse_market_ticker("KXMLBGAME-26SEP142140MIAAZ-MIA")
    assert parsed is not None
    assert (parsed.series, parsed.date_part, parsed.time_part) == (
        "KXMLBGAME", "26SEP14", "2140")
    assert parsed.teams == "MIAAZ" and parsed.suffix == "MIA"
    assert parsed.date == pd.Timestamp("2026-09-14")


def test_the_start_time_keeps_a_doubleheader_as_two_games() -> None:
    # The game key is what every series shares and what a board is keyed by. If
    # it dropped the clock, both halves of a doubleheader would be one game.
    first = kalshi.parse_market_ticker("KXMLBGAME-26SEP121610LADMIA-MIA")
    second = kalshi.parse_market_ticker("KXMLBGAME-26SEP122010LADMIA-MIA")
    assert first is not None and second is not None
    assert first.game_key != second.game_key


def test_football_and_basketball_tickers_have_no_time_and_are_unchanged() -> None:
    nfl = kalshi.parse_market_ticker("KXNFLGAME-26SEP21NYGLAR-NYG")
    wnba = kalshi.parse_market_ticker("KXWNBAGAME-26AUG30CONNDAL-DAL")
    assert nfl is not None and nfl.time_part == "" and nfl.teams == "NYGLAR"
    assert wnba is not None and wnba.time_part == "" and wnba.teams == "CONNDAL"
    assert wnba.game_key == "26AUG30CONNDAL"


def test_all_four_sports_are_mapped_at_both_venues() -> None:
    for league in ("nfl", "ncaaf", "mlb", "wnba"):
        assert league in KALSHI_SERIES_BY_LEAGUE
        assert league in POLYMARKET_LEAGUE
        assert league in EXCHANGE_LEAGUES
    # Each sport files the same four full-game series.
    for series in KALSHI_SERIES_BY_LEAGUE.values():
        assert len(series) == 4
        assert {kalshi.GAME_MARKET_BY_SERIES[s] for s in series} == {
            "moneyline", "spread", "total", "team_total"}


def test_every_mapped_polymarket_league_has_a_tag_and_parses_its_slugs() -> None:
    for gamma_slug in POLYMARKET_LEAGUE.values():
        assert gamma_slug in TAG_IDS
    assert parse_event_slug("mlb-phi-atl-2026-09-11") is not None
    assert parse_event_slug("wnba-conn-atl-2026-09-17") is not None
    # A sport we do not carry still refuses rather than half-parsing.
    assert parse_event_slug("nhl-bos-mtl-2026-09-17") is None


def test_a_venue_name_shorter_than_ours_resolves_by_prefix() -> None:
    """The lookup used to point only the way college needs.

    College boards write more than the model does ("Georgia Bulldogs" for
    "Georgia"); every other sport writes less ("Miami" for "Miami Marlins").
    Matching in one direction alone left all thirty baseball clubs unresolved,
    which reads as an exchange with no markets.
    """
    known = ["Miami Marlins", "Los Angeles Dodgers", "Los Angeles Angels"]
    assert known_by_prefix(["Miami"], known) == {"Miami": "Miami Marlins"}
    # Ambiguity resolves to nothing rather than to a guess.
    assert known_by_prefix(["Los Angeles"], known) == {}
    assert known_by_prefix(["Los Angeles D"], known) == {
        "Los Angeles D": "Los Angeles Dodgers"}


def test_the_awkward_baseball_labels_all_resolve() -> None:
    # Three clubs the prefix rule alone cannot reach: a truncation that is not
    # a prefix, a nickname, and a name the college "St." expansion corrupts.
    names = {"CWS": "Chicago WS", "ATH": "A's", "STL": "St. Louis",
             "CHC": "Chicago C", "SF": "San Francisco"}
    known = ["Chicago White Sox", "Athletics", "St. Louis Cardinals",
             "Chicago Cubs", "San Francisco Giants"]
    resolved = exchange_aliases(names, known, kalshi.CODE_FIXUPS_BY_LEAGUE["mlb"])
    assert resolved == {
        "CWS": "Chicago White Sox", "ATH": "Athletics",
        "STL": "St. Louis Cardinals", "CHC": "Chicago Cubs",
        "SF": "San Francisco Giants",
    }


def test_the_college_direction_still_works() -> None:
    # The reverse match must not cost the case it was written for.
    names = {"UGA": "Georgia", "SJSU": "San Jose St."}
    known = ["Georgia", "San Jose State", "Georgia Southern"]
    resolved = exchange_aliases(names, known, kalshi.CODE_FIXUPS_BY_LEAGUE["ncaaf"])
    assert resolved == {"UGA": "Georgia", "SJSU": "San Jose State"}


def test_a_real_baseball_board_reaches_the_slate() -> None:
    """End to end on a captured payload: winner, spread and total all priced."""
    base = pd.DataFrame(
        {
            "game_id": ["evt-phi-atl"],
            "kickoff": [pd.Timestamp("2026-09-11 23:15:00")],
            "home_team": ["Atlanta Braves"],
            "away_team": ["Philadelphia Phillies"],
        }
    )
    known = ["Atlanta Braves", "Philadelphia Phillies"]
    lines, note = kalshi_board(
        {"KXMLBGAME": MLB, "KXMLBSPREAD": MLB, "KXMLBTOTAL": MLB},
        known, base, STAMP, league="mlb",
    )
    assert note["games"] == 1
    assert note["unresolved_sides"] == 0
    assert set(lines["game_id"]) == {"evt-phi-atl"}
    # Every market family survives, and team-named sides are canonical.
    assert {"moneyline", "spread", "total"} <= set(lines["market"])
    team_named = lines[lines["market"].isin(["moneyline", "spread"])]
    assert set(team_named["side"]) <= {"home", "away"}


_WNBA_BASE = pd.DataFrame(
    {
        "game_id": ["evt-wsh-pdx"],
        "kickoff": [pd.Timestamp("2026-08-23 20:00:00")],
        "home_team": ["Portland Fire"],
        "away_team": ["Washington Mystics"],
    }
)
_WNBA_TEAMS = ["Portland Fire", "Washington Mystics"]


def test_a_basketball_board_resolves_its_own_labels() -> None:
    """The capture is settled markets — the league had no open board on the day.

    A finalized contract quotes $1.00 both ways, which is not a price anyone
    could have taken, so it is refused and the board prices nothing. What this
    still pins is the half that was broken: the ticker grammar and the identity
    resolution both reach the sportsbook board's game.
    """
    lines, note = kalshi_board(
        {"KXWNBAGAME": WNBA}, _WNBA_TEAMS, _WNBA_BASE, STAMP, league="wnba"
    )
    assert note["games"] == 1, "the game must resolve onto the base board"
    assert lines.empty, "a settled contract has no live ask to price"


def test_a_basketball_board_prices_once_the_market_is_open() -> None:
    # The same captured markets with an open two-way quote written over the
    # settled $1.00, since no WNBA board was open on the capture day.
    opened = {
        "markets": [
            {**m, "status": "active", "yes_ask_dollars": ask, "no_ask_dollars": other}
            for m, ask, other in zip(
                WNBA["markets"], ("0.4300", "0.5900"), ("0.5900", "0.4300"), strict=False
            )
        ]
    }
    lines, note = kalshi_board(
        {"KXWNBAGAME": opened}, _WNBA_TEAMS, _WNBA_BASE, STAMP, league="wnba"
    )
    assert note["games"] == 1 and note["unresolved_sides"] == 0
    assert set(lines["game_id"]) == {"evt-wsh-pdx"}
    assert set(lines["market"]) == {"moneyline"}
    assert set(lines["side"]) == {"home", "away"}
