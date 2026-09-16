"""The dataset refresh has to check itself, because nothing downstream will.

A push made with ``GITHUB_TOKEN`` deliberately does not trigger workflows. So
when the refresh bot pushes to main, **no CI run happens at all** — and on
2026-09-16 that is exactly how main went red and stayed red: fd1c4f6 moved
``datasets/`` and left ``velocity/eval/ladders.py`` behind, which is a cache OF
those datasets, and the breakage surfaced only when an unrelated PR was tested
against the merged result.

The repair was to make the job regenerate what it invalidates and run the suite
before it writes anything. These pin that, because it is a property of a YAML
file with no other test over it: deleting the verify step would look like
tidying and would silently restore the old failure mode.

Asserted against the file's text rather than a parsed tree — the sibling
schedule test does the same, and PyYAML is not a declared dependency.
"""

from __future__ import annotations

from pathlib import Path

import pytest

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github" / "workflows" / "refresh-datasets.yml"
)


@pytest.fixture(scope="module")
def text() -> str:
    return WORKFLOW.read_text()


def test_the_refresh_workflow_exists(text: str) -> None:
    # A path typo would make every assertion below vacuously pass.
    assert "Refresh datasets" in text


def test_it_regenerates_the_table_its_own_data_invalidates(text: str) -> None:
    """``OFFSET_BIAS`` is derived from ``datasets/``; moving one moves the other."""
    assert "scripts/calibrate_ladders.py --write" in text


def test_it_runs_the_suite_before_it_pushes(text: str) -> None:
    """The gate the job was missing — and the order is the whole point."""
    verify = text.index("pytest -q")
    push = text.rindex("git push")
    assert verify < push, "the verify step must run BEFORE the push, not after"


def test_the_verify_covers_lint_and_types_too(text: str) -> None:
    # A refresh cannot break lint or types today, but the step is the repo's
    # only pre-push gate on main and narrowing it to one checker is how it
    # stops catching the next thing.
    for check in ("ruff check .", "mypy", "pytest -q"):
        assert check in text, f"the pre-push verify no longer runs {check!r}"


def test_it_commits_the_derived_table_with_the_data(text: str) -> None:
    """Committing the data without the table it feeds IS the 2026-09-16 bug."""
    assert "git add datasets/ velocity/eval/ladders.py" in text


def test_the_push_is_gated_on_the_data_having_moved(text: str) -> None:
    # Every step after the detection is conditional on it, so an unchanged
    # refresh costs nothing and cannot produce an empty commit.
    assert "steps.moved.outputs.changed == 'true'" in text
    assert text.count("steps.moved.outputs.changed == 'true'") >= 3


def test_the_job_installs_what_the_verify_needs(text: str) -> None:
    # `pip install -e .` alone has no pytest, so the verify step would fail on
    # a missing binary rather than on a real result.
    assert "pip install -e '.[dev]'" in text
