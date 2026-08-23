"""Snapshot FantasyPros player projections to a private parquet + dump raw shape.

FantasyPros consensus projections feed the props model as an external prior.
This snapshots the current **NFL** projections into a private parquet (the
public v2 API has no NCAAF projections endpoint — see the OpenAPI spec — so
college projections come from elsewhere). The free public tier can answer a
``position=ALL`` request tier-limited with zero players, so the collector
falls back to per-position fetches when that happens. ``--inspect`` dumps the
first normalized row for schema discovery.

Runs as a **GitHub Actions** job (where ``FP_API_KEY`` lives) and uploads its
output as a **private Actions artifact**; it never commits. Triggering the
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
from velocity.ingest.fantasypros import FantasyProsClient, normalize_projections

# The FantasyPros public v2 API serves projections for NFL, MLB and NBA only —
# there is no NCAAF projections path (confirmed against the published OpenAPI
# spec), which is why the college endpoint 404s. MLB projections are SEASON
# totals (week is an NFL concept); the DFS scorer normalizes them to per-game
# rates, so they're snapshot at week 0 year-round.
LEAGUES = ("nfl", "mlb")

# The free public tier serves per-position requests reliably; a position=ALL
# request can come back tier-limited (`public_api_limited`) with zero players.
# The collector tries ALL first and falls back to fetching these one by one.
NFL_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")


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
    if _players_of(raw):
        return normalize_projections(raw, season=season, week=week), notes
    if league != "nfl":
        return normalize_projections(raw, season=season, week=week), notes
    notes.append("position=ALL returned no players; falling back to per-position fetches")
    frames = []
    for position in NFL_POSITIONS:
        part = client.raw_projections(league, season, position=position, week=week)
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
                        help="games parquet used by --week auto")
    parser.add_argument("--leagues", nargs="+", default=list(LEAGUES))
    parser.add_argument("--out", default="artifacts/fp", help="output folder (private, not git)")
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
            schedule = pd.read_parquet(args.schedule)
            week = current_week(schedule, pd.Timestamp(now).tz_localize(None))
            print(f"auto week from {args.schedule}: {week}"
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
        df = df.assign(league=league, collected_at=stamp)
        frames.append(df)
        print(f"  {league}: {len(df)} projection rows, {df['player_name'].nunique()} players")

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
