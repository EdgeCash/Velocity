"""ESPN injuries ingest — the keyless report, flattened and team-resolved, offline.

Exercises only the pure ``normalize_injuries`` / ``resolve_injury_teams`` /
``is_out_status`` mappings against a frozen slice of the real payload (two NFL
teams for the football vocabulary, one MLB team for the injured-list one). The
network :class:`ESPNClient` is not touched here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from velocity.ingest.espn import (
    ESPN_LEAGUE_PATHS,
    INJURY_COLUMNS,
    is_out_status,
    normalize_injuries,
    resolve_injury_teams,
)

FIXTURE = Path(__file__).parent / "fixtures" / "espn_injuries.json"
PAYLOAD = json.loads(FIXTURE.read_text())


def _frame() -> pd.DataFrame:
    return normalize_injuries(PAYLOAD, "nfl")


def test_normalize_flattens_every_team_and_entry() -> None:
    frame = _frame()
    assert list(frame.columns) == INJURY_COLUMNS
    # 4 + 4 Cardinals/Falcons rows + 3 Diamondbacks rows in the frozen slice.
    assert len(frame) == 11
    assert {"Arizona Cardinals", "Atlanta Falcons", "Arizona Diamondbacks"} == set(
        frame["team_name"]
    )


def test_normalize_carries_the_clinical_detail_a_weekly_report_lacks() -> None:
    """Type, body location and expected return are the point of this feed."""
    frame = _frame()
    injured = frame[frame["is_out"]]
    assert not injured.empty
    assert injured["injury_type"].notna().any()
    assert injured["return_date"].notna().any()
    # Identity for the intel layer's lookup.
    assert injured["player_name"].notna().all()
    assert injured["position"].notna().all()


def test_out_statuses_cover_football_and_the_baseball_injured_list() -> None:
    assert is_out_status("Out")
    assert is_out_status("Injured Reserve")
    assert is_out_status("Doubtful")
    assert is_out_status("Suspension")
    # Baseball's list, whose length keeps changing — matched on shape.
    assert is_out_status("10-Day-IL")
    assert is_out_status("15-Day-IL")
    assert is_out_status("60-Day-IL")
    assert is_out_status("7-Day IL")


def test_probable_players_are_not_treated_as_out() -> None:
    """The intel contract is that availability VETOES; a maybe must not veto."""
    assert not is_out_status("Active")
    assert not is_out_status("Questionable")
    assert not is_out_status("Day-To-Day")


def test_an_unknown_status_reads_as_available_not_out() -> None:
    """A missing out costs a signal; an invented one vetoes a bet on a starter."""
    assert not is_out_status("Fully Cleared Probable Maybe")
    assert not is_out_status("")
    assert not is_out_status(None)


def test_normalize_drops_an_entry_naming_nobody() -> None:
    payload = {"injuries": [{"displayName": "Ghost FC", "injuries": [
        {"status": "Out", "athlete": {}},
        {"status": "Out"},
        {"status": "Out", "athlete": {"displayName": "Real Person"}},
    ]}]}
    frame = normalize_injuries(payload, "nfl")
    assert list(frame["player_name"]) == ["Real Person"]


def test_normalize_is_empty_and_typed_for_an_empty_payload() -> None:
    for payload in ({}, {"injuries": []}, None):
        frame = normalize_injuries(payload, "nfl")
        assert frame.empty
        assert list(frame.columns) == INJURY_COLUMNS


def test_resolve_maps_abbreviations_onto_the_models_rating_keys() -> None:
    frame = normalize_injuries(PAYLOAD, "nfl")
    resolved, unresolved = resolve_injury_teams(frame, ["ARI", "ATL"])
    assert not unresolved
    assert set(resolved["team"]) == {"ARI", "ATL"}
    # The Diamondbacks rows carry abbreviation ARI too, which is exactly why a
    # report is resolved against ONE league's universe at a time.
    assert len(resolved) == len(frame)


def test_resolve_maps_full_names_where_the_model_keys_that_way() -> None:
    """MLB and WNBA models key by full team name, not abbreviation."""
    frame = normalize_injuries(PAYLOAD, "mlb")
    frame = frame[frame["team_name"] == "Arizona Diamondbacks"]
    resolved, unresolved = resolve_injury_teams(frame, ["Arizona Diamondbacks"], "mlb")
    assert not unresolved
    assert set(resolved["team"]) == {"Arizona Diamondbacks"}


def test_resolve_refuses_a_prefix_collision_rather_than_guessing() -> None:
    """Two ESPN teams claiming one model team is a prefix match misfiring.

    ``exchange_aliases`` falls back to prefix matching — the thing that lets
    "San Jose St." find "San Jose State". It also makes both "Arizona
    Cardinals" and "Arizona Coyotes" match "Arizona Diamondbacks", and nothing
    downstream could tell which one the injury list belonged to. Neither is
    kept.
    """
    payload = {"injuries": [
        {"displayName": name, "injuries": [
            {"status": "Out", "athlete": {"displayName": f"Player {i}",
                                          "team": {"displayName": name}}}]}
        for i, name in enumerate(["Arizona Cardinals", "Arizona Coyotes"])
    ]}
    resolved, unresolved = resolve_injury_teams(
        normalize_injuries(payload, "mlb"), ["Arizona Diamondbacks"]
    )
    assert resolved.empty
    assert unresolved == ["Arizona Cardinals", "Arizona Coyotes"]


def test_resolve_scopes_to_one_league_so_shared_abbreviations_cannot_cross() -> None:
    """ESPN calls the Cardinals ARI and the Diamondbacks ARI too.

    Resolved in one pass, a multi-league bank hands one of them the other's
    injury list — and there is no collision to detect, because there is only
    one "ARI" to go around. Scoping by league is what prevents it.
    """
    frame = normalize_injuries(PAYLOAD, "nfl")
    # The frozen slice deliberately mixes NFL and MLB teams, as a real
    # multi-league bank does.
    frame.loc[frame["team_name"] == "Arizona Diamondbacks", "league"] = "mlb"

    nfl, _ = resolve_injury_teams(frame, ["ARI", "ATL"], league="nfl")
    assert set(nfl["team_name"]) == {"Arizona Cardinals", "Atlanta Falcons"}

    mlb, _ = resolve_injury_teams(frame, ["Arizona Diamondbacks"], league="mlb")
    assert set(mlb["team_name"]) == {"Arizona Diamondbacks"}


def test_same_city_rivals_still_resolve_exactly() -> None:
    """The collision guard must not cost us teams that share a city name."""
    payload = {"injuries": [
        {"displayName": name, "injuries": [
            {"status": "Out", "athlete": {"displayName": f"Player {i}",
                                          "team": {"abbreviation": abbr,
                                                   "displayName": name}}}]}
        for i, (abbr, name) in enumerate(
            [("CHC", "Chicago Cubs"), ("CHW", "Chicago White Sox"),
             ("NYY", "New York Yankees"), ("NYM", "New York Mets")]
        )
    ]}
    known = ["Chicago Cubs", "Chicago White Sox", "New York Yankees", "New York Mets"]
    resolved, unresolved = resolve_injury_teams(normalize_injuries(payload, "mlb"), known)
    assert not unresolved
    assert set(resolved["team"]) == set(known)


def test_resolve_drops_and_reports_a_team_the_model_does_not_know() -> None:
    payload = {"injuries": [{"displayName": "Nowhere United", "injuries": [
        {"status": "Out", "athlete": {"displayName": "A Player",
                                      "team": {"abbreviation": "ZZZ",
                                               "displayName": "Nowhere United"}}},
    ]}]}
    resolved, unresolved = resolve_injury_teams(normalize_injuries(payload, "nfl"), ["ARI"])
    assert resolved.empty
    assert unresolved == ["Nowhere United"]


def test_resolve_handles_an_empty_report_without_raising() -> None:
    resolved, unresolved = resolve_injury_teams(normalize_injuries({}, "nfl"), ["ARI"])
    assert resolved.empty
    assert unresolved == []
    assert "team" in resolved.columns


def test_every_league_we_price_has_an_espn_path() -> None:
    """The six leagues the runner accepts all resolve to an ESPN endpoint."""
    assert {"nfl", "ncaaf", "mlb", "wnba", "ncaab", "nhl"} == set(ESPN_LEAGUE_PATHS)


def test_the_frame_satisfies_the_intel_layers_injury_contract() -> None:
    """ContextLibrary.build reads is_out, then team/player_name/position/status."""
    from velocity.intel.context import ContextLibrary

    frame, _ = resolve_injury_teams(normalize_injuries(PAYLOAD, "nfl"), ["ARI", "ATL"])
    games = pd.DataFrame({
        "game_id": ["g1"], "season": [2026], "kickoff": [pd.Timestamp("2026-09-01")],
        "home_team": ["ARI"], "away_team": ["ATL"],
        "home_score": [21.0], "away_score": [17.0],
    })
    lib = ContextLibrary.build(games, None, frame, as_of=pd.Timestamp("2026-09-20"))
    outs = lib.outs_for("ARI")
    assert outs, "the intel layer saw no outs from a report that has them"
    assert all(o.player_name and o.status for o in outs)
