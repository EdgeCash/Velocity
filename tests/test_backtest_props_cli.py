"""backtest_props_mlb CLI — offline loader smoke.

The walk-forward run itself is credit-free but needs StatsAPI (the as-of model build),
so it lives behind the module's network pragma and is exercised in
test_backtest_props_mlb. This covers the thin CLI plumbing that has no network: the
recursive ``props_``/``events_`` parquet loader, including the artifact-subdir layout
(each downloaded run extracts into its own folder) and the missing-file guard.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).parent.parent
SCRIPT = REPO / "scripts" / "backtest_props_mlb.py"

_spec = importlib.util.spec_from_file_location("backtest_props_cli", SCRIPT)
assert _spec and _spec.loader
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


def test_load_concat_walks_artifact_subdirs(tmp_path: Path) -> None:
    # Two downloaded runs, each extracted into its own subdir with its own snapshot tag.
    (tmp_path / "hist-props-1").mkdir()
    (tmp_path / "hist-props-2").mkdir()
    pd.DataFrame({"game_id": ["a"], "market": ["hits"]}).to_parquet(
        tmp_path / "hist-props-1" / "props_mlb_20260710T160000Z.parquet", index=False
    )
    pd.DataFrame({"game_id": ["b"], "market": ["total_bases"]}).to_parquet(
        tmp_path / "hist-props-2" / "props_mlb_20260711T160000Z.parquet", index=False
    )
    out = cli._load_concat(tmp_path, "props")
    assert sorted(out["game_id"]) == ["a", "b"]  # both runs concatenated across subdirs


def test_load_concat_raises_on_empty_archive(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="no props_"):
        cli._load_concat(tmp_path, "props")
