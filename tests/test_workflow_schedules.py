"""Every scheduled workflow gets its own odd minute.

GitHub runs all of Actions' cron from one queue, and that queue is at its
worst on the minute everybody picks. `0 * * * *` is the default anyone writes
first, so the top of the hour is where the whole platform piles up — this
repo's own 16:00 slate has been starting closer to 18:40, two and a half hours
late, with nothing wrong on our side. `:30` is the second-most-crowded minute
for the same reason. Moving off both is free and it is the only lever we have
over queue time.

The second rule is local rather than platform-wide: two of OUR workflows on
the same minute are a race, not a jam. `live-slate.yml` reads the artifacts
that `dfs-slate.yml`, `collect-odds.yml` and `collect-football-props.yml`
produce, and it used to share 16:00 with `dfs-slate.yml` exactly — so the
site's DFS entries were always one window behind, because the run it
downloaded was still in progress. Distinct minutes are what make the ordering
real.

Neither rule can be enforced by a linter or by Actions itself, and both are
easy to undo by copy-pasting a cron from another file, so they are pinned
here. The minute map lives in docs/LAUNCH.md.
"""

from __future__ import annotations

import re
from collections import defaultdict
from itertools import pairwise
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
_CRON = re.compile(r'^\s*-\s*cron:\s*["\']([^"\']+)["\']', re.M)

# The minutes the rest of the platform is sitting on.
CROWDED = {"0", "30"}


def schedules() -> list[tuple[str, str]]:
    """Every `(workflow file, cron expression)` in the repo."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        found.extend((path.name, cron) for cron in _CRON.findall(path.read_text()))
    return found


def test_there_are_scheduled_workflows_to_check():
    # A regex that quietly matches nothing would make every test below pass.
    assert len(schedules()) >= 10


@pytest.mark.parametrize(("name", "cron"), schedules())
def test_no_workflow_starts_on_a_crowded_minute(name, cron):
    minute = cron.split()[0]
    assert minute not in CROWDED, (
        f"{name} is scheduled at minute :{minute.zfill(2)} ({cron!r}). "
        "The top and half of the hour are where all of GitHub Actions queues "
        "up; pick an odd minute from the map in docs/LAUNCH.md instead."
    )


@pytest.mark.parametrize(("name", "cron"), schedules())
def test_every_cron_is_five_fields(name, cron):
    assert len(cron.split()) == 5, f"{name}: {cron!r} is not a 5-field cron"


def test_no_two_workflows_share_a_start_slot():
    slots = defaultdict(set)
    for name, cron in schedules():
        minute, hour, dom, _month, dow = cron.split()
        # Keyed on the fields that decide whether two entries can fire
        # together. Same minute in a different hour is fine and deliberate.
        slots[(minute, hour, dom, dow)].add(name)
    clashes = {slot: sorted(names) for slot, names in slots.items() if len(names) > 1}
    assert not clashes, f"workflows sharing a start slot: {clashes}"


def expand_hours(field: str) -> set[int]:
    """The hour field of a cron as the set of hours it fires in.

    Enough of the syntax for what this repo writes: ``*``, ``*/3``, ``16,22``
    and plain numbers. A form we do not handle is loud rather than silently
    empty, because an empty set would make the ordering check below pass by
    comparing nothing.
    """
    hours: set[int] = set()
    for part in field.split(","):
        if part == "*":
            hours |= set(range(24))
        elif part.startswith("*/"):
            hours |= set(range(0, 24, int(part[2:])))
        elif part.isdigit():
            hours.add(int(part))
        else:
            raise AssertionError(f"unhandled cron hour field: {field!r}")
    return hours


def starts_by_hour(name: str) -> dict[int, set[int]]:
    """`{hour: {minute, ...}}` for one workflow."""
    out: dict[int, set[int]] = defaultdict(set)
    for other, cron in schedules():
        if other != name:
            continue
        minute, hour, *_ = cron.split()
        for h in expand_hours(hour):
            out[h].add(int(minute))
    return out


def test_live_slate_starts_after_the_workflows_it_reads():
    """The consumer runs last in its hour, or it consumes yesterday's work.

    `live-slate.yml` downloads the newest SUCCESSFUL run of dfs-slate,
    collect-odds and collect-football-props. Starting level with one of them
    means the download finds the PREVIOUS run — which is exactly how the site
    came to show the 16:00 DFS lineups from the day before.
    """
    live = starts_by_hour("live-slate.yml")
    assert live, "live-slate.yml has no schedule to check"

    for hour, starts in live.items():
        latest = max(starts)
        for producer in ("dfs-slate.yml", "collect-odds.yml",
                         "collect-football-props.yml"):
            for pstart in starts_by_hour(producer).get(hour, ()):
                assert pstart < latest, (
                    f"{producer} starts at {hour:02d}:{pstart:02d}, at or after "
                    f"live-slate's {hour:02d}:{latest:02d} — live-slate would "
                    "download the previous run's artifact instead of this one's"
                )


def test_live_slate_windows_are_never_more_than_three_hours_apart():
    """Three hours is the widest gap the site can carry between rebuilds.

    GitHub starts this schedule two to three hours after its cron and drops
    runs outright (docs/LAUNCH.md, "What the schedule really does"). Two
    windows a day meant one reclaimed runner left the board eighteen hours
    stale through a Saturday afternoon; windows three hours apart give every
    kickoff a run landing before it and a neighbour to cover a dropped one.
    """
    # The union across game days: Saturday carries all five windows, and the
    # other days a subset of the same hours, so the minute map stays one row.
    hours = sorted(starts_by_hour("live-slate.yml"))
    assert hours, "live-slate.yml has no schedule to check"
    assert hours[0] <= 11, (
        f"first window {hours[0]:02d}:xx — with the measured delay it lands "
        "after the noon-ET college slate"
    )
    assert hours[-1] >= 23, (
        f"last window {hours[-1]:02d}:xx leaves the late slates on an old board"
    )
    gaps = [b - a for a, b in pairwise(hours)]
    assert max(gaps) <= 3, f"live-slate windows more than three hours apart: {hours}"



@pytest.mark.parametrize("name", ["live-slate.yml", "dfs-slate.yml"])
def test_the_slate_workflows_run_on_football_days(name):
    """Football only: every slate cron names its days of the week.

    A ``*`` day-of-week is the summer cadence coming back by copy-paste — the
    summer leagues played every night, football does not, and a daily run on
    a Tuesday prices an empty board for twenty minutes of billed runner time.
    """
    crons = [cron for other, cron in schedules() if other == name]
    assert crons, f"{name} has no schedule"
    for cron in crons:
        dow = cron.split()[4]
        assert dow != "*", f"{name}: {cron!r} runs every day of the week"
