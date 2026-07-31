"""Grade the most recent prior day's slate → the email's model-status record.

The workflow downloads previous runs' private artifacts into a folder; this
script finds the **latest slate from the most recent prior date** (US/Central —
the operator's day, so both daily runs report the same finished day), grades it
against StatsAPI, and writes ``record_<league>_<stamp>.parquet`` into the slate
output folder, where the email renderer picks it up.

Grading sources (all free StatsAPI):
* schedule finals → full-game markets,
* per-game linescores → F5 and first-inning (NRFI/YRFI) segments,
* per-game box scores → player props and parlay prop legs.

Anything that cannot be graded honestly stays ``pending``. Every failure mode
(no prior slate, empty day, a feed down) exits 0 — the record section is
additive and must never block the slate email.

    python scripts/grade_yesterday.py --prev-dir artifacts/previous \
        --out-dir artifacts/slate --league mlb
"""

from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

_STAMP = r"(\d{8}T\d{6}Z)"
_OPERATOR_TZ = ZoneInfo("America/Chicago")


def _stamps(prev_dir: Path, league: str) -> dict[str, dict[str, Path]]:
    """All persisted frames under ``prev_dir``, grouped by run stamp.

    Returns ``{stamp: {kind: path}}`` for kinds ``slate``/``props``/``parlays``/
    ``games``; a newer duplicate of the same (stamp, kind) wins arbitrarily —
    they are identical files from the same run.
    """
    lg = re.escape(league)
    patterns = {
        "slate": rf"slate_{lg}_{_STAMP}\.parquet",
        "props": rf"slate_{lg}_props_{_STAMP}\.parquet",
        "parlays": rf"slate_{lg}_parlays_{_STAMP}\.parquet",
        "games": rf"games_{lg}_{_STAMP}\.parquet",
        "projections": rf"projections_{lg}_{_STAMP}\.parquet",
        "distributions": rf"distributions_{lg}_{_STAMP}\.parquet",
    }
    out: dict[str, dict[str, Path]] = {}
    for path in prev_dir.rglob("*.parquet"):
        for kind, pattern in patterns.items():
            match = re.fullmatch(pattern, path.name)
            if match:
                out.setdefault(match.group(1), {})[kind] = path
    return out


def _pick_prior_stamp(stamps: dict[str, dict[str, Path]], now_utc: datetime) -> str | None:
    """The latest stamp whose operator-local date precedes today's."""
    today = now_utc.astimezone(_OPERATOR_TZ).date()
    prior = [
        s for s in stamps
        if datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        .astimezone(_OPERATOR_TZ).date() < today
    ]
    return max(prior) if prior else None


def _load(paths: dict[str, Path], kind: str) -> pd.DataFrame | None:
    path = paths.get(kind)
    return pd.read_parquet(path) if path is not None else None


def _newest_cumulative(prev_dir: Path, league: str) -> pd.DataFrame | None:
    """The season record chain from the newest downloaded artifact carrying one."""
    pattern = rf"cumulative_record_{re.escape(league)}_{_STAMP}\.parquet"
    matches = sorted(
        (p for p in prev_dir.rglob("*.parquet") if re.fullmatch(pattern, p.name)),
        key=lambda p: p.name,
    )
    return pd.read_parquet(matches[-1]) if matches else None


def main() -> None:  # pragma: no cover - network orchestration (pure parts live in report/)
    parser = argparse.ArgumentParser(description="Grade the previous day's slate")
    parser.add_argument("--prev-dir", required=True, help="downloaded previous artifacts")
    parser.add_argument("--out-dir", required=True, help="folder to write the record parquet")
    parser.add_argument("--league", default="mlb")
    args = parser.parse_args()

    from velocity.backtest.props_mlb import grade_prop_ledger
    from velocity.ingest.mlb import load_boxscore, load_linescore, load_schedule
    from velocity.report.daily_record import (
        accumulate_record,
        build_daily_record,
        empty_record,
        grade_parlay_frame,
        record_headline,
    )
    from velocity.report.results import (
        attach_segment_scores,
        finals_for_slate,
        gamepks_for_slate,
    )
    from velocity.report.scorecard import grade_slate
    from velocity.wagering.props_slate import build_name_index

    now = datetime.now(UTC)
    stamps = _stamps(Path(args.prev_dir), args.league)
    stamp = _pick_prior_stamp(stamps, now)
    if stamp is None:
        print("no prior-day slate found in the downloaded artifacts; skipping record")
        return
    paths = stamps[stamp]
    slate = _load(paths, "slate")
    props = _load(paths, "props")
    parlays = _load(paths, "parlays")
    games_map = _load(paths, "games")
    n_plays = sum(0 if f is None else len(f) for f in (slate, props, parlays))
    print(f"grading slate {stamp}: {n_plays} play(s)")

    record = None
    finals = None
    if n_plays == 0 or games_map is None or games_map.empty:
        record = empty_record()
        record["slate_date"] = pd.Timestamp(datetime.strptime(stamp, "%Y%m%dT%H%M%SZ"))
    else:
        # Finals + gamePk bridge for the slate's day (±1 day covers UTC drift).
        slate_day = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").date()
        start = (slate_day - timedelta(days=1)).isoformat()
        end = (slate_day + timedelta(days=1)).isoformat()
        schedule = load_schedule(start, end)
        finals = finals_for_slate(games_map, schedule)
        pk_map = gamepks_for_slate(games_map, schedule)

        linescores: dict[str, dict[str, float | None]] = {}
        boxscores_frames: list[pd.DataFrame] = []
        for row in pk_map.to_dict("records"):
            pk, gid = str(row["game_pk"]), str(row["game_id"])
            try:
                linescores[pk] = load_linescore(pk)
            except Exception as exc:  # noqa: BLE001 - one game's feed never blocks the rest
                print(f"linescore {pk} skipped ({exc})")
            try:
                boxscores_frames.append(load_boxscore(pk, game_id=gid))
            except Exception as exc:  # noqa: BLE001
                print(f"boxscore {pk} skipped ({exc})")
        finals = attach_segment_scores(finals, pk_map, linescores)
        boxscores = (
            pd.concat(boxscores_frames, ignore_index=True)
            if boxscores_frames
            else pd.DataFrame(columns=["game_id", "player_id", "player_name"])
        )

        games_graded = None if slate is None or slate.empty else grade_slate(slate, finals)
        props_graded = None
        if props is not None and not props.empty:
            name_to_id = build_name_index(boxscores) if not boxscores.empty else {}
            props_graded = grade_prop_ledger(props, boxscores, name_to_id)
        parlays_graded = (
            None if parlays is None or parlays.empty
            else grade_parlay_frame(parlays, finals, boxscores)
        )

        matchups = {
            str(r["game_id"]): f"{r['away_team']} @ {r['home_team']}"
            for r in games_map.to_dict("records")
        }
        record = build_daily_record(
            games_graded, props_graded, parlays_graded,
            matchups=matchups,
            slate_date=datetime.strptime(stamp, "%Y%m%dT%H%M%SZ"),
        )

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    out_stamp = now.strftime("%Y%m%dT%H%M%SZ")
    dest = out / f"record_{args.league}_{out_stamp}.parquet"
    record.to_parquet(dest, index=False)
    print(record_headline(record))
    print(f"wrote {len(record)} graded row(s) to {dest}")

    # Season chain: fold the day into the newest cumulative record and carry it
    # forward in this run's artifact (the next run downloads it and continues).
    cumulative = accumulate_record(_newest_cumulative(Path(args.prev_dir), args.league), record)
    cumulative.to_parquet(
        out / f"cumulative_record_{args.league}_{out_stamp}.parquet", index=False
    )
    print(f"season record: {len(cumulative)} graded row(s) accumulated")

    # Post-game graphics — the Sim Check cards (actual result on the pregame
    # distribution) and the model record card. Best-effort: rendering trouble
    # never blocks the graded record itself.
    try:
        from velocity.report.sim_check import build_sim_checks
        from velocity.report.social_png import render_record_card, render_sim_checks

        projections_frame = _load(paths, "projections")
        distributions = _load(paths, "distributions")
        if (
            projections_frame is not None
            and distributions is not None
            and finals is not None
            and games_map is not None
        ):
            checks = build_sim_checks(projections_frame, distributions, finals, games_map)
            rendered = render_sim_checks(checks, out, out_stamp,
                                         asset_dir=out / ".assets")
            print(f"rendered {len(rendered)} sim check card(s)")
        settled = record[record["result"].isin(["win", "loss", "push"])]
        if not settled.empty:
            when = record["slate_date"].dropna()
            date_label = "" if when.empty else pd.Timestamp(when.iloc[0]).strftime("%b %-d")
            card_dest = out / f"recordcard_{args.league}_{out_stamp}.png"
            render_record_card(record, cumulative, card_dest, date_label=date_label)
            print(f"rendered model record card to {card_dest}")
    except Exception as exc:  # noqa: BLE001
        print(f"post-game cards skipped: {exc}")


if __name__ == "__main__":
    main()
