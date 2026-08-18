"""Build the MLB starters dataset from statsapi box scores (free, committable).

The starting pitcher is the dominant factor in MLB pricing — the market moves
on probables, and a team-level scores fit is blind to them. This banks the
per-game starter (plus his defense-independent pitching line) for every game
in the committed frame, so the lab can fit the starter-decomposed model:

    runs = intercept + offense[batting team] + bullpen[fielding team]
                     + starter[pitcher]

— structurally the QB decomposition (``fit_qb_ratings``) on a games-long
frame. One statsapi boxscore call per game (~0.15s spacing); statsapi is
keyless and free, so the output commits like every ``datasets/`` file.

    python scripts/build_mlb_pitching.py --games datasets/mlb/games.parquet
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

_BOX_URL = "https://statsapi.mlb.com/api/v1/game/{pk}/boxscore"
_SCHED_URL = ("https://statsapi.mlb.com/api/v1/schedule?sportId=1"
              "&startDate={start}&endDate={end}&hydrate=probablePitcher")
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


def _outs(innings_pitched: object) -> float | None:
    """statsapi 'inningsPitched' ("6.2" = 6 innings 2 outs) → total outs."""
    try:
        text = str(innings_pitched)
        whole, _, frac = text.partition(".")
        return float(int(whole) * 3 + int(frac or 0))
    except (TypeError, ValueError):
        return None


def extract_starters(payload: dict, game_id: str) -> list[dict[str, object]]:
    """One boxscore payload → a starter row per side (pure, offline-tested).

    The starter is the pitcher whose in-game ``gamesStarted`` is 1. His
    defense-independent line (K, BB, HBP, HR, outs, batters faced) rides
    along for FIP-style quality metrics later. A side with no identifiable
    starter (suspended-game oddities) contributes nothing rather than a guess.
    """
    rows: list[dict[str, object]] = []
    for side in ("home", "away"):
        team = ((payload.get("teams") or {}).get(side) or {})
        team_name = ((team.get("team") or {}).get("name"))
        for player in (team.get("players") or {}).values():
            stats = ((player.get("stats") or {}).get("pitching") or {})
            if not stats or int(stats.get("gamesStarted") or 0) != 1:
                continue
            person = player.get("person") or {}
            if person.get("id") is None or not team_name:
                continue
            rows.append({
                "game_id": str(game_id),
                "team": str(team_name),
                "side": side,
                "starter_id": str(person["id"]),
                "starter_name": str(person.get("fullName") or person["id"]),
                "outs": _outs(stats.get("inningsPitched")),
                "batters_faced": pd.to_numeric(stats.get("battersFaced"),
                                               errors="coerce"),
                "k": pd.to_numeric(stats.get("strikeOuts"), errors="coerce"),
                "bb": pd.to_numeric(stats.get("baseOnBalls"), errors="coerce"),
                "hbp": pd.to_numeric(stats.get("hitBatsmen"), errors="coerce"),
                "hr": pd.to_numeric(stats.get("homeRuns"), errors="coerce"),
            })
            break  # one starter per side
    return rows


def extract_probables(
    payload: dict,
) -> dict[tuple[str, str, None], tuple[str | None, str | None]]:
    """Schedule payload → ``StarterAwareModel`` lookup keyed ``(home, away, None)``.

    Probable pitchers are public pregame knowledge (the rest-spot argument).
    The live slate prices by team pair without a kickoff, so a doubleheader's
    two games share a key — the first listed game wins; the slate carries one
    row per matchup either way. A side with no announced probable maps to
    ``None`` and prices league-average.
    """
    lookup: dict[tuple[str, str, None], tuple[str | None, str | None]] = {}
    for date in payload.get("dates") or []:
        for game in date.get("games") or []:
            teams = game.get("teams") or {}
            home = ((teams.get("home") or {}).get("team") or {}).get("name")
            away = ((teams.get("away") or {}).get("team") or {}).get("name")
            if not home or not away:
                continue
            key = (str(home), str(away), None)
            if key in lookup:
                continue
            hsp = ((teams.get("home") or {}).get("probablePitcher") or {}).get("id")
            asp = ((teams.get("away") or {}).get("probablePitcher") or {}).get("id")
            lookup[key] = (None if hsp is None else str(hsp),
                           None if asp is None else str(asp))
    return lookup


def fetch_probables(
    start: str, end: str
) -> dict[tuple[str, str, None], tuple[str | None, str | None]]:  # pragma: no cover - network
    """Probables for the [start, end] date window (YYYY-MM-DD, free statsapi)."""
    return extract_probables(_get(_SCHED_URL.format(start=start, end=end)))


def bank_starters(
    games_path: str | Path, out_path: str | Path, sleep: float = 0.15
) -> int:  # pragma: no cover - network orchestration
    """Incrementally bank starters for every game not yet covered; returns new rows."""
    games = pd.read_parquet(games_path)
    out_path = Path(out_path)
    existing = pd.read_parquet(out_path) if out_path.exists() else pd.DataFrame()
    done = set(existing["game_id"].astype(str)) if not existing.empty else set()
    todo = [g for g in games["game_id"].astype(str) if g not in done]
    print(f"{len(games)} games; {len(done)} already banked; fetching {len(todo)}")

    rows: list[dict[str, object]] = []
    failures = 0
    for i, game_id in enumerate(todo):
        try:
            payload = _get(_BOX_URL.format(pk=game_id))
            rows.extend(extract_starters(payload, game_id))
        except Exception as exc:  # noqa: BLE001 - one bad game never sinks the bank
            failures += 1
            print(f"  {game_id}: fetch failed ({exc})")
            if failures > 50:
                raise
        if i and i % 500 == 0:
            print(f"  {i}/{len(todo)} games fetched")
        time.sleep(sleep)

    fresh = pd.DataFrame(rows)
    combined = (
        pd.concat([existing, fresh], ignore_index=True)
        if not existing.empty else fresh
    )
    if combined.empty:
        raise SystemExit("no starter rows — nothing written")
    combined = combined.drop_duplicates(subset=["game_id", "side"], keep="last")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(out_path, index=False)
    print(f"wrote {len(combined)} starter rows "
          f"({combined['game_id'].nunique()} games) to {out_path}")
    return len(fresh)


def main() -> None:  # pragma: no cover - network orchestration
    parser = argparse.ArgumentParser(description="Bank MLB starters per game")
    parser.add_argument("--games", default="datasets/mlb/games.parquet")
    parser.add_argument("--out", default="datasets/mlb/starters.parquet")
    parser.add_argument("--sleep", type=float, default=0.15)
    args = parser.parse_args()
    bank_starters(args.games, args.out, args.sleep)


if __name__ == "__main__":
    main()
