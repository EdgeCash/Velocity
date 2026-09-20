"""The read seam — newest stamp per league, and nothing else interpreted."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from velocity.export.artifacts import (
    collect,
    latest_frame,
    newest,
    newest_stamp,
    stamp_to_timestamp,
)


def _write(folder: Path, name: str, rows: list[dict]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(folder / name, index=False)


def test_newest_takes_the_last_stamp(tmp_path: Path) -> None:
    _write(tmp_path, "slate_nfl_20260919T120000Z.parquet", [{"x": 1}])
    _write(tmp_path, "slate_nfl_20260920T120000Z.parquet", [{"x": 2}])
    found = newest(tmp_path, r"slate_nfl_(\d{8}T\d{6}Z)\.parquet")
    assert found is not None and "20260920" in found.name


def test_missing_folder_is_not_an_error(tmp_path: Path) -> None:
    assert newest(tmp_path / "nope", r".*") is None
    assert collect(tmp_path / "nope", "slate").empty


def test_latest_frame_tags_league_and_stamp(tmp_path: Path) -> None:
    _write(tmp_path, "slate_nfl_20260920T120000Z.parquet", [{"x": 2}])
    frame = latest_frame(tmp_path, "slate_{league}", "nfl")
    assert frame is not None
    assert frame.loc[0, "league"] == "nfl"
    assert frame.loc[0, "stamp"] == "20260920T120000Z"


def test_collect_takes_one_stamp_per_league(tmp_path: Path) -> None:
    _write(tmp_path, "slate_nfl_20260919T120000Z.parquet", [{"x": 1}])
    _write(tmp_path, "slate_nfl_20260920T120000Z.parquet", [{"x": 2}])
    _write(tmp_path, "slate_ncaaf_20260920T120000Z.parquet", [{"x": 3}])
    frame = collect(tmp_path, "slate")
    # The stale NFL stamp is not concatenated in beside the fresh one.
    assert sorted(frame["x"]) == [2, 3]
    assert set(frame["league"]) == {"nfl", "ncaaf"}


def test_newest_stamp_reports_when_the_numbers_were_made(tmp_path: Path) -> None:
    _write(tmp_path, "slate_nfl_20260919T120000Z.parquet", [{"x": 1}])
    _write(tmp_path, "games_nfl_20260920T120000Z.parquet", [{"x": 1}])
    frames = {"slate": collect(tmp_path, "slate"), "games": collect(tmp_path, "games")}
    assert newest_stamp(frames) == "20260920T120000Z"
    assert newest_stamp({"none": pd.DataFrame()}) is None


def test_stamp_to_timestamp_round_trips_and_refuses_junk() -> None:
    ts = stamp_to_timestamp("20260920T175300Z")
    assert ts is not None and str(ts) == "2026-09-20 17:53:00+00:00"
    assert stamp_to_timestamp("not a stamp") is None
