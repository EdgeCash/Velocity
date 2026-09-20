"""The weekly orchestrator — the plan, the log, and an offline end-to-end export."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from velocity.export.dashboard import DASHBOARD_COLUMNS
from velocity.export.dfs import DFS_COLUMNS, DFS_OPTIMIZER_COLUMNS
from velocity.export.games import GAMES_COLUMNS
from velocity.export.plays import PLAYS_COLUMNS
from velocity.export.props import PROPS_COLUMNS
from velocity.run_weekly import (
    StepResult,
    build_parser,
    dfs_command,
    export_step,
    load_artifacts,
    main,
    plan,
    run_command,
)

STAMP = "20260920T175300Z"


def _args(**overrides: object):  # type: ignore[no-untyped-def]
    base = build_parser().parse_args([])
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _bank(folder: Path) -> None:
    """A minimal but complete artifact folder, as one live run would leave it."""
    folder.mkdir(parents=True, exist_ok=True)

    def write(name: str, rows: list[dict]) -> None:
        pd.DataFrame(rows).to_parquet(folder / f"{name}_{STAMP}.parquet", index=False)

    write("games_nfl", [{"game_id": "g1", "home_team": "Carolina",
                         "away_team": "Atlanta", "kickoff": "2026-09-21T17:00:00Z"}])
    write("projections_nfl", [{"game_id": "g1", "mu_home": 21.0, "mu_away": 23.2,
                               "p_home_win": 0.44, "fair_spread": 2.2,
                               "fair_total": 44.2, "n_sims": 100_000}])
    rng = np.random.default_rng(1)
    margin = rng.integers(-21, 21, 4000)
    total = rng.integers(24, 66, 4000)
    rows = []
    for kind, samples in (("margin", margin), ("total", total)):
        values, counts = np.unique(samples, return_counts=True)
        rows += [{"game_id": "g1", "kind": kind, "value": int(v),
                  "prob": float(c) / samples.size}
                 for v, c in zip(values, counts, strict=True)]
    write("distributions_nfl", rows)
    write("weather_nfl", [{"game_id": "g1", "home_team": "Carolina",
                           "away_team": "Atlanta", "wind_mph": 14.0,
                           "precip_in": 0.0, "temp_f": 51.0, "wind_points": -0.9,
                           "precip_points": 0.0, "total_points": -0.9}])
    write("slate_nfl", [{"game_id": "g1", "market": "total", "side": "under",
                         "point": 48.5, "book": "dk", "price": -110,
                         "p_model": 0.62, "p_fair": 0.52, "edge": 0.10,
                         "stake": 2.4, "note": None, "rule_tier": "A",
                         "rule_record": "56.2% over 299 bets"}])
    write("intel_nfl", [{"game_id": "g1", "player": None, "market": "total",
                         "side": "under", "conviction": 0.71, "tier": "A",
                         "recommended": True, "rationale": "rest edge to the road side"}])
    write("slate_nfl_props", [{"game_id": "g1", "player": "Bijan Robinson",
                               "market": "rush_yds", "side": "over", "point": 64.5,
                               "book": "dk", "price": -115, "p_model": 0.60,
                               "p_fair": 0.53, "edge": 0.07, "stake": 1.4,
                               "note": None}])
    write("prop_dist_nfl", [{"game_id": "g1", "player_key": "robinson-b",
                             "player": "Bijan Robinson", "team": "ATL",
                             "position": "RB", "market": "rush_yds",
                             "projection": 71.2, "median": 69.0, "p75": 88.0,
                             "p90": 106.0, "p99": 141.0, "n_sims": 100_000}])
    write("dfs_pool_nfl", [
        {"player_name": "Bijan Robinson", "position": "RB", "team": "ATL",
         "salary": 7600, "points": 17.4, "value": 2.29},
        {"player_name": "Drake London", "position": "WR", "team": "ATL",
         "salary": 6100, "points": 13.1, "value": 2.15},
    ])
    write("dfs_dist_nfl", [
        {"player": "Bijan Robinson", "projection": 17.4, "median": 16.2,
         "p75": 22.0, "p90": 27.5, "p99": 38.1, "n_sims": 20_000},
        {"player": "Drake London", "projection": 13.1, "median": 11.8,
         "p75": 17.0, "p90": 22.4, "p99": 33.0, "n_sims": 20_000},
    ])
    write("record_nfl", [
        {"market": "total", "result": "win", "profit": 0.91, "stake": 1.0,
         "price_clv": 0.02, "line_clv": 0.5, "rule_tier": "A"},
        {"market": "total", "result": "loss", "profit": -1.0, "stake": 1.0,
         "price_clv": -0.01, "line_clv": -0.5, "rule_tier": "A"},
    ])


def test_plan_orders_the_steps_and_names_the_leagues() -> None:
    steps = [name for name, _ in plan(_args(leagues=["nfl", "ncaaf"]))]
    assert steps == ["refresh", "slate:nfl", "slate:ncaaf",
                     "dfs:nfl", "dfs:ncaaf", "export"]


def test_plan_honours_a_step_selection() -> None:
    steps = [name for name, _ in plan(_args(steps=["export"], leagues=["nfl"]))]
    assert steps == ["export"]


def test_dfs_step_is_unrunnable_without_its_two_feeds() -> None:
    assert dfs_command(_args(), "nfl") is None
    command = dfs_command(_args(salaries="s.parquet", fp="fp.parquet"), "nfl")
    assert command is not None and "--salaries" in command and "--fp" in command


def test_a_failing_command_is_data_not_an_exception() -> None:
    import sys

    result = run_command("boom", [sys.executable, "-c", "import sys; sys.exit(3)"])
    assert result.status == "failed"
    assert "exit 3" in result.detail


def test_a_missing_program_is_reported_not_raised() -> None:
    result = run_command("nope", ["/definitely/not/a/program"])
    assert result.status == "failed"


def test_step_result_logs_one_json_object() -> None:
    payload = json.loads(StepResult("x", "ok", 1.234, "detail", ["a.csv"]).as_json())
    assert payload == {"step": "x", "status": "ok", "seconds": 1.23,
                       "detail": "detail", "outputs": ["a.csv"]}


def test_load_artifacts_names_every_family_the_export_reads(tmp_path: Path) -> None:
    _bank(tmp_path / "slate")
    frames = load_artifacts(tmp_path / "slate", ("nfl",))
    for key in ("games", "projections", "distributions", "weather", "slate",
                "intel", "props", "prop_dist", "dfs_pool", "dfs_dist", "record"):
        assert not frames[key].empty, f"{key} did not load"


def test_export_step_writes_all_six_files_from_banked_artifacts(tmp_path: Path) -> None:
    _bank(tmp_path / "slate")
    out = tmp_path / "exports"
    result = export_step(_args(slate_dir=str(tmp_path / "slate"), out=str(out),
                               leagues=["nfl"], data_dir=str(tmp_path / "datasets")))
    assert result.status == "ok", result.detail
    # generated_at is when the NUMBERS were made, not when the CSV was written.
    assert STAMP in result.detail

    contracts = {
        "games.csv": GAMES_COLUMNS, "props.csv": PROPS_COLUMNS,
        "dfs.csv": DFS_COLUMNS, "dfs_optimizer.csv": DFS_OPTIMIZER_COLUMNS,
        "plays.csv": PLAYS_COLUMNS, "dashboard.csv": DASHBOARD_COLUMNS,
    }
    for name, columns in contracts.items():
        path = out / name
        assert path.exists(), f"{name} missing"
        with path.open(encoding="utf-8-sig", newline="") as handle:
            assert next(csv.reader(handle)) == list(columns)

    games = pd.read_csv(out / "games.csv")
    assert games.loc[0, "generated_at"] == "2026-09-20T17:53:00Z"
    assert games.loc[0, "market_total"] == pytest.approx(48.5)
    assert 0.0 < float(games.loc[0, "over_probability"]) < 1.0
    assert "wind 14 mph" in str(games.loc[0, "weather"])
    # Intel conviction 0.71 becomes a 0-10 confidence.
    assert games.loc[0, "confidence"] == pytest.approx(7.1)

    plays = pd.read_csv(out / "plays.csv")
    assert plays.loc[0, "tier"] == "A+"
    assert "rule A (unders 4+)" in plays.loc[0, "reason"]
    assert set(plays["bet_type"]) == {"game", "prop"}

    props = pd.read_csv(out / "props.csv")
    assert props.loc[0, "team"] == "ATL"
    assert props.loc[0, "90th_percentile"] == pytest.approx(106.0)

    optimizer = pd.read_csv(out / "dfs_optimizer.csv")
    row = optimizer[optimizer["player"] == "Bijan Robinson"].iloc[0]
    assert row["cash"] == pytest.approx(16.2)
    assert row["gpp"] == pytest.approx(27.5)
    assert row["ceiling"] == pytest.approx(38.1)

    dashboard = pd.read_csv(out / "dashboard.csv")
    top = dashboard[dashboard["section"] == "top_plays"].iloc[0]
    assert "[A+]" in top["detail"]


def test_export_step_survives_an_empty_artifact_folder(tmp_path: Path) -> None:
    out = tmp_path / "exports"
    result = export_step(_args(slate_dir=str(tmp_path / "empty"), out=str(out),
                               leagues=["nfl"], data_dir=str(tmp_path / "datasets")))
    assert result.status == "ok"
    assert "no artifacts for" in result.detail
    for name in ("games.csv", "props.csv", "dfs.csv", "dfs_optimizer.csv",
                 "plays.csv", "dashboard.csv"):
        assert (out / name).exists()
    assert pd.read_csv(out / "games.csv").empty


def test_main_exits_zero_on_a_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--dry-run", "--steps", "export"])
    assert code == 0
    assert "0 failed" in capsys.readouterr().out


def test_strict_stops_at_the_first_failure(tmp_path: Path,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    # refresh runs a real script with a bad datasets root; strict must then
    # skip the rest rather than push on to the slate.
    code = main([
        "--steps", "refresh", "export", "--strict",
        "--slate-dir", str(tmp_path / "slate"),
        "--out", str(tmp_path / "out"),
        "--refresh-league", "nfl",
        "--data-dir", "/proc/definitely-not-writable",
    ])
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    statuses = {entry["step"]: entry["status"] for entry in lines}
    if statuses.get("refresh") == "failed":
        assert statuses["export"] == "skipped"
        assert code == 1
    else:  # pragma: no cover - the refresh reached the network and worked
        pytest.skip("refresh unexpectedly succeeded in this environment")
