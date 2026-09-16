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
    parser.add_argument("--lineups", default=None,
                        help="banked bp_lineups_*.parquet — today's batting "
                             "orders, used where statsapi has not posted yet")
    parser.add_argument("--out", required=True, help="output folder")
    parser.add_argument("--roster-size", type=int, default=3,
                        help="picks for the DK single-stat contest")
    parser.add_argument("--season", type=int, default=0,
                        help="season to fit (0 = the newest in the bank)")
    args = parser.parse_args()

    from build_dfs_lineup import apply_confirmed_cards, apply_projected_cards
    from build_mlb_pitching import fetch_probables
    from velocity.models.props_hr import HomeRunModel

    batters = pd.read_parquet(args.batters)
    games = pd.read_parquet(args.games)
    starters = pd.read_parquet(args.starters)
    statcast = pd.read_parquet(args.statcast) if args.statcast else None
    lineups = pd.read_parquet(args.lineups) if args.lineups else None
    if lineups is not None and "league" in lineups.columns:
        lineups = lineups[lineups["league"].astype(str) == "mlb"]
    season = args.season or int(games["season"].max())
    model = HomeRunModel.fit(batters, games, starters, statcast, season=season)
    if not model.batter_rate:
        print("no fitted batter rates; nothing to board")
        return
    print(f"fit on season {season}: {len(model.batter_rate)} batters, "
          f"league rate {model.league_rate:.4f}, "
          f"{len(model.park_factor)} parks, {len(model.pitcher_factor)} arms, "
          f"{model.statcast_batters} on a Statcast prior")
    # The batted-ball prior IS this model — barrel rate predicts next season's
    # HR/PA at r^2 ~ 0.39 against prior-season HR/PA's 0.36, and that gap is the
    # edge over a market anchored on the counting stat. Without it the model
    # degrades to plain shrinkage, silently, and looks exactly like a healthy
    # fit: the live board ran that way for months because the workflow
    # collected the Savant snapshot and then called this script without
    # --statcast. Say so where a run log will show it.
    if not model.statcast_batters:
        print("::warning title=Home-run board has no Statcast prior::"
              "every batter fell back to the league rate — the board is running "
              "on plain shrinkage, which is the thing the model exists to beat. "
              + ("--statcast was not passed." if args.statcast is None else
                 f"--statcast {args.statcast} had no usable barrel_rate rows."))

    # Today's probables give each side's opposing starter; the venue is the
    # home club. A game with no announced probable still boards — the batter
    # rate and park carry it, the pitcher term simply stays neutral.
    today = date.today()
    tomorrow = today + timedelta(days=1)
    probables = fetch_probables(str(today), str(tomorrow))
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

    # Two card sources, layered lowest-priority first.
    #
    # BettingPros posts hours earlier than statsapi and covers the whole slate
    # (2026-09-16: sixty of sixty sides at 16:53 UTC against statsapi's eight
    # at 18:55), so it fills the window the live slate actually runs in. Then
    # statsapi's confirmed card overrides it wherever it exists — it is the
    # manager's order rather than the book's read of it, and it is keyed on the
    # same MLBAM ids the banks use, with no name matching in between.
    #
    # Both layers express "no opinion" the same way — a team nobody carded
    # stays unrestricted — which is what lets them compose.
    eligible = None
    if lineups is not None and not lineups.empty:
        from velocity.wagering.props_slate import build_name_index

        index = build_name_index(
            batters.rename(columns={"batter_id": "player_id",
                                    "batter_name": "player_name"})
        )
        eligible = apply_projected_cards(slot_of, team_of, lineups, index)
    confirmed = apply_confirmed_cards(slot_of, team_of, str(today), str(tomorrow))
    if confirmed is not None:
        # Intersect rather than replace: statsapi calls every bat on an
        # unposted team eligible, which would otherwise undo the restriction
        # BettingPros already has for that team.
        eligible = confirmed if eligible is None else (eligible & confirmed)

    rows: list[dict[str, object]] = []
    for (home, away, _k), (home_sp, away_sp) in probables.items():
        for team, opposing_sp in ((home, away_sp), (away, home_sp)):
            for pid, batter_team in team_of.items():
                if batter_team != team:
                    continue
                if eligible is not None and pid not in eligible:
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
