"""Backtest the DK lineup builders against DK's own history — classic and showdown.

The optimizers are exact by construction; the tests pin that. This asks the
question exactness cannot answer: **do our projections, run through them,
build rosters worth entering?**

Both halves are free. Historical boards come from walking retired DK
draft-group ids (``scripts/harvest_dk_history.py``) — real salaries, real
pools, real captain prices. Actual points come from the banked box scores,
which carry the DK scoring line for every batter and starting pitcher.

Everything the pool knows is **pregame**, which took two passes to get right:

* a player's projection uses his most recent starting lineup slot BEFORE
  this game, never the slot he actually hit in;
* every board player with a fitted rate stays in the pool whether or not he
  ended up appearing, and one who never appears scores the 0.0 DK pays.
  Restricting the pool to players who turned up would hand every build a
  scratch-proof roster nobody could have entered;
* the opposing starter comes off DK's own probable flag, and the venue off
  the schedule — both public before lock.

Three comparisons per board:

* **salary** — the same optimizer run on DK's prices. DK's salary IS the
  market's projection, so beating it is the whole claim.
* **field** — random legal rosters, giving each build a percentile rather
  than a bare total (contests pay rank, not points).
* **ceiling** — the optimizer run on the actuals: the best roster that
  existed. Nobody hits it; the gap sizes the room.

    python scripts/validate_dfs_lineups.py --format showdown \\
        --boards 'artifacts/dk_history/dk_history_mlb_*.parquet'
"""

from __future__ import annotations

import argparse
import glob

import numpy as np
import pandas as pd
from velocity.dfs.backtest import (
    board_games,
    board_probables,
    norm,
    player_day_index,
    prepare_banks,
    random_rosters,
    realized,
    recent_slots,
)
from velocity.dfs.optimizer import MLB_CLASSIC
from velocity.dfs.pipeline import solve_mlb_lineup
from velocity.dfs.showdown import MLB_SHOWDOWN, build_showdown, showdown_board
from velocity.models.dfs_mlb import DfsMlbModel

FIELD_SAMPLES = 300
# The MLB classic position quota, for sampling a legal random roster.
CLASSIC_GROUPS = (("P", 2), ("C", 1), ("1B", 1), ("2B", 1), ("3B", 1),
                  ("SS", 1), ("OF", 3))
_PITCHER_POSITIONS = frozenset({"P", "SP", "RP"})


def load_boards(pattern: str, board_format: str) -> pd.DataFrame:
    """Every harvested board of one shape, collapsed to one row per player."""
    frames = []
    for path in sorted(glob.glob(pattern)):
        raw = pd.read_parquet(path)
        if "format" in raw.columns:
            raw = raw[raw["format"] == board_format]
        if raw.empty:
            continue
        for _gid, group in raw.groupby(raw["draft_group_id"].astype(str)):
            board = showdown_board(group) if board_format == "showdown" else group
            if len(board) >= 12:  # a real board, not a stub
                frames.append(board)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def name_ids(bat: pd.DataFrame, sp: pd.DataFrame) -> tuple[dict, dict]:
    """Normalized name → bank id, for batters and starters.

    Identity, not outcome: a player's id does not depend on what he did in
    the game being scored, so resolving it from the whole bank leaks nothing.
    """
    batters = {str(k): str(v) for k, v in
               zip(bat["key"], bat["batter_id"], strict=True)}
    starters = {str(k): str(v) for k, v in
                zip(sp["key"], sp["starter_id"], strict=True)}
    return batters, starters


def build_pool(board, *, model, index, played, slots, batter_ids, starter_ids,
               board_format: str, confirmed: bool = False) -> pd.DataFrame:
    """One board priced by the model and scored by the box score.

    ``confirmed`` selects WHEN the roster is built, which turns out to matter
    more than any projection term. False is the early build: nobody knows the
    card, so every banked bat is choosable and his slot is his most recent
    one. True is the build most DFS players actually make — after lineups
    post — so the pool is the announced nine per side and the batting order
    is a fact rather than a guess.
    """
    games_of = board_games(board, index)
    probables = board_probables(board)
    venue_of = {str(row["game_id"]): str(row["home_team"])
                for row in played.to_dict("records")}
    # Which announced starter each DK team FACES, from the board itself.
    faces: dict[tuple[str, str], str] = {}
    for competition, names in probables.items():
        rows = board[(board["competition"].astype(str) == competition)
                     & board["probable"].fillna(False).astype(bool)]
        by_team = dict(zip(rows["team"].astype(str), rows["player_name"].astype(str),
                           strict=True))
        for team in by_team:
            other = [n for t, n in by_team.items() if t != team]
            if len(names) >= 2 and other:
                faces[(competition, team)] = other[0]

    rows_out: list[dict[str, object]] = []
    for entry in board.to_dict("records"):
        key = norm(entry["player_name"])
        stamp = pd.Timestamp(entry["kickoff"])
        if pd.isna(stamp):
            continue
        competition = str(entry["competition"])
        position = str(entry.get("position") or "").upper().strip()
        head = position.split("/")[0]
        is_pitcher = head in _PITCHER_POSITIONS
        appeared = index.get((key, stamp.normalize().date()))
        if is_pitcher:
            # The live pool keeps only DK's flagged probables: DK lists every
            # rostered arm, and a non-probable never starts.
            if not bool(entry.get("probable")):
                continue
            pid = starter_ids.get(key)
            points = None if pid is None else model.project_pitcher(pid)
        else:
            if confirmed and not (appeared and appeared["started"]):
                continue  # not in the posted card
            pid = batter_ids.get(key)
            opponent = faces.get((competition, str(entry.get("team"))))
            slot = (appeared["lineup_slot"] if confirmed and appeared
                    else slots.get(pid))
            points = None if pid is None else model.project_hitter(
                pid,
                opposing_starter=(starter_ids.get(norm(opponent))
                                  if opponent else None),
                venue=venue_of.get(games_of.get(competition, "")),
                lineup_slot=slot)
        if points is None:
            continue  # a player the bank has never seen is never rostered
        rows_out.append({
            "player_name": entry["player_name"],
            "position": "P" if is_pitcher else (head or "UTIL"),
            "team": entry.get("team"), "salary": int(entry["salary"]),
            "captain_salary": int(entry.get("captain_salary")
                                  or round(int(entry["salary"]) * 1.5)),
            "points": float(points),
            # DK pays 0.0 to a player who never appears; so do we.
            "actual": float(appeared["actual"]) if appeared else 0.0,
            "played": appeared is not None,
        })
    pool = pd.DataFrame(rows_out)
    if pool.empty or board_format != "classic":
        return pool
    # Multi-eligibility combos price at the first listed position, as live.
    return pool


def solve(pool: pd.DataFrame, board_format: str, column: str = "points"):
    """Build the best roster for a format from an arbitrary points column."""
    priced = pool.assign(points=pool[column])
    if board_format == "showdown":
        return build_showdown(priced, spec=MLB_SHOWDOWN)
    return solve_mlb_lineup(priced, MLB_CLASSIC)


def evaluate(boards, bat, sp, played, *, board_format, step_days, min_train,
             confirmed=False):
    rng = np.random.default_rng(17)
    index = player_day_index(bat, sp)
    batter_ids, starter_ids = name_ids(bat, sp)
    boards = boards.copy()
    boards["kickoff"] = pd.to_datetime(boards["kickoff"], errors="coerce")
    boards = boards.dropna(subset=["kickoff"])
    if boards.empty:
        return pd.DataFrame(), pd.DataFrame()

    size = 6 if board_format == "showdown" else 10
    captain = board_format == "showdown"
    groups = None if captain else CLASSIC_GROUPS
    min_teams = 2 if captain else 1

    rows, players = [], []
    start = boards["kickoff"].min().normalize()
    for cutoff in pd.date_range(start, boards["kickoff"].max(),
                                freq=f"{step_days}D"):
        train = played[played["kickoff"] < cutoff]
        if len(train) < min_train:
            continue
        train_ids = set(train["game_id"])
        model = DfsMlbModel.fit(bat[bat["game_id"].isin(train_ids)],
                                sp[sp["game_id"].isin(train_ids)], train)
        slots = recent_slots(bat, cutoff)
        window = boards[(boards["kickoff"] >= cutoff)
                        & (boards["kickoff"] < cutoff + pd.Timedelta(days=step_days))]
        for gid, board in window.groupby(window["draft_group_id"].astype(str)):
            pool = build_pool(board, model=model, index=index, played=played,
                              slots=slots, batter_ids=batter_ids,
                              starter_ids=starter_ids, board_format=board_format,
                              confirmed=confirmed)
            if len(pool) < size * 2:
                continue
            ours = solve(pool, board_format)
            chalk = solve(pool.assign(chalk=pool["salary"] / 1000.0),
                          board_format, "chalk")
            ceiling = solve(pool, board_format, "actual")
            if ours is None or chalk is None or ceiling is None:
                continue
            field = random_rosters(pool, rng, size=size, n=FIELD_SAMPLES,
                                   captain=captain, min_teams=min_teams,
                                   groups=groups)
            actual_of = dict(zip(pool["player_name"], pool["actual"], strict=True))
            mine = realized(ours.slots, actual_of)
            players.append(pool.assign(draft_group_id=str(gid),
                                       kickoff=board["kickoff"].min()))
            rows.append({
                "draft_group_id": str(gid),
                "kickoff": board["kickoff"].min(),
                "n_pool": len(pool),
                "n_games": int(board["competition"].nunique()),
                "ours": mine,
                "chalk": realized(chalk.slots, actual_of),
                "ceiling": realized(ceiling.slots, actual_of),
                "field_mean": float(field.mean()) if len(field) else np.nan,
                "percentile": (float((field < mine).mean())
                               if len(field) else np.nan),
                "chalk_percentile": (
                    float((field < realized(chalk.slots, actual_of)).mean())
                    if len(field) else np.nan),
                "scratched": int((~pool.loc[
                    pool["player_name"].isin([s.player_name for s in ours.slots]),
                    "played"]).sum()),
            })
    return (pd.DataFrame(rows),
            pd.concat(players, ignore_index=True) if players else pd.DataFrame())


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the DK lineup builders")
    parser.add_argument("--boards", required=True, help="harvested boards (glob ok)")
    parser.add_argument("--format", default="showdown",
                        choices=("showdown", "classic"))
    parser.add_argument("--batters", default="datasets/mlb/batters.parquet")
    parser.add_argument("--starters", default="datasets/mlb/starters.parquet")
    parser.add_argument("--games", default="datasets/mlb/games.parquet")
    parser.add_argument("--step-days", type=int, default=14)
    parser.add_argument("--min-train-games", type=int, default=400)
    parser.add_argument("--lineups", default="early",
                        choices=("early", "confirmed"),
                        help="when the roster is built: 'early' (before the "
                             "card posts, every banked bat choosable) or "
                             "'confirmed' (after it posts — the announced "
                             "nine, batting order known)")
    parser.add_argument("--out", default=None, help="write the per-board frame here")
    args = parser.parse_args()

    boards = load_boards(args.boards, args.format)
    if boards.empty:
        print(f"no {args.format} boards in the harvest; nothing to backtest")
        return
    print(f"{boards['draft_group_id'].nunique()} {args.format} boards "
          f"/ {len(boards):,} priced players")

    bat, sp, played = prepare_banks(
        pd.read_parquet(args.batters), pd.read_parquet(args.starters),
        pd.read_parquet(args.games))
    result, priced = evaluate(boards, bat, sp, played, board_format=args.format,
                              step_days=args.step_days,
                              min_train=args.min_train_games,
                              confirmed=args.lineups == "confirmed")
    if result.empty:
        print("no board matched a banked box score; nothing to report")
        return

    print(f"\nbuilt {args.lineups} (before the card posts)" if args.lineups == "early"
          else "\nbuilt confirmed (after the card posts)")
    print(f"scored {len(result)} boards "
          f"({result['kickoff'].min().date()} .. {result['kickoff'].max().date()})")
    print(f"  our build          {result['ours'].mean():7.2f} DK pts")
    print(f"  DK-salary build    {result['chalk'].mean():7.2f}")
    print(f"  random field       {result['field_mean'].mean():7.2f}")
    print(f"  retrospective best {result['ceiling'].mean():7.2f}")
    beat = float((result["ours"] > result["chalk"]).mean())
    print(f"\n  beat the salary build on {beat:.1%} of boards")
    print(f"  mean field percentile     {result['percentile'].mean():.1%} "
          f"(the salary build: {result['chalk_percentile'].mean():.1%})")
    share = float((result["ours"] / result["ceiling"].replace(0, np.nan)).mean())
    print(f"  share of the achievable ceiling  {share:.1%}")
    print(f"  rostered a player who never appeared: "
          f"{result['scratched'].mean():.2f} per roster")
    diff = result["ours"] - result["chalk"]
    if len(diff) > 2 and diff.std(ddof=1) > 0:
        t = float(diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff))))
        print(f"  ours - salary: {diff.mean():+.2f} DK pts (t = {t:+.2f}, "
              f"n = {len(diff)})")

    if not priced.empty:
        # Level calibration by class: the optimizer chooses ACROSS classes, so
        # a systematic level error builds the wrong roster from a right order.
        klass = priced["position"].astype(str).isin({"P"}).map(
            {True: "pitchers", False: "hitters"})
        print("\n  projection level by class:")
        for name, group in priced.groupby(klass):
            bias = group["points"].mean() - group["actual"].mean()
            print(f"    {name:9} projected {group['points'].mean():6.2f} "
                  f"vs actual {group['actual'].mean():6.2f} "
                  f"({bias:+.2f}, n = {len(group):,})")

    if args.out:
        result.to_parquet(args.out, index=False)
        print(f"\nwrote per-board results to {args.out}")


if __name__ == "__main__":
    main()
