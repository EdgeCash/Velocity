"""Bank the NHL datasets from the official NHL API (free, keyless).

Games: 32 club-season calls per season, deduped by game id — regular
season + playoffs with finals and the OT/SO indicator. Starting goalies:
one boxscore call per played game (the API flags the starter), fetched
through a small thread pool with a polite per-request delay.

    python scripts/build_nhl_datasets.py --seasons 2023 2024 2025 \
        --out datasets/nhl

Incremental: an existing goalies file is topped up, not refetched.
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from velocity.ingest.hockey import (
    NHL_CLUBS,
    boxscore_url,
    club_season_url,
    extract_goalie_starts,
    fetch_json,
    normalize_nhl_games,
)


def fetch_season_games(start_year: int, sleep: float) -> pd.DataFrame:
    payloads = []
    for club in NHL_CLUBS:
        try:
            payloads.append(fetch_json(club_season_url(club, start_year)))
        except Exception as exc:  # noqa: BLE001 - a missing club (relocation) is fine
            print(f"  {club} {start_year}: {exc}")
        time.sleep(sleep)
    frame = normalize_nhl_games(payloads, start_year)
    print(f"  season {start_year}: {len(frame)} games "
          f"({int(frame['home_score'].notna().sum())} final)")
    return frame


def fetch_goalies(game_ids: list[str], sleep: float, workers: int) -> pd.DataFrame:
    def one(gid: str) -> list[dict]:
        time.sleep(sleep)
        try:
            rows = extract_goalie_starts(fetch_json(boxscore_url(gid)), gid)
        except Exception:  # noqa: BLE001 - a missing boxscore contributes nothing
            return []
        return rows

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, batch in enumerate(pool.map(one, game_ids)):
            rows.extend(batch)
            if (i + 1) % 200 == 0:
                print(f"  boxscores: {i + 1}/{len(game_ids)}")
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bank NHL games + starting goalies")
    parser.add_argument("--seasons", nargs="+", type=int, required=True,
                        help="season start years, e.g. 2023 2024 2025")
    parser.add_argument("--out", default="datasets/nhl")
    parser.add_argument("--sleep", type=float, default=0.05)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--skip-goalies", action="store_true")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    frames = [fetch_season_games(year, args.sleep) for year in args.seasons]
    games = pd.concat(frames, ignore_index=True).drop_duplicates("game_id")
    games = games.sort_values("kickoff").reset_index(drop=True)
    games.to_parquet(out / "games.parquet", index=False)
    print(f"wrote {len(games)} games to {out / 'games.parquet'}")

    if args.skip_goalies:
        return
    played = games[games["home_score"].notna()]["game_id"].astype(str).tolist()
    existing = None
    goalies_path = out / "starters.parquet"
    if goalies_path.exists():
        existing = pd.read_parquet(goalies_path)
        done = set(existing["game_id"].astype(str))
        played = [g for g in played if g not in done]
    print(f"fetching goalie starts for {len(played)} games...")
    fresh = fetch_goalies(played, args.sleep, args.workers)
    goalies = (pd.concat([existing, fresh], ignore_index=True)
               if existing is not None and not fresh.empty
               else (fresh if existing is None else existing))
    if not goalies.empty:
        goalies = goalies.drop_duplicates(subset=["game_id", "side"])
        goalies.to_parquet(goalies_path, index=False)
    print(f"wrote {len(goalies)} goalie starts to {goalies_path}")


if __name__ == "__main__":
    main()
