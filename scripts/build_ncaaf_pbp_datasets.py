"""Build the committed NCAAF play-by-play dataset from CFBD (the college EPA feed).

The college analogue of ``build_nfl_pbp_datasets.py``: fetch CFBD's ``/plays``
per (season, week, season type), distill each payload onto the canonical
``Plays`` schema via :func:`velocity.ingest.ncaaf.distill_rest_plays` (CFBD's
``ppa`` is the EPA column), and write one ``datasets/ncaaf/plays.parquet``.
CFBD is a free feed, so the output is committed to the public repo like every
other ``datasets/`` file.

The (season, week, type) combinations come from the committed games file, so
the plays fetched are exactly the games the backtest walks — no wasted calls
and no label drift. Seasons missing from the games file fall back to regular
weeks 1–16 plus postseason week 1.

    CFBD_API_KEY=... python scripts/build_ncaaf_pbp_datasets.py --seasons 2015 2024
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd
from velocity.ingest.ncaaf import distill_rest_plays

_CFBD_BASE = "https://api.collegefootballdata.com"
# canonical season_type → the CFBD query spelling
_SEASON_TYPES = {"REG": "regular", "POST": "postseason"}


def _cfbd_get(endpoint: str, key: str, **params: object) -> list[dict]:  # pragma: no cover
    """One CFBD REST call, with backoff on throttles and transient errors."""
    query = "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(
        f"{_CFBD_BASE}/{endpoint}?{query}", headers={"Authorization": f"Bearer {key}"}
    )
    for attempt, delay in enumerate((0, 5, 15, 30)):
        if delay:
            print(f"    retrying in {delay}s")
            time.sleep(delay)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            print(f"    HTTP {exc.code} from /{endpoint}")
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 3:
                raise
            print(f"    {exc} from /{endpoint}")
    raise RuntimeError("unreachable")


def week_plan(games: pd.DataFrame, season: int) -> list[tuple[int, str]]:
    """The (week, cfbd season type) fetch list for ``season``.

    Read from the committed games file when it covers the season (fetch
    exactly the weeks the backtest walks); otherwise the standard college
    shape: regular weeks 1–16 + postseason week 1.
    """
    rows = games[games["season"] == season] if not games.empty else games
    if len(rows) == 0:
        return [(w, "regular") for w in range(1, 17)] + [(1, "postseason")]
    plan = (
        rows[["week", "season_type"]]
        .drop_duplicates()
        .sort_values(["season_type", "week"], ascending=[False, True])
    )
    return [
        (int(week), _SEASON_TYPES.get(str(stype), "regular"))
        for week, stype in plan.itertuples(index=False)
    ]


def main() -> None:  # pragma: no cover - network orchestration
    parser = argparse.ArgumentParser(description="Build datasets/ncaaf/plays.parquet from CFBD")
    parser.add_argument("--seasons", nargs=2, type=int, metavar=("FIRST", "LAST"),
                        required=True, help="inclusive season range, e.g. 2015 2024")
    parser.add_argument("--games", default="datasets/ncaaf/games.parquet",
                        help="committed games file that defines the week plan")
    parser.add_argument("--out", default="datasets/ncaaf/plays.parquet")
    parser.add_argument("--sleep", type=float, default=0.4,
                        help="pause between CFBD calls (rate-limit courtesy)")
    args = parser.parse_args()

    key = os.environ.get("CFBD_API_KEY", "")
    if not key:
        raise SystemExit("CFBD_API_KEY is required")
    try:
        games = pd.read_parquet(args.games)
    except Exception as exc:  # noqa: BLE001 - the fallback plan still works
        print(f"games file unavailable ({exc}); using the standard week plan")
        games = pd.DataFrame()

    first, last = args.seasons
    frames: list[pd.DataFrame] = []
    for season in range(first, last + 1):
        season_rows = 0
        for week, stype in week_plan(games, season):
            time.sleep(args.sleep)
            payload = _cfbd_get(
                "plays", key, year=season, week=week,
                seasonType=stype, classification="fbs",
            )
            plays = distill_rest_plays(payload, season, week)
            if plays.empty:
                continue
            frames.append(plays)
            season_rows += len(plays)
        print(f"  {season}: {season_rows} plays")

    if not frames:
        raise SystemExit("no plays fetched — nothing written")
    out = pd.concat(frames, ignore_index=True)
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(dest, index=False)
    print(f"wrote {len(out)} plays ({out['game_id'].nunique()} games, "
          f"seasons {first}–{last}) to {dest}")


if __name__ == "__main__":
    main()
