"""Today's home-run board — every projected bat ranked by P(goes deep).

Serves the two surfaces the HR model feeds:

* the **prop** (``batter_home_runs``, a 0.5 line), priced against the book's
  number when a prop snapshot is supplied, and
* DK's salary-free **"Home Runs" single-stat contest**, which is a pure
  ranking problem — no cap, no optimizer, just the top N expected home runs.
  With no line to beat, the model competes against a field's opinions rather
  than a priced market, which is where a lottery-ticket edge survives.

    python scripts/build_hr_board.py --statcast artifacts/statcast/<f>.parquet \
        --out artifacts/slate
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the daily HR board")
    parser.add_argument("--batters", default="datasets/mlb/batters.parquet")
    parser.add_argument("--games", default="datasets/mlb/games.parquet")
    parser.add_argument("--starters", default="datasets/mlb/starters.parquet")
    parser.add_argument("--statcast", default=None, help="Statcast snapshot parquet")
    parser.add_argument("--out", required=True, help="output folder")
    parser.add_argument("--roster-size", type=int, default=3,
                        help="picks for the DK single-stat contest")
    parser.add_argument("--season", type=int, default=0,
                        help="season to fit (0 = the newest in the bank)")
    args = parser.parse_args()

    from build_mlb_pitching import fetch_probables
    from velocity.models.props_hr import HomeRunModel

    batters = pd.read_parquet(args.batters)
    games = pd.read_parquet(args.games)
    starters = pd.read_parquet(args.starters)
    statcast = pd.read_parquet(args.statcast) if args.statcast else None
    season = args.season or int(games["season"].max())
    model = HomeRunModel.fit(batters, games, starters, statcast, season=season)
    if not model.batter_rate:
        print("no fitted batter rates; nothing to board")
        return
    print(f"fit on season {season}: {len(model.batter_rate)} batters, "
          f"league rate {model.league_rate:.4f}, "
          f"{len(model.park_factor)} parks, {len(model.pitcher_factor)} arms")

    # Today's probables give each side's opposing starter; the venue is the
    # home club. A game with no announced probable still boards — the batter
    # rate and park carry it, the pitcher term simply stays neutral.
    today = date.today()
    probables = fetch_probables(str(today), str(today + timedelta(days=1)))
    if not probables:
        print("no probables posted; nothing to board")
        return

    # Who plays where: the most recent lineup slot each batter held this season
    # is the honest pregame guess until the official card is posted.
    recent = batters.copy()
    recent["game_id"] = recent["game_id"].astype(str)
    ids = set(games.loc[games["season"] == season, "game_id"].astype(str))
    recent = recent[recent["game_id"].astype(str).isin(ids)]
    if "started" in recent.columns:
        recent = recent[recent["started"].astype(bool)]
    recent = recent.sort_values("game_id").drop_duplicates("batter_id", keep="last")
    slot_of = dict(zip(recent["batter_id"].astype(str),
                       recent["lineup_slot"].astype(int), strict=False))
    team_of = dict(zip(recent["batter_id"].astype(str),
                       recent["team"].astype(str), strict=False))
    name_of = dict(zip(recent["batter_id"].astype(str),
                       recent["batter_name"].astype(str), strict=False))

    rows: list[dict[str, object]] = []
    for (home, away, _k), (home_sp, away_sp) in probables.items():
        for team, opposing_sp in ((home, away_sp), (away, home_sp)):
            for pid, batter_team in team_of.items():
                if batter_team != team:
                    continue
                slot = slot_of.get(pid)
                p = model.probability(pid, opposing_starter=opposing_sp,
                                      venue=home, lineup_slot=slot)
                expected = model.expected_home_runs(
                    pid, opposing_starter=opposing_sp, venue=home,
                    lineup_slot=slot)
                if p is None or expected is None:
                    continue
                rows.append({
                    "batter_id": pid, "player": name_of.get(pid, pid),
                    "team": team, "opponent": away if team == home else home,
                    "venue": home, "lineup_slot": slot,
                    "opposing_starter": opposing_sp,
                    "p_home_run": round(p, 4),
                    "expected_home_runs": round(expected, 4),
                })
    if not rows:
        print("no batters matched today's teams; nothing to board")
        return

    board = pd.DataFrame(rows).sort_values("expected_home_runs", ascending=False)
    board = board.reset_index(drop=True)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = out / f"hr_board_mlb_{stamp}.parquet"
    board.assign(league="mlb").to_parquet(dest, index=False)
    print(f"\nwrote {len(board)} boarded bats to {dest}")

    picks = board.head(args.roster_size)
    print(f"\n=== DK single-stat HOME RUNS — top {args.roster_size} ===")
    print(picks[["player", "team", "opponent", "venue", "lineup_slot",
                 "p_home_run", "expected_home_runs"]].to_string(index=False))


if __name__ == "__main__":
    main()
