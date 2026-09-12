"""Grade the previous day's DraftKings entries against the box scores.

The DFS surface builds six boards a day and, until this, kept no receipt of
any of them: the optimizers are exact and the projections are backtested, but
nothing ever asked what the lineups that actually went out scored. This is
that question, answered the same way the betting slate's record answers it —
read yesterday's banked entries, join the realized DK points, write the
record parquet beside them.

    python scripts/grade_dfs.py --prev-dir artifacts/previous \
        --out-dir artifacts/slate --league mlb

Actuals are free and keyless in every league that has them:

* **MLB** — statsapi boxscores for the graded day, through the same two
  extractors that bank the datasets (batters and starting pitchers), scored
  by :mod:`velocity.models.dfs_mlb`.
* **NFL** — the nflverse weekly release, scored by
  :mod:`velocity.models.dfs_nfl`, with kickoffs off the schedule.
* **WNBA** — the wehoop player-box release, scored by
  :mod:`velocity.models.dfs_wnba`.

NCAAF builds entries but cannot be graded: there is no free college
player-box-score feed in this repo, and a grade against guessed actuals would
be worse than no grade. Those boards report ungraded and say why.

Only entries whose projection is in **DK points** are graded. DK's Single
Stat formats project touchdowns or home runs, which are not the same unit and
must not be summed against a fantasy score; they are skipped by name.

Every failure mode exits 0. The record is additive, and a feed being down
must never cost the day its lineups.
"""

from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from velocity.dfs.backtest import grade_lineup_frame, lineup_record

_STAMP = r"(\d{8}T\d{6}Z)"
_OPERATOR_TZ = ZoneInfo("America/Chicago")

# The banked entry frames. All three carry one row per roster slot with the
# build's projection in ``points`` and the player's game in ``kickoff``.
ENTRY_KINDS = ("dfs_lineup", "dfs_showdown", "dfs_tiered")
# Leagues with a free player-level actuals feed. NCAAF is absent on purpose.
GRADEABLE = ("mlb", "nfl", "wnba")


def entry_paths(prev_dir: Path, league: str) -> dict[str, list[Path]]:
    """Every banked entry frame under ``prev_dir``, grouped by run stamp."""
    lg = re.escape(league)
    patterns = [rf"{kind}_{lg}_{_STAMP}\.parquet" for kind in ENTRY_KINDS]
    out: dict[str, list[Path]] = {}
    for path in prev_dir.rglob("*.parquet"):
        for pattern in patterns:
            match = re.fullmatch(pattern, path.name)
            if match:
                out.setdefault(match.group(1), []).append(path)
    return out


def latest_prior_day(stamps: list[str], now_utc: datetime) -> list[str]:
    """Every stamp from the most recent operator day before today.

    The DFS workflow runs six times a day and each run banks its own boards,
    so a day's receipt is all of that day's stamps rather than the newest one.
    Grading stops at yesterday: today's boards have not been played.
    """
    today = now_utc.astimezone(_OPERATOR_TZ).date()
    days: dict[Any, list[str]] = {}
    for stamp in stamps:
        day = (datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
               .astimezone(_OPERATOR_TZ).date())
        if day < today:
            days.setdefault(day, []).append(stamp)
    if not days:
        return []
    return sorted(days[max(days)])


def gradeable_entries(paths: list[Path]) -> pd.DataFrame:
    """The banked slot rows whose projection is in DK fantasy points.

    DK's Single Stat boards project a raw stat — touchdowns, home runs — and
    the tiered builder records which by stamping ``unit`` on every row. Summing
    those against a DK score would be adding two different quantities, so they
    are dropped here rather than silently graded.
    """
    frames: list[pd.DataFrame] = []
    for path in sorted(paths):
        frame = pd.read_parquet(path)
        if frame.empty:
            continue
        if "unit" in frame.columns:
            frame = frame[frame["unit"].astype(str) == "DK pts"]
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    entries = pd.concat(frames, ignore_index=True)
    if "format" not in entries.columns:
        entries["format"] = "classic"
    return entries


_Index = dict[tuple[str, Any], dict[str, Any]]


def mlb_day_index(slate_date: datetime) -> _Index:  # pragma: no cover - network
    """(name, date) → realized DK points, from the day's statsapi boxscores.

    One schedule call around the graded day and one boxscore call per game,
    through the same extractors that bank ``datasets/mlb`` — so the actuals a
    lineup is graded against are the ones the model was fitted on.
    """
    from build_mlb_pitching import (
        _BOX_URL,
        _SCHED_URL,
        _get,
        extract_batters,
        extract_starters,
    )
    from velocity.dfs.backtest import player_day_index, prepare_banks
    from velocity.ingest.inseason import normalize_mlb_schedule

    day = slate_date.date()
    payload = _get(_SCHED_URL.format(start=(day - timedelta(days=1)).isoformat(),
                                     end=(day + timedelta(days=1)).isoformat()))
    games = normalize_mlb_schedule(payload, slate_date.year)
    if games.empty:
        return {}
    batters: list[dict[str, object]] = []
    starters: list[dict[str, object]] = []
    for pk in games["game_id"].astype(str):
        try:
            box = _get(_BOX_URL.format(pk=pk))
        except Exception:  # noqa: BLE001 - one bad boxscore never blocks the rest
            continue
        batters.extend(extract_batters(box, pk))
        starters.extend(extract_starters(box, pk))
    if not batters and not starters:
        return {}
    bat, sp, _played = prepare_banks(
        pd.DataFrame(batters), pd.DataFrame(starters), games)
    return player_day_index(bat, sp)


def nfl_day_index(slate_date: datetime) -> tuple[_Index, _Index]:  # pragma: no cover - network
    """(name, date) and (team, date) → realized DK points, from nflverse.

    Two indexes because a DK roster names its defense as a club rather than a
    player, and the weekly PLAYER release has no row for one. The team index
    carries the defenses, scored off the team-week release and the opponent's
    final score (:func:`velocity.dfs.dst.dst_actuals`).
    """
    from velocity.dfs.backtest import nfl_player_day_index, prepare_nfl_weeks
    from velocity.dfs.dst import dst_actuals, dst_day_index
    from velocity.ingest.nfl import load_dfs_weeks, load_schedules, load_team_weeks

    season = slate_date.year if slate_date.month >= 3 else slate_date.year - 1
    weeks = load_dfs_weeks([season])
    if weeks.empty:
        return {}, {}
    schedule = load_schedules([season])
    players = nfl_player_day_index(prepare_nfl_weeks(weeks, schedule))
    defenses = dst_day_index(dst_actuals(load_team_weeks([season]), schedule))
    return players, defenses


def wnba_day_index(slate_date: datetime) -> _Index:  # pragma: no cover - network
    """(name, date) → realized DK points, from the wehoop player-box release.

    The same release the committed dataset is banked from, fetched fresh so a
    grade the morning after a slate sees that night's box scores rather than
    whatever the last dataset commit happened to hold.
    """
    from build_wnba_player_box import fetch_player_box
    from velocity.dfs.backtest import norm
    from velocity.models.dfs_wnba import wnba_dk_points

    box = fetch_player_box(slate_date.year)
    if box.empty:
        return {}
    box = box.assign(actual=wnba_dk_points(box))
    return {
        (norm(row["athlete_display_name"]),
         pd.Timestamp(row["game_date"]).normalize().date()): {
            "actual": float(row["actual"]), "game_id": str(row["game_id"]),
            "team": str(row.get("team_abbreviation") or "")}
        for row in box.to_dict("records")
        if pd.notna(row["game_date"])
    }


def day_index_for(  # pragma: no cover - network
    league: str, slate_date: datetime
) -> tuple[_Index, _Index]:
    """The league's realized-points indexes, or empty when the feed is down."""
    try:
        if league == "mlb":
            return mlb_day_index(slate_date), {}
        if league == "wnba":
            return wnba_day_index(slate_date), {}
        if league == "nfl":
            return nfl_day_index(slate_date)
    except Exception as exc:  # noqa: BLE001 - a feed down never blocks the slate
        print(f"{league} actuals unavailable ({exc})")
    return {}, {}


def record_lines(record: pd.DataFrame, league: str) -> list[str]:
    """The per-entry record as log lines, best entry first."""
    if record.empty:
        return [f"{league}: no DFS entry graded"]
    lines = []
    for row in record.sort_values("realized", ascending=False).to_dict("records"):
        parts: list[str] = []
        for key in ("format", "slate", "suffix"):
            value = str(row.get(key) or "").strip()
            # A classic board labels the lock time AND the grouping, and on a
            # single-grouping day they are the same word twice.
            if value and value not in parts:
                parts.append(value)
        label = " · ".join(parts) or str(row["draft_group_id"])
        lines.append(
            f"  {label}: {row['realized']:.1f} DK pts realized vs "
            f"{row['projected']:.1f} projected ({row['error']:+.1f}); "
            f"{row['n_matched']}/{row['n_slots']} players found"
        )
    return [f"{league}: {len(record)} entr(ies) graded", *lines]


def main() -> None:  # pragma: no cover - network orchestration (pure parts live in dfs/)
    parser = argparse.ArgumentParser(description="Grade the previous day's DFS entries")
    parser.add_argument("--prev-dir", required=True, help="downloaded previous DFS artifacts")
    parser.add_argument("--out-dir", required=True, help="folder to write the record parquet")
    parser.add_argument("--league", default="mlb",
                        choices=["mlb", "nfl", "ncaaf", "wnba"])
    args = parser.parse_args()

    if args.league not in GRADEABLE:
        print(f"{args.league}: no free player-level actuals feed; entries stay ungraded")
        return

    stamps = entry_paths(Path(args.prev_dir), args.league)
    graded_stamps = latest_prior_day(sorted(stamps), datetime.now(UTC))
    if not graded_stamps:
        print("no prior-day DFS entries in the downloaded artifacts; skipping")
        return
    entries = gradeable_entries([p for s in graded_stamps for p in stamps[s]])
    if entries.empty:
        print("prior-day entries carry no DK-points board; skipping")
        return

    slate_date = datetime.strptime(graded_stamps[-1], "%Y%m%dT%H%M%SZ")
    print(f"grading {len(entries)} roster slot(s) from {len(graded_stamps)} run(s) "
          f"on {slate_date.date()}")
    index, teams = day_index_for(args.league, slate_date)
    if not index:
        print("no actuals for the graded day; nothing written")
        return

    graded = grade_lineup_frame(entries, index, team_index=teams, slate_date=slate_date)
    record = lineup_record(graded)
    print("\n".join(record_lines(record, args.league)))

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    slate_day = pd.Timestamp(slate_date.date())
    record.assign(league=args.league, slate_date=slate_day).to_parquet(
        out / f"dfs_record_{args.league}_{stamp}.parquet", index=False)
    graded.assign(league=args.league, slate_date=slate_day).to_parquet(
        out / f"dfs_players_{args.league}_{stamp}.parquet", index=False)
    print(f"wrote {len(record)} entr(ies) and {len(graded)} slot row(s) to {out}")


if __name__ == "__main__":
    main()
