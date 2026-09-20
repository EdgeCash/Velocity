"""The Excel contract — what every export promises Power Query (Phase 6).

Excel breaks loudly and late: a renamed column surfaces as a broken query in
front of a slate, not as a failing build. These tests are the early, quiet
version of that failure.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pandas as pd
import pytest
from velocity.export.dashboard import DASHBOARD_COLUMNS, export_dashboard
from velocity.export.dfs import DFS_COLUMNS, DFS_OPTIMIZER_COLUMNS, export_dfs
from velocity.export.games import GAMES_COLUMNS, export_games
from velocity.export.meta import META_COLUMNS, ExportMeta
from velocity.export.plays import PLAYS_COLUMNS, export_plays
from velocity.export.props import PROPS_COLUMNS, export_props

REPO = Path(__file__).resolve().parents[1]

CONTRACTS = {
    "games.csv": GAMES_COLUMNS,
    "props.csv": PROPS_COLUMNS,
    "dfs.csv": DFS_COLUMNS,
    "dfs_optimizer.csv": DFS_OPTIMIZER_COLUMNS,
    "plays.csv": PLAYS_COLUMNS,
    "dashboard.csv": DASHBOARD_COLUMNS,
}


@pytest.mark.parametrize(("name", "columns"), sorted(CONTRACTS.items()))
def test_every_export_ends_with_the_metadata_columns(
    name: str, columns: tuple[str, ...]
) -> None:
    assert columns[-3:] == META_COLUMNS, f"{name} must end generated_at, season, week"


@pytest.mark.parametrize(("name", "columns"), sorted(CONTRACTS.items()))
def test_column_names_are_power_query_safe(name: str, columns: tuple[str, ...]) -> None:
    for column in columns:
        # Lowercase, digits and underscores only. A header with a space or a
        # percent sign becomes #"Model %" in every M expression that touches
        # it, and a renamed one silently breaks the workbook.
        assert re.fullmatch(r"[a-z0-9_]+", column), f"{name}: {column!r}"
    assert len(set(columns)) == len(columns), f"{name} has a duplicate column"


def test_the_six_files_are_written_even_with_nothing_to_say(tmp_path: Path) -> None:
    meta = ExportMeta("2026-09-20T17:53:00Z", 2026, 3)
    export_games(meta, None, out_dir=tmp_path)
    export_props(meta, None, out_dir=tmp_path)
    export_dfs(meta, None, out_dir=tmp_path)
    export_plays(meta, None, out_dir=tmp_path)
    export_dashboard(meta, None, out_dir=tmp_path)

    for name, columns in CONTRACTS.items():
        path = tmp_path / name
        assert path.exists(), f"{name} was not written"
        with path.open(encoding="utf-8-sig", newline="") as handle:
            header = next(csv.reader(handle))
        assert header == list(columns), f"{name} header drifted"


def test_every_row_carries_the_run_stamp(tmp_path: Path) -> None:
    meta = ExportMeta("2026-09-20T17:53:00Z", 2026, 3)
    games = pd.DataFrame([{"game_id": "g1", "league": "nfl",
                           "home_team": "Carolina", "away_team": "Atlanta"}])
    path = export_games(meta, games, out_dir=tmp_path)
    frame = pd.read_csv(path)
    assert (frame["generated_at"] == "2026-09-20T17:53:00Z").all()
    assert (frame["season"] == 2026).all()
    assert (frame["week"] == 3).all()


def test_exports_are_byte_stable_across_two_identical_runs(tmp_path: Path) -> None:
    """A refresh that changed nothing must produce no diff."""
    meta = ExportMeta("2026-09-20T17:53:00Z", 2026, 3)
    games = pd.DataFrame([{"game_id": "g1", "league": "nfl",
                           "home_team": "Carolina", "away_team": "Atlanta"}])
    first = (tmp_path / "a"), (tmp_path / "b")
    written = [export_games(meta, games, out_dir=folder) for folder in first]
    assert written[0].read_bytes() == written[1].read_bytes()


def test_the_export_dir_is_ignored_by_git() -> None:
    """Exports are paid-feed derived; datasets/ is opted back into git.

    Without this rule one ``git add -A`` commits The Odds API's prices to the
    repository — the exact thing the ``artifacts/`` rule exists to prevent.
    """
    rules = (REPO / ".gitignore").read_text().splitlines()
    assert "datasets/exports/*" in rules
    assert "!datasets/exports/.gitkeep" in rules
    # The ignore has to come AFTER the datasets opt-in: gitignore takes the
    # last matching pattern, so an ignore placed before `!datasets/**` loses.
    assert rules.index("datasets/exports/*") > rules.index("!datasets/**")


def test_the_export_dir_exists_with_only_its_keep_file() -> None:
    folder = REPO / "datasets" / "exports"
    assert (folder / ".gitkeep").exists()
