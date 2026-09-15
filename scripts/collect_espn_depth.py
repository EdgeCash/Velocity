"""Snapshot ESPN rosters and depth charts (the collector).

The NFL starter map works out each team's QB1 by *inference*: it reads
FantasyPros' projected passing yards and takes the busiest passer. That proxy
fails exactly where it matters — a stale projection, or a backup projected high
in a week his starter is expected to sit, and the inference disagrees with the
depth chart, which is the thing that says it outright.

Two endpoints joined locally, because the depth chart names athletes only by
``$ref``: the roster (one call per team) supplies ids and names, the depth
chart (one call per team) supplies rank order. Left to the API, naming a depth
chart is one fetch per player — hundreds a team.

    python scripts/collect_espn_depth.py --out artifacts/espn --leagues nfl

Keyless and quota-free, but ~2 calls per team, so it is spaced and defaults to
the leagues that actually have a consumer rather than everything reachable.

    --leagues nfl mlb nhl     # every league ESPN files depth charts for

WNBA 500s and college football 400s on the depth-chart route; those leagues
collect rosters only, and the log says so rather than inventing an ordering.
"""

from __future__ import annotations

import argparse
import time
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from velocity.ingest.espn import ESPNClient, depth_by_position

# Leagues with a consumer today. NFL's depth chart feeds the QB starter map
# (velocity/features/starters.py); the others are collected on request.
DEFAULT_LEAGUES = ("nfl",)
# The position whose depth ordering gets summarized in the run log — the one
# a human reads to sanity-check that the snapshot is sane.
HEADLINE_POSITION = {"nfl": "QB", "nhl": "G", "mlb": "SP"}


def _season(now: datetime, league: str) -> int:
    """The season a depth chart is filed under.

    Football seasons are named for the year they start, so anything before
    March belongs to the previous year's season. The other leagues run inside
    a calendar year.
    """
    if league in ("nfl", "ncaaf"):
        return now.year if now.month >= 3 else now.year - 1
    return now.year


def collect(
    leagues: tuple[str, ...], stamp: pd.Timestamp, season_for: dict[str, int],
    pause: float = 0.7,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Rosters and depth charts across ``leagues``, plus leagues that failed."""
    client = ESPNClient()
    rosters: list[pd.DataFrame] = []
    depths: list[pd.DataFrame] = []
    failed: list[str] = []

    for league in leagues:
        try:
            teams = client.teams(league)
        except Exception as exc:  # noqa: BLE001 - one league never sinks the rest
            print(f"  {league}: teams listing failed ({exc}); skipping")
            failed.append(league)
            continue
        print(f"  {league}: {len(teams)} teams")

        league_depth: list[pd.DataFrame] = []
        no_depth_route = False
        for team_id in teams["team_id"]:
            try:
                roster = client.roster(league, team_id)
            except Exception as exc:  # noqa: BLE001 - a team, not the league
                print(f"    team {team_id}: roster failed ({exc})")
                continue
            rosters.append(roster.assign(collected_at=stamp))
            time.sleep(pause)
            if no_depth_route:
                continue
            try:
                chart = client.depth_chart(league, team_id, season_for[league], roster)
            except urllib.error.HTTPError as exc:
                # 4xx/5xx on the FIRST team means the league has no depth-chart
                # route at all (WNBA 500s, college football 400s) — stop asking
                # 30 more times for the same answer.
                if not league_depth:
                    print(f"    {league}: no depth-chart route (HTTP {exc.code}); "
                          "collecting rosters only")
                    no_depth_route = True
                else:
                    print(f"    team {team_id}: depth chart failed ({exc})")
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"    team {team_id}: depth chart failed ({exc})")
                continue
            league_depth.append(chart.assign(collected_at=stamp))
            time.sleep(pause)

        if league_depth:
            frame = pd.concat(league_depth, ignore_index=True)
            depths.append(frame)
            named = int(frame["player_name"].notna().sum())
            print(f"  {league}: {len(frame)} depth rows ({named} named) across "
                  f"{frame['team_abbreviation'].nunique()} teams")
            position = HEADLINE_POSITION.get(league)
            if position:
                starters = depth_by_position(frame, position)
                shown = ", ".join(f"{t} {n[0]}" for t, n in sorted(starters.items())[:4])
                print(f"    {position}1: {len(starters)} teams — {shown}"
                      f"{' …' if len(starters) > 4 else ''}")

    roster_out = pd.concat(rosters, ignore_index=True) if rosters else pd.DataFrame()
    depth_out = pd.concat(depths, ignore_index=True) if depths else pd.DataFrame()
    return roster_out, depth_out, failed


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot ESPN rosters and depth charts")
    parser.add_argument("--out", default="artifacts/espn",
                        help="output folder (private artifact, not git)")
    parser.add_argument("--leagues", nargs="+", default=list(DEFAULT_LEAGUES),
                        help=f"leagues to snapshot (default: {' '.join(DEFAULT_LEAGUES)})")
    parser.add_argument("--season", type=int, default=None,
                        help="depth-chart season (default: derived from today)")
    parser.add_argument("--pause", type=float, default=0.7,
                        help="seconds between calls — courtesy on a keyless public API")
    args = parser.parse_args()

    now = datetime.now(UTC)
    stamp = pd.Timestamp(now).tz_localize(None)
    seasons = {lg: args.season or _season(now, lg) for lg in args.leagues}
    print(f"ESPN rosters/depth charts @ {now.isoformat()} "
          f"(seasons {seasons})")

    roster, depth, failed = collect(tuple(args.leagues), stamp, seasons, args.pause)
    if failed and len(failed) == len(args.leagues):
        raise SystemExit(f"every league failed: {failed}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = now.strftime("%Y%m%dT%H%M%SZ")
    roster.to_parquet(out / f"espn_rosters_{tag}.parquet", index=False)
    print(f"wrote {len(roster)} roster rows")
    depth.to_parquet(out / f"espn_depth_{tag}.parquet", index=False)
    print(f"wrote {len(depth)} depth rows")
    if depth.empty:
        print("note: no depth rows at all — every league refused the route, or a "
              "payload shape change worth looking at")


if __name__ == "__main__":
    main()
