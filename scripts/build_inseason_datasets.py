"""Build/refresh the MLB + WNBA games datasets from free keyless feeds.

The football dead-zone content pipeline needs games frames to fit the scores
ratings from; both summer leagues have free, keyless schedule/score APIs:

* **MLB** — statsapi.mlb.com ``/api/v1/schedule`` (one call per season month;
  finals land minutes after games end).
* **WNBA** — ESPN's public scoreboard, one call per date across the season
  window (May–October), empty dates skipped.

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
from velocity.ingest.inseason import normalize_mlb_schedule, normalize_wnba_scoreboard

_MLB_URL = ("https://statsapi.mlb.com/api/v1/schedule"
            "?sportId=1&startDate={start}&endDate={end}")
_WNBA_URL = ("https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/"
             "scoreboard?dates={ymd}")
# Season windows (month, day): generous bounds; empty dates cost one skip.
_MLB_WINDOW = ((2, 20), (11, 10))
_WNBA_WINDOW = ((5, 1), (10, 31))


def _get(url: str) -> dict:  # pragma: no cover - network
    req = urllib.request.Request(url, headers={"User-Agent": "velocity-datasets"})
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
    """One season of completed WNBA games, one scoreboard call per date."""
    (m1, d1), (m2, d2) = _WNBA_WINDOW
    start = date(season, m1, d1)
    end = min(date(season, m2, d2), today)
    frames = []
    cursor = start
    while cursor <= end:
        payload = _get(_WNBA_URL.format(ymd=cursor.strftime("%Y%m%d")))
        frame = normalize_wnba_scoreboard(payload, season)
        if not frame.empty:
            frames.append(frame)
        cursor += timedelta(days=1)
        time.sleep(0.25)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


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
        out = pd.concat(frames, ignore_index=True).drop_duplicates(subset="game_id")
        dest = Path(args.out) / league / "games.parquet"
        dest.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(dest, index=False)
        print(f"wrote {len(out)} {league} games to {dest}")


if __name__ == "__main__":
    main()
