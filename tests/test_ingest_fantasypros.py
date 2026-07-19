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
