"""Fetch game-day weather history from Open-Meteo → datasets/nfl/weather.parquet.

One archive request per stadium era (free API, no key), daily variables only:
max 10m wind speed (mph), mean temperature (°F), precipitation sum (inch).
The output is one row per (team, date) covering every date the team hosted a
game in the committed schedule — a compact, committable frame the weather
variants join at fit time (velocity/features/weather.py).

    python scripts/fetch_weather.py --data datasets/nfl --out datasets/nfl/weather.parquet
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd
from velocity.features.weather import OUTDOOR_ROOFS, STADIUM_ERAS

_API = "https://archive-api.open-meteo.com/v1/archive"


def _fetch_daily(lat: float, lon: float, start: str, end: str) -> pd.DataFrame:
    query = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "start_date": start, "end_date": end,
        "daily": "wind_speed_10m_max,temperature_2m_mean,precipitation_sum",
        "wind_speed_unit": "mph", "temperature_unit": "fahrenheit",
        "precipitation_unit": "inch", "timezone": "UTC",
    })
    last_error: Exception | None = None
    for delay in (0, 5, 15, 30):  # transient TLS/proxy hiccups happen mid-run
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(f"{_API}?{query}", timeout=60) as resp:  # noqa: S310
                payload = json.loads(resp.read())
            break
        except Exception as exc:  # noqa: BLE001 - retried, re-raised after the last try
            last_error = exc
    else:
        raise RuntimeError(f"archive fetch failed after retries: {last_error}")
    daily = payload.get("daily") or {}
    return pd.DataFrame({
        "date": pd.to_datetime(daily.get("time", [])),
        "wind_max": daily.get("wind_speed_10m_max", []),
        "temp_mean": daily.get("temperature_2m_mean", []),
        "precip": daily.get("precipitation_sum", []),
    })


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch NFL game-day weather history")
    parser.add_argument("--data", default="datasets/nfl")
    parser.add_argument("--out", default="datasets/nfl/weather.parquet")
    args = parser.parse_args()

    games = pd.read_parquet(Path(args.data) / "games.parquet")
    outdoor = games[games["roof"].astype(str).isin(OUTDOOR_ROOFS)].copy()
    outdoor["date"] = pd.to_datetime(outdoor["kickoff"]).dt.normalize()

    frames: list[pd.DataFrame] = []
    for team, first, last, lat, lon in STADIUM_ERAS:
        era = outdoor[
            (outdoor["home_team"] == team)
            & (outdoor["season"] >= first)
            & ((outdoor["season"] <= last) if last is not None else True)
        ]
        if era.empty:
            continue
        start = era["date"].min().strftime("%Y-%m-%d")
        end = era["date"].max().strftime("%Y-%m-%d")
        daily = _fetch_daily(lat, lon, start, end)
        wanted = daily[daily["date"].isin(set(era["date"]))].copy()
        wanted["team"] = team
        frames.append(wanted)
        print(f"  {team} ({first}-{last or 'now'}): {len(wanted)} game days", flush=True)
        time.sleep(1.0)  # a free API deserves gentle pacing

    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["team", "date"]).reset_index(drop=True)
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(dest, index=False)
    covered = out["wind_max"].notna().mean()
    print(f"wrote {len(out)} team-date rows to {dest} "
          f"({covered:.0%} with wind readings)")


if __name__ == "__main__":
    main()
