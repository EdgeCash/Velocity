"""DK salary ingest — pure normalizers + the collector's offline path.

The draftables normalizer is pinned against a frozen payload in the real DK
shape: roster-slot duplicates dedupe to one row per player, a row without a
salary is dropped (never guessed), and the output validates the ``Salaries``
schema. The CLI's ``--from-file`` path is the same code the Action runs minus
the network fetch.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
from velocity.dfs.salaries import (
    Salaries,
    assign_draft_groups,
    normalize_draft_groups,
    normalize_draftables,
)

REPO = Path(__file__).parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "dk_draftables.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text())


def test_normalize_draftables_validates_and_dedupes() -> None:
    out = normalize_draftables(_payload(), "12345")
    Salaries.validate(out)
    # Kelce appears twice (TE + FLEX roster slots) → one row; the null-salary
    # player is dropped → 2 rows total.
    assert len(out) == 2
    assert set(out["player_name"]) == {"Josh Allen", "Travis Kelce"}
    allen = out[out["player_name"] == "Josh Allen"].iloc[0]
    assert allen["salary"] == 8200
    assert allen["position"] == "QB"
    assert allen["team"] == "BUF"
    assert allen["competition"] == "BUF @ KC"
    assert allen["draft_group_id"] == "12345"
    # DK's offset timestamp lands as a naive UTC kickoff.
    assert pd.Timestamp(allen["kickoff"]) == pd.Timestamp("2026-09-11 00:20:00")


def test_normalize_draftables_empty_payload() -> None:
    out = normalize_draftables({}, "0")
    assert out.empty
    assert "salary" in out.columns


def test_normalize_draft_groups() -> None:
    lobby = {
        "DraftGroups": [
            {"DraftGroupId": 111, "ContestTypeId": 21, "StartDate": "2026-09-13T17:00:00Z",
             "GameCount": 13, "DraftGroupTag": "Featured"},
            {"DraftGroupId": None},  # malformed entry is skipped
        ]
    }
    groups = normalize_draft_groups(lobby)
    assert groups["draft_group_id"].tolist() == ["111"]
    assert groups.loc[0, "game_count"] == 13


def test_collector_from_file_banks_normalized_parquet(tmp_path: Path) -> None:
    out = tmp_path / "salaries"
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "collect_dk_salaries.py"),
         "--from-file", str(FIXTURE), "--draft-group", "12345",
         "--league", "nfl", "--out", str(out)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    files = list(out.glob("dk_salaries_nfl_*.parquet"))
    assert files
    salaries = pd.read_parquet(files[0])
    assert len(salaries) == 2
    assert (salaries["league"] == "nfl").all()
    assert "collected_at" in salaries.columns


def test_normalize_draft_groups_cleans_the_suffix() -> None:
    lobby = {"DraftGroups": [
        {"DraftGroupId": 1, "ContestTypeId": 28, "GameCount": 7,
         "StartDate": "2026-08-24T23:40:00.0000000Z",
         "ContestStartTimeSuffix": None},
        {"DraftGroupId": 2, "ContestTypeId": 28, "GameCount": 3,
         "StartDate": "2026-08-24T22:40:00.0000000Z",
         "ContestStartTimeSuffix": " (Turbo)"},
    ]}
    groups = normalize_draft_groups(lobby)
    assert list(groups["suffix"]) == ["", "Turbo"]


def test_collector_banks_slate_metadata_with_rows(tmp_path: Path) -> None:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "collect_dk_salaries",
        Path(__file__).resolve().parents[1] / "scripts" / "collect_dk_salaries.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["collect_dk_salaries"] = mod
    spec.loader.exec_module(mod)

    groups = normalize_draft_groups({"DraftGroups": [
        {"DraftGroupId": 7, "ContestTypeId": 28, "GameCount": 2,
         "StartDate": "2026-08-24T22:40:00.0000000Z",
         "ContestStartTimeSuffix": " (Night)"},
    ]})
    frame = normalize_draftables({"draftables": [
        {"displayName": "A Player", "salary": 5000, "playerDkId": 1,
         "position": "OF", "teamAbbreviation": "NYY",
         "competition": {"name": "NYY @ BOS",
                         "startTime": "2026-08-25T01:40:00Z"}},
    ]}, "7")
    mod._write_league("mlb", [frame], tmp_path, "stamp",
                      pd.Timestamp("2026-08-24"), groups=groups)
    banked = pd.read_parquet(tmp_path / "dk_salaries_mlb_stamp.parquet")
    row = banked.iloc[0]
    assert row["contest_type_id"] == 28
    assert row["suffix"] == "Night"
    assert row["slate_start"] == pd.Timestamp("2026-08-24 22:40")


def test_normalize_draftables_extracts_the_probable_flag() -> None:
    payload = {"draftables": [
        {"displayName": "Real Probable", "salary": 8500, "playerDkId": 1,
         "position": "SP", "teamAbbreviation": "BOS",
         "playerGameAttributes": [{"id": 112, "value": "Alcantara (R)"},
                                  {"id": 1, "value": "true"}]},
        {"displayName": "Bench Arm", "salary": 8500, "playerDkId": 2,
         "position": "SP", "teamAbbreviation": "BOS",
         "playerGameAttributes": [{"id": 130, "value": "false"}]},
        {"displayName": "A Hitter", "salary": 5000, "playerDkId": 3,
         "position": "OF", "teamAbbreviation": "BOS"},
    ]}
    frame = normalize_draftables(payload, "7").set_index("player_name")
    assert bool(frame.loc["Real Probable", "probable"])
    assert not bool(frame.loc["Bench Arm", "probable"])
    assert not bool(frame.loc["A Hitter", "probable"])


def test_a_lobby_that_ignores_its_sport_filter_keeps_only_its_own_boards() -> None:
    """The contamination that banked 13,192 NFL and MLB rows as WNBA salaries.

    ``getcontests?sport=WNBA`` came back with 75 draft groups on a night the
    league played twice, against 34 for a full NFL Sunday — it was serving
    everybody's boards. Ownership goes to the lobby with the most specific
    claim, so the sports whose filter works are untouched and the one whose
    lobby is really everybody's keeps only what is unique to it.
    """
    owned = assign_draft_groups(
        {
            "nfl": ["101", "102"],
            "mlb": ["201"],
            # The unfiltered lobby: everyone else's groups plus two of its own.
            "wnba": ["101", "102", "201", "301", "302"],
        }
    )
    assert owned["nfl"] == {"101", "102"}
    assert owned["mlb"] == {"201"}
    assert owned["wnba"] == {"301", "302"}


def test_a_sport_with_no_boards_of_its_own_ends_up_empty() -> None:
    # NBA in September: one group in the lobby, and it belongs to baseball.
    owned = assign_draft_groups({"mlb": ["201"], "nba": ["201"]})
    assert owned["mlb"] == {"201"}
    assert owned["nba"] == set()


def test_ownership_does_not_depend_on_iteration_order() -> None:
    # Two lobbies of the same size claiming one group: the tie breaks on the
    # league name, so two runs of the same day agree.
    first = assign_draft_groups({"mlb": ["9"], "nhl": ["9"]})
    second = assign_draft_groups({"nhl": ["9"], "mlb": ["9"]})
    assert first == second
    assert first["mlb"] == {"9"}


def test_an_uncontested_lobby_is_left_exactly_as_it_came() -> None:
    owned = assign_draft_groups({"nfl": ["1", "2", "3"]})
    assert owned["nfl"] == {"1", "2", "3"}


# ---------------------------------------------------------------------------
# The legacy lineup endpoint as the fallback when the draftables API refuses.
# From 2026-09-16 every api.draftkings.com draftables call from the Actions
# runners came back 403 while the lobby on www kept answering; the archive
# banked zero rows for two days behind green runs.
# ---------------------------------------------------------------------------


def _legacy_payload() -> dict:
    # The keys and shapes are the real endpoint's (banked 2026-09-18): the
    # team list is keyed by the players' ``tsid`` and carries the game's
    # start as a .NET epoch; ``pdkid`` is DK's player id; ``pp`` the
    # probable-pitcher flag; ``rosposid`` the roster slot.
    return {
        "playerList": [
            {"pid": 1001, "pdkid": 693001, "fn": "Josh", "ln": "Allen", "pn": "QB",
             "s": 8200, "tid": 324, "htid": 324, "atid": 334, "htabbr": "BUF",
             "atabbr": "DET", "tsid": 6175619, "rosposid": 66, "ppg": "24.5",
             "pp": 0, "i": "", "IsDisabledFromDrafting": False},
            {"pid": 1002, "pdkid": 693002, "fn": "Jahmyr", "ln": "Gibbs", "pn": "RB",
             "s": 7900, "tid": 334, "htid": 324, "atid": 334, "htabbr": "BUF",
             "atabbr": "DET", "tsid": 6175619, "rosposid": 67, "ppg": "18.1",
             "pp": 1, "i": "Q", "IsDisabledFromDrafting": False},
            # No price: dropped by the normalizer, never guessed.
            {"pid": 1003, "pdkid": 693003, "fn": "Practice", "ln": "Squad", "pn": "WR",
             "s": None, "tid": 334, "htid": 324, "atid": 334, "htabbr": "BUF",
             "atabbr": "DET", "tsid": 6175619, "rosposid": 68, "i": ""},
            # A game the team list does not know: the player's own fields and
            # the slate start carry it.
            {"pid": 1004, "pdkid": 693004, "fn": "Bijan", "ln": "Robinson", "pn": "RB",
             "s": 8100, "tid": 200, "htid": 200, "atid": 201, "htabbr": "ATL",
             "atabbr": "CAR", "tsid": 999, "rosposid": 67, "i": ""},
        ],
        "teamList": {
            "6175619": {"ht": "BUF", "htid": 324, "at": "DET", "atid": 334,
                        "tz": "/Date(1789676100000)/", "status": "Pre-Game"},
        },
    }


def test_legacy_players_convert_to_the_draftables_shape() -> None:
    from velocity.dfs.salaries import legacy_players_to_draftables

    converted = legacy_players_to_draftables(
        _legacy_payload(), "555", start="2026-09-20T17:00:00")
    assert converted["_source"] == "legacy"
    assert converted["_legacy"] == _legacy_payload()  # DK's real shape is kept
    out = normalize_draftables(converted, "555")
    Salaries.validate(out)
    assert list(out["player_name"]) == ["Josh Allen", "Jahmyr Gibbs", "Bijan Robinson"]
    allen = out[out["player_name"] == "Josh Allen"].iloc[0]
    gibbs = out[out["player_name"] == "Jahmyr Gibbs"].iloc[0]
    bijan = out[out["player_name"] == "Bijan Robinson"].iloc[0]
    # The player's team is whichever of the game's two the tid matches.
    assert (allen["team"], gibbs["team"], bijan["team"]) == ("BUF", "DET", "ATL")
    assert (allen["competition"], bijan["competition"]) == ("DET @ BUF", "CAR @ ATL")
    assert allen["salary"] == 8200 and gibbs["position"] == "RB"
    # DK's player id, as the API path banks it — not the legacy pid.
    assert (allen["player_id"], gibbs["player_id"]) == ("693001", "693002")
    # The API spells a healthy player "None"; the legacy field is "".
    assert (allen["status"], gibbs["status"]) == ("None", "Q")
    # The game's own start from the team list — a .NET epoch of the Eastern
    # wall clock (20:15 "UTC" is 8:15 PM ET) — and the slate's start only
    # where the team list is silent.
    assert pd.Timestamp(allen["kickoff"]) == pd.Timestamp("2026-09-18 00:15:00")
    assert pd.Timestamp(bijan["kickoff"]) == pd.Timestamp("2026-09-20 17:00:00")
    # The roster slot and the probable flag ride through.
    assert (allen["roster_slot_id"], gibbs["roster_slot_id"]) == ("66", "67")
    assert (bool(allen["probable"]), bool(gibbs["probable"])) == (False, True)


def test_legacy_salary_free_boards_take_the_tiered_path() -> None:
    from velocity.dfs.salaries import legacy_players_to_draftables
    from velocity.dfs.tiered import normalize_tiered

    # A Tiers board: every ``s`` is 0 and the roster slot is the tier.
    payload = {
        "playerList": [
            {"pid": 1, "pdkid": 11, "fn": "Aaron", "ln": "Judge", "pn": "OF", "s": 0,
             "tid": 1, "htid": 1, "atid": 2, "htabbr": "NYY", "atabbr": "BOS",
             "tsid": 5, "rosposid": 278, "ppg": "12.3", "i": ""},
            {"pid": 2, "pdkid": 12, "fn": "Juan", "ln": "Soto", "pn": "OF", "s": 0,
             "tid": 2, "htid": 1, "atid": 2, "htabbr": "NYY", "atabbr": "BOS",
             "tsid": 5, "rosposid": 279, "ppg": "11.0", "i": ""},
        ],
        "teamList": {"5": {"ht": "NYY", "htid": 1, "at": "BOS", "atid": 2,
                           "tz": "/Date(1789689600000)/"}},
    }
    converted = legacy_players_to_draftables(payload, "777")
    # 00:00 on the Eastern wall clock → 04:00 UTC.
    assert converted["draftables"][0]["competition"]["startTime"] == "2026-09-18T04:00:00+00:00"
    assert normalize_draftables(converted, "777").empty  # a 0 is no salary, not a free player
    tiers = normalize_tiered(converted, "777")
    assert list(tiers["tier"]) == [1, 2]
    assert list(tiers["dk_stat"]) == [12.3, 11.0]  # the lobby's number, attribute 408
    assert list(tiers["team"]) == ["NYY", "BOS"]


def test_client_falls_back_to_the_legacy_endpoint_when_the_api_refuses() -> None:
    import urllib.error

    from velocity.dfs.salaries import (
        DRAFTABLES_URL,
        LEGACY_PLAYERS_URL,
        DraftKingsClient,
    )

    calls: list[str] = []

    class Refusing(DraftKingsClient):
        def _get(self, url: str) -> dict:
            calls.append(url)
            if url == DRAFTABLES_URL.format(group_id="555"):
                raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)  # type: ignore[arg-type]
            assert url == LEGACY_PLAYERS_URL.format(group_id="555")
            return _legacy_payload()

    payload = Refusing().draftables("555", start="2026-09-18T00:15:00")
    assert payload["_source"] == "legacy"
    assert len(payload["draftables"]) == 4
    assert calls == [DRAFTABLES_URL.format(group_id="555"),
                     LEGACY_PLAYERS_URL.format(group_id="555")]


def test_client_keeps_the_api_payload_and_names_its_source() -> None:
    from velocity.dfs.salaries import DraftKingsClient

    class Serving(DraftKingsClient):
        def _get(self, url: str) -> dict:
            return _payload()

    payload = Serving().draftables("12345")
    assert payload["_source"] == "api"
    assert len(normalize_draftables(payload, "12345")) == 2


def test_client_reraises_when_both_hosts_refuse_and_on_a_final_status() -> None:
    import urllib.error

    import pytest
    from velocity.dfs.salaries import DraftKingsClient

    class BothRefuse(DraftKingsClient):
        def _get(self, url: str) -> dict:
            code = 403 if "api.draftkings.com" in url else 500
            raise urllib.error.HTTPError(url, code, "nope", None, None)  # type: ignore[arg-type]

    with pytest.raises(urllib.error.HTTPError) as caught:
        BothRefuse().draftables("555")
    assert caught.value.code == 403  # the API's refusal, the legacy one chained
    assert isinstance(caught.value.__cause__, urllib.error.HTTPError)

    class Missing(DraftKingsClient):
        def _get(self, url: str) -> dict:
            raise urllib.error.HTTPError(url, 404, "gone", None, None)  # type: ignore[arg-type]

    with pytest.raises(urllib.error.HTTPError) as gone:
        Missing().draftables("555")
    assert gone.value.code == 404  # no such group is final, not a refusal


def test_client_identifies_as_a_browser() -> None:
    from velocity.dfs.salaries import _HEADERS

    assert "Chrome/" in _HEADERS["User-Agent"] and "Safari/" in _HEADERS["User-Agent"]
    assert _HEADERS["Accept"].startswith("application/json")
    assert _HEADERS["Referer"].startswith("https://www.draftkings.com")
