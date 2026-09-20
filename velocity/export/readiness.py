"""Is the board actually usable, and is it usable *in time*?

A live-slate run exits 0 when the engine finished. It exits 0 whether the
prop board was fresh enough to price or four hours stale, whether the DFS
pool reached disk or not, whether the weather fetch returned anything. The
workflow goes green, the artifact uploads, and the first anyone knows about
a half-empty board is opening the workbook — possibly after kickoff.

Measured on run #132 (2026-09-20): green job, no props (the collector's
15:19 cron started at 18:12, so the newest board was 279 minutes old against
a 75-minute bar), no DFS pool, no weather. Nothing in the run said so.

This module is the missing statement. It reads what the export actually
produced and answers two questions an operator has before kickoff:

* **Is every surface I rely on here?** — per surface: present, how many rows.
* **Is it recent enough, given when the games start?** — the age of the
  numbers against the time until the next kickoff on the card.

It is pure: frames in, a verdict out. Nothing here fetches, fixes or
retries — it reports, and the caller decides whether that is a red build,
a line on the dashboard, or both.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from velocity.export.meta import EXPORT_DIR, ExportMeta, utc_now, write_csv

READINESS_COLUMNS: tuple[str, ...] = (
    "surface",
    "label",
    "status",
    "rows",
    "required",
    "detail",
    "verdict",
    "board_age_minutes",
    "minutes_to_kickoff",
    "generated_at",
    "season",
    "week",
)

# The three verdicts, worst first when combining.
NOT_READY = "NOT READY"
DEGRADED = "DEGRADED"
READY = "READY"
_VERDICT_RANK = {NOT_READY: 0, DEGRADED: 1, READY: 2}

# Per-surface status values.
OK = "ok"
MISSING = "missing"
STALE = "stale"

# How old the numbers may be before the board is called stale. Four hours is
# deliberately generous: the slate's own banked-board reuse bar is 75 minutes,
# but that governs whether to PAY for a fresh pull, which is a different
# question from whether a human should act on what is already here. A board
# older than this has almost certainly missed a day's line movement.
DEFAULT_MAX_AGE_MIN = 240.0

# How close to kickoff the board must be to count as "in time". Inside this
# window a stale board is not merely old, it is too late to replace: a live
# slate run takes 20-30 minutes and a scheduled one starts 1h48-3h00 late
# (docs/LATENCY_AUDIT.md), so the operator needs to know now, by hand.
DEFAULT_KICKOFF_WARN_MIN = 90.0


@dataclass(frozen=True)
class Surface:
    """One thing the board is made of, and whether the card needs it."""

    key: str
    label: str
    required: bool


# Required means: without this the betting card is not a betting card. The
# optional ones are whole products (props, DFS) whose absence degrades the
# run without invalidating what is there — stated, never silently dropped.
SURFACES: tuple[Surface, ...] = (
    Surface("games", "Board (games)", True),
    Surface("projections", "Model projections", True),
    Surface("market", "Market lines", True),
    Surface("plays", "Curated plays", False),
    Surface("props", "Player props", False),
    Surface("dfs_pool", "DFS pool", False),
    Surface("weather", "Weather", False),
    Surface("record", "Settled record", False),
)


@dataclass(frozen=True)
class SurfaceStatus:
    """What one surface contributed to this run."""

    key: str
    label: str
    status: str
    rows: int
    required: bool
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == OK


@dataclass(frozen=True)
class Readiness:
    """The whole verdict, and the timing that qualifies it."""

    verdict: str
    surfaces: tuple[SurfaceStatus, ...]
    board_age_minutes: float | None = None
    minutes_to_kickoff: float | None = None
    next_kickoff: pd.Timestamp | None = None

    @property
    def missing(self) -> tuple[SurfaceStatus, ...]:
        """Surfaces that contributed nothing. Stale is a different complaint."""
        return tuple(s for s in self.surfaces if s.status == MISSING)

    @property
    def stale(self) -> tuple[SurfaceStatus, ...]:
        return tuple(s for s in self.surfaces if s.status == STALE)

    @property
    def unhealthy(self) -> tuple[SurfaceStatus, ...]:
        return tuple(s for s in self.surfaces if not s.ok)

    @property
    def missing_required(self) -> tuple[SurfaceStatus, ...]:
        return tuple(s for s in self.unhealthy if s.required)

    def summary_line(self) -> str:
        """One line an operator can act on, for a log or a spreadsheet cell."""
        bits = [self.verdict]
        if self.board_age_minutes is not None:
            bits.append(f"numbers {self.board_age_minutes:.0f} min old")
        if self.minutes_to_kickoff is not None:
            bits.append(
                f"next kickoff in {self.minutes_to_kickoff:.0f} min"
                if self.minutes_to_kickoff >= 0
                else f"first kickoff {abs(self.minutes_to_kickoff):.0f} min ago"
            )
        if self.stale:
            bits.append("stale: " + ", ".join(s.label for s in self.stale))
        if self.missing:
            bits.append("missing: " + ", ".join(s.label for s in self.missing))
        return " · ".join(bits)


def utc_now_default() -> pd.Timestamp:
    """Now, in UTC — one clock for the whole assessment."""
    return utc_now()


def _rows(frame: pd.DataFrame | None) -> int:
    return 0 if frame is None or frame.empty else int(len(frame))


def _next_kickoff(games: pd.DataFrame | None, now: pd.Timestamp) -> pd.Timestamp | None:
    """The earliest kickoff on the card that has not happened yet.

    Falls back to the earliest kickoff at all when every game has started —
    "the first one went off 40 minutes ago" is the answer the operator needs
    then, not silence.
    """
    if games is None or games.empty or "kickoff" not in games.columns:
        return None
    kicks = pd.to_datetime(games["kickoff"], errors="coerce", utc=True).dropna()
    if kicks.empty:
        return None
    ahead = kicks[kicks >= now]
    return pd.Timestamp(ahead.min() if not ahead.empty else kicks.min())


def assess(  # noqa: PLR0913 - one argument per thing being judged
    frames: Mapping[str, pd.DataFrame | None],
    *,
    now: pd.Timestamp,
    generated_at: pd.Timestamp | str | None = None,
    max_age_min: float = DEFAULT_MAX_AGE_MIN,
    kickoff_warn_min: float = DEFAULT_KICKOFF_WARN_MIN,
    details: Mapping[str, str] | None = None,
    surfaces: Sequence[Surface] = SURFACES,
) -> Readiness:
    """Judge one run's output. ``frames`` is keyed by surface.

    ``details`` supplies a per-surface explanation the caller already knows
    and this module cannot infer — most usefully *why* a surface is empty,
    which is the difference between "no props tonight" and "the prop board
    was four hours stale".
    """
    notes = dict(details or {})
    statuses: list[SurfaceStatus] = []
    for surface in surfaces:
        rows = _rows(frames.get(surface.key))
        statuses.append(SurfaceStatus(
            key=surface.key,
            label=surface.label,
            status=OK if rows else MISSING,
            rows=rows,
            required=surface.required,
            detail=notes.get(surface.key, ""),
        ))

    age: float | None = None
    if generated_at is not None:
        made = pd.Timestamp(generated_at)
        if made.tzinfo is None:
            made = made.tz_localize("UTC")
        age = float((now - made).total_seconds() / 60.0)

    kickoff = _next_kickoff(frames.get("games"), now)
    to_kick = (
        float((kickoff - now).total_seconds() / 60.0) if kickoff is not None else None
    )

    verdict = READY
    if any(s.required and not s.ok for s in statuses):
        verdict = NOT_READY
    elif age is not None and age > max_age_min:
        # Old numbers are only "not ready" when there is no time to replace
        # them; otherwise they are a run that should be redone, which is a
        # different instruction.
        verdict = NOT_READY if (to_kick is not None and to_kick < kickoff_warn_min) \
            else DEGRADED
    elif any(not s.ok for s in statuses):
        verdict = DEGRADED

    if age is not None and age > max_age_min:
        statuses = [
            s if s.key != "games" else SurfaceStatus(
                s.key, s.label, STALE if s.ok else s.status, s.rows, s.required,
                s.detail or f"numbers are {age:.0f} min old (bar {max_age_min:.0f})",
            )
            for s in statuses
        ]

    return Readiness(
        verdict=verdict,
        surfaces=tuple(statuses),
        board_age_minutes=age,
        minutes_to_kickoff=to_kick,
        next_kickoff=kickoff,
    )


def readiness_frame(readiness: Readiness) -> pd.DataFrame:
    """The verdict as a table — one row per surface."""
    base = [c for c in READINESS_COLUMNS if c not in ("generated_at", "season", "week")]
    rows = [
        {
            "surface": s.key,
            "label": s.label,
            "status": s.status,
            "rows": s.rows,
            "required": "yes" if s.required else "no",
            "detail": s.detail,
            "verdict": readiness.verdict,
            "board_age_minutes": (None if readiness.board_age_minutes is None
                                  else round(readiness.board_age_minutes, 1)),
            "minutes_to_kickoff": (None if readiness.minutes_to_kickoff is None
                                   else round(readiness.minutes_to_kickoff, 1)),
        }
        for s in readiness.surfaces
    ]
    return pd.DataFrame(rows, columns=base)


def export_readiness(
    meta: ExportMeta, readiness: Readiness, *, out_dir: str | Path = EXPORT_DIR
) -> Path:
    """Write ``readiness.csv``. Returns the path."""
    return write_csv(
        readiness_frame(readiness), Path(out_dir) / "readiness.csv",
        READINESS_COLUMNS, meta,
    )


def verdict_of(path: str | Path) -> str:
    """Read back a written ``readiness.csv`` and return its verdict.

    The gate step in CI runs after the artifact upload, in a fresh process,
    so it re-reads rather than recomputing: a gate that recalculates can
    disagree with the file the operator is holding.
    """
    frame = pd.read_csv(path)
    if frame.empty or "verdict" not in frame.columns:
        return NOT_READY
    verdicts = [str(v) for v in frame["verdict"].dropna().unique()]
    if not verdicts:
        return NOT_READY
    return min(verdicts, key=lambda v: _VERDICT_RANK.get(v, 0))


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - CLI seam
    """``python -m velocity.export.readiness --check <readiness.csv>``.

    Exit 0 when the board is READY or DEGRADED, 1 when it is NOT READY, so a
    workflow can go red on a board that is missing what a card needs — and
    stay green on one that merely has no props tonight.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="python -m velocity.export.readiness")
    parser.add_argument("--check", required=True, help="path to readiness.csv")
    parser.add_argument("--strict", action="store_true",
                        help="also fail on DEGRADED (any missing surface at all)")
    args = parser.parse_args(argv)

    path = Path(args.check)
    if not path.exists():
        print(f"readiness: {path} not written — the export did not complete")
        return 1
    verdict = verdict_of(path)
    frame = pd.read_csv(path)
    for row in frame.to_dict("records"):
        mark = "ok " if str(row.get("status")) == OK else "!! "
        detail = f" — {row['detail']}" if str(row.get("detail") or "") else ""
        print(f"  {mark}{row['label']}: {row['status']} ({row['rows']} rows){detail}")
    print(f"readiness: {verdict}")
    if verdict == NOT_READY:
        return 1
    return 1 if (args.strict and verdict == DEGRADED) else 0


if __name__ == "__main__":  # pragma: no cover - the CLI seam
    raise SystemExit(main())
