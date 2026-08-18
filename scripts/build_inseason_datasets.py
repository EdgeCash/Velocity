"""Build/refresh the MLB + WNBA games datasets from free keyless feeds.

The football dead-zone content pipeline needs games frames to fit the scores
ratings from; both summer leagues have free, keyless schedule/score APIs:

* **MLB** — statsapi.mlb.com ``/api/v1/schedule`` (one call per season month;
  finals land minutes after games end).
* **WNBA** — wehoop's per-season schedule parquet (sportsdataverse-data
  GitHub releases; ESPN's own API 403s datacenter IPs).

Both are free feeds, so the output commits to the public repo like every
other ``datasets/`` file. Completed games only, full-name team keys (they
match The Odds API's event names).

    python scripts/build_inseason_datasets.py --seasons 2024 2026
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from velocity.ingest.inseason import (
    normalize_mlb_schedule,
    normalize_wehoop_schedule,
)

_MLB_URL = ("https://statsapi.mlb.com/api/v1/schedule"
            "?sportId=1&startDate={start}&endDate={end}")
# wehoop's per-season schedule parquet (sportsdataverse-data releases): the
# CI-reachable WNBA feed — ESPN's own API 403s datacenter IPs.
_WNBA_URL = ("https://github.com/sportsdataverse/sportsdataverse-data/releases/"
             "download/espn_wnba_schedules/wnba_schedule_{season}.parquet")
# MLB season window (month, day): generous bounds; empty dates cost one skip.
_MLB_WINDOW = ((2, 20), (11, 10))


# ESPN's edge 403s non-browser agents (proven on backfill run 32181998406);
# statsapi doesn't care. One browser UA serves both.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _get(url: str) -> dict:  # pragma: no cover - network
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    for attempt, delay in enumerate((0, 5, 15)):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                return json.loads(resp.read())
        except Exception:  # noqa: BLE001 - retried; the last attempt raises
            if attempt == 2:
                raise
    raise RuntimeError("unreachable")


def fetch_mlb_season(season: int, today: date) -> pd.DataFrame:  # pragma: no cover
    """One season of completed MLB games, fetched in month chunks."""
    (m1, d1), (m2, d2) = _MLB_WINDOW
    start = date(season, m1, d1)
    end = min(date(season, m2, d2), today)
    frames = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=30), end)
        payload = _get(_MLB_URL.format(start=cursor.isoformat(),
                                       end=chunk_end.isoformat()))
        frame = normalize_mlb_schedule(payload, season)
        if not frame.empty:
            frames.append(frame)
        cursor = chunk_end + timedelta(days=1)
        time.sleep(0.3)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def fetch_wnba_season(season: int, today: date) -> pd.DataFrame:  # pragma: no cover
    """One season of completed WNBA games — the wehoop release parquet.

    ESPN's own edge 403s datacenter IPs (proven live: runs 32181998406 and
    32182552123, sandbox included), so the transport is the nflverse pattern:
    wehoop publishes one schedule parquet per season as a GitHub release
    asset, always reachable from CI. One request per season.
    """
    import io as _io
    import urllib.request as _request

    del today  # the release parquet always carries the season to date
    url = _WNBA_URL.format(season=season)
    req = _request.Request(url, headers={"User-Agent": _USER_AGENT})
    for attempt, delay in enumerate((0, 5, 15)):
        if delay:
            time.sleep(delay)
        try:
            with _request.urlopen(req, timeout=120) as resp:  # noqa: S310
                raw = pd.read_parquet(_io.BytesIO(resp.read()))
            break
        except Exception:  # noqa: BLE001 - retried; the last attempt raises
            if attempt == 2:
                raise
    return normalize_wehoop_schedule(raw, season)


def main() -> None:  # pragma: no cover - network orchestration
    parser = argparse.ArgumentParser(description="Build MLB + WNBA games datasets")
    parser.add_argument("--seasons", nargs=2, type=int, metavar=("FIRST", "LAST"),
                        required=True, help="inclusive season range, e.g. 2024 2026")
    parser.add_argument("--leagues", nargs="+", default=["mlb", "wnba"])
    parser.add_argument("--out", default="datasets")
    args = parser.parse_args()

    today = date.today()
    first, last = args.seasons
    fetchers = {"mlb": fetch_mlb_season, "wnba": fetch_wnba_season}
    for league in args.leagues:
        frames = []
        for season in range(first, last + 1):
            frame = fetchers[league](season, today)
            print(f"  {league} {season}: {len(frame)} completed games")
            if not frame.empty:
                frames.append(frame)
        if not frames:
            print(f"  {league}: nothing fetched — not writing")
            continue
        out = pd.concat(frames, ignore_index=True).drop_duplicates(subset="game_id", keep="last")
        dest = Path(args.out) / league / "games.parquet"
        dest.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(dest, index=False)
        print(f"wrote {len(out)} {league} games to {dest}")


if __name__ == "__main__":
    main()
