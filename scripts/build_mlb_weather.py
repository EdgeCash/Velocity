"""Bank game-time weather for every committed MLB game (free, committable).

The home-run model says it cannot price weather for want of a bank to fit a
coefficient on (audit finding 2). statsapi serves the observed reading for
every game already played — temperature, and a **ballpark-relative** wind that
names whether it blew out to centre or in from left — on the ``feed/live``
endpoint beside the boxscore the starters bank already walks. Keyless and
free, so the output commits like every other ``datasets/`` file.

One call per game, incremental: a game already banked is never re-fetched, so
the first run walks history once and every later run costs only the new games.

    python scripts/build_mlb_weather.py --games datasets/mlb/games.parquet
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

import pandas as pd
from velocity.ingest.mlb_weather import (
    WEATHER_COLUMNS,
    normalize_weather,
    weather_slice,
)

_FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live"
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) velocity-datasets"


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


def bank_weather(
    games_path: str | Path, out_path: str | Path, sleep: float = 0.08
) -> int:  # pragma: no cover - network orchestration
    """Fetch and bank weather for every game not already covered."""
    games = pd.read_parquet(games_path)
    out_path = Path(out_path)
    existing = pd.read_parquet(out_path) if out_path.exists() else pd.DataFrame()
    done = set(existing["game_id"].astype(str)) if not existing.empty else set()
    todo = [g for g in games["game_id"].astype(str) if g not in done]
    print(f"{len(games)} games; {len(done)} already banked; fetching {len(todo)}")

    # Trimmed at the point of fetch: a feed/live payload is the whole game,
    # ~0.87 MB of it, so holding one per game is 6 GB of JSON and an
    # out-of-memory kill rather than a bank.
    slices: dict[str, object] = {}
    failures = 0

    def _write() -> pd.DataFrame:
        fresh = normalize_weather(slices)
        combined = (pd.concat([existing, fresh], ignore_index=True)
                    if not existing.empty else fresh)
        combined = combined.drop_duplicates("game_id", keep="last")
        combined = combined.sort_values("game_id").reset_index(drop=True)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        combined[list(WEATHER_COLUMNS)].to_parquet(out_path, index=False)
        return combined

    for i, game_id in enumerate(todo):
        try:
            slices[game_id] = weather_slice(_get(_FEED_URL.format(pk=game_id)))
        except Exception as exc:  # noqa: BLE001 - one bad game never sinks the bank
            failures += 1
            print(f"  {game_id}: fetch failed ({exc})", flush=True)
            if failures > 50:
                raise
        if i and i % 500 == 0:
            # Checkpointed, so a walk this long is never all-or-nothing: an
            # interrupted run resumes from what it already banked.
            _write()
            print(f"  {i}/{len(todo)} games fetched (checkpointed)", flush=True)
        time.sleep(sleep)

    combined = _write()

    # Say what landed. A bank of nulls is the failure mode that looks like a
    # bank, so the counts are the point rather than the row total.
    readings = int(combined["wind_mph"].notna().sum())
    closed = int(combined["roof_closed"].fillna(False).astype(bool).sum())
    unknown = combined.loc[combined["wind_vector"].isna() & combined["wind_text"].notna(),
                           "wind_text"]
    print(f"wrote {len(combined)} games to {out_path}: {readings} with a wind "
          f"reading, {closed} under a closed roof")
    if len(unknown):
        print(f"  {len(unknown)} unmapped wind directions (skipped, never guessed): "
              f"{sorted(set(unknown.astype(str)))}")
    return len(combined)


def main() -> None:  # pragma: no cover - network orchestration
    parser = argparse.ArgumentParser(description="Bank MLB game-time weather")
    parser.add_argument("--games", default="datasets/mlb/games.parquet")
    parser.add_argument("--out", default="datasets/mlb/weather.parquet")
    parser.add_argument("--sleep", type=float, default=0.08)
    args = parser.parse_args()
    bank_weather(args.games, args.out, sleep=args.sleep)


if __name__ == "__main__":
    main()
