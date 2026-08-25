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
_LINEUP_URL = ("https://statsapi.mlb.com/api/v1/schedule?sportId=1"
               "&startDate={start}&endDate={end}&hydrate=lineups")
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
                # Completes the DK pitcher line (IP and K are already above).
                "er": pd.to_numeric(stats.get("earnedRuns"), errors="coerce"),
                "hits_allowed": pd.to_numeric(stats.get("hits"), errors="coerce"),
                "win": pd.to_numeric(stats.get("wins"), errors="coerce"),
            })
            break  # one starter per side
    return rows


def extract_batters(payload: dict, game_id: str) -> list[dict[str, object]]:
    """One boxscore payload → a row per batter who came to the plate.

    The home-run model's substrate: plate appearances and home runs per
    batter per game, plus the lineup slot he hit from (``lineup_slot`` 1-9,
    0 for a pinch hitter who entered outside the order). ``started`` marks
    the nine in the posted order — the slot a pregame projection can count
    on for plate appearances. Pure and offline-tested, like
    :func:`extract_starters`; a player with no plate appearance (a defensive
    replacement, the whole bullpen) contributes nothing.
    """
    rows: list[dict[str, object]] = []
    for side in ("home", "away"):
        team = ((payload.get("teams") or {}).get(side) or {})
        team_name = ((team.get("team") or {}).get("name"))
        if not team_name:
            continue
        for player in (team.get("players") or {}).values():
            stats = ((player.get("stats") or {}).get("batting") or {})
            pa = pd.to_numeric(stats.get("plateAppearances"), errors="coerce")
            if not stats or pd.isna(pa) or pa <= 0:
                continue
            person = player.get("person") or {}
            if person.get("id") is None:
                continue
            # statsapi encodes the slot as "500" (5th, starter) / "501"
            # (the first man off the bench in that slot). The TEAM-level
            # battingOrder list is the FINAL lineup, so a starter who was
            # replaced is absent from it — the player-level code is the only
            # honest read of who actually started.
            order = str(player.get("battingOrder") or "")
            slot = int(order) // 100 if order.isdigit() else 0

            def num(key: str, _stats: dict = stats) -> float | None:
                return pd.to_numeric(_stats.get(key), errors="coerce")

            rows.append({
                "game_id": str(game_id),
                "team": str(team_name),
                "side": side,
                "batter_id": str(person["id"]),
                "batter_name": str(person.get("fullName") or person["id"]),
                "lineup_slot": slot,
                "started": order.isdigit() and order.endswith("00"),
                "pa": pa,
                "ab": num("atBats"),
                # The full DraftKings scoring line. Singles are derived at
                # scoring time (hits minus extra-base hits) so the bank stays
                # the box score's own vocabulary.
                "h": num("hits"),
                "double": num("doubles"),
                "triple": num("triples"),
                "hr": num("homeRuns"),
                "rbi": num("rbi"),
                "r": num("runs"),
                "bb": num("baseOnBalls"),
                "hbp": num("hitByPitch"),
                "sb": num("stolenBases"),
            })
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


def extract_lineups(payload: dict) -> dict[str, list[str]]:
    """Schedule payload → team name → the nine announced bats, IN ORDER.

    statsapi publishes the confirmed card a couple of hours before first
    pitch, keyed by the same MLBAM player ids the banks use — no name
    matching. That is worth a great deal to a DFS build: measured on the
    showdown backtest, a roster built before lineups post carries **2.1
    players who never appear** out of six, and building from the confirmed
    card instead is worth about 11 DK points a roster.

    Same doubleheader caveat as :func:`extract_probables`: teams key by name,
    so the first listed game wins. Absent (not yet posted) simply means the
    team is missing from the map and the caller falls back.
    """
    lineups: dict[str, list[str]] = {}
    for date in payload.get("dates") or []:
        for game in date.get("games") or []:
            teams = game.get("teams") or {}
            card = game.get("lineups") or {}
            for side, key in (("home", "homePlayers"), ("away", "awayPlayers")):
                name = ((teams.get(side) or {}).get("team") or {}).get("name")
                players = card.get(key) or []
                if not name or not players or str(name) in lineups:
                    continue
                lineups[str(name)] = [str(p["id"]) for p in players
                                      if p.get("id") is not None]
    return lineups


def fetch_lineups(start: str, end: str) -> dict[str, list[str]]:  # pragma: no cover - network
    """Confirmed batting orders for the [start, end] window (free statsapi)."""
    return extract_lineups(_get(_LINEUP_URL.format(start=start, end=end)))


def fetch_probables(
    start: str, end: str
) -> dict[tuple[str, str, None], tuple[str | None, str | None]]:  # pragma: no cover - network
    """Probables for the [start, end] date window (YYYY-MM-DD, free statsapi)."""
    return extract_probables(_get(_SCHED_URL.format(start=start, end=end)))


def _merge_bank(existing: pd.DataFrame, fresh: pd.DataFrame,
                keys: list[str], out_path: Path, label: str) -> None:
    """Concat + dedupe a bank onto its parquet (no-op when nothing was read)."""
    combined = (pd.concat([existing, fresh], ignore_index=True)
                if not existing.empty else fresh)
    if combined.empty:
        raise SystemExit(f"no {label} rows — nothing written")
    combined = combined.drop_duplicates(subset=keys, keep="last")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(out_path, index=False)
    print(f"wrote {len(combined)} {label} rows "
          f"({combined['game_id'].nunique()} games) to {out_path}")


def bank_starters(
    games_path: str | Path, out_path: str | Path, sleep: float = 0.15,
    batters_out: str | Path | None = None,
) -> int:  # pragma: no cover - network orchestration
    """Incrementally bank per-game lines for every game not yet covered.

    One boxscore call per game feeds both banks: the starters frame always,
    and — when ``batters_out`` is given — the batter plate-appearance frame
    the home-run model fits on. A game already present in EVERY requested
    bank is skipped, so adding the batter bank re-walks history once and
    then stays incremental.
    """
    games = pd.read_parquet(games_path)
    out_path = Path(out_path)
    existing = pd.read_parquet(out_path) if out_path.exists() else pd.DataFrame()
    done = set(existing["game_id"].astype(str)) if not existing.empty else set()
    bat_path = Path(batters_out) if batters_out else None
    bat_existing = (pd.read_parquet(bat_path)
                    if bat_path and bat_path.exists() else pd.DataFrame())
    if bat_path is not None:
        bat_done = (set(bat_existing["game_id"].astype(str))
                    if not bat_existing.empty else set())
        done &= bat_done  # a game counts as banked only when both banks hold it
    todo = [g for g in games["game_id"].astype(str) if g not in done]
    print(f"{len(games)} games; {len(done)} already banked; fetching {len(todo)}")

    rows: list[dict[str, object]] = []
    bat_rows: list[dict[str, object]] = []
    failures = 0
    for i, game_id in enumerate(todo):
        try:
            payload = _get(_BOX_URL.format(pk=game_id))
            rows.extend(extract_starters(payload, game_id))
            if bat_path is not None:
                bat_rows.extend(extract_batters(payload, game_id))
        except Exception as exc:  # noqa: BLE001 - one bad game never sinks the bank
            failures += 1
            print(f"  {game_id}: fetch failed ({exc})")
            if failures > 50:
                raise
        if i and i % 500 == 0:
            print(f"  {i}/{len(todo)} games fetched")
        time.sleep(sleep)

    fresh = pd.DataFrame(rows)
    _merge_bank(existing, fresh, ["game_id", "side"], out_path, "starter")
    if bat_path is not None:
        _merge_bank(bat_existing, pd.DataFrame(bat_rows),
                    ["game_id", "batter_id"], bat_path, "batter")
    return len(fresh)


def main() -> None:  # pragma: no cover - network orchestration
    parser = argparse.ArgumentParser(description="Bank MLB starters per game")
    parser.add_argument("--games", default="datasets/mlb/games.parquet")
    parser.add_argument("--out", default="datasets/mlb/starters.parquet")
    parser.add_argument("--batters", default=None,
                        help="also bank per-game batter PA/HR here "
                             "(datasets/mlb/batters.parquet)")
    parser.add_argument("--sleep", type=float, default=0.15)
    args = parser.parse_args()
    bank_starters(args.games, args.out, args.sleep, batters_out=args.batters)


if __name__ == "__main__":
    main()
