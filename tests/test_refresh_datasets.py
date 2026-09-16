"""Dataset season-refresh — pure merge + per-source normalizers, offline.

The weekly refresh must be idempotent and schema-stable: re-running replaces
the target season's rows without touching history, and the fresh frames are
aligned onto the committed files' exact columns. Sources are exercised from
frozen/synthetic payloads; the network fetchers stay behind pragmas.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).parent.parent

_spec = importlib.util.spec_from_file_location(
    "refresh_datasets", REPO / "scripts" / "refresh_datasets.py"
)
rd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rd)  # type: ignore[union-attr]


def test_current_season_rolls_over_in_august() -> None:
    assert rd.current_season(datetime(2026, 8, 9, tzinfo=UTC)) == 2026
    assert rd.current_season(datetime(2026, 2, 8, tzinfo=UTC)) == 2025  # Super Bowl week
    assert rd.current_season(datetime(2026, 7, 31, tzinfo=UTC)) == 2025


def test_merge_season_replaces_only_the_target_season() -> None:
    existing = pd.DataFrame({
        "game_id": ["a", "b", "c"],
        "season": [2025, 2025, 2026],
        "home_score": [20, 24, 10],
    })
    fresh = pd.DataFrame({
        "game_id": ["c", "d"],
        "season": [2026, 2026],
        "home_score": [10, 31],
        "extra_col": [1, 2],  # not in the committed file → dropped by alignment
    })
    merged = rd.merge_season(existing, fresh, 2026)
    assert sorted(merged["game_id"]) == ["a", "b", "c", "d"]
    assert list(merged.columns) == ["game_id", "season", "home_score"]
    assert (merged[merged["season"] == 2025]["game_id"].tolist()) == ["a", "b"]
    # Idempotent: running the same merge again changes nothing.
    again = rd.merge_season(merged, fresh, 2026)
    assert again.sort_values("game_id").reset_index(drop=True).equals(
        merged.sort_values("game_id").reset_index(drop=True)
    )


def test_merge_season_aligns_missing_columns_to_null() -> None:
    existing = pd.DataFrame({
        "game_id": ["a"], "season": [2025], "spread_line": [3.5],
    })
    fresh = pd.DataFrame({"game_id": ["b"], "season": [2026]})  # no spread_line
    merged = rd.merge_season(existing, fresh, 2026)
    assert merged.loc[merged["game_id"] == "b", "spread_line"].isna().all()


def test_nfl_games_from_schedules_keeps_played_and_lines() -> None:
    raw = pd.read_csv(REPO / "tests" / "fixtures" / "raw_nfl_schedules.csv")
    games = rd.nfl_games_from_schedules(raw, 2023)
    assert not games.empty
    assert (games["season"] == 2023).all()
    assert games["home_score"].notna().all()
    # The fixture carries no lines columns → aligned to null, not crashed.
    assert games["spread_line"].isna().all()
    # A season with no rows returns an empty frame (nothing to refresh).
    assert rd.nfl_games_from_schedules(raw, 1999).empty


def test_ncaaf_games_from_cfbd_normalizes_and_flips_spread() -> None:
    games_json = [
        {
            "id": 4011, "season": 2026, "week": 1, "seasonType": "regular",
            "startDate": "2026-09-05T19:30:00.000Z",
            "homeTeam": "Georgia", "awayTeam": "Clemson",
            "neutralSite": True, "homePoints": 31, "awayPoints": 17,
        },
        {  # unplayed → dropped
            "id": 4012, "season": 2026, "week": 2, "seasonType": "regular",
            "startDate": "2026-09-12T19:30:00.000Z",
            "homeTeam": "Alabama", "awayTeam": "Auburn",
            "neutralSite": False, "homePoints": None, "awayPoints": None,
        },
    ]
    lines_json = [
        {"id": 4011, "lines": [
            {"spread": -13.5, "overUnder": 49.5},
            {"spread": -14.5, "overUnder": 50.5},
        ]},
    ]
    games = rd.ncaaf_games_from_cfbd(games_json, lines_json, 2026)
    assert len(games) == 1
    row = games.iloc[0]
    assert row["game_id"] == "4011"
    assert row["season_type"] == "REG"
    assert bool(row["neutral_site"]) is True
    # CFBD negative-home-favored → positive-home-favored, at the median line.
    assert row["spread_line"] == 14.0
    assert row["total_line"] == 50.0
    assert pd.Timestamp(row["kickoff"]) == pd.Timestamp("2026-09-05 19:30:00")


def test_ncaaf_games_without_lines_still_join() -> None:
    games_json = [{
        "id": 5, "season": 2026, "week": 0, "seasonType": "regular",
        "startDate": "2026-08-22T16:00:00.000Z",
        "homeTeam": "Hawai'i", "awayTeam": "Stanford",
        "neutralSite": False, "homePoints": 21, "awayPoints": 20,
    }]
    games = rd.ncaaf_games_from_cfbd(games_json, [], 2026)
    assert len(games) == 1
    assert games.iloc[0]["spread_line"] is None or pd.isna(games.iloc[0]["spread_line"])


def test_the_ncaaf_player_bank_is_topped_up_before_the_cfbd_key_is_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The top-up lived where nothing called it, and the board priced from it.

    ``refresh_inseason`` carried an ``if league == "ncaaf"`` branch for its
    whole life; that function is only ever called with "mlb" and "wnba", so
    the branch never ran once. The bank sat at whatever the last hand-run had
    left in it — 2026 week 1, while the season played on — and the DFS board
    went on pricing from it, because a stale bank looks exactly like a fresh
    one. It is also ahead of the CFBD key check: cfbfastR is keyless.
    """
    out = tmp_path / "ncaaf"
    out.mkdir()
    (out / "player_games.parquet").write_bytes(b"")  # only existence is read
    called: list[tuple[list[int], Path]] = []
    monkeypatch.setattr(
        rd, "refresh_ncaaf_player_games",
        lambda o, season: called.append(([season], o)),
    )
    monkeypatch.delenv("CFBD_API_KEY", raising=False)

    with pytest.raises(SystemExit):
        rd.refresh_ncaaf(out, 2026)
    assert called == [([2026], out)]


def test_a_deleted_batter_bank_is_rebuilt_rather_than_skipped_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The guard read ``batters_out=batters if batters.exists() else None``.

    So a bank that was deleted or never created was never rebuilt: the refresh
    ran clean, wrote nothing, and the home-run and DFS models went on fitting
    whatever was left. ``bank_starters`` has always handled an absent bank
    correctly — a game counts as banked only when both banks hold it — so the
    only thing stopping recovery was the caller.
    """
    out = tmp_path / "mlb"
    out.mkdir()
    (out / "starters.parquet").write_bytes(b"")  # only existence is read
    games = out / "games.parquet"
    games.write_bytes(b"")
    called: list[dict] = []
    monkeypatch.setitem(
        __import__("sys").modules, "build_mlb_pitching",
        type("m", (), {"bank_starters": lambda *a, **k: called.append(k)})(),
    )

    rd.refresh_mlb_player_banks(out, games)

    assert called and called[0]["batters_out"] == out / "batters.parquet"
    # And the long first run is announced rather than looking hung.
    assert "rebuilding it from scratch" in capsys.readouterr().out


def test_an_existing_batter_bank_is_topped_up_quietly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "mlb"
    out.mkdir()
    (out / "starters.parquet").write_bytes(b"")
    (out / "batters.parquet").write_bytes(b"")
    games = out / "games.parquet"
    games.write_bytes(b"")
    called: list[dict] = []
    monkeypatch.setitem(
        __import__("sys").modules, "build_mlb_pitching",
        type("m", (), {"bank_starters": lambda *a, **k: called.append(k)})(),
    )

    rd.refresh_mlb_player_banks(out, games)

    assert called and called[0]["batters_out"] == out / "batters.parquet"
    assert "rebuilding" not in capsys.readouterr().out


def test_no_starters_bank_at_all_still_means_run_the_backfill(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The outer guard stays — a league is a content surface, not a surprise.

    Never having been backfilled is a different thing from a bank going
    missing, and only the second is a recovery this should attempt.
    """
    out = tmp_path / "mlb"
    out.mkdir()
    rd.refresh_mlb_player_banks(out, out / "games.parquet")
    assert "run the backfill first" in capsys.readouterr().out

