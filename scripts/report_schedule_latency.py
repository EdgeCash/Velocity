#!/usr/bin/env python3
"""How late does GitHub actually start our scheduled runs? — measured.

The owner's Decision 4 (docs/DECISIONS.md) is to stay on GitHub-hosted
runners and **gather data** before considering anything else. This is the
instrument for that: it reads the Actions API, pairs each scheduled run with
the cron slot it was meant to fill, and reports the delay.

    python scripts/report_schedule_latency.py --days 28
    python scripts/report_schedule_latency.py --workflow live-slate.yml --csv out.csv

Needs a token with ``actions:read`` in ``GH_TOKEN`` or ``GITHUB_TOKEN``
(Actions provides one automatically). Read-only; writes nothing but the
report it is asked for.

Two numbers matter and the report leads with both: how late a run starts,
and how many slots never fire at all. The second is the one that costs a
slate, and it is invisible in any single run's log.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

API = "https://api.github.com"

# The workflows whose lateness has a cost attached. live-slate is the board
# itself; the other two are what it reads, and a prop board that lands after
# the slate is a props surface that does not exist (docs/DECISIONS.md D1).
DEFAULT_WORKFLOWS = (
    "live-slate.yml",
    "collect-football-props.yml",
    "collect-odds.yml",
    "dfs-slate.yml",
)


@dataclass(frozen=True)
class Run:
    """One scheduled run, and how late it started."""

    workflow: str
    run_number: int
    created_at: datetime   # when GitHub queued it (the cron's own moment)
    started_at: datetime   # when a runner actually picked it up
    conclusion: str
    url: str

    @property
    def delay_minutes(self) -> float:
        return (self.started_at - self.created_at).total_seconds() / 60.0


def _get(path: str, token: str, **params: object) -> dict:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    request = urllib.request.Request(  # noqa: S310 - fixed https host
        f"{API}{path}?{query}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.loads(response.read())


def _parse(stamp: object) -> datetime | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None


def scheduled_runs(
    repo: str, workflow: str, token: str, *, since: datetime, pages: int = 4
) -> Iterator[Run]:
    """Every ``schedule``-triggered run of one workflow since ``since``.

    ``created_at`` is when GitHub queued the run — the cron's own moment —
    and ``run_started_at`` is when a runner picked it up. The gap between
    them is the queue delay, which is the thing that cannot be read off any
    single run's log.
    """
    for page in range(1, pages + 1):
        payload = _get(
            f"/repos/{repo}/actions/workflows/{workflow}/runs", token,
            event="schedule", per_page=100, page=page,
        )
        runs = payload.get("workflow_runs") or []
        if not runs:
            return
        for row in runs:
            created = _parse(row.get("created_at"))
            started = _parse(row.get("run_started_at")) or created
            if created is None or started is None:
                continue
            if created < since:
                return
            yield Run(
                workflow=workflow,
                run_number=int(row.get("run_number", 0)),
                created_at=created,
                started_at=started,
                conclusion=str(row.get("conclusion") or "in_progress"),
                url=str(row.get("html_url") or ""),
            )


def cron_slots(workflow_file: Path, since: datetime, until: datetime) -> int:
    """How many times this workflow's crons SHOULD have fired in the window.

    Deliberately simple: it expands the minute/hour/day-of-week forms this
    repository actually writes. A form it does not understand raises rather
    than quietly undercounting, because an undercount would make the drop
    rate look better than it is.
    """
    import re

    crons = re.findall(r'- cron: "([^"]+)"', workflow_file.read_text())
    if not crons:
        return 0

    def field(spec: str, lo: int, hi: int) -> set[int]:
        out: set[int] = set()
        for part in spec.split(","):
            if part == "*":
                out |= set(range(lo, hi + 1))
            elif part.startswith("*/"):
                out |= set(range(lo, hi + 1, int(part[2:])))
            elif "-" in part:
                a, b = part.split("-")
                out |= set(range(int(a), int(b) + 1))
            elif part.isdigit():
                out.add(int(part))
            else:
                raise SystemExit(f"unhandled cron field {part!r} in {spec!r}")
        return out

    expected = 0
    cursor = since.replace(minute=0, second=0, microsecond=0)
    while cursor <= until:
        for cron in crons:
            minute, hour, _dom, _mon, dow = cron.split()
            hours = field(hour, 0, 23)
            days = field(dow, 0, 6)
            # cron's Sunday is 0; Python's Monday is 0.
            if cursor.hour in hours and ((cursor.weekday() + 1) % 7) in days:
                slot = cursor.replace(minute=int(field(minute, 0, 59).pop()))
                if since <= slot <= until:
                    expected += 1
        cursor += timedelta(hours=1)
    return expected


def describe(runs: Sequence[Run], expected: int) -> list[str]:
    """The report for one workflow."""
    if not runs:
        return ["  no scheduled runs in the window"]
    delays = sorted(r.delay_minutes for r in runs)
    fired = len(runs)
    lines = [
        f"  runs fired        {fired}" + (f" of {expected} cron slots "
                                          f"({fired / expected:.0%})"
                                          if expected else ""),
        f"  delay median      {statistics.median(delays):>6.0f} min",
        f"  delay mean        {statistics.fmean(delays):>6.0f} min",
        f"  delay min / max   {delays[0]:>6.0f} / {delays[-1]:.0f} min",
    ]
    if expected and fired < expected:
        lines.append(f"  SLOTS DROPPED     {expected - fired} "
                     f"({1 - fired / expected:.0%} of the schedule never ran)")
    worst = max(runs, key=lambda r: r.delay_minutes)
    lines.append(f"  worst             +{worst.delay_minutes:.0f} min  "
                 f"run #{worst.run_number}  {worst.url}")
    failed = [r for r in runs if r.conclusion not in ("success", "in_progress")]
    if failed:
        lines.append(f"  non-success       {len(failed)} run(s): "
                     + ", ".join(f"#{r.run_number} {r.conclusion}" for r in failed[:5]))
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/report_schedule_latency.py",
        description="Measure how late GitHub starts this repo's scheduled runs.",
    )
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY",
                                                         "EdgeCash/Velocity"))
    parser.add_argument("--days", type=int, default=28)
    parser.add_argument("--workflow", action="append", dest="workflows",
                        help="repeatable; defaults to the slate and what it reads")
    parser.add_argument("--csv", default=None, help="also write the raw runs here")
    args = parser.parse_args(argv)

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("need GH_TOKEN or GITHUB_TOKEN with actions:read")
        return 1

    until = datetime.now(UTC)
    since = until - timedelta(days=args.days)
    workflows = tuple(args.workflows or DEFAULT_WORKFLOWS)

    print(f"Schedule latency — {args.repo}")
    print(f"window: {since:%Y-%m-%d} to {until:%Y-%m-%d} ({args.days} days)\n")

    everything: list[Run] = []
    for name in workflows:
        try:
            runs = list(scheduled_runs(args.repo, name, token, since=since))
        except urllib.error.HTTPError as exc:
            print(f"{name}\n  could not read: {exc}\n")
            continue
        everything.extend(runs)
        path = Path(".github/workflows") / name
        expected = cron_slots(path, since, until) if path.exists() else 0
        print(name)
        for line in describe(runs, expected):
            print(line)
        print()

    if everything:
        delays = sorted(r.delay_minutes for r in everything)
        print("ALL WORKFLOWS")
        print(f"  runs              {len(delays)}")
        print(f"  delay median      {statistics.median(delays):>6.0f} min")
        print(f"  delay p90         {delays[int(len(delays) * 0.9) - 1]:>6.0f} min")
        print(f"  delay max         {delays[-1]:>6.0f} min")

    if args.csv and everything:
        import csv as _csv

        dest = Path(args.csv)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w", newline="", encoding="utf-8") as handle:
            writer = _csv.writer(handle)
            writer.writerow(["workflow", "run_number", "created_at",
                             "started_at", "delay_minutes", "conclusion", "url"])
            for run in sorted(everything, key=lambda r: r.created_at):
                writer.writerow([
                    run.workflow, run.run_number,
                    run.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    run.started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    f"{run.delay_minutes:.1f}", run.conclusion, run.url,
                ])
        print(f"\nraw runs → {dest}")
    return 0


if __name__ == "__main__":  # pragma: no cover - the CLI seam
    raise SystemExit(main())
