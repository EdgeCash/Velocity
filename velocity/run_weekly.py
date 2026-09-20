"""The weekly orchestrator — one command from banked data to Excel-ready CSV.

    python -m velocity.run_weekly --slate-dir artifacts/slate

The pipeline it drives already exists; what did not exist was a way to run it
end to end without reading a workflow file. Each stage is a named step, each
step logs one structured line, and a step that fails does not take the rest
of the run with it — because the most common failure in a live week is one
provider being down, and a props outage should cost the props, not the board.

    refresh   scripts/refresh_datasets.py   committed datasets
    slate     scripts/run_live_slate.py     ratings, 100k sims, projections,
                                            props, intel, staked slate
    dfs       scripts/build_dfs_lineup.py   DK pool, lineups, distributions
    export    velocity/export               the six CSVs Excel reads

``export`` runs in-process and last, over whatever the earlier steps left in
the artifact folder. That ordering is the point: the export step is seconds,
so a board can be re-exported from banked artifacts without paying for a
thirty-minute engine run, which is the whole latency argument in
docs/HYBRID_MIGRATION_PLAN.md §5.

Exit status is 0 when every requested step succeeded, 1 otherwise. The log is
JSON Lines on stdout — one object per step, parseable by whatever reads the
Actions log — with a human summary at the end.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from velocity.export.artifacts import (
    collect,
    load_board,
    newest_stamp,
    stamp_to_timestamp,
)
from velocity.export.dashboard import export_dashboard
from velocity.export.dfs import export_dfs
from velocity.export.games import export_games
from velocity.export.meta import EXPORT_DIR, ExportMeta
from velocity.export.plays import export_plays
from velocity.export.props import export_props
from velocity.export.readiness import assess, export_readiness, utc_now_default
from velocity.export.workbook import WORKBOOK_NAME, build_workbook

STEPS: tuple[str, ...] = ("refresh", "slate", "dfs", "export")
DEFAULT_LEAGUES: tuple[str, ...] = ("nfl", "ncaaf")

# Every artifact family the export step reads, and the export it feeds.
# Named here rather than inline so a missing family is reported as a missing
# INPUT — "no prop board this week" — instead of surfacing later as a column
# of blanks nobody can explain.
ARTIFACT_FAMILIES: tuple[tuple[str, str, str], ...] = (
    # (key this module uses, the stamped family's prefix, what it feeds)
    ("games", "games", "games.csv"),
    ("projections", "projections", "games.csv"),
    ("distributions", "distributions", "games.csv"),
    ("weather", "weather", "games.csv"),
    ("slate", "slate", "games.csv / plays.csv"),
    ("intel", "intel", "games.csv / plays.csv"),
    ("props", "slate_{league}_props", "props.csv / plays.csv"),
    ("prop_dist", "prop_dist", "props.csv"),
    ("dfs_pool", "dfs_pool", "dfs.csv"),
    ("dfs_dist", "dfs_dist", "dfs.csv"),
    ("record", "record", "dashboard.csv"),
)


@dataclass
class StepResult:
    """What one step did, for the log and the exit status."""

    step: str
    status: str  # "ok" | "failed" | "skipped"
    seconds: float
    detail: str = ""
    outputs: list[str] = field(default_factory=list)

    def as_json(self) -> str:
        return json.dumps({
            "step": self.step,
            "status": self.status,
            "seconds": round(self.seconds, 2),
            "detail": self.detail,
            "outputs": self.outputs,
        }, sort_keys=True)


def log(result: StepResult) -> StepResult:
    """Emit one structured line and hand the result back."""
    print(result.as_json(), flush=True)
    return result


def run_command(step: str, command: Sequence[str], *, dry_run: bool = False) -> StepResult:
    """Run one subprocess step, converting any failure into a result.

    A non-zero exit is data, not an exception: the orchestrator's contract is
    that it always reaches the export step, and a collector that 404s should
    not be able to break that.
    """
    started = time.monotonic()
    if dry_run:
        return StepResult(step, "skipped", 0.0, "dry run: " + " ".join(command))
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, no shell, fixed program
            list(command), check=False, capture_output=True, text=True,
        )
    except OSError as exc:  # the interpreter or script is not where we think
        return StepResult(step, "failed", time.monotonic() - started, str(exc))
    elapsed = time.monotonic() - started
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        detail = tail[-1] if tail else f"exit {proc.returncode}"
        return StepResult(step, "failed", elapsed, f"exit {proc.returncode}: {detail}")
    return StepResult(step, "ok", elapsed, " ".join(command))


def refresh_command(args: argparse.Namespace) -> list[str]:
    return [
        sys.executable, "scripts/refresh_datasets.py",
        "--league", str(args.refresh_league),
        "--out", str(args.data_dir),
    ]


def slate_command(args: argparse.Namespace, league: str) -> list[str]:
    command = [
        sys.executable, "scripts/run_live_slate.py",
        "--league", league,
        "--data", str(Path(args.data_dir) / league),
        "--out", str(args.slate_dir),
        "--bankroll", str(args.bankroll),
        "--min-edge", str(args.min_edge),
    ]
    if args.snapshot_dir:
        command += ["--odds-dir", str(args.snapshot_dir)]
    return command


def dfs_command(args: argparse.Namespace, league: str) -> list[str] | None:
    """The DFS command, or ``None`` when its two required inputs are absent.

    ``build_dfs_lineup.py`` requires a DK salary snapshot and a FantasyPros
    projection frame, and both are paid/keyed feeds banked by their own
    collectors. Without them the step is genuinely not runnable, and argparse
    would say so as a usage error — which reads in the log like the
    orchestrator is broken rather than like the week has no salaries yet.
    """
    if not args.salaries or not args.fp:
        return None
    return [
        sys.executable, "scripts/build_dfs_lineup.py",
        "--league", league,
        "--salaries", str(args.salaries),
        "--fp", str(args.fp),
        "--out", str(args.slate_dir),
    ]


def load_artifacts(slate_dir: Path, leagues: Sequence[str]) -> dict[str, pd.DataFrame]:
    """Every family the export step reads, newest stamp per league."""
    wanted = tuple(leagues)
    return {
        key: collect(slate_dir, prefix, wanted)
        for key, prefix, _ in ARTIFACT_FAMILIES
    }


def _season_games(data_dir: Path, leagues: Sequence[str]) -> pd.DataFrame | None:
    """A committed schedule to read season and week off, or ``None``.

    The first league that has one wins. Football's weeks line up across NFL
    and NCAAF closely enough for a masthead; where they do not, the cell is
    the NFL's, which is the one the workbook's Historical tab keys on.
    """
    for league in leagues:
        path = Path(data_dir) / league / "games.parquet"
        if path.exists():
            try:
                return pd.read_parquet(path)
            except Exception:  # noqa: BLE001 - a label, never worth raising
                continue
    return None


def export_step(args: argparse.Namespace) -> StepResult:
    """Build all six CSVs from whatever the artifact folder holds."""
    started = time.monotonic()
    slate_dir = Path(args.slate_dir)
    out_dir = Path(args.out)
    leagues = tuple(args.leagues)
    try:
        frames = load_artifacts(slate_dir, leagues)
        stamp = newest_stamp(frames)
        when = stamp_to_timestamp(stamp) if stamp else None
        meta = ExportMeta.from_games(
            _season_games(Path(args.data_dir), leagues), generated_at=when
        )

        games = frames["games"]
        slate = frames["slate"]
        props = frames["props"]
        plays_frame = None

        # The market side of the betting card. Without it build_games falls
        # back to the staked slate, which carries a price only for the bets
        # that cleared the gate — so every unbet game exports with no market
        # spread, total or moneyline at all, and the card becomes a column of
        # projections with nothing to compare them to. Measured on a real run:
        # 4 of 51 games had a market total and none had a spread.
        board = load_board(args.odds_dir, games if not games.empty else None)

        paths = [
            export_games(
                meta, games if not games.empty else None,
                projections=frames["projections"] if not frames["projections"].empty else None,
                out_dir=out_dir,
                board=board if not board.empty else None,
                slate=slate if not slate.empty else None,
                distributions=(frames["distributions"]
                               if not frames["distributions"].empty else None),
                weather=frames["weather"] if not frames["weather"].empty else None,
                intel=frames["intel"] if not frames["intel"].empty else None,
                bankroll=args.bankroll,
            ),
            export_props(
                meta, props if not props.empty else None,
                frames["prop_dist"] if not frames["prop_dist"].empty else None,
                out_dir=out_dir,
            ),
        ]
        paths.extend(export_dfs(
            meta,
            frames["dfs_pool"] if not frames["dfs_pool"].empty else None,
            frames["dfs_dist"] if not frames["dfs_dist"].empty else None,
            out_dir=out_dir,
        ))
        paths.append(export_plays(
            meta, slate if not slate.empty else None,
            props=props if not props.empty else None,
            games=games if not games.empty else None,
            projections=frames["projections"] if not frames["projections"].empty else None,
            intel=frames["intel"] if not frames["intel"].empty else None,
            bankroll=args.bankroll,
            out_dir=out_dir,
        ))

        # The dashboard's "top" sections read the files just written, so the
        # summary and the boards can never disagree about what the top play
        # was.
        plays_frame = pd.read_csv(out_dir / "plays.csv")
        props_frame = pd.read_csv(out_dir / "props.csv")
        dfs_frame = pd.read_csv(out_dir / "dfs.csv")
        paths.append(export_dashboard(
            meta,
            frames["record"] if not frames["record"].empty else None,
            plays=plays_frame,
            props=props_frame,
            dfs=dfs_frame,
            out_dir=out_dir,
        ))

        # ...and the same six tables as one finished workbook, read back from
        # the CSVs rather than rebuilt, so the file and the files cannot
        # disagree. This is the only artifact usable on a tablet: Excel for
        # iPad has no Power Query (docs/EXCEL_IPAD.md).
        # Is the board usable, and usable in time? A run that finished is not
        # the same claim as a board an operator can bet before kickoff, and
        # until now only the first one was ever stated (velocity/export/readiness.py).
        readiness = assess(
            {
                "games": games, "projections": frames["projections"],
                "market": board, "plays": plays_frame, "props": props_frame,
                "dfs_pool": frames["dfs_pool"], "weather": frames["weather"],
                "record": frames["record"],
            },
            now=utc_now_default(),
            generated_at=when,
            details={
                "market": (f"{len(board)} line(s) from the odds archive"
                           if not board.empty else
                           "no odds archive passed (--odds-dir)"),
            },
        )
        paths.append(export_readiness(meta, readiness, out_dir=out_dir))

        paths.append(build_workbook(
            out_dir / WORKBOOK_NAME, meta,
            games=pd.read_csv(out_dir / "games.csv"),
            props=props_frame,
            dfs=dfs_frame,
            dfs_optimizer=pd.read_csv(out_dir / "dfs_optimizer.csv"),
            plays=plays_frame,
            dashboard=pd.read_csv(out_dir / "dashboard.csv"),
            readiness=readiness,
        ))
    except Exception as exc:  # noqa: BLE001 - the orchestrator reports, never raises
        return StepResult("export", "failed", time.monotonic() - started, repr(exc))

    empty = [key for key, _, _ in ARTIFACT_FAMILIES
             if frames.get(key, pd.DataFrame()).empty]
    detail = f"stamp {stamp or 'unknown'}"
    # Say which market source the card got. "Board is empty" is the difference
    # between a full betting card and a page of projections, and it is not
    # visible in the CSV — every cell just reads blank.
    detail += (f"; board {len(board)} line(s) from the odds archive"
               if not board.empty
               else "; NO odds archive — market numbers come from the staked "
                    "slate only (pass --odds-dir)")
    if empty:
        detail += f"; no artifacts for: {', '.join(sorted(set(empty)))}"
    # The verdict leads the line: it is the one part an operator scanning a
    # green job actually needs to see.
    detail = f"{readiness.summary_line()} | {detail}"
    return StepResult("export", "ok", time.monotonic() - started, detail,
                      [str(p) for p in paths])


def plan(args: argparse.Namespace) -> list[tuple[str, list[str] | None]]:
    """The steps this invocation will run, in order.

    A ``None`` command means either the in-process export step or a
    subprocess step whose inputs are missing; :func:`run` tells them apart by
    the step name, so an unrunnable DFS step is logged as ``skipped`` with
    the reason rather than run and failed.
    """
    steps: list[tuple[str, list[str] | None]] = []
    if "refresh" in args.steps:
        steps.append(("refresh", refresh_command(args)))
    if "slate" in args.steps:
        steps.extend((f"slate:{lg}", slate_command(args, lg)) for lg in args.leagues)
    if "dfs" in args.steps:
        steps.extend((f"dfs:{lg}", dfs_command(args, lg)) for lg in args.leagues)
    if "export" in args.steps:
        steps.append(("export", None))
    return steps


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m velocity.run_weekly",
        description="Run the weekly pipeline and write the Excel exports.",
    )
    parser.add_argument("--slate-dir", default="artifacts/slate",
                        help="where the stamped artifacts are read and written")
    parser.add_argument("--out", default=str(EXPORT_DIR),
                        help="where the CSVs land (default: datasets/exports)")
    parser.add_argument("--data-dir", default="datasets",
                        help="committed datasets root")
    parser.add_argument("--snapshot-dir", default=None,
                        help="banked odds snapshots, for the slate's board reuse")
    parser.add_argument("--odds-dir", default=None,
                        help="the hourly odds archive (odds_lines_*.parquet). Without "
                             "it games.csv can only show market numbers for games that "
                             "earned a bet")
    parser.add_argument("--salaries", default=None,
                        help="normalized DK salaries parquet (the dfs step needs it)")
    parser.add_argument("--fp", default=None,
                        help="FantasyPros projections parquet (the dfs step needs it)")
    parser.add_argument("--refresh-league", default="both",
                        choices=["nfl", "ncaaf", "mlb", "wnba", "both", "all"],
                        help="what the refresh step pulls ('both' = the football pair)")
    parser.add_argument("--leagues", nargs="+", default=list(DEFAULT_LEAGUES))
    parser.add_argument("--steps", nargs="+", default=list(STEPS), choices=list(STEPS),
                        help="which steps to run (default: all)")
    parser.add_argument("--bankroll", type=float, default=100.0)
    parser.add_argument("--min-edge", type=float, default=0.02)
    parser.add_argument("--strict", action="store_true",
                        help="stop at the first failing step instead of carrying on")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan without running anything")
    return parser


def run(args: argparse.Namespace) -> list[StepResult]:
    """Execute the plan, logging each step. Never raises on a step failure."""
    results: list[StepResult] = []
    for name, command in plan(args):
        if command is None and name.startswith("dfs"):
            result = StepResult(
                name, "skipped", 0.0,
                "no DK salary snapshot (--salaries) or FantasyPros frame (--fp)",
            )
        elif command is None:
            result = (StepResult(name, "skipped", 0.0, "dry run: in-process export")
                      if args.dry_run else export_step(args))
        else:
            result = run_command(name, command, dry_run=args.dry_run)
        results.append(log(result))
        if args.strict and result.status == "failed":
            remaining = [n for n, _ in plan(args)][len(results):]
            results.extend(log(StepResult(n, "skipped", 0.0, "strict: an earlier step failed"))
                           for n in remaining)
            break
    return results


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    results = run(args)
    failed = [r for r in results if r.status == "failed"]
    ok = sum(1 for r in results if r.status == "ok")
    print(f"\nweekly run: {ok} ok, {len(failed)} failed, "
          f"{sum(1 for r in results if r.status == 'skipped')} skipped")
    for result in failed:
        print(f"  FAILED {result.step}: {result.detail}")
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover - the CLI seam
    raise SystemExit(main())
