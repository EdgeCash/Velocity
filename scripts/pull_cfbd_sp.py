"""Pull Bill Connelly's SP+ ratings from CFBD into a season-level parquet.

CFBD serves SP+ (`/ratings/sp`) first-party — same key, same attribution
posture as the games/lines pull. One row per (season, team): the overall
rating (points vs an average team, the same scale as our ``net``), the
offense/defense components (points per game vs average), and the ranking.

What this is FOR, stated up front (docs/BACKTEST_NCAAF.md addendum): our
results-only fit knows nothing about rosters, so week-1 ratings are last
year's team. SP+ carries returning production/recruiting/transfers. The
candidate use is a **previous-season SP+ prior** for the early weeks —
the NCAAB Torvik pseudo-games pattern — which is point-in-time honest
(CFBD serves final ratings per season, so season N's rating may only ever
inform season N+1's prior; using season N's own rating in-season is
lookahead). It is NOT a direct edge source: SP+ itself tracks the closing
line (~53% ATS historically), and our lab's standing finding is that the
close is the accuracy ceiling for ratings families.

    CFBD_API_KEY=... python scripts/pull_cfbd_sp.py --years 2014-2025
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path

import pandas as pd

_BASE = "https://api.collegefootballdata.com"


def _get(endpoint: str, key: str, **params: object) -> list[dict]:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(
        f"{_BASE}/{endpoint}?{query}", headers={"Authorization": f"Bearer {key}"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.loads(resp.read())


def sp_frame(payloads: dict[int, list[dict]]) -> pd.DataFrame:
    """CFBD ``/ratings/sp`` payloads → one row per (season, team).

    Entries without a team or overall rating (the endpoint's average rows)
    are dropped. Team names are CFBD school names — the games file's own
    convention, so no aliasing is needed downstream.
    """
    rows = []
    for season, entries in sorted(payloads.items()):
        for entry in entries:
            team, rating = entry.get("team"), entry.get("rating")
            if not team or rating is None or team == "nationalAverages":
                continue
            rows.append({
                "season": int(season),
                "team": str(team),
                "conference": entry.get("conference"),
                "rating": float(rating),
                "ranking": entry.get("ranking"),
                "offense": (entry.get("offense") or {}).get("rating"),
                "defense": (entry.get("defense") or {}).get("rating"),
                "special_teams": (entry.get("specialTeams") or {}).get("rating"),
            })
    return pd.DataFrame(rows)


def _parse_years(spec: str) -> range:
    if "-" in spec:
        lo, hi = spec.split("-")
        return range(int(lo), int(hi) + 1)
    return range(int(spec), int(spec) + 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull CFBD SP+ season ratings")
    parser.add_argument("--years", required=True, help="e.g. 2014-2025 or 2024")
    parser.add_argument("--key-file", help="file containing the CFBD API key")
    parser.add_argument("--out", default="datasets/ncaaf", help="output folder")
    args = parser.parse_args()

    key = ""
    if args.key_file:
        key = Path(args.key_file).read_text().strip()
    key = key or os.environ.get("CFBD_API_KEY", "")
    if not key:
        raise SystemExit("no API key (use --key-file or set CFBD_API_KEY)")

    payloads = {}
    for year in _parse_years(args.years):
        payloads[year] = _get("ratings/sp", key, year=year)
        print(f"  {year}: {len(payloads[year])} entries")
    frame = sp_frame(payloads)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out / "sp_ratings.parquet", index=False)
    print(f"wrote {len(frame)} SP+ rows to {out}/sp_ratings.parquet "
          "(CFBD data used with attribution)")


if __name__ == "__main__":
    main()
