"""Snapshot FantasyPros player projections to a private parquet + dump raw shape.

FantasyPros consensus projections feed the props model as an external prior.
This snapshots the current **NFL** projections into a private parquet (the
public v2 API has no NCAAF projections endpoint — see the OpenAPI spec — so
college projections come from elsewhere). The free public tier can answer a
``position=ALL`` request tier-limited with zero players, so the collector
falls back to per-position fetches when that happens. ``--inspect`` prints the
stat-key census — every key the feed serves, how many players carry a non-zero
value, and which of them the props model actually reads. That census is the
answer to "does FantasyPros project attempts?", which is what blocks two of the
largest unmapped BettingPros prop slugs; it costs no extra request, so it rides
the scheduled runs as well as a manual dispatch.

Runs as a **GitHub Actions** job (where ``FP_API_KEY`` lives) and uploads its
output as an **Actions artifact**; it never commits. Triggering the
workflow manually (``workflow_dispatch``) with ``--inspect`` doubles as the in-CI
verification + schema discovery, since the sandbox can't see the secret.

    FP_API_KEY=... python scripts/collect_fantasypros.py --season 2026 --out artifacts/fp
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.ingest.fantasypros import (
    FantasyProsClient,
    describe_stat_keys,
    normalize_projections,
    unmelted_stat_keys,
)

# The FantasyPros public v2 API serves projections for NFL, MLB and NBA only —
# there is no NCAAF projections path (confirmed against the published OpenAPI
# spec), which is why the college endpoint 404s.
#
# NFL only. MLB was collected from the day this script was written and read by
# NOTHING, every run, for months — and because the tier answers position=ALL
# empty it fell through all nine per-position fallbacks first, so each run spent
# ten requests to bank zero rows. The 2026-09-16 census run made that legible
# for the first time (0 projection rows, 0 players, a warning nobody had been
# in a position to act on).
#
# Nothing reads it and nothing should: MLB DFS prices from collect_mlb_player_
# stats.py (the dfs-slate workflow swaps PROJ_FILE explicitly), and MLB props
# come from the banked starters frame via _mlb_k_slate — which run_live_slate
# documents as having "no FantasyPros dependency". Season-total consensus is
# strictly worse than both.
#
# Re-adding a league here is a decision to spend requests on it, so say what
# will read the rows before you do.
LEAGUES = ("nfl",)

# Leagues whose snapshot has no consumer, and why. Kept as a mechanism rather
# than deleted with its last entry: the note it used to carry for MLB was
# correct for months and still did not stop the requests being spent, because
# saying a thing in a log is not the same as not doing it. Anything that lands
# here again should be asked the same question — what reads these rows? — and
# dropped from LEAGUES if the answer is nothing.
UNCONSUMED_LEAGUES: dict[str, str] = {}

NFL_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")
# The free public tier serves per-position requests reliably; a position=ALL
# request can come back tier-limited (`public_api_limited`) with zero players,
# and the collector then fetches these one by one. That fallback is also what
# made an unconsumed league expensive: an empty ALL response costs one request,
# then one more per position.
FALLBACK_POSITIONS = {
    "nfl": NFL_POSITIONS,
}


def _players_of(raw: object) -> list:
    if isinstance(raw, dict):
        return raw.get("players") or raw.get("data") or []
    return list(raw or [])


def current_week(games: pd.DataFrame, today: pd.Timestamp) -> int:
    """The in-season NFL week to snapshot, or 0 outside the season window.

    Week 0 (season-long) projections are useless to the weekly consumers —
    the DFS lineup and the prop sim both refuse them — so in-season the
    collector must ask for the week actually being played: the smallest week
    of the newest season whose last kickoff is still ahead of ``today``.
    Before the season opener (minus a few days of lead-in) and after the
    final kickoff this returns 0, and the consumers skip honestly.
    """
    if games.empty or "kickoff" not in games.columns:
        return 0
    season = games[games["season"] == games["season"].max()].copy()
    kick = pd.to_datetime(season["kickoff"])
    if today < kick.min() - pd.Timedelta(days=4):
        return 0
    by_week = season.assign(_kick=kick).groupby("week")["_kick"].max()
    ahead = by_week[by_week >= today]
    return int(ahead.index.min()) if len(ahead) else 0


def resolve_schedule(path: str, *, fetch: bool = True) -> tuple[pd.DataFrame, str]:
    """The schedule ``current_week`` reads — the live nflverse one when it can.

    The committed games frame carries **played** games only, so before the
    opener it holds last season and ``current_week`` sees no kickoff ahead:
    on 2026-09-08 it answered 0 and the whole weekly surface (props, DFS,
    pick'em) refused the season-long snapshot it got instead
    (docs/SYSTEM_REVIEW.md §1.1). nflverse publishes the full schedule,
    unplayed games included, keyless — so that is the source, with the
    committed frame as the offline fallback. Returns ``(frame, source)``.
    """
    if fetch:
        try:
            from velocity.ingest.nfl import NFLVERSE_SCHEDULE_URL, normalize_schedules

            raw = pd.read_csv(NFLVERSE_SCHEDULE_URL, low_memory=False)
            return normalize_schedules(raw), "nflverse schedule"
        except Exception as exc:  # noqa: BLE001 - fall back to the committed frame
            print(f"nflverse schedule fetch failed ({exc}); using {path}")
    return pd.read_parquet(path), path


def fetch_league_frame(
    client: FantasyProsClient, league: str, season: int, week: int
) -> tuple[pd.DataFrame, list[str]]:
    """One league's normalized projections, with the tier-limit fallback.

    Returns ``(frame, notes)`` — notes carry anything worth surfacing in the
    log (limitation keys, the fallback being taken). ``position=ALL`` is
    schema-valid but the limited public tier can answer it with zero players;
    when that happens each position is fetched individually and the results
    are concatenated.
    """
    notes: list[str] = []
    raw = client.raw_projections(league, season, week=week)
    if isinstance(raw, dict):
        odd_keys = sorted(set(raw) - {"season", "week", "count", "positions",
                                      "scoring", "players", "data"})
        if odd_keys:
            notes.append(f"non-standard response keys: {odd_keys}")
    # A projection served as a compound string ("18/25") never reaches the long
    # frame, so the stat-key census would call it absent. Say so where the raw
    # payload is still in scope.
    dropped = unmelted_stat_keys(raw)
    if dropped:
        notes.append(f"volume-like keys the melt drops (non-numeric values): {dropped}")
    if _players_of(raw):
        return normalize_projections(raw, season=season, week=week), notes
    positions = FALLBACK_POSITIONS.get(league)
    if positions is None:
        return normalize_projections(raw, season=season, week=week), notes
    notes.append("position=ALL returned no players; falling back to per-position fetches")
    frames = []
    for position in positions:
        part = client.raw_projections(league, season, position=position, week=week)
        # The ALL response was empty, so the dropped-key check above saw
        # nothing — which is precisely the tier-limited case. Re-run it here,
        # once, on the first payload that carries any.
        if not dropped:
            dropped = unmelted_stat_keys(part)
            if dropped:
                notes.append("volume-like keys the melt drops (non-numeric values): "
                             f"{dropped}")
        got = normalize_projections(part, season=season, week=week)
        if not got.empty:
            frames.append(got)
    if not frames:
        return normalize_projections(raw, season=season, week=week), notes
    merged = pd.concat(frames, ignore_index=True).drop_duplicates(
        subset=["player_id", "player_name", "stat"]
    )
    return merged.reset_index(drop=True), notes




def _snapshot_injuries(client, args, now, stamp) -> None:  # type: ignore[no-untyped-def]
    """Bank one NFL injuries snapshot into the artifact folder (best-effort).

    Live snapshots complement the committed nflverse history
    (datasets/nfl/injuries.parquet — official designations, weekly): these
    carry the *current* report between official postings, which is what the
    live slate's veto signals need at bet time.
    """
    try:
        from velocity.ingest.fantasypros import normalize_injuries

        raw_inj = client.raw_injuries("nfl", args.season,
                                      week=args.week if args.week else None)
        injuries = normalize_injuries(raw_inj).assign(
            league="nfl", season=args.season, week=args.week, collected_at=stamp
        )
        dest_dir = Path(args.out)
        dest_dir.mkdir(parents=True, exist_ok=True)
        inj_dest = dest_dir / f"fp_injuries_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet"
        injuries.to_parquet(inj_dest, index=False)
        n_out = int(injuries["is_out"].sum()) if not injuries.empty else 0
        print(f"wrote {len(injuries)} injury rows ({n_out} out) to {inj_dest}")
    except Exception as exc:  # noqa: BLE001 - additive surface
        print(f"injuries snapshot skipped ({exc})")




def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot FantasyPros projections")
    parser.add_argument("--season", type=int, required=True, help="projection season, e.g. 2026")
    parser.add_argument("--week", default="auto",
                        help="'auto' derives the in-season week from the committed "
                             "schedule (0 outside the season); an integer pins it "
                             "(0 = full-season projections)")
    parser.add_argument("--schedule", default="datasets/nfl/games.parquet",
                        help="games parquet used by --week auto when the nflverse "
                             "schedule cannot be fetched")
    parser.add_argument("--no-fetch-schedule", action="store_true",
                        help="resolve --week auto from the committed games parquet only")
    parser.add_argument("--leagues", nargs="+", default=list(LEAGUES))
    parser.add_argument("--out", default="artifacts/fp", help="output folder (artifact, never git)")
    parser.add_argument(
        "--inspect", action="store_true", help="print the raw shape of the first player (dry-run)"
    )
    parser.add_argument(
        "--injuries-only", action="store_true",
        help="bank only the injuries snapshot (the daily cadence — reports "
             "finalize Friday and inactives land Sunday, far faster than the "
             "weekly projections refresh)",
    )
    args = parser.parse_args()

    client = FantasyProsClient.from_env()
    now = datetime.now(UTC)
    stamp = pd.Timestamp(now).tz_localize(None)
    if str(args.week) == "auto":
        try:
            schedule, source = resolve_schedule(args.schedule, fetch=not args.no_fetch_schedule)
            week = current_week(schedule, pd.Timestamp(now).tz_localize(None))
            print(f"auto week from {source}: {week}"
                  + (" (outside the season window)" if week == 0 else ""))
        except Exception as exc:  # noqa: BLE001 - a broken schedule falls back to 0
            print(f"auto week failed ({exc}); using 0")
            week = 0
    else:
        week = int(args.week)
    args.week = week
    print(f"FantasyPros snapshot @ {now.isoformat()} (season {args.season}, week {args.week})")

    if args.injuries_only:
        _snapshot_injuries(client, args, now, stamp)
        return

    frames: list[pd.DataFrame] = []
    failed: list[str] = []
    for league in args.leagues:
        if league == "ncaaf":
            # No NCAAF projections path exists in the public v2 API — requesting
            # it can only 404. Skipped by design (not counted as a failure).
            print("  ncaaf: the FantasyPros public API has no NCAAF projections "
                  "endpoint; skipping")
            continue
        try:
            # Week is an NFL concept; every other sport's projections are
            # season totals, fetched at week 0.
            week = args.week if league == "nfl" else 0
            df, notes = fetch_league_frame(client, league, args.season, week)
        except Exception as exc:  # noqa: BLE001 - one league's feed never blocks the other
            print(f"  {league}: fetch failed ({exc}); skipping")
            failed.append(league)
            continue
        for note in notes:
            print(f"  [{league}] {note}")
        if args.inspect and not df.empty:
            first = df.iloc[0].to_dict()
            print(f"  [{league}] first normalized row:\n{json.dumps(first, indent=2, default=str)}")
            # One row of a LONG frame shows one stat key, which is a poor way
            # to discover a schema — it was the only thing --inspect said, and
            # it could not answer "does this feed project attempts?". The
            # census can, and costs no extra request.
            for line in describe_stat_keys(df, league):
                print(line)
        df = df.assign(league=league, collected_at=stamp)
        frames.append(df)
        print(f"  {league}: {len(df)} projection rows, {df['player_name'].nunique()} players")
        # A league that comes back empty has been costing calls for nothing —
        # up to ten a run once the per-position fallback fires. That is cheap,
        # but it is also invisible: an empty frame banks, the parquet writes,
        # the job goes green, and the log line reads like any other. Say it
        # where a run summary shows it.
        if df.empty:
            print(f"::warning title=FantasyPros {league} empty::"
                  f"{league} returned no projection rows for season {args.season} "
                  f"week {week}. Either the tier does not serve it or the request "
                  f"shape is wrong; drop it from LEAGUES or fix the call — it is "
                  f"spending requests either way.")
        elif league in UNCONSUMED_LEAGUES:
            print(f"  note: {league} projections are banked but nothing reads them "
                  f"({UNCONSUMED_LEAGUES[league]})")

    attempted = [lg for lg in args.leagues if lg != "ncaaf"]
    if failed and len(failed) == len(attempted):
        raise SystemExit(f"every league fetch failed: {failed}")

    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    dest_dir = Path(args.out)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"fp_projections_{now.strftime('%Y%m%dT%H%M%SZ')}.parquet"
    out.to_parquet(dest, index=False)
    print(f"wrote {len(out)} rows to {dest}")

    # Injury report snapshot — banked alongside the projections (the daily
    # --injuries-only runs are the primary cadence; this weekly ride-along is
    # a free extra sample). Best-effort: injuries never sink the projections.
    _snapshot_injuries(client, args, now, stamp)
    if out.empty:
        print("note: no projections returned (off-season, wrong season/week, "
              "or a limited public API tier)")


if __name__ == "__main__":
    main()
