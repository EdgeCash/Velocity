"""BettingPros ingest — nested live JSON flattens to canonical lines, offline.

Exercises only the pure ``normalize_offers`` / ``to_lines`` mappings against a
frozen sample that mimics the ``/offers`` + ``/markets`` response shapes. The
network :class:`BettingProsClient` is not touched here.
"""

from __future__ import annotations

import json

import pandas as pd
from velocity.ingest.bettingpros import (
    BP_PROP_SLUG_TO_MARKET,
    GAME_MARKET_BY_SLUG,
    PROPS_PAGE_LIMIT,
    BettingProsClient,
    bp_board,
    bp_book_keys,
    describe_payload_shape,
    describe_slug_coverage,
    merge_prop_pages,
    normalize_books,
    normalize_lineups,
    normalize_offers,
    normalize_props,
    pagination,
    payload_errors,
    resolve_sides_within_game,
    scrub_secrets,
    served_nothing,
    slug_coverage,
    snapshot_age_minutes,
    to_lines,
)
from velocity.store.schema import Lines

# A tiny but faithful slice: three game markets on one event, plus a player prop.
MARKETS = [
    {"id": 1, "slug": "moneyline", "category": "game-odds"},
    {"id": 2, "slug": "spread", "category": "game-odds"},
    {"id": 3, "slug": "total", "category": "game-odds"},
    {"id": 71, "slug": "receiving-yards", "category": "player-props"},
]

OFFERS = [
    {
        "id": "off-spread",
        "market_id": 2,
        "event_id": 1001,
        "player_id": None,
        "team_id": None,
        "selections": [
            {
                "selection": None,
                "participant": "15154",
                "label": "Chiefs",
                "books": [
                    {"id": 10, "lines": [{"cost": -110, "line": -3.5, "main": True, "best": True}]},
                    {"id": 12, "lines": [{"cost": -108, "line": -3.5, "main": True}]},
                ],
            },
            {
                "selection": None,
                "participant": "15155",
                "label": "Bills",
                "books": [
                    {"id": 10, "lines": [{"cost": -110, "line": 3.5, "main": True, "best": True}]},
                ],
            },
        ],
    },
    {
        "id": "off-total",
        "market_id": 3,
        "event_id": 1001,
        "selections": [
            {
                "selection": "Over",
                "books": [
                    {
                        "id": 10,
                        "lines": [
                            {"cost": -110, "line": 47.5, "main": True, "best": True},
                            # a stale alternate the book pulled — must be skipped
                            {"cost": -105, "line": 48.5, "main": False, "is_off": True},
                        ],
                    }
                ],
            },
            {
                "selection": "Under",
                "books": [
                    {"id": 10, "lines": [{"cost": -110, "line": 47.5, "main": True, "best": True}]}
                ],
            },
        ],
    },
    {
        "id": "off-ml",
        "market_id": 1,
        "event_id": 1001,
        "selections": [
            {"selection": "Chiefs", "books": [{"id": 10, "lines": [{"cost": -175, "line": 0}]}]},
            {"selection": "Bills", "books": [{"id": 10, "lines": [{"cost": 150, "line": 0}]}]},
        ],
    },
    {
        "id": "off-prop",
        "market_id": 71,
        "event_id": 1001,
        "player_id": "20999",
        "selections": [
            {"selection": "Over", "books": [{"id": 10, "lines": [{"cost": -115, "line": 275.5}]}]},
            {"selection": "Under", "books": [{"id": 10, "lines": [{"cost": -105, "line": 275.5}]}]},
        ],
    },
]


def _stamp(offers: list[dict], when: str = "2026-01-05T18:00:00Z") -> list[dict]:
    """Add the required ``updated`` timestamp every real BP line carries."""
    for offer in offers:
        for selection in offer.get("selections", []):
            for book in selection.get("books", []):
                for line in book.get("lines", []):
                    line.setdefault("updated", when)
    return offers


_stamp(OFFERS)


def test_normalize_offers_flattens_every_active_line() -> None:
    long = normalize_offers(OFFERS, MARKETS)
    # spread: 2 books home + 1 book away = 3; total: over(1) + under(1) = 2 (stale
    # alt dropped); ml: 2; prop: 2 → 9 rows.
    assert len(long) == 9
    assert set(long["market_slug"]) == {
        "moneyline",
        "spread",
        "total",
        "receiving-yards",
    }
    # The pulled alternate (48.5) never appears.
    assert 48.5 not in set(long["point"].dropna())


def test_to_lines_keeps_only_game_markets_and_validates() -> None:
    lines = to_lines(normalize_offers(OFFERS, MARKETS))
    Lines.validate(lines)
    assert set(lines["market"]) == {"spread", "total", "moneyline"}
    # The player prop is excluded from the game-level Lines view.
    assert len(lines) == 7  # 3 spread + 2 total + 2 ml


def test_moneyline_point_is_null_others_carry_number() -> None:
    lines = to_lines(normalize_offers(OFFERS, MARKETS))
    ml = lines[lines["market"] == "moneyline"]
    assert ml["point"].isna().all()
    total = lines[lines["market"] == "total"]
    assert set(total["point"]) == {47.5}


def test_price_is_integer_american() -> None:
    lines = to_lines(normalize_offers(OFFERS, MARKETS))
    assert lines["price"].dtype.kind == "i"
    assert -175 in set(lines["price"])


def test_line_id_is_unique_and_stable() -> None:
    lines = to_lines(normalize_offers(OFFERS, MARKETS))
    assert lines["line_id"].is_unique
    # Re-running yields identical ids (no time/random component).
    again = to_lines(normalize_offers(OFFERS, MARKETS))
    assert list(lines["line_id"]) == list(again["line_id"])


def test_empty_offers_yield_empty_valid_lines() -> None:
    lines = to_lines(normalize_offers([], MARKETS))
    Lines.validate(lines)
    assert lines.empty


def test_offers_without_markets_have_blank_slugs() -> None:
    # Without the markets map, slugs are unknown → no game markets survive to_lines.
    long = normalize_offers(OFFERS)
    assert (long["market_slug"] == "").all()
    assert to_lines(long).empty


def test_game_market_slugs_map_to_canonical_markets() -> None:
    assert set(GAME_MARKET_BY_SLUG.values()) == {"spread", "total", "moneyline"}


def test_from_env_requires_api_key(monkeypatch) -> None:
    monkeypatch.delenv("BP_API_KEY", raising=False)
    try:
        BettingProsClient.from_env()
    except RuntimeError as exc:
        assert "BP_API_KEY" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected RuntimeError when BP_API_KEY is unset")


def test_from_env_premium_flag(monkeypatch) -> None:
    monkeypatch.setenv("BP_API_KEY", "partner-key")
    monkeypatch.setenv("BP_USER_ID", "4455432")
    monkeypatch.setenv("BP_USER_KEY", "user-key")
    client = BettingProsClient.from_env()
    assert client.is_premium
    monkeypatch.delenv("BP_USER_ID")
    assert not BettingProsClient.from_env().is_premium


def test_stale_and_replaced_lines_dropped() -> None:
    offers = [
        {
            "id": "o1",
            "market_id": 3,
            "event_id": 5,
            "selections": [
                {
                    "selection": "Over",
                    "books": [
                        {
                            "id": 9,
                            "lines": [
                                {"cost": -110, "line": 40, "replaced": True},
                                {"cost": -110, "line": 41, "active": False},
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    assert normalize_offers(offers, MARKETS).empty
    assert isinstance(to_lines(normalize_offers(offers, MARKETS)), pd.DataFrame)


def test_normalize_props_flattens_the_board() -> None:
    from velocity.ingest.bettingpros import normalize_props

    payload = {
        "props": [
            {
                "sport": "NFL",
                "market_id": 102,
                "event_id": 555,
                "participant": {
                    "id": "p1", "name": "Josh Allen",
                    "player": {"team": "BUF", "position": "QB"},
                },
                "over": {"line": 249.5, "odds": -115, "book": 12,
                         "consensus_line": 250.5},
                "under": {"line": 249.5, "odds": -105, "book": 10,
                          "consensus_line": 250.5},
                "projection": {
                    "recommended_side": "over", "value": 264.2,
                    "probability": 0.58, "expected_value": 4.1,
                    "bet_rating": 4, "diff": 14.7,
                },
            },
            {  # free-tier row: premium projection fields nulled by the API
                "sport": "NFL",
                "market_id": 103,
                "event_id": 555,
                "participant": {"id": "p2", "name": "James Cook",
                                "player": {"team": "BUF", "position": "RB"}},
                "over": {"line": 84.5, "odds": -110, "book": 12},
                "under": {"line": 84.5, "odds": -110, "book": 12},
                "projection": {"recommended_side": None, "value": None,
                               "probability": None, "expected_value": None,
                               "bet_rating": None, "diff": None},
            },
            {"sport": "NFL", "participant": {"id": "x"}},  # no market_id → dropped
            "junk",
        ]
    }
    df = normalize_props(payload)
    assert len(df) == 2
    assert (df["market_slug"] == "").all()  # no metadata passed → empty, not a guess
    df = normalize_props(payload, {102: "passing-yards"})
    allen = df[df["player_name"] == "Josh Allen"].iloc[0]
    assert allen["market_slug"] == "passing-yards"  # stamped from /markets metadata
    cook_slug = df[df["player_name"] == "James Cook"].iloc[0]["market_slug"]
    assert cook_slug == ""  # id 103 unknown to the listing → empty
    assert allen["market_id"] == 102
    assert allen["team"] == "BUF"
    assert allen["over_line"] == 249.5
    assert allen["over_book"] == 12
    assert allen["consensus_over_line"] == 250.5
    assert allen["projection"] == 264.2
    assert allen["recommended_side"] == "over"
    assert allen["expected_value"] == 4.1
    cook = df[df["player_name"] == "James Cook"].iloc[0]
    assert pd.isna(cook["projection"])  # premium-nulled stays NaN, not an error
    assert pd.isna(cook["expected_value"])
    assert normalize_props(None).empty
    assert normalize_props({}).empty


# --------------------------------------------------------------------------
# The board — a banked snapshot aligned onto the slate's own games
# --------------------------------------------------------------------------

BASE_EVENTS = pd.DataFrame(
    {
        "game_id": ["odds-1"],
        "home_team": ["Kansas City Chiefs"],
        "away_team": ["Buffalo Bills"],
        "kickoff": [pd.Timestamp("2026-01-05T23:00:00")],
    }
)
KNOWN_TEAMS = ["KC", "BUF"]
NOW = pd.Timestamp("2026-01-05T19:00:00")
COLLECTED = pd.Timestamp("2026-01-05T18:00:00")


def _banked(collected_at: pd.Timestamp = COLLECTED):
    """The collector's two parquets, as frames: canonical lines + the events map."""
    lines = to_lines(normalize_offers(OFFERS, MARKETS)).assign(
        league="nfl", collected_at=collected_at
    )
    events = pd.DataFrame(
        {
            "game_id": ["1001"],
            "home_team": ["Kansas City Chiefs"],
            "away_team": ["Buffalo Bills"],
            "kickoff": [pd.Timestamp("2026-01-05T23:00:00")],
            "league": ["nfl"],
            "collected_at": [collected_at],
        }
    )
    return lines, events


def test_bp_board_aligns_onto_the_base_board_and_prefixes_books() -> None:
    lines, events = _banked()
    board, notes = bp_board(lines, events, KNOWN_TEAMS, BASE_EVENTS, now=NOW, league="nfl")
    assert not board.empty
    assert notes["stale"] is False
    # Re-keyed onto the sportsbook board's game id, not BettingPros' own.
    assert set(board["game_id"]) == {"odds-1"}
    # Every book carries the feed in its identity, so a grader can tell a
    # BettingPros number from an Odds API one at the same book.
    assert all(str(b).startswith("bp:") for b in board["book"])
    # Sides speak the slate's language.
    assert set(board["side"]) <= {"home", "away", "over", "under"}


def test_bp_board_resolves_book_names_when_the_listing_is_supplied() -> None:
    lines, events = _banked()
    board, _ = bp_board(
        lines, events, KNOWN_TEAMS, BASE_EVENTS, now=NOW, league="nfl",
        book_names={"10": "draftkings", "12": "fanduel"},
    )
    assert {"bp:draftkings", "bp:fanduel"} == set(board["book"])


def test_bp_board_refuses_a_snapshot_past_the_age_gate() -> None:
    lines, events = _banked(collected_at=pd.Timestamp("2026-01-05T10:00:00"))
    board, notes = bp_board(
        lines, events, KNOWN_TEAMS, BASE_EVENTS, now=NOW, league="nfl", max_age_minutes=200.0
    )
    assert board.empty
    assert notes["stale"] is True
    assert notes["age_min"] == 540.0


def test_bp_board_treats_an_unstamped_snapshot_as_stale() -> None:
    """An age we cannot establish is the failure the gate exists to prevent."""
    lines, events = _banked()
    lines = lines.drop(columns=["collected_at"]).assign(timestamp=pd.NaT)
    board, notes = bp_board(lines, events, KNOWN_TEAMS, BASE_EVENTS, now=NOW, league="nfl")
    assert board.empty
    assert notes["stale"] is True


def test_bp_board_filters_to_the_league_being_priced() -> None:
    lines, events = _banked()
    board, notes = bp_board(lines, events, KNOWN_TEAMS, BASE_EVENTS, now=NOW, league="ncaaf")
    assert board.empty
    assert notes["games"] == 0


def test_bp_board_drops_a_game_the_base_board_does_not_carry() -> None:
    lines, events = _banked()
    other = BASE_EVENTS.assign(home_team=["Denver Broncos"], away_team=["Las Vegas Raiders"])
    board, _ = bp_board(lines, events, ["DEN", "LV"], other, now=NOW, league="nfl")
    assert board.empty


def test_bp_board_output_validates_as_lines() -> None:
    lines, events = _banked()
    board, _ = bp_board(lines, events, KNOWN_TEAMS, BASE_EVENTS, now=NOW, league="nfl")
    Lines.validate(board)


def test_bp_book_keys_reports_every_venue_the_runner_must_paper() -> None:
    lines, events = _banked()
    board, _ = bp_board(lines, events, KNOWN_TEAMS, BASE_EVENTS, now=NOW, league="nfl")
    assert bp_book_keys(board) == {"bp:10", "bp:12"}
    assert bp_book_keys(board.iloc[0:0]) == frozenset()


def test_normalize_books_slugs_names_and_abstains_on_junk() -> None:
    assert normalize_books({"books": [{"id": 10, "name": "DraftKings"}]}) == {"10": "draftkings"}
    assert normalize_books(
        {"books": [{"id": 13, "name": "Caesars Sportsbook"}]}
    ) == {"13": "caesars-sportsbook"}
    # No id or no name contributes nothing — the board falls back to the raw id.
    assert normalize_books({"books": [{"id": None, "name": "x"}, {"id": 4}]}) == {}
    assert normalize_books(None) == {}


def test_snapshot_age_prefers_collected_at_over_a_lines_own_timestamp() -> None:
    """A book that has not repriced in a day still has a live number today."""
    lines, _ = _banked()
    lines = lines.assign(timestamp=pd.Timestamp("2026-01-01T00:00:00"))
    assert snapshot_age_minutes(lines, NOW) == 60.0


def test_bp_board_keeps_spreads_and_moneylines_despite_nickname_labels() -> None:
    """The silent failure this board is built to avoid.

    BettingPros labels selections "Chiefs"/"Bills" while its events name the
    teams in full. Matching those as plain strings drops every spread and
    moneyline and leaves a board of totals — priced, carded, and wrong about
    what it is.
    """
    lines, events = _banked()
    board, notes = bp_board(lines, events, KNOWN_TEAMS, BASE_EVENTS, now=NOW, league="nfl")
    assert set(board["market"]) == {"spread", "total", "moneyline"}
    assert notes["unresolved_sides"] == 0
    # Both sides of the spread survive, from both books that quoted it.
    spread = board[board["market"] == "spread"]
    assert set(spread["side"]) == {"home", "away"}


def test_within_game_side_resolution_drops_an_ambiguous_label() -> None:
    """A label matching both teams — or neither — is never guessed."""
    events = pd.DataFrame(
        {"game_id": ["g1"], "home_team": ["New York Giants"], "away_team": ["New York Jets"]}
    )
    lines = pd.DataFrame(
        {
            "game_id": ["g1", "g1", "g1"],
            "side": ["New York", "Jets", "Packers"],
            "market": ["moneyline"] * 3,
        }
    )
    out = resolve_sides_within_game(lines, events)
    # "New York" matches both, "Packers" neither; only "Jets" resolves.
    assert list(out["side"]) == ["away"]


def test_bp_board_picks_its_league_out_of_a_multi_league_bank() -> None:
    """The collector banks NFL, NCAAF and MLB into one parquet per run.

    A board that ignored the league column would price baseball rows onto a
    football card the moment MLB joined the collector.
    """
    nfl_lines, nfl_events = _banked()
    mlb_lines = nfl_lines.assign(league="mlb", game_id="2002")
    mlb_events = nfl_events.assign(
        league="mlb", game_id="2002",
        home_team="Kansas City Royals", away_team="Buffalo Bisons",
    )
    lines = pd.concat([nfl_lines, mlb_lines], ignore_index=True)
    events = pd.concat([nfl_events, mlb_events], ignore_index=True)

    board, notes = bp_board(lines, events, KNOWN_TEAMS, BASE_EVENTS, now=NOW, league="nfl")
    assert notes["games"] == 1
    # Only the football game's rows survived; the baseball ones never met the
    # football board at all.
    assert set(board["game_id"]) == {"odds-1"}
    assert len(board) == len(nfl_lines)


# --------------------------------------------------------------------------
# Slug coverage — the report that makes an abstaining board visible
# --------------------------------------------------------------------------


def _props_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "market_slug": (
            ["passing-yards"] * 4 + ["receptions"] * 2 + ["anytime-touchdown"] * 3 + [""]
        )
    })


def test_slug_coverage_counts_rows_and_separates_mapped_from_unmapped() -> None:
    table = slug_coverage(_props_frame())
    by_slug = {row["market_slug"]: row for row in table.to_dict("records")}
    assert by_slug["passing-yards"]["rows"] == 4
    assert by_slug["passing-yards"]["market"] == "pass_yards"
    assert by_slug["passing-yards"]["mapped"] is True
    assert by_slug["passing-yards"]["priced"] is True
    # A slug the table does not name abstains — reported, never guessed at.
    assert by_slug["anytime-touchdown"]["mapped"] is False
    assert by_slug["anytime-touchdown"]["market"] == ""
    # Busiest first, so the biggest gap is the first thing read.
    assert table.iloc[0]["market_slug"] == "passing-yards"


def test_slug_coverage_surfaces_a_snapshot_whose_market_listing_failed() -> None:
    """An empty slug is a collector problem and must read as one."""
    table = slug_coverage(_props_frame())
    assert "(no slug)" in set(table["market_slug"])


def test_slug_coverage_is_empty_for_a_board_with_no_props() -> None:
    assert slug_coverage(pd.DataFrame()).empty
    assert slug_coverage(pd.DataFrame({"other": [1]})).empty


def test_describe_slug_coverage_names_every_unmapped_slug() -> None:
    lines = "\n".join(describe_slug_coverage(_props_frame(), "NFL"))
    # 4 distinct slugs (two mapped, one unmapped, one blank), 6 of 10 rows usable.
    assert "2 of 4 slug(s) mapped, 6 of 10 rows usable" in lines
    assert "UNMAPPED anytime-touchdown" in lines
    assert "BP_PROP_SLUG_TO_MARKET" in lines


def test_every_mapped_slug_points_at_a_market_we_actually_price() -> None:
    """A slug mapped to a market no model prices banks rows nothing can use."""
    from velocity.ingest.bettingpros import BP_PROP_SLUG_TO_MARKET
    from velocity.store.schema import PROP_MARKETS

    unpriced = {
        slug: market
        for slug, market in BP_PROP_SLUG_TO_MARKET.items()
        if market not in PROP_MARKETS
    }
    assert not unpriced, unpriced


# --------------------------------------------------------------------------
# Paging and credential scrubbing — measured against a real banked payload
# --------------------------------------------------------------------------


def test_secrets_in_the_echoed_request_url_are_redacted() -> None:
    """BettingPros echoes the full request URL, credentials and all.

    The collector banks the raw payload to an artifact that outlives the run
    by a month, so the partner key and user id were being written to disk on
    every snapshot. Measured on a live 2026-09-15 response: key=, user= and
    auth= each appeared three times.
    """
    payload = {"_pagination": {
        "self": "/v3/props?auth=user&key=abc123def456&user=4455432&limit=5000",
    }, "props": [{"nested": ["?key=abc123def456"]}]}
    scrubbed = scrub_secrets(payload)
    blob = json.dumps(scrubbed)
    assert "abc123def456" not in blob
    assert "4455432" not in blob
    assert blob.count("REDACTED") == 4  # three in the URL, one nested
    # Structure and everything else survive, so a scrubbed payload still
    # normalizes identically.
    assert list(scrubbed) == list(payload)


def test_scrubbing_leaves_ordinary_values_alone() -> None:
    payload = {"props": [{"player_name": "A. Keyman", "line": 249.5, "book": 10}]}
    assert scrub_secrets(payload) == payload


def test_pagination_reads_the_servers_own_page_count() -> None:
    """The metadata nothing read, which is why truncation was invisible."""
    assert pagination({"_pagination": {
        "page": 1, "limit": 200, "total_pages": 5, "total_items": 940,
    }}) == {"page": 1, "limit": 200, "total_pages": 5, "total_items": 940}
    # No metadata, or junk, means "treat it as one page" — chosen, not assumed.
    assert pagination({}) == {}
    assert pagination({"_pagination": {"total_pages": "many"}}) == {}
    assert pagination(None) == {}


def test_merged_pages_concatenate_and_record_what_was_collected() -> None:
    pages = [
        {"label": "NFL Props", "_pagination": {"total_pages": 3, "total_items": 5},
         "props": [{"a": 1}, {"a": 2}]},
        {"props": [{"a": 3}, {"a": 4}]},
        {"props": [{"a": 5}]},
    ]
    merged = merge_prop_pages(pages)
    assert [p["a"] for p in merged["props"]] == [1, 2, 3, 4, 5]
    # The envelope comes from page one...
    assert merged["label"] == "NFL Props"
    # ...and the merged metadata says what was actually gathered, so a
    # truncated run is legible instead of looking like a small board.
    assert merged["_pagination"]["pages_collected"] == 3
    assert merged["_pagination"]["items_collected"] == 5
    assert merged["_pagination"]["total_items"] == 5


def test_merging_nothing_is_empty_not_a_crash() -> None:
    assert merge_prop_pages([])["props"] == []


def test_the_page_limit_matches_what_the_server_enforces() -> None:
    """Asking for more is silently capped, so the constant is the real cap."""
    assert PROPS_PAGE_LIMIT == 200


def test_every_mapped_slug_still_points_at_a_market_we_price() -> None:
    """Re-pinned after the strikeouts mapping landed."""
    from velocity.store.schema import PROP_MARKETS

    assert BP_PROP_SLUG_TO_MARKET["strikeouts"] == "pitcher_strikeouts"
    assert all(m in PROP_MARKETS for m in BP_PROP_SLUG_TO_MARKET.values())


def test_the_two_football_slugs_added_from_the_coverage_report_are_mapped() -> None:
    """Both were measured against the banked player-weeks before being added.

    ``rushing-receiving-yards`` needed no new projection — the sim already
    draws both legs — and ``interceptions`` rides a FantasyPros key the DFS
    scorer has been consuming all along.
    """
    assert BP_PROP_SLUG_TO_MARKET["rushing-receiving-yards"] == "rush_rec_yards"
    assert BP_PROP_SLUG_TO_MARKET["interceptions"] == "interceptions"


def test_the_attempt_slugs_are_mapped_now_the_census_confirmed_the_feed() -> None:
    """These waited on a measurement, and got one.

    The FantasyPros stat-key census (run 35108513721) found ``rush_att`` on
    309 of 422 skill players and ``pass_att`` on 70 of 82 passers. Before that
    nothing in this codebase read an attempt projection, so mapping either
    would have abstained silently rather than honestly — which is the failure
    mode the coverage report exists to surface.
    """
    assert BP_PROP_SLUG_TO_MARKET["rushing-attempts"] == "rush_attempts"
    assert BP_PROP_SLUG_TO_MARKET["passing-attempts"] == "pass_attempts"


def test_completions_map_now_the_actuals_are_banked() -> None:
    """This test used to assert the opposite, and was right to.

    The projection was always served; what was missing was a completions
    column in ``player_weeks`` — no dispersion to fit, nothing to settle
    against, and a market that prices but cannot settle stakes and sits
    pending forever (#208). nflverse served the column all along; we simply
    never kept it. Banking it is what unblocked this.
    """
    assert BP_PROP_SLUG_TO_MARKET["passing-completions"] == "pass_completions"


# --------------------------------------------------------------------------
# Payload shape reporting — the OpenAPI document types every array as [{}]
# --------------------------------------------------------------------------


def test_shape_report_separates_what_we_read_from_what_we_do_not() -> None:
    rows = [
        {"id": 1, "projection": {"value": 1.0}, "performance": {"over_pct": 0.6}},
        {"id": 2, "projection": {"value": 2.0}},
    ]
    lines = "\n".join(describe_payload_shape(rows, ["id", "projection"], "NFL props"))
    assert "2 row(s), 3 distinct key(s)" in lines
    assert "read  id" in lines
    assert "UNREAD performance" in lines
    # Presence is per-key, so a field only some rows carry is visible as such.
    assert "1/2" in lines
    assert "1 key(s) we do not read" in lines


def test_shape_report_describes_structure_and_never_values() -> None:
    """A payload that echoes credentials must not be made worse by the report."""
    rows = [{
        "self": "/v3/props?key=SUPERSECRET&user=4455432",
        "books": [{"id": 10}, {"id": 12}],
        "count": 7,
        "missing": None,
    }]
    lines = "\n".join(describe_payload_shape(rows, [], "probe"))
    assert "SUPERSECRET" not in lines
    assert "4455432" not in lines
    assert "str" in lines                    # the URL reports as a type
    assert "list[2] of object" in lines
    assert "int" in lines and "null" in lines


def test_shape_report_names_a_nested_objects_own_keys() -> None:
    """The point is to write a normalizer from it, so one level down shows."""
    rows = [{"projection": {"value": 1.0, "bet_rating": 4, "probability": 0.6}}]
    lines = "\n".join(describe_payload_shape(rows, [], "x"))
    assert "object{value, bet_rating, probability}" in lines


def test_shape_report_is_inert_for_junk() -> None:
    assert "0 row(s)" in describe_payload_shape([], [], "x")[0]
    assert "0 row(s)" in describe_payload_shape([None, 3, "x"], [], "x")[0]  # type: ignore[list-item]


def test_props_asks_for_the_full_board() -> None:
    """Defaults that would silently truncate, pinned.

    ``ev_threshold`` defaults to TRUE server-side, which returns only props
    outside EV > 40% or < -25% — a filtered board that looks like a whole one.

    This used to pin ``include_correlated_picks`` as present too. That
    assertion was wrong and it held the bug in place: see
    ``test_props_no_longer_asks_for_correlated_picks``.
    """
    import inspect

    src = inspect.getsource(BettingProsClient.props)
    assert '"ev_threshold": "false"' in src
    assert '"limit": PROPS_PAGE_LIMIT' in src


def test_events_asks_for_the_blocks_that_ride_on_the_same_call() -> None:
    """lineups/park_factors default true; notes/officials default false."""
    import inspect

    src = inspect.getsource(BettingProsClient.events_payload)
    for key in ("lineups", "park_factors", "notes", "officials"):
        assert f'"{key}": "true"' in src, key


# ---- what the 2026-09-16 board actually returned ---------------------------
# Both of these are shapes taken from a real banked run (artifact
# bp-lines-35090630550), not invented: the first made a complete pull report
# itself as truncated, the second made a provider outage read as an off-day.


def test_pagination_carries_the_merge_counters_not_just_the_servers_keys() -> None:
    """A complete 3-page pull was printing "1 of 3 page(s)" and warning.

    `pages_collected` / `items_collected` are added by `merge_prop_pages`, not
    by the server. Filtering the block down to the server's four keys dropped
    them, so the collector's `meta.get("pages_collected", 1)` fell back to 1
    every single run — on a pull that had fetched everything.
    """
    merged = merge_prop_pages([
        {"_pagination": {"page": 1, "limit": 200, "total_pages": 3, "total_items": 551},
         "props": [{"market_id": 1}] * 200},
        {"props": [{"market_id": 1}] * 200},
        {"props": [{"market_id": 1}] * 151},
    ])
    meta = pagination(merged)
    assert meta["pages_collected"] == 3
    assert meta["items_collected"] == 551
    # And the comparison the collector makes off it now answers correctly.
    assert meta["pages_collected"] >= meta["total_pages"]


def test_the_error_sentinel_is_not_an_empty_board() -> None:
    """HTTP 200, healthy envelope, `props: ["error"]` — one per page.

    Observed on MLB, NCAAF, WNBA and NHL while NFL served 551 real rows, with
    `label` reading "MLB props for September 16th, 2026" and `total_items`
    2493 throughout. `normalize_props` skips non-Mapping rows, so thirteen of
    these normalize to zero and the run prints an empty board — an outage and
    an off-day become the same line of output.
    """
    outage = {
        "label": "MLB props for September 16th, 2026",
        "_pagination": {"page": 1, "limit": 200, "total_pages": 13,
                        "total_items": 2493, "pages_collected": 13,
                        "items_collected": 13},
        "props": ["error"] * 13,
    }
    assert payload_errors(outage) == 13
    # The thing that made it invisible: it still normalizes to nothing.
    assert normalize_props(outage).empty

    # A real board reports no errors, and a genuinely empty one is not an
    # outage — the two have to stay distinguishable in both directions.
    assert payload_errors({"props": [{"market_id": 1, "participant": {"player": {}}}]}) == 0
    assert payload_errors({"props": []}) == 0
    assert payload_errors({}) == 0
    assert payload_errors(None) == 0


def test_props_no_longer_asks_for_correlated_picks() -> None:
    """Asking for it returns an EMPTY board on every sport except NFL.

    Measured 2026-09-16 (run 35099581241): MLB served 0 rows with the flag and
    200 without, against total_items 2669; NCAAF, WNBA and NHL the same. It
    was added in #201 for a field nothing reads yet, and it cost four of the
    five prop boards. The default is pinned here because putting it back looks
    harmless.
    """
    import inspect

    from velocity.ingest.bettingpros import BettingProsClient

    source = inspect.getsource(BettingProsClient.props)
    body = source.split('defaults: dict[str, object] = {')[1].split('}')[0]
    assert '"include_correlated_picks"' not in body


def test_an_empty_board_with_a_full_envelope_is_a_failure() -> None:
    """No sentinel, just nothing — while total_items insists there are 2669."""
    assert served_nothing({
        "_pagination": {"total_items": 2669, "total_pages": 14}, "props": [],
    })
    # A genuinely empty board says so in both numbers, and is not a failure.
    assert not served_nothing({"_pagination": {"total_items": 0}, "props": []})
    # A served board is not a failure whatever else is true of it.
    assert not served_nothing({
        "_pagination": {"total_items": 550}, "props": [{"market_id": 1}],
    })
    assert not served_nothing({})
    assert not served_nothing(None)


def test_the_whole_nfl_board_is_mapped_now() -> None:
    """Completions was the last slug the board served and we abstained on.

    The coverage report that started this had 5 of 10 slugs mapped and 388 of
    551 NFL rows usable. Every slug that report listed now points at a market
    the props stack prices.
    """
    from velocity.store.schema import PROP_MARKETS

    served_by_the_nfl_board = (
        "receiving-yards", "receptions", "rushing-yards", "passing-yards",
        "passing-touchdowns", "rushing-attempts", "rushing-receiving-yards",
        "passing-attempts", "passing-completions", "interceptions",
    )
    unmapped = [s for s in served_by_the_nfl_board if s not in BP_PROP_SLUG_TO_MARKET]
    assert not unmapped, f"still abstaining on {unmapped}"
    assert all(BP_PROP_SLUG_TO_MARKET[s] in PROP_MARKETS
               for s in served_by_the_nfl_board)


# --------------------------------------------------------------------------
# Today's batting order — audit finding 1 (2026-09-16)
# --------------------------------------------------------------------------
#
# /events has served `lineups` all along (it defaults true), and the collector
# kept five of the event's twenty-three fields. Meanwhile build_hr_board.py
# took a batter's slot from his PREVIOUS game, so a hitter who moved in the
# order was priced at the wrong plate appearances and a benched hitter was
# priced as a starter.
#
# The shape here is copied from a real banked payload (run 35124718421), not
# imagined — which is the discipline the slug table cost this repo twice.


def _lineup_event(game_id="98076", home_type="confirmed", visitor_type="projected"):
    def slot(n, name, pid, team, pos):
        return {"slot": n, "position": pos,
                "participant": {"id": pid, "name": name,
                                "player": {"team": team, "position": pos}}}
    return {
        "id": game_id,
        "lineups": {
            "home_lineup": [slot(1, "Carlos Jorge", "45120", "CIN", "CF"),
                            slot(2, "Elly De La Cruz", "44884", "CIN", "SS")],
            "visitor_lineup": [slot(1, "Josue De Paula", "46763", "LAD", "DH")],
            "home_lineup_type": home_type,
            "visitor_lineup_type": visitor_type,
        },
    }


def test_lineups_normalize_to_one_row_per_slot() -> None:
    frame = normalize_lineups({"events": [_lineup_event()]}, "mlb")
    assert len(frame) == 3
    assert set(frame["side"]) == {"home", "away"}, "BP says visitor; we say away"
    assert sorted(frame[frame["side"] == "home"]["slot"]) == [1, 2]
    assert frame["league"].eq("mlb").all()


def test_confirmed_and_projected_are_distinguishable() -> None:
    """The half that makes this better than a scraped order.

    A projected card is a guess the book is publishing; a confirmed one is the
    manager's. A veto on absence has to tell them apart, so it is a column
    rather than a filter applied at normalize time.
    """
    frame = normalize_lineups({"events": [_lineup_event()]}, "mlb")
    home = frame[frame["side"] == "home"]
    away = frame[frame["side"] == "away"]
    assert home["is_confirmed"].all()
    assert not away["is_confirmed"].any()
    assert set(frame["lineup_type"]) == {"confirmed", "projected"}


def test_the_provider_id_is_kept_but_is_not_ours() -> None:
    """BP numbers players itself — four to five digits, not six-digit MLBAM.

    Kept because it is the stable key if a crosswalk ever appears; a consumer
    joins on the folded NAME today.
    """
    frame = normalize_lineups({"events": [_lineup_event()]}, "mlb")
    assert set(frame["bp_player_id"]) == {"45120", "44884", "46763"}


def test_a_sport_without_lineups_yields_no_rows() -> None:
    """Football events carry no `lineups` key at all — that is not an error."""
    assert normalize_lineups({"events": [{"id": "1", "home": "KC"}]}, "nfl").empty


def test_malformed_payloads_are_empty_not_a_crash() -> None:
    for payload in (None, {}, [], {"events": None}, {"events": [None]},
                    {"events": [{"id": "1", "lineups": "nope"}]},
                    {"events": [{"id": "1", "lineups": {"home_lineup": "nope"}}]}):
        assert normalize_lineups(payload, "mlb").empty, payload


def test_an_entry_without_a_name_is_dropped() -> None:
    """A nameless slot cannot be joined to anything, so it is not a row."""
    event = _lineup_event()
    event["lineups"]["home_lineup"].append(
        {"slot": 3, "position": "1B", "participant": {"id": "99"}})
    frame = normalize_lineups({"events": [event]}, "mlb")
    assert len(frame) == 3
    assert frame["player_name"].notna().all()


def test_the_payload_or_its_event_list_both_work() -> None:
    event = _lineup_event()
    assert len(normalize_lineups({"events": [event]}, "mlb")) == 3
    assert len(normalize_lineups([event], "mlb")) == 3


def test_lineup_names_resolve_to_the_banked_batters() -> None:
    """The join that makes this usable — and it needs the accent fold.

    BettingPros serves unaccented names and statsapi banks accented ones; on a
    real slate that gap was 90.7% against 100% folded.
    """
    from velocity.util.names import fold_name

    event = _lineup_event()
    event["lineups"]["home_lineup"][0]["participant"]["name"] = "Jose Ramirez"
    frame = normalize_lineups({"events": [event]}, "mlb")
    banked = {fold_name("José Ramírez"): "660670"}
    keys = [fold_name(n) for n in frame["player_name"]]
    assert banked.get(keys[0]) == "660670"
