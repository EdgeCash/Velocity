"""Render a team's season-outlook card (the FPI-screenshot format, our style).

One team, its whole season in one portrait card: finished games as results,
remaining games as the model's win probability, projected record as the hero.
The schedule comes from the league's free feed (nflverse schedules CSV for
the NFL — it carries unplayed games, unlike the committed played-only games
file; CFBD for NCAAF, ``CFBD_API_KEY`` required), and the win probabilities
come from the same promoted projector the live slate runs.

    python scripts/render_outlook_card.py --league nfl --team KC
    CFBD_API_KEY=... python scripts/render_outlook_card.py --league ncaaf --team Georgia
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from run_live_slate import _build_projection  # noqa: E402


def _nfl_schedule(season: int) -> pd.DataFrame:  # pragma: no cover - network
    from velocity.ingest.nfl import NFLVERSE_SCHEDULE_URL

    raw = pd.read_csv(NFLVERSE_SCHEDULE_URL, low_memory=False)
    raw = raw[(raw["season"] == season) & (raw["game_type"] == "REG")]
    return raw[["season", "week", "home_team", "away_team",
                "home_score", "away_score"]].copy()


def _ncaaf_schedule(season: int) -> pd.DataFrame:  # pragma: no cover - network
    from refresh_datasets import _cfbd_get

    key = os.environ.get("CFBD_API_KEY", "")
    if not key:
        raise SystemExit("CFBD_API_KEY is required for the NCAAF schedule")
    games = _cfbd_get("games", key, year=season, seasonType="regular")
    rows = [
        {
            "season": season,
            "week": int(g.get("week", 0)),
            "home_team": g.get("homeTeam"),
            "away_team": g.get("awayTeam"),
            "home_score": g.get("homePoints"),
            "away_score": g.get("awayPoints"),
        }
        for g in games
        if g.get("homeTeam") and g.get("awayTeam")
    ]
    return pd.DataFrame(rows)


def main() -> None:  # pragma: no cover - network orchestration
    from refresh_datasets import current_season
    from velocity.report.outlook import build_outlook
    from velocity.report.outlook_png import render_outlook

    parser = argparse.ArgumentParser(description="Render a season-outlook card")
    parser.add_argument("--league", choices=["nfl", "ncaaf"], required=True)
    parser.add_argument("--team", required=True,
                        help="NFL club code (KC) or NCAAF school name (Georgia)")
    parser.add_argument("--season", type=int, default=None)
    parser.add_argument("--data", default=None,
                        help="datasets folder (default datasets/<league>)")
    parser.add_argument("--n-sims", type=int, default=4000)
    parser.add_argument("--out", default="artifacts/outlook")
    parser.add_argument("--asset-dir", default=None,
                        help="logo cache folder (NFL nicety; omit for code blocks)")
    args = parser.parse_args()
    args.data = args.data or f"datasets/{args.league}"
    args.snapshot_file = None  # the projector fits from committed data only
    args.max_days = 0

    season = args.season if args.season is not None else current_season()
    schedule = (_nfl_schedule if args.league == "nfl" else _ncaaf_schedule)(season)
    project, known = _build_projection(args)
    if args.team not in set(schedule["home_team"]) | set(schedule["away_team"]):
        near = sorted(t for t in known if args.team.lower() in str(t).lower())[:8]
        raise SystemExit(f"{args.team!r} not on the {season} schedule"
                         + (f"; close matches: {near}" if near else ""))

    outlook = build_outlook(
        schedule, args.team,
        lambda home, away: project(home, away).p_home_win(),
        season=season,
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(c for c in args.team if c.isalnum()) or "team"
    dest = Path(args.out) / f"outlook_{args.league}_{safe}_{stamp}.png"
    render_outlook(
        outlook, dest, league=args.league,
        asset_dir=args.asset_dir,
        note=f"win probabilities from the promoted {args.league.upper()} model · "
             f"{args.n_sims} sims per game · informational only",
    )
    print(f"{outlook.projected_line()} ({outlook.wins}-{outlook.losses} banked, "
          f"{sum(1 for r in outlook.rows if not r.played)} games ahead)")
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
