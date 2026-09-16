"""FantasyPros ingest — tolerant projections melt to a long frame, offline.

Exercises only the pure ``normalize_projections`` mapping. Because the live
schema isn't pinned, the tests cover both plausible shapes (stats nested under a
``stats`` key, and stats as flat top-level fields) plus messy values, so the
tolerance is proven regardless of how the real response spells things.
"""

from __future__ import annotations

import pandas as pd
from velocity.ingest.fantasypros import FantasyProsClient, normalize_projections

# Shape A: stats nested under "stats" (one common FantasyPros layout).
NESTED = {
    "season": "2026",
    "week": 0,
    "position": "QB",
    "players": [
        {
            "fpid": 16393,
            "name": "Patrick Mahomes",
            "team": "KC",
            "position": "QB",
            "stats": {
                "pass_yds": "4,500.5",
                "pass_tds": 38,
                "pass_int": 9,
                "rush_yds": 275,
                "points": 385.4,
            },
        },
        {
            "fpid": 17234,
            "name": "Josh Allen",
            "team": "BUF",
            "position": "QB",
            "stats": {"pass_yds": 4100, "pass_tds": 32, "rush_yds": 560, "points": 402.1},
        },
    ],
}

# Shape B: stats as flat top-level keys, alias identity fields, a non-numeric junk field.
FLAT = {
    "data": [
        {
            "player_id": 900,
            "player_name": "Christian McCaffrey",
            "team_id": "SF",
            "position_id": "RB",
            "rush_att": 280,
            "rush_yds": 1350,
            "rec": 68,
            "rec_yds": 540,
            "player_page_url": "/nfl/projections/christian-mccaffrey.php",  # skipped
        }
    ]
}


def test_nested_stats_melt_to_long_rows() -> None:
    df = normalize_projections(NESTED, season=2026, week=0)
    assert set(df.columns) == {
        "season", "week", "player_id", "player_name", "team",
        "position", "stat", "value", "source",
    }
    mahomes = df[df["player_name"] == "Patrick Mahomes"]
    assert set(mahomes["stat"]) == {"pass_yds", "pass_tds", "pass_int", "rush_yds", "points"}
    # "4,500.5" parses through the comma.
    assert float(mahomes[mahomes["stat"] == "pass_yds"]["value"].iloc[0]) == 4500.5
    assert (df["source"] == "fantasypros").all()
    assert (df["season"] == 2026).all()


def test_flat_stats_and_alias_identity_fields() -> None:
    df = normalize_projections(FLAT, season=2026, week=3)
    assert len(df) == 4  # rush_att, rush_yds, rec, rec_yds — url is non-numeric, skipped
    row = df.iloc[0]
    assert row["player_id"] == "900"
    assert row["player_name"] == "Christian McCaffrey"
    assert row["team"] == "SF"
    assert row["position"] == "RB"
    assert (df["week"] == 3).all()
    assert "player_page_url" not in set(df["stat"])


def test_bare_list_payload_supported() -> None:
    df = normalize_projections(NESTED["players"], season=2026, week=0)
    assert df["player_name"].nunique() == 2


def test_non_numeric_and_bool_values_skipped() -> None:
    player = {"name": "X", "team": "KC", "pass_yds": 100, "starter": True, "note": "n/a"}
    df = normalize_projections({"players": [player]}, season=2026, week=1)
    assert set(df["stat"]) == {"pass_yds"}  # bool and non-numeric string dropped


def test_empty_and_missing_players() -> None:
    assert normalize_projections({"players": []}, season=2026, week=0).empty
    assert normalize_projections({}, season=2026, week=0).empty
    assert normalize_projections([], season=2026, week=0).empty


def test_values_are_floats() -> None:
    df = normalize_projections(NESTED, season=2026, week=0)
    assert df["value"].dtype.kind == "f"


def test_missing_identity_fields_become_none() -> None:
    payload = {"players": [{"pass_yds": 4000}]}  # no name/team/pos/id
    df = normalize_projections(payload, season=2026, week=0)
    assert df["player_name"].isna().all()
    assert df["player_id"].isna().all()
    assert df["stat"].iloc[0] == "pass_yds"


def test_from_env_requires_key(monkeypatch) -> None:
    monkeypatch.delenv("FP_API_KEY", raising=False)
    try:
        FantasyProsClient.from_env()
    except RuntimeError as exc:
        assert "FP_API_KEY" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected RuntimeError when FP_API_KEY is unset")


def test_from_env_builds_client(monkeypatch) -> None:
    monkeypatch.setenv("FP_API_KEY", "fp-secret")
    assert FantasyProsClient.from_env().api_key == "fp-secret"


def test_source_override() -> None:
    df = normalize_projections(NESTED, season=2026, week=0, source="fp-consensus")
    assert (df["source"] == "fp-consensus").all()
    assert isinstance(df, pd.DataFrame)


class _StubClient:
    """raw_projections stub: position=ALL comes back tier-limited and empty."""

    def raw_projections(self, sport, season, position="ALL", week=0):
        if position == "ALL":
            return {"public_api_limited": True, "players": []}
        if position in ("QB", "RB"):
            return {"players": [{
                "fpid": f"{position}-1", "name": f"{position} One",
                "team_id": "BUF", "position_id": position,
                "stats": {"pass_yds" if position == "QB" else "rush_yds": 100.0},
            }]}
        return {"players": []}


def test_fetch_league_frame_falls_back_per_position() -> None:
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "collect_fantasypros",
        Path(__file__).resolve().parents[1] / "scripts" / "collect_fantasypros.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    frame, notes = mod.fetch_league_frame(_StubClient(), "nfl", 2026, 0)
    assert any("public_api_limited" in n for n in notes)  # limitation surfaced
    assert any("per-position" in n for n in notes)  # fallback taken
    assert set(frame["player_name"]) == {"QB One", "RB One"}
    assert len(frame) == 2


def test_current_week_tracks_the_schedule() -> None:
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "collect_fp_week",
        Path(__file__).resolve().parents[1] / "scripts" / "collect_fantasypros.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    games = pd.DataFrame({
        "season": [2026] * 4 + [2025],
        "week": [1, 1, 2, 3, 18],
        "kickoff": pd.to_datetime([
            "2026-09-10 20:20", "2026-09-13 17:00",  # week 1
            "2026-09-20 17:00", "2026-09-27 17:00",  # weeks 2-3
            "2026-01-04 18:00",  # old season, ignored
        ]),
    })
    ts = pd.Timestamp
    assert mod.current_week(games, ts("2026-08-11")) == 0  # preseason: too early
    assert mod.current_week(games, ts("2026-09-08")) == 1  # opener lead-in
    assert mod.current_week(games, ts("2026-09-12")) == 1  # mid-week 1
    assert mod.current_week(games, ts("2026-09-15")) == 2  # week 1 finished
    assert mod.current_week(games, ts("2026-10-15")) == 0  # season over
    assert mod.current_week(games.iloc[0:0], ts("2026-09-12")) == 0  # no schedule


def test_normalize_injuries_flags_out_statuses() -> None:
    from velocity.ingest.fantasypros import normalize_injuries

    payload = {"injuries": [
        {"fpid": 1, "name": "QB One", "team": "BUF", "position": "QB",
         "status": "Out", "injury": "ankle"},
        {"fpid": 2, "name": "WR Two", "team": "KC", "position": "WR",
         "status": "Questionable"},
        {"fpid": 3, "name": "RB Three", "team": "KC", "position": "RB",
         "injury_status": "IR"},
        {"fpid": 4, "name": "No Status"},  # says nothing → dropped
        "junk",
    ]}
    df = normalize_injuries(payload)
    assert len(df) == 3
    by_name = df.set_index("player_name")
    assert bool(by_name.loc["QB One", "is_out"])
    assert not bool(by_name.loc["WR Two", "is_out"])  # questionables mostly play
    assert bool(by_name.loc["RB Three", "is_out"])  # alias status key + IR
    assert normalize_injuries(None).empty
    assert normalize_injuries({}).empty


# --------------------------------------------------------------------------
# Stat-key census — making an unread projection visible (2026-09-16)
# --------------------------------------------------------------------------
#
# The sibling of the BettingPros slug-coverage report, and it exists for the
# same reason: a stat key nothing maps contributes nothing, silently and
# correctly, so a feed we read five stats out of looks exactly like a feed that
# serves five. Two BP prop slugs are blocked on a question this answers.


def _census_payload() -> dict:
    return {"players": [
        {"fpid": "1", "name": "Josh Allen", "team": "BUF", "position": "QB",
         "pass_yds": 265.0, "pass_tds": 1.8, "pass_att": 33.4,
         "pass_cmp": "21/33", "rush_yds": 38.0, "rush_att": 6.2, "rec": 0.0,
         # Real keys the live feed serves that nothing reads — the census's
         # whole purpose is making these visible rather than invisible.
         "fumbles": 0.11, "points_ppr": 23.16},
        {"fpid": "2", "name": "James Cook", "team": "BUF", "position": "RB",
         "rush_yds": 64.0, "rush_att": 14.1, "rec": 2.6, "rec_yds": 19.0,
         "pass_att": 0.0, "pass_cmp": "0/0", "fumbles": 0.04,
         "points_ppr": 12.4},
    ]}


def _census_frame() -> pd.DataFrame:
    from velocity.ingest.fantasypros import normalize_projections

    return normalize_projections(_census_payload(), season=2026, week=3)


def test_the_census_separates_what_we_read_from_what_we_do_not() -> None:
    from velocity.ingest.fantasypros import stat_key_census

    census = stat_key_census(_census_frame()).set_index("stat")
    assert census.loc["pass_yds", "mapped"]
    assert census.loc["pass_yds", "market"] == "pass_yards"
    # Attempts were the open question this census was built to answer; it did
    # (run 35108513721), and they are priced now.
    assert census.loc["rush_att", "market"] == "rush_attempts"
    assert census.loc["pass_att", "market"] == "pass_attempts"
    # The whole point: served, numeric, and nothing reads it.
    assert not census.loc["fumbles", "mapped"]
    assert census.loc["fumbles", "market"] == ""


def test_non_zero_is_counted_apart_from_rows() -> None:
    """A key served as a structural zero is a placeholder, not a projection.

    Mapping a market onto one would abstain just as surely as leaving it
    unmapped, only less honestly — so the report has to distinguish them.
    """
    from velocity.ingest.fantasypros import stat_key_census

    census = stat_key_census(_census_frame()).set_index("stat")
    assert census.loc["pass_att", "rows"] == 2
    assert census.loc["pass_att", "non_zero"] == 1  # Cook's 0.0 does not count


def test_an_all_zero_key_is_reported_as_a_placeholder() -> None:
    """The live feed really does this — eight milestone keys are all zero.

    ``pass_yds_300``, ``rush_yds_100``, ``scrimage_yards_100`` and friends are
    structural zeros in a weekly projection. Mapping a market onto one would
    abstain just as surely as leaving it unmapped, only less honestly, so the
    report has to tell a placeholder apart from a projection.
    """
    from velocity.ingest.fantasypros import describe_stat_keys, normalize_projections

    # NOT pass_cmp: that was this test's placeholder until completions became
    # a market, which is the third time a census pin has been invalidated by
    # the very change it was describing. ``targets`` is volume-like, plausible
    # for this feed to serve, and nothing prices it.
    payload = {"players": [
        {"fpid": "1", "name": "A QB", "team": "BUF", "position": "QB",
         "pass_yds": 250.0, "targets": 0.0},
    ]}
    lines = "\n".join(
        describe_stat_keys(normalize_projections(payload, season=2026, week=1), "nfl")
    )
    assert "SERVED BUT ALL ZERO" in lines
    # The per-key verdict, not the closing guidance — which mentions the word
    # in a different sense ("Anything else AVAILABLE here needs a model").
    assert "AVAILABLE — nothing reads this yet" not in lines


def test_the_census_guidance_reflects_what_is_actually_still_open() -> None:
    """This text prints four times a week; stale guidance misleads at that rate.

    Attempts WERE the open question and are priced now, so the report must not
    keep advertising them as an unblocked opportunity. Completions is the live
    one, and its blocker is on our side — the feed serves it.
    """
    from velocity.ingest.fantasypros import describe_stat_keys

    lines = "\n".join(describe_stat_keys(_census_frame(), "nfl"))
    assert "all priced" in lines
    assert "whole NFL BettingPros board is mapped" in lines
    assert "unblocks a market" not in lines
    assert "no completions column" not in lines


def test_a_feed_with_no_volume_key_says_the_slugs_stay_unmapped() -> None:
    from velocity.ingest.fantasypros import describe_stat_keys, normalize_projections

    payload = {"players": [
        {"fpid": "1", "name": "A QB", "team": "BUF", "position": "QB",
         "pass_yds": 250.0, "pass_tds": 1.5},
    ]}
    lines = "\n".join(
        describe_stat_keys(normalize_projections(payload, season=2026, week=1), "nfl")
    )
    assert "serves no attempt/completion projection" in lines


def test_a_projection_served_as_a_compound_string_is_still_reported() -> None:
    """``normalize_projections`` drops non-numeric values silently.

    A completions projection arriving as "21/33" would vanish from the long
    frame, and the census — which reads that frame — would answer "not served"
    to a feed that serves it. This is the check that catches it, run where the
    raw payload is still in scope.
    """
    from velocity.ingest.fantasypros import unmelted_stat_keys

    assert unmelted_stat_keys(_census_payload()) == ["pass_cmp"]
    # And it really is absent from the melted frame — that is the hazard.
    assert "pass_cmp" not in set(_census_frame()["stat"])


def test_unmelted_keys_ignores_numeric_and_non_volume_fields() -> None:
    from velocity.ingest.fantasypros import unmelted_stat_keys

    payload = {"players": [
        {"fpid": "1", "name": "A QB", "team": "BUF", "position": "QB",
         "pass_yds": 250.0, "player_page_url": "http://x", "notes": "questionable"},
    ]}
    assert unmelted_stat_keys(payload) == []


def test_the_census_of_nothing_is_empty_not_a_crash() -> None:
    from velocity.ingest.fantasypros import describe_stat_keys, stat_key_census

    empty = pd.DataFrame(columns=["stat", "value"])
    assert stat_key_census(empty).empty
    assert "no projection rows" in describe_stat_keys(empty, "nfl")[0]


def test_every_mapped_stat_key_the_census_reports_is_one_the_model_reads() -> None:
    """The census must not invent a mapping the sim would not honour."""
    from velocity.ingest.fantasypros import stat_key_census
    from velocity.models.props_football import FP_STAT_TO_MARKET

    census = stat_key_census(_census_frame())
    for row in census.to_dict("records"):
        if row["mapped"]:
            assert FP_STAT_TO_MARKET[row["stat"]] == row["market"]


class _TierLimitedClient:
    """``position=ALL`` answers tier-limited and empty; per-position serves."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def raw_projections(self, league, season, position="ALL", week=0):  # type: ignore[no-untyped-def]
        self.calls.append(position)
        if position == "ALL":
            return {"players": [], "limitation": "public_api_limited"}
        if position == "QB":
            return {"players": [{"fpid": "1", "name": "A QB", "team": "BUF",
                                 "position": "QB", "pass_yds": 250.0,
                                 "pass_att": 33.0, "pass_cmp": "21/33"}]}
        if position == "RB":
            return {"players": [{"fpid": "2", "name": "A RB", "team": "BUF",
                                 "position": "RB", "rush_yds": 60.0,
                                 "rush_att": 13.0, "pass_cmp": "0/0"}]}
        return {"players": []}


def test_the_dropped_key_check_survives_the_tier_limit_fallback() -> None:
    """The case that matters: the ALL response is empty, so it sees nothing.

    A first pass ran the check only against the ``position=ALL`` payload — the
    one the limited public tier answers with zero players. It would have
    reported "no dropped keys" on exactly the feed shape this collector exists
    to handle. The re-check runs against the first per-position payload.
    """
    from scripts.collect_fantasypros import fetch_league_frame

    frame, notes = fetch_league_frame(_TierLimitedClient(), "nfl", 2026, 3)
    assert not frame.empty
    dropped = [n for n in notes if "melt drops" in n]
    assert len(dropped) == 1, f"expected exactly one note, got {notes}"
    assert "pass_cmp" in dropped[0]


def test_the_fallback_still_recovers_the_volume_keys() -> None:
    """The census has to answer off the fallback board, not just off ALL."""
    from scripts.collect_fantasypros import fetch_league_frame
    from velocity.ingest.fantasypros import stat_key_census

    frame, _ = fetch_league_frame(_TierLimitedClient(), "nfl", 2026, 3)
    served = set(stat_key_census(frame)["stat"])
    assert {"pass_att", "rush_att"} <= served


def test_the_football_guidance_does_not_print_under_another_sport() -> None:
    """The BP slugs this unblocks are football markets.

    Printing "rushing-attempts stays unmapped" under the MLB census would read
    as a finding about a feed that says nothing about it.
    """
    from velocity.ingest.fantasypros import describe_stat_keys, normalize_projections

    mlb = normalize_projections(
        {"players": [{"fpid": "1", "name": "A SP", "team": "NYY",
                      "position": "SP", "k": 180.0}]},
        season=2026, week=0,
    )
    lines = "\n".join(describe_stat_keys(mlb, "mlb"))
    assert "BettingPros" not in lines
    assert "priced" not in lines
    # The same shape under NFL does carry the football guidance. A volume-like
    # key has to be present for that block to have anything to say.
    nfl = normalize_projections(
        {"players": [{"fpid": "1", "name": "A QB", "team": "BUF",
                      "position": "QB", "pass_yds": 250.0, "pass_cmp": 21.0}]},
        season=2026, week=1,
    )
    nfl_lines = "\n".join(describe_stat_keys(nfl, "nfl"))
    assert "BettingPros board is mapped" in nfl_lines


# --------------------------------------------------------------------------
# The collector must not spend requests on rows nothing reads (2026-09-16)
# --------------------------------------------------------------------------


def test_the_collector_only_fetches_leagues_something_reads() -> None:
    """MLB cost ten requests a run for months to bank zero rows.

    It was documented the whole time — UNCONSUMED_LEAGUES carried a correct
    note about it — which is the point: saying a thing in a log is not the
    same as not doing it. MLB DFS prices from collect_mlb_player_stats.py and
    MLB props from the banked starters frame, so nothing here was ever read.
    """
    from scripts.collect_fantasypros import FALLBACK_POSITIONS, LEAGUES

    assert LEAGUES == ("nfl",)
    assert "mlb" not in FALLBACK_POSITIONS, (
        "a per-position fallback for a league we do not fetch is the expensive "
        "half of the bug — one request, then one per position"
    )


def test_nothing_is_fetched_while_marked_unconsumed() -> None:
    """The invariant the old note failed to enforce.

    If a league is known to have no consumer, it must not be in the fetch set.
    Re-adding one is a decision to spend requests on it.
    """
    from scripts.collect_fantasypros import LEAGUES, UNCONSUMED_LEAGUES

    still_fetched = [lg for lg in UNCONSUMED_LEAGUES if lg in LEAGUES]
    assert not still_fetched, (
        f"{still_fetched} are marked unconsumed and still fetched — either "
        "something reads them (drop the note) or nothing does (drop the fetch)"
    )


# --------------------------------------------------------------------------
# The census must know about every consumer, not just the props model
# --------------------------------------------------------------------------


def _multi_consumer_frame():
    from velocity.ingest.fantasypros import normalize_projections

    return normalize_projections({"players": [
        {"fpid": "1", "name": "A QB", "team": "BUF", "position": "QB",
         "pass_yds": 265.0, "rush_tds": 0.3, "fumbles": 0.1, "points_ppr": 23.2},
        {"fpid": "2", "name": "BUF D/ST", "team": "BUF", "position": "DST",
         "def_sack": 2.5, "def_int": 0.8, "def_ff": 0.7, "def_tyda": 330.0},
        {"fpid": "3", "name": "A K", "team": "BUF", "position": "K",
         "fg": 1.3, "fga": 1.6, "xpt": 2.1},
    ]}, season=2026, week=2)


def test_the_dfs_layer_counts_as_a_consumer() -> None:
    """The census first shipped reporting the props model's view as the feed's.

    velocity/dfs/dst.py has been reading the team-defense block since the DST
    projection landed — DK classic needs a defense, and the pool used to join
    it at 0.0 points. Calling those keys UNREAD invites someone to wire up a
    second consumer for data already in use.
    """
    from velocity.ingest.fantasypros import stat_key_census

    census = stat_key_census(_multi_consumer_frame()).set_index("stat")
    assert census.loc["def_sack", "read"]
    assert census.loc["def_sack", "consumer"] == "DFS: DST"
    assert not census.loc["def_sack", "mapped"], "it is not a prop market"
    assert census.loc["fumbles", "consumer"] == "DFS: DK scoring"
    assert census.loc["rush_tds", "consumer"] == "props: anytime_td"


def test_deliberately_unread_keys_read_as_declined_not_unread() -> None:
    """"Declined" and "nobody looked" are different findings.

    DK scores neither forced fumbles nor yards allowed, and we compute DK
    points from the components rather than trusting a scoring variant. Leaving
    those as UNREAD makes a reader re-derive the same conclusion every time.
    """
    from velocity.ingest.fantasypros import describe_stat_keys

    lines = "\n".join(describe_stat_keys(_multi_consumer_frame(), "nfl"))
    assert "declined def_ff" in lines
    assert "DK does not score forced fumbles" in lines
    assert "declined def_tyda" in lines
    assert "declined points_ppr" in lines


def test_the_kicker_keys_are_the_only_thing_genuinely_unexamined() -> None:
    """The gap this report exists to surface.

    fg / fga / xpt are projections nothing reads and nothing has declined —
    while DK's Showdown board has a kicker slot and dst.py's own note calls a
    kicker "routinely a live captain". If a consumer appears, this test should
    fail and be updated; that is the point of it.
    """
    from velocity.ingest.fantasypros import stat_key_census

    census = stat_key_census(_multi_consumer_frame())
    unread = set(census[~census["read"]]["stat"]) - {"def_ff", "def_tyda", "points_ppr"}
    assert unread == {"fg", "fga", "xpt"}, f"the unexamined set moved: {unread}"


def test_the_guidance_does_not_still_advertise_priced_markets() -> None:
    """Third time this text has gone stale one commit after its own change."""
    from velocity.ingest.fantasypros import describe_stat_keys, normalize_projections

    frame = normalize_projections({"players": [
        {"fpid": "1", "name": "A QB", "team": "BUF", "position": "QB",
         "pass_yds": 265.0, "pass_att": 33.0, "pass_cmp": 21.0},
    ]}, season=2026, week=2)
    lines = "\n".join(describe_stat_keys(frame, "nfl"))
    assert "all priced" in lines
    assert "no completions column" not in lines
    assert "unblocks a market" not in lines
