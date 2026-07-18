"""Build a compact canonical NCAAF games dataset from a box-score CSV.

The uploaded ``cfb_box-scores_2002-2025.csv`` is game-level (no play-by-play), so
it feeds the schedule-only **scores** rating rather than EPA. This script maps it
onto the canonical :class:`~velocity.store.schema.Games` schema and writes
``datasets/ncaaf/games.parquet``.

Details handled:

* **game_id** is synthesized from season + date + teams (the source has none); a
  team plays at most once per date, so this is unique.
* **week** is NaN for postseason rows — filled with a high value (20) so bowls
  sort *after* the regular season in the walk-forward, and mapped ``game_type``
  ('regular'→REG, 'post'→POST).
* **kickoff** combines the date and Eastern kickoff time when present.

Run from the repo root::

    python scripts/build_ncaaf_boxscores.py --src /tmp/cfb/cfb_box-scores_2002-2025.csv
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

SEASON_TYPE_MAP = {"regular": "REG", "post": "POST", "postseason": "POST", "preseason": "PRE"}
POSTSEASON_WEEK = 20


def _slug(value: object) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", str(value))


def build(src: Path, out: Path) -> int:
    df = pd.read_csv(src, low_memory=False)
    df = df.dropna(subset=["score_home", "score_away", "home", "away", "date"])

    season = df["season"].astype(int)
    week = pd.to_numeric(df["week"], errors="coerce").fillna(POSTSEASON_WEEK).astype(int)
    season_type = df["game_type"].astype(str).str.lower().map(SEASON_TYPE_MAP).fillna("REG")

    time = df.get("time_et").fillna("") if "time_et" in df.columns else ""
    kickoff = pd.to_datetime(
        df["date"].astype(str) + " " + (time if isinstance(time, str) else time.astype(str)),
        errors="coerce",
    )
    kickoff = kickoff.fillna(pd.to_datetime(df["date"], errors="coerce"))

    game_id = (
        season.astype(str)
        + "_" + df["date"].astype(str).map(_slug)
        + "_" + df["away"].map(_slug)
        + "_" + df["home"].map(_slug)
    )

    games = pd.DataFrame(
        {
            "game_id": game_id,
            "league": "ncaaf",
            "season": season,
            "week": week,
            "season_type": season_type,
            "kickoff": kickoff,
            "home_team": df["home"].astype(str),
            "away_team": df["away"].astype(str),
            "neutral_site": df["neutral"].astype(bool),
            "roof": None,
            "surface": None,
            "home_score": pd.to_numeric(df["score_home"], errors="coerce"),
            "away_score": pd.to_numeric(df["score_away"], errors="coerce"),
        }
    )
    # A team plays once per date; drop any exact game_id collision defensively.
    games = games.drop_duplicates("game_id").reset_index(drop=True)

    out.mkdir(parents=True, exist_ok=True)
    games.to_parquet(out / "games.parquet", index=False)
    return len(games)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build canonical NCAAF games from box scores")
    parser.add_argument("--src", required=True, help="path to cfb_box-scores CSV")
    parser.add_argument("--out", default="datasets/ncaaf", help="output folder")
    args = parser.parse_args()
    n = build(Path(args.src), Path(args.out))
    print(f"wrote {n} games to {args.out}/games.parquet")


if __name__ == "__main__":
    main()
