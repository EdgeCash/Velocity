"""Refresh the committed datasets with the current season's rows.

The live slate fits its ratings from ``datasets/<league>/`` — frozen history
plus this script's weekly current-season top-up (the ``refresh-datasets``
workflow). Each run replaces the target season's rows wholesale and leaves
every other season untouched, so re-running is idempotent and finals that land
late simply arrive on the next refresh.

Sources (both free):

* **NFL** — nflverse: the schedules CSV (finals + closing ``spread_line`` /
  ``total_line``) and the per-season play-by-play release parquet (EPA), the
  same feeds `velocity.ingest.nfl` normalizes.
* **NCAAF** — CFBD games + betting lines via the REST API (``CFBD_API_KEY``),
  with the same conventions as ``scripts/pull_cfbd_lines.py`` (consensus
  median line; CFBD's negative-home-favored spread flipped to positive).

Only **played** games join the games files (the ratings fit on scores; the
schema keeps scores int). Unplayed rows would be dead weight until graded.

    python scripts/refresh_datasets.py --league nfl --season 2026
    CFBD_API_KEY=... python scripts/refresh_datasets.py --league ncaaf --season 2026
    python scripts/refresh_datasets.py --league both   # season inferred from today
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

import pandas as pd
from velocity.ingest.nfl import NFLVERSE_SCHEDULE_URL, load_pbp, normalize_schedules

_CFBD_BASE = "https://api.collegefootballdata.com"


def current_season(today: datetime | None = None) -> int:
    """The football season a date belongs to (Jan–Jul rows are last year's)."""
    now = today or datetime.now(UTC)
    return now.year if now.month >= 8 else now.year - 1


def merge_season(existing: pd.DataFrame, fresh: pd.DataFrame, season: int) -> pd.DataFrame:
    """Replace ``season``'s rows in ``existing`` with ``fresh``, aligned to its columns.

    Pure and idempotent: everything outside the target season is untouched;
    ``fresh`` is reindexed onto the existing file's columns (missing ones go
    null) so the committed schema never drifts under a refresh.
    """
    kept = existing[existing["season"] != season]
    aligned = fresh.reindex(columns=existing.columns)
    return pd.concat([kept, aligned], ignore_index=True)


def nfl_games_from_schedules(raw: pd.DataFrame, season: int) -> pd.DataFrame:
    """One season of the nflverse schedules CSV → the committed games shape.

    Canonical ``Games`` columns plus the closing ``spread_line`` /
    ``total_line`` carried through (as in the original dataset build). Only
    played games are kept.
    """
    raw = raw[raw["season"] == season]
    raw = raw[raw["home_score"].notna() & raw["away_score"].notna()].copy()
    if raw.empty:
        return pd.DataFrame()
    games = normalize_schedules(raw)
    lines = raw[["game_id"]].copy()
    lines["game_id"] = lines["game_id"].astype(str)
    for col in ("spread_line", "total_line"):
        lines[col] = pd.to_numeric(raw[col], errors="coerce") if col in raw.columns else None
    return games.merge(lines, on="game_id", how="left")


def nfl_plays_for_season(season: int) -> pd.DataFrame:  # pragma: no cover - network
    """Current-season canonical plays, distilled exactly like the committed file.

    ``load_pbp`` normalizes the nflverse release parquet onto the canonical
    ``Plays`` schema; the same non-null ``posteam``/``epa`` filter as the
    original dataset build keeps only the rows the EPA ratings consume.
    """
    plays = load_pbp([season])
    return plays[plays["posteam"].notna() & plays["epa"].notna()].reset_index(drop=True)


def _cfbd_get(endpoint: str, key: str, **params: object) -> list[dict]:  # pragma: no cover
    query = "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(
        f"{_CFBD_BASE}/{endpoint}?{query}", headers={"Authorization": f"Bearer {key}"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.loads(resp.read())


def _consensus(lines: list[dict], field: str) -> float | None:
    vals = [line[field] for line in lines if line.get(field) is not None]
    return float(median(vals)) if vals else None


def ncaaf_games_from_cfbd(
    games_json: list[dict], lines_json: list[dict], season: int
) -> pd.DataFrame:
    """CFBD games + lines payloads → the committed NCAAF games shape (played only).

    Same conventions as ``scripts/pull_cfbd_lines.py``: consensus (median)
    provider line per game, and CFBD's negative-home-favored spread flipped so
    positive = home favored. Games without a line still join — the fit needs
    scores; lines are extra.
    """
    lines_by_id: dict[object, list[dict]] = {
        entry["id"]: (entry.get("lines") or []) for entry in lines_json
    }
    rows: list[dict] = []
    for game in games_json:
        if game.get("homePoints") is None or game.get("awayPoints") is None:
            continue
        provider_lines = lines_by_id.get(game["id"], [])
        spread = _consensus(provider_lines, "spread")
        rows.append(
            {
                "game_id": str(game["id"]),
                "league": "ncaaf",
                "season": int(game.get("season", season)),
                "week": int(game.get("week", 0)),
                "season_type": "POST" if game.get("seasonType") == "postseason" else "REG",
                "kickoff": game.get("startDate"),
                "home_team": game["homeTeam"],
                "away_team": game["awayTeam"],
                "neutral_site": bool(game.get("neutralSite", False)),
                "roof": None,
                "surface": None,
                "home_score": int(game["homePoints"]),
                "away_score": int(game["awayPoints"]),
                "spread_line": -spread if spread is not None else None,
                "total_line": _consensus(provider_lines, "overUnder"),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        kickoff = pd.to_datetime(df["kickoff"], errors="coerce", utc=True)
        df["kickoff"] = kickoff.dt.tz_localize(None)
    return df


def _refresh_file(path: Path, fresh: pd.DataFrame, season: int, label: str) -> None:
    existing = pd.read_parquet(path)
    before = int((existing["season"] == season).sum())
    merged = merge_season(existing, fresh, season)
    merged.to_parquet(path, index=False)
    print(f"  {label}: season {season} rows {before} → {len(fresh)} ({len(merged)} total)")


def refresh_nfl(out: Path, season: int) -> None:  # pragma: no cover - network
    raw = pd.read_csv(NFLVERSE_SCHEDULE_URL, low_memory=False)
    games = nfl_games_from_schedules(raw, season)
    if games.empty:
        print(f"  nfl: no played {season} games yet — nothing to refresh")
        return
    _refresh_file(out / "games.parquet", games, season, "nfl games")
    try:
        plays = nfl_plays_for_season(season)
    except Exception as exc:  # noqa: BLE001 - pbp release can lag the schedule
        print(f"  nfl plays skipped ({exc}); games refreshed alone")
        return
    # The committed plays file predates the str-typed canonical ids; normalize
    # both sides so the merge doesn't mix dtypes.
    existing = pd.read_parquet(out / "plays.parquet")
    for col in ("play_id", "game_id"):
        existing[col] = existing[col].astype(str)
    merged = merge_season(existing, plays, season)
    merged.to_parquet(out / "plays.parquet", index=False)
    print(f"  nfl plays: {len(plays)} season rows ({len(merged)} total)")


def refresh_ncaaf(out: Path, season: int) -> None:  # pragma: no cover - network
    key = os.environ.get("CFBD_API_KEY", "")
    if not key:
        raise SystemExit("CFBD_API_KEY is required for the NCAAF refresh")
    games_json = _cfbd_get("games", key, year=season, seasonType="both")
    lines_json = _cfbd_get("lines", key, year=season, seasonType="both")
    games = ncaaf_games_from_cfbd(games_json, lines_json, season)
    if games.empty:
        print(f"  ncaaf: no played {season} games yet — nothing to refresh")
        return
    _refresh_file(out / "games.parquet", games, season, "ncaaf games")

    # College plays top-up (the EPA feed) — only once the backfill has
    # committed a plays file, and best-effort: a CFBD pbp hiccup never sinks
    # the games refresh above. Weeks come from the games just fetched, so the
    # calls match the season's actual shape.
    plays_path = out / "plays.parquet"
    if not plays_path.exists():
        return
    try:
        from velocity.ingest.ncaaf import distill_rest_plays

        combos = sorted({
            (int(g["week"]), str(g.get("seasonType", "regular")))
            for g in games_json
            if g.get("homePoints") is not None and g.get("week") is not None
        })
        frames = []
        for week, stype in combos:
            payload = _cfbd_get(
                "plays", key, year=season, week=week,
                seasonType="postseason" if stype == "postseason" else "regular",
                classification="fbs",
            )
            plays = distill_rest_plays(payload, season, week)
            if not plays.empty:
                frames.append(plays)
        if not frames:
            print(f"  ncaaf plays: nothing scored for {season} yet")
            return
        fresh = pd.concat(frames, ignore_index=True)
        merged = merge_season(pd.read_parquet(plays_path), fresh, season)
        merged.to_parquet(plays_path, index=False)
        print(f"  ncaaf plays: {len(fresh)} season rows ({len(merged)} total)")
    except Exception as exc:  # noqa: BLE001 - plays are additive to the refresh
        print(f"  ncaaf plays skipped ({exc}); games refreshed alone")


def refresh_inseason(out: Path, season: int, league: str) -> None:  # pragma: no cover
    """Top up an in-season league (mlb/wnba) from its free keyless feed.

    Only refreshes a dataset the backfill has already created — the summer
    leagues are content surfaces, not silently-appearing datasets.
    """
    from datetime import date as _date

    from build_inseason_datasets import fetch_mlb_season, fetch_wnba_season

    path = out / "games.parquet"
    if not path.exists():
        print(f"  {league}: no committed games file yet — run the backfill first")
        return
    fetcher = {"mlb": fetch_mlb_season, "wnba": fetch_wnba_season}[league]
    fresh = fetcher(season, _date.today())
    if fresh.empty:
        print(f"  {league}: no completed {season} games yet — nothing to refresh")
        return
    _refresh_file(path, fresh, season, f"{league} games")


def main() -> None:  # pragma: no cover - network orchestration
    parser = argparse.ArgumentParser(description="Refresh datasets with the current season")
    parser.add_argument("--league", default="both",
                        choices=["nfl", "ncaaf", "mlb", "wnba", "both", "all"],
                        help="'both' = the football pair; 'all' adds mlb + wnba")
    parser.add_argument("--season", type=int, default=None,
                        help="season year (default: inferred from today)")
    parser.add_argument("--out", default="datasets", help="datasets root folder")
    args = parser.parse_args()

    season = args.season if args.season is not None else current_season()
    out = Path(args.out)
    print(f"refreshing season {season}")
    if args.league in ("nfl", "both", "all"):
        refresh_nfl(out / "nfl", season)
    if args.league in ("ncaaf", "both", "all"):
        refresh_ncaaf(out / "ncaaf", season)
    # The summer leagues run on the calendar year, not the football year
    # (a June slate belongs to *this* year's season).
    calendar_season = args.season if args.season is not None else datetime.now(UTC).year
    if args.league in ("mlb", "all"):
        refresh_inseason(out / "mlb", calendar_season, "mlb")
    if args.league in ("wnba", "all"):
        refresh_inseason(out / "wnba", calendar_season, "wnba")


if __name__ == "__main__":
    main()
