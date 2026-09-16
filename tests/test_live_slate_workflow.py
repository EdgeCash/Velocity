"""The live slate has to PASS the data it collects, not merely collect it.

On 2026-09-16 the data audit found the home-run board running on plain
shrinkage: ``live-slate.yml`` fetched the Savant snapshot and then called
``build_hr_board.py`` without ``--statcast``. The sibling ``dfs-slate.yml`` had
it right the whole time.

Nothing caught it, and three things had to line up for that:

* ``--statcast`` defaults to ``None`` and ``_statcast_prior`` returns ``{}`` —
  a deliberate, documented, silent fallback;
* ``|| true`` and ``continue-on-error`` swallow anything that might have said
  so;
* the flag appears in the workflows directory, so a repo-wide grep answers
  "passed". The check has to be per-invocation, which is what this file does.

Asserted against the file's text rather than a parsed tree — the sibling
workflow tests do the same, and PyYAML is not a declared dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


@pytest.fixture(scope="module")
def live() -> str:
    return (WORKFLOWS / "live-slate.yml").read_text()


def _hr_board_step(text: str) -> str:
    """Just the home-run board step — a flag elsewhere in the file is not this."""
    start = text.index("Build the MLB home-run board")
    rest = text[start:]
    # The step ends at the next step's "- name:" at the same indentation.
    end = rest.index("\n      - name:", 1)
    return rest[:end]


def test_the_live_home_run_board_passes_the_statcast_snapshot(live: str) -> None:
    """The bug, pinned at the only place that can prove it.

    Scoped to the step, because the whole point is that the flag existing
    somewhere in this directory is what made the omission invisible.
    """
    step = _hr_board_step(live)
    assert "collect_mlb_statcast.py" in step, "the snapshot is not even collected"
    assert "--statcast" in step, (
        "build_hr_board.py is called without --statcast, so the batted-ball "
        "prior never runs and the board prices on plain shrinkage — the thing "
        "the model exists to beat"
    )


def test_the_snapshot_is_collected_before_it_is_passed(live: str) -> None:
    step = _hr_board_step(live)
    assert step.index("collect_mlb_statcast.py") < step.index("--statcast")


def test_both_workflows_that_build_the_board_pass_it() -> None:
    """dfs-slate.yml was already correct; neither may regress.

    A future third caller would need the same line, which is the argument for
    asserting the property across every workflow that builds the board rather
    than naming the two.
    """
    builders = [p for p in WORKFLOWS.glob("*.yml")
                if "build_hr_board.py" in p.read_text()]
    assert builders, "no workflow builds the home-run board at all"
    for path in builders:
        step = _hr_board_step(path.read_text())
        assert "--statcast" in step, f"{path.name} builds the board without --statcast"


def test_the_board_reports_whether_the_prior_ran() -> None:
    """The log line that would have made this visible from the start."""
    script = (Path(__file__).resolve().parents[1]
              / "scripts" / "build_hr_board.py").read_text()
    assert "statcast_batters" in script
    assert re.search(r"::warning[^\n]*Statcast prior", script), (
        "a board with no Statcast prior must say so loudly — a silent fallback "
        "is what let this run for months"
    )
