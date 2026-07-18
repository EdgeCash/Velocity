"""Pull CFBD games + betting lines into a canonical games-with-lines parquet.

CollegeFootballData is reachable from the sandbox with a free API key. This
script fetches per-season games (final scores) and betting lines, joins them by
CFBD game id, and writes a games file carrying the closing ``spread_line`` /
``total_line`` — the inputs for the against-the-spread / over-under market test.

Conventions handled:

* CFBD's REST API returns **camelCase** (``homeTeam``, ``homePoints``), unlike the
  ``cfbd`` Python client's snake_case — mapped here directly.
* CFBD ``spread`` is **negative when the home team is favored**; nflverse (and our
  ATS evaluation) use **positive = home favored**, so we store
  ``spread_line = -consensus(spread)``. ``total_line`` is the consensus
  ``overUnder``.

The API key is read from ``--key-file`` (kept out of the repo) or the
``CFBD_API_KEY`` environment variable — never hard-coded.

    python scripts/pull_cfbd_lines.py --years 2015-2024 --key-file KEYFILE --out datasets/ncaaf
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path
from statistics import median

import pandas as pd

_BASE = "https://api.collegefootballdata.com"


def _get(endpoint: str, key: str, **params: object) -> list[dict]:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(
        f"{_BASE}/{endpoint}?{query}", headers={"Authorization": f"Bearer {key}"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.loads(resp.read())


def _consensus(lines: list[dict], field: str) -> float | None:
    vals = [line[field] for line in lines if line.get(field) is not None]
    return float(median(vals)) if vals else None


def pull(years: range, key: str) -> pd.DataFrame:
    rows: list[dict] = []
    for year in years:
        games = {g["id"]: g for g in _get("games", key, year=year, seasonType="both")}
        lines = _get("lines", key, year=year, seasonType="both")
        n_lines = 0
        for entry in lines:
            game = games.get(entry["id"])
            provider_lines = entry.get("lines") or []
            if game is None or not provider_lines:
                continue
            spread = _consensus(provider_lines, "spread")
            total = _consensus(provider_lines, "overUnder")
            if spread is None and total is None:
                continue
            n_lines += 1
            rows.append(
                {
                    "game_id": str(game["id"]),
                    "league": "ncaaf",
                    "season": int(game["season"]),
                    "week": int(game["week"]),
                    "season_type": "POST" if game["seasonType"] == "postseason" else "REG",
                    "kickoff": game.get("startDate"),
                    "home_team": game["homeTeam"],
                    "away_team": game["awayTeam"],
                    "neutral_site": bool(game.get("neutralSite", False)),
                    "roof": None,
                    "surface": None,
                    "home_score": game.get("homePoints"),
                    "away_score": game.get("awayPoints"),
                    # Flip CFBD (negative = home favored) → nflverse (positive = home favored).
                    "spread_line": -spread if spread is not None else None,
                    "total_line": total,
                }
            )
        print(f"  {year}: {len(games)} games, {n_lines} with lines")
    df = pd.DataFrame(rows)
    df["kickoff"] = pd.to_datetime(df["kickoff"], errors="coerce", utc=True).dt.tz_localize(None)
    return df.dropna(subset=["home_score", "away_score"]).reset_index(drop=True)


def _parse_years(spec: str) -> range:
    if "-" in spec:
        lo, hi = spec.split("-")
        return range(int(lo), int(hi) + 1)
    return range(int(spec), int(spec) + 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull CFBD games + lines")
    parser.add_argument("--years", required=True, help="e.g. 2015-2024 or 2023")
    parser.add_argument("--key-file", help="file containing the CFBD API key")
    parser.add_argument("--out", default="datasets/ncaaf", help="output folder")
    args = parser.parse_args()

    key = ""
    if args.key_file:
        key = Path(args.key_file).read_text().strip()
    key = key or os.environ.get("CFBD_API_KEY", "")
    if not key:
        raise SystemExit("no API key (use --key-file or set CFBD_API_KEY)")

    df = pull(_parse_years(args.years), key)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / "games_lines.parquet", index=False)
    print(f"wrote {len(df)} games with lines to {out}/games_lines.parquet")


if __name__ == "__main__":
    main()
