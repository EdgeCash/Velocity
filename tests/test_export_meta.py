"""The export writer's contract: stable shape, stamped rows, a file that exists."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from velocity.export.meta import (
    ExportMeta,
    empty_export,
    finalize,
    round_columns,
    stamp_text,
    write_csv,
)

COLUMNS = ("a", "b", "generated_at", "season", "week")


def test_stamp_text_is_utc_iso_seconds() -> None:
    assert stamp_text("2026-09-20T17:53:00Z") == "2026-09-20T17:53:00Z"
    # A naive instant is read as UTC, not as the runner's local zone.
    assert stamp_text(pd.Timestamp("2026-09-20 17:53:00")) == "2026-09-20T17:53:00Z"
    # A non-UTC zone is converted, not truncated.
    assert stamp_text(pd.Timestamp("2026-09-20 13:53:00-04:00")) == "2026-09-20T17:53:00Z"


def test_meta_from_games_reads_season_and_next_week() -> None:
    games = pd.DataFrame({
        "season": [2026, 2026, 2026],
        "week": [1, 2, 3],
        "home_score": [21, 17, None],
        "away_score": [20, 13, None],
    })
    meta = ExportMeta.from_games(games, generated_at="2026-09-20T17:53:00Z")
    assert (meta.season, meta.week) == (2026, 3)


def test_meta_from_games_says_nothing_when_the_frame_cannot() -> None:
    meta = ExportMeta.from_games(None)
    assert meta.season is None and meta.week is None
    assert meta.generated_at.endswith("Z")


def test_finalize_reindexes_onto_the_contract() -> None:
    meta = ExportMeta("2026-09-20T17:53:00Z", 2026, 3)
    # 'b' is missing from the source and 'extra' is not in the contract.
    frame = pd.DataFrame({"a": [1], "extra": ["x"]})
    out = finalize(frame, COLUMNS, meta)
    assert list(out.columns) == list(COLUMNS)
    assert out["b"].isna().all()
    assert out.loc[0, "season"] == 2026


def test_empty_source_still_writes_a_header(tmp_path: Path) -> None:
    meta = ExportMeta("2026-09-20T17:53:00Z", 2026, 3)
    dest = write_csv(pd.DataFrame(), tmp_path / "x.csv", COLUMNS, meta)
    text = dest.read_text(encoding="utf-8-sig")
    assert text.splitlines() == ["a,b,generated_at,season,week"]


def test_written_file_is_bom_utf8_with_lf(tmp_path: Path) -> None:
    meta = ExportMeta("2026-09-20T17:53:00Z", 2026, 3)
    frame = pd.DataFrame({"a": ["Kyren Williams"], "b": [1.5]})
    dest = write_csv(frame, tmp_path / "x.csv", COLUMNS, meta)
    raw = dest.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # Excel needs telling it is UTF-8
    assert b"\r\n" not in raw  # deterministic across runners


def test_empty_export_has_the_columns() -> None:
    assert list(empty_export(COLUMNS).columns) == list(COLUMNS)
    assert empty_export(COLUMNS).empty


def test_round_columns_ignores_absent_and_coerces_text() -> None:
    frame = pd.DataFrame({"a": [1.23456], "b": ["not a number"]})
    out = round_columns(frame, ("a", "b", "missing"), 2)
    assert out.loc[0, "a"] == pytest.approx(1.23)
    assert pd.isna(out.loc[0, "b"])
