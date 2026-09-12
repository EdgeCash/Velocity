"""Which banked board a run prices against.

The slate re-reads the hourly collector's ``/odds`` payloads instead of buying
the same board twice. Picking the wrong one is not a cosmetic bug: a stale
board is priced against prices that have already moved, and a board old enough
that its games have started produces an empty card. These pin the rule that the
capture stamp in the filename — not the file's mtime — decides.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

_SCRIPT = Path(__file__).parent.parent / "scripts" / "run_live_slate.py"


def _runner():
    spec = importlib.util.spec_from_file_location("run_live_slate", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bank(root: Path, names: list[str]) -> None:
    """Write payload files in the artifact shape: <run id>/<artifact>/raw/<name>."""
    for i, name in enumerate(names):
        raw = root / f"run{i}" / f"odds-lines-{i}" / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / name).write_text("[]")


def test_stamp_is_parsed_only_for_the_asked_league() -> None:
    runner = _runner()
    assert runner.parse_snapshot_stamp(
        "odds_nfl_20260911T150034Z.json", "nfl"
    ) == pd.Timestamp("2026-09-11 15:00:34")
    # Another league's payload, a parquet, and a malformed stamp are all misses.
    assert runner.parse_snapshot_stamp("odds_mlb_20260911T150034Z.json", "nfl") is None
    assert runner.parse_snapshot_stamp("odds_nfl_20260911T150034Z.parquet", "nfl") is None
    assert runner.parse_snapshot_stamp("odds_nfl_latest.json", "nfl") is None


def test_newest_stamp_wins_regardless_of_write_order(tmp_path: Path) -> None:
    """The oldest file is written last — mtime ordering would pick it.

    This is the production failure exactly: the workflow downloads collector
    runs newest-first, so the oldest payload is the last one unzipped and has
    the newest mtime.
    """
    runner = _runner()
    _bank(tmp_path, [
        "odds_nfl_20260911T140000Z.json",   # newest board, written first
        "odds_nfl_20260909T150034Z.json",   # oldest board, written last
    ])
    now = pd.Timestamp("2026-09-11 15:00:00")
    path, stamp = runner.newest_banked_board(tmp_path, "nfl", now, 75.0)
    assert stamp == pd.Timestamp("2026-09-11 14:00:00")
    assert path is not None and path.name == "odds_nfl_20260911T140000Z.json"


def test_a_stale_bank_is_refused_but_still_reported(tmp_path: Path) -> None:
    runner = _runner()
    _bank(tmp_path, ["odds_mlb_20260909T184809Z.json"])
    now = pd.Timestamp("2026-09-11 19:09:56")
    path, stamp = runner.newest_banked_board(tmp_path, "mlb", now, 75.0)
    # Refused for pricing...
    assert path is None
    # ...but the caller still learns how stale the bank is, so the log can say
    # "2 days old" instead of only "no payload".
    assert stamp == pd.Timestamp("2026-09-09 18:48:09")


def test_other_leagues_and_empty_banks_do_not_match(tmp_path: Path) -> None:
    runner = _runner()
    _bank(tmp_path, ["odds_ncaaf_20260911T150034Z.json"])
    assert runner.newest_banked_board(
        tmp_path, "nfl", pd.Timestamp("2026-09-11 15:10:00"), 75.0) == (None, None)
    assert runner.newest_banked_board(
        tmp_path / "absent", "nfl", pd.Timestamp("2026-09-11 15:10:00"), 75.0) == (None, None)


def test_resolver_sets_snapshot_file_only_when_fresh(tmp_path: Path) -> None:
    """``--snapshot-file`` is the one attribute every downstream check reads."""
    runner = _runner()
    _bank(tmp_path, ["odds_nfl_20260911T143000Z.json"])
    parser = runner.build_parser()

    fresh = parser.parse_args(["--league", "nfl", "--snapshot-dir", str(tmp_path)])
    runner.resolve_banked_board(fresh, pd.Timestamp("2026-09-11 15:00:00"))
    assert fresh.snapshot_file is not None
    assert fresh.snapshot_file.endswith("odds_nfl_20260911T143000Z.json")

    # Same bank, two days later: nothing qualifies, so the run pulls live.
    stale = parser.parse_args(["--league", "nfl", "--snapshot-dir", str(tmp_path)])
    runner.resolve_banked_board(stale, pd.Timestamp("2026-09-13 15:00:00"))
    assert stale.snapshot_file is None


def test_an_explicit_snapshot_file_always_wins(tmp_path: Path) -> None:
    runner = _runner()
    _bank(tmp_path, ["odds_nfl_20260911T143000Z.json"])
    args = runner.build_parser().parse_args(
        ["--league", "nfl", "--snapshot-dir", str(tmp_path), "--snapshot-file", "pinned.json"]
    )
    runner.resolve_banked_board(args, pd.Timestamp("2026-09-11 15:00:00"))
    assert args.snapshot_file == "pinned.json"


def test_the_age_limit_defaults_to_the_workflow_value() -> None:
    runner = _runner()
    args = runner.build_parser().parse_args(["--league", "nfl"])
    assert args.board_max_age_min == 75.0
    assert args.snapshot_dir is None
