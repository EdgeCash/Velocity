"""Backfill a season of the canonical games file from the banked boxscores.

Why this exists: the 2025 NCAAF season was missing from
``datasets/ncaaf/games.parquet`` entirely — the CFBD lines pull was last run
through 2024 and the in-season refresher only appends current rows — so every
college fit was pricing 2026 off ratings that ended in January 2025. The
boxscores file (``build_ncaaf_boxscores.py``) already carries the missing
season's finals; this script lifts one season of those into the games schema.

What it can and cannot backfill, stated plainly: scores, schedule, and
neutral-site flags — yes. Closing ``spread_line``/``total_line`` — no; those
need the CFBD lines pull (``pull_cfbd_lines.py``, key required), so the
backfilled rows carry NaN lines and are **fit-only**: the ratings see them,
the market backtest (which drops lineless rows) does not.

Team names are bridged from the boxscore CSV's conventions to the games
file's CFBD conventions via an explicit alias table; a backfilled name that
matches neither the alias table nor the games file's existing universe is
kept verbatim but reported, so a silent split-team (one school under two
keys) can't slip in.

    python scripts/backfill_games_from_boxscores.py --data datasets/ncaaf --season 2025
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

# Boxscore-CSV name → games-file (CFBD) name. Only names that differ.
BOX_TO_CFBD = {
    "Albany": "UAlbany",
    "Appalachian State": "App State",
    "Bethune Cookman": "Bethune-Cookman",
    "Cal Poly SLO": "Cal Poly",
    "Central Conn. State": "Central Connecticut",
    "Citadel": "The Citadel",
    "Connecticut": "UConn",
    "Elon University": "Elon",
    "FIU": "Florida International",
    "Gardner Webb": "Gardner-Webb",
    "Grambling State": "Grambling",
    "Hawaii": "Hawai'i",
    "LIU": "Long Island University",
    "McNeese State": "McNeese",
    "Miami (FL)": "Miami",
    "Nicholls State": "Nicholls",
    "SE Missouri State": "Southeast Missouri State",
    "Sam Houston State": "Sam Houston",
    "San Jose State": "San José State",
    "Tennessee-Martin": "UT Martin",
    "UC-Davis": "UC Davis",
    "UL-Lafayette": "Louisiana",
    "UL-Monroe": "UL Monroe",
    "USF": "South Florida",
    "Virginia Military": "VMI",
}

GAMES_COLUMNS = ["game_id", "league", "season", "week", "season_type", "kickoff",
                 "home_team", "away_team", "neutral_site", "roof", "surface",
                 "home_score", "away_score", "spread_line", "total_line"]


def backfill(games: pd.DataFrame, boxscores: pd.DataFrame, season: int) -> pd.DataFrame:
    """The games frame with ``season`` appended from boxscores (lines = NaN)."""
    if (games["season"] == season).any():
        raise SystemExit(f"games file already has {int((games['season'] == season).sum())} "
                         f"rows for {season} — refusing to double-append")
    rows = boxscores[boxscores["season"] == season].copy()
    if rows.empty:
        raise SystemExit(f"boxscores carry no season {season}")
    for side in ("home_team", "away_team"):
        rows[side] = rows[side].map(lambda t: BOX_TO_CFBD.get(t, t))

    known = set(games["home_team"]) | set(games["away_team"])
    new_names = sorted((set(rows["home_team"]) | set(rows["away_team"])) - known)
    if new_names:
        print(f"{len(new_names)} team name(s) not seen in the games file before "
              f"(FCS visitors are expected here — check for split spellings):")
        for name in new_names:
            print(f"  {name}")

    rows["spread_line"] = np.nan
    rows["total_line"] = np.nan
    rows = rows[GAMES_COLUMNS]
    merged = pd.concat([games, rows], ignore_index=True)
    return merged.sort_values(["kickoff", "game_id"]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill one season of games from boxscores")
    parser.add_argument("--data", default="datasets/ncaaf")
    parser.add_argument("--season", type=int, required=True)
    args = parser.parse_args()

    folder = Path(args.data)
    games_path = folder / "games.parquet"
    box_path = next(folder.glob("boxscores_*.parquet"))
    games = pd.read_parquet(games_path)
    boxscores = pd.read_parquet(box_path)

    merged = backfill(games, boxscores, args.season)
    added = len(merged) - len(games)
    merged.to_parquet(games_path, index=False)
    print(f"appended {added} season-{args.season} games from {box_path.name} "
          f"→ {games_path} ({len(merged)} rows; backfilled rows carry no lines)")


if __name__ == "__main__":
    main()
