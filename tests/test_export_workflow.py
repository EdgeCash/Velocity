"""The export's place in CI: inside the run that made the numbers, and on a button.

GitHub starts a *scheduled* run 1h48-3h00 after its cron and starts a
*dispatched* one within a minute (measured; docs/LATENCY_AUDIT.md). Those two
facts decide where the export step lives, and neither is visible in the YAML,
so they are pinned here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
LIVE_SLATE = WORKFLOWS / "live-slate.yml"
REFRESH_EXPORTS = WORKFLOWS / "refresh-exports.yml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _steps(path: Path, job: str) -> list[dict]:
    return _load(path)["jobs"][job]["steps"]


def test_the_slate_run_exports_before_it_uploads() -> None:
    """The export reads what the earlier steps banked, so it must run after them.

    And it must run BEFORE the upload, or the CSVs never leave the runner.
    """
    steps = _steps(LIVE_SLATE, "slate")
    names = [step.get("name", "") for step in steps]
    export = next(i for i, n in enumerate(names) if n == "Export the Excel datasets")
    upload = next(i for i, n in enumerate(names) if n.startswith("Upload slates"))
    build = next(i for i, n in enumerate(names) if n == "Build slates")
    assert build < export < upload


def test_the_slate_export_runs_the_export_step_alone() -> None:
    """`--steps export` is the whole point: it re-simulates nothing.

    Dropping the flag would re-run the engine inside a run that has already
    run it, doubling a thirty-minute job to produce identical numbers.
    """
    steps = _steps(LIVE_SLATE, "slate")
    run = next(s["run"] for s in steps if s.get("name") == "Export the Excel datasets")
    assert "velocity.run_weekly" in run
    assert "--steps export" in run


def test_the_slate_artifact_carries_the_csvs() -> None:
    steps = _steps(LIVE_SLATE, "slate")
    upload = next(s for s in steps if str(s.get("name", "")).startswith("Upload slates"))
    assert "artifacts/exports/*.csv" in upload["with"]["path"]


def test_the_refresh_workflow_has_no_schedule() -> None:
    """A cron here would return data staler than doing nothing.

    A scheduled refresh starts 1h48-3h00 late, by which time the artifact it
    would export from is older than the one the last slate run already
    produced. The fast path is workflow_dispatch, which starts in under a
    minute.
    """
    triggers = _load(REFRESH_EXPORTS)[True]  # PyYAML reads the `on:` key as True
    assert "schedule" not in triggers
    assert "workflow_dispatch" in triggers
    assert "workflow_run" in triggers


def test_the_refresh_workflow_waits_on_the_dfs_build() -> None:
    """The one case a machine can see coming: DFS finishing after its window."""
    triggers = _load(REFRESH_EXPORTS)[True]
    assert triggers["workflow_run"]["workflows"] == ["DFS slate"]
    # The name has to match dfs-slate.yml's `name:` exactly or the hook is dead.
    assert _load(WORKFLOWS / "dfs-slate.yml")["name"] == "DFS slate"


def test_the_refresh_workflow_installs_runtime_only() -> None:
    """No [ingest] extras: the export reads parquet and writes CSV."""
    steps = _steps(REFRESH_EXPORTS, "export")
    install = next(s for s in steps if str(s.get("name", "")).startswith("Install"))
    assert "pip install -e ." in install["run"]
    assert "[ingest]" not in install["run"]


def test_the_refresh_workflow_fails_loudly_with_no_csvs() -> None:
    """A refresh that silently uploads nothing is worse than one that fails."""
    steps = _steps(REFRESH_EXPORTS, "export")
    upload = next(s for s in steps if str(s.get("name", "")).startswith("Upload"))
    assert upload["with"]["if-no-files-found"] == "error"


@pytest.mark.parametrize("path", [LIVE_SLATE, REFRESH_EXPORTS])
def test_the_workflows_only_read_from_actions(path: Path) -> None:
    """Export needs to download artifacts, never to write to the repository."""
    permissions = _load(path)["permissions"]
    assert permissions["contents"] == "read"
    assert permissions["actions"] == "read"
