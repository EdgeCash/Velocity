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
    return {
        "playerList": [
            {"pid": 1001, "fn": "Josh", "ln": "Allen", "pn": "QB", "s": 8200,
             "tid": 324, "htid": 324, "atid": 325, "htabbr": "BUF", "atabbr": "DET",
             "i": "", "IsDisabledFromDrafting": False},
            {"pid": 1002, "fn": "Jahmyr", "ln": "Gibbs", "pn": "RB", "s": 7900,
             "tid": 325, "htid": 324, "atid": 325, "htabbr": "BUF", "atabbr": "DET",
             "i": "Q", "IsDisabledFromDrafting": False},
            # No price: dropped by the normalizer, never guessed.
            {"pid": 1003, "fn": "Practice", "ln": "Squad", "pn": "WR", "s": None,
             "tid": 325, "htid": 324, "atid": 325, "htabbr": "BUF", "atabbr": "DET",
             "i": ""},
        ],
    }


def test_legacy_players_convert_to_the_draftables_shape() -> None:
    from velocity.dfs.salaries import legacy_players_to_draftables

    converted = legacy_players_to_draftables(
        _legacy_payload(), "555", start="2026-09-18T00:15:00")
    assert converted["_source"] == "legacy"
    assert converted["_legacy"] == _legacy_payload()  # DK's real shape is kept
    out = normalize_draftables(converted, "555")
    Salaries.validate(out)
    assert list(out["player_name"]) == ["Josh Allen", "Jahmyr Gibbs"]
    allen = out[out["player_name"] == "Josh Allen"].iloc[0]
    gibbs = out[out["player_name"] == "Jahmyr Gibbs"].iloc[0]
    # The player's team is whichever of the game's two the tid matches.
    assert (allen["team"], gibbs["team"]) == ("BUF", "DET")
    assert allen["competition"] == "DET @ BUF"
    assert allen["salary"] == 8200 and gibbs["position"] == "RB"
    # The API spells a healthy player "None"; the legacy field is "".
    assert (allen["status"], gibbs["status"]) == ("None", "Q")
    # No per-game start on this endpoint: the slate's start stands in.
    assert pd.Timestamp(allen["kickoff"]) == pd.Timestamp("2026-09-18 00:15:00")
    assert out["roster_slot_id"].isna().all()


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
    assert len(payload["draftables"]) == 3
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
