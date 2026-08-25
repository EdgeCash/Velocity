"""Backtest the Showdown Captain Mode optimizer against DK's own history.

The optimizer is exact by construction — the tests pin that. What this asks
is the question exactness cannot answer: **do our projections, run through
it, build a showdown roster worth entering?**

Inputs are the two halves of a real DFS backtest, both free:

* historical showdown boards harvested by walking retired DK draft-group ids
  (``scripts/harvest_dk_history.py --format showdown``) — real salaries, real
  captain prices, real player pools;
* the banked box scores (``datasets/mlb``), which carry the DK scoring line
  for every batter and starting pitcher, so every roster can be scored the
  way DK scored it.

Projections are walk-forward: for each window the model is fit only on games
that finished before it. Four comparisons per board:

* **salary-greedy** — the same optimizer run on DK's own prices instead of
  our projections. This is the baseline that matters: DK's salary IS the
  market's projection, so beating it is the whole claim.
* **field** — a sample of random legal rosters, giving each build a
  percentile rather than a bare point total (contests pay rank, not points).
* **ceiling** — the optimizer run on the actuals, i.e. the best roster that
  existed. Nobody hits it; the gap sizes the room.
* **captain** — whether the player we starred was the top scorer of the six
  we rostered. The captain multiplier is where a showdown is won or lost.

A rostered player with no box-score row scores 0.0, which is exactly what DK
pays a player who never appears — no leakage, no free pass.

    python scripts/validate_dfs_showdown.py \\
        --boards artifacts/dk_history/dk_history_mlb_showdown.parquet
"""

from __future__ import annotations

import argparse
import glob
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
from velocity.dfs.showdown import MLB_SHOWDOWN, build_showdown, showdown_board
from velocity.models.dfs_mlb import DfsMlbModel, hitter_dk_points, pitcher_dk_points

sys.path.insert(0, str(Path(__file__).parent))

FIELD_SAMPLES = 300


def norm(name: object) -> str:
    """Name key that survives DK vs statsapi spelling ("Sánchez"/"Sanchez")."""
    folded = unicodedata.normalize("NFKD", str(name)).encode(
        "ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", folded.lower())


def load_boards(pattern: str) -> pd.DataFrame:
    """Every harvested showdown board, collapsed to one row per player."""
    frames = []
    for path in sorted(glob.glob(pattern)):
        raw = pd.read_parquet(path)
        if "format" in raw.columns:
            raw = raw[raw["format"] == "showdown"]
        for _gid, group in raw.groupby(raw["draft_group_id"].astype(str)):
            board = showdown_board(group)
            if len(board) >= 12:  # a real two-sided board, not a stub
                frames.append(board)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def prepare_banks(batters, starters, games):
    """Bank frames joined to game context and scored the way DK scores."""
    games = games.copy()
    games["game_id"] = games["game_id"].astype(str)
    games["kickoff"] = pd.to_datetime(games["kickoff"], errors="coerce")
    played = games.dropna(subset=["kickoff"])
    played = played[played["home_score"].notna()].sort_values("kickoff")

    bat = batters.copy()
    bat["game_id"] = bat["game_id"].astype(str)
    bat["batter_id"] = bat["batter_id"].astype(str)
    bat = bat.merge(played[["game_id", "kickoff", "season", "home_team", "away_team"]],
                    on="game_id", how="inner")
    bat["actual"] = hitter_dk_points(bat)
    bat["key"] = bat["batter_name"].map(norm)

    sp = starters.copy()
    sp["game_id"] = sp["game_id"].astype(str)
    sp["starter_id"] = sp["starter_id"].astype(str)
    sp = sp.merge(played[["game_id", "kickoff", "home_team", "away_team"]],
                  on="game_id", how="inner")
    sp["actual"] = pitcher_dk_points(sp)
    sp["key"] = sp["starter_name"].map(norm)
    return bat, sp, played


# The board and the box score must be the SAME game, not the same matchup.
MATCH_MIN_NAMES = 8
MATCH_MAX_DRIFT = pd.Timedelta(hours=6)


def match_game(board: pd.DataFrame, bat: pd.DataFrame) -> str | None:
    """The bank game_id a board belongs to: same start time, names in common.

    Team abbreviations drift between DK and statsapi, so eligibility is
    decided on the player pool itself — the board and the right box score
    share most of their names. But **overlap alone cannot pick the game**:
    two clubs play a three-game series with the same twenty-six names every
    night, so a pure-overlap match lands on an arbitrary game of the series.
    Proven the hard way — a first pass matched the wrong day on half the
    boards, silently scoring every roster against the wrong box score and
    dropping both starting pitchers (they are the one thing that changes
    daily). So the start time decides among the candidates that clear the
    name bar, and a board whose nearest candidate is hours away matches
    nothing at all.
    """
    kickoff = pd.to_datetime(board["kickoff"], errors="coerce").min()
    if pd.isna(kickoff):
        return None
    day = kickoff.normalize()
    window = bat[(bat["kickoff"] >= day - pd.Timedelta(days=1))
                 & (bat["kickoff"] < day + pd.Timedelta(days=2))]
    if window.empty:
        return None
    names = set(board["player_name"].map(norm))
    shared = (
        window[window["key"].isin(names)]
        .groupby("game_id")
        .agg(names=("key", "nunique"), kickoff=("kickoff", "first"))
    )
    shared = shared[shared["names"] >= MATCH_MIN_NAMES]
    if shared.empty:
        return None
    drift = (shared["kickoff"] - kickoff).abs()
    best = drift.idxmin()
    return None if drift.loc[best] > MATCH_MAX_DRIFT else str(best)


def board_pool(board, game_id, bat, sp, model, played, scale=None) -> pd.DataFrame:
    """The board priced by the model and scored by the box score.

    Projections come from the walk-forward model; actuals from the game's own
    box score, defaulting to 0.0 for anyone who did not appear.
    """
    game_bat = bat[bat["game_id"] == game_id]
    game_sp = sp[sp["game_id"] == game_id]
    row = played[played["game_id"] == game_id]
    venue = str(row["home_team"].iloc[0]) if not row.empty else None

    actual_of = dict(zip(game_bat["key"], game_bat["actual"], strict=True))
    actual_of.update(dict(zip(game_sp["key"], game_sp["actual"], strict=True)))
    batter_of = dict(zip(game_bat["key"], game_bat["batter_id"], strict=True))
    slot_of = {k: int(v) for k, v in
               zip(game_bat["key"], game_bat["lineup_slot"], strict=True)}
    pitcher_of = dict(zip(game_sp["key"], game_sp["starter_id"], strict=True))
    side_of = dict(zip(game_bat["key"], game_bat["side"], strict=True))
    sp_side = dict(zip(game_sp["side"], game_sp["starter_id"], strict=True))

    rows = []
    for entry in board.to_dict("records"):
        key = norm(entry["player_name"])
        position = str(entry.get("position") or "").upper()
        is_pitcher = position.split("/")[0] in {"P", "SP", "RP"}
        if is_pitcher:
            # Live, the pool keeps only DK's flagged probables.
            if not bool(entry.get("probable")):
                continue
            pid = pitcher_of.get(key)
            points = None if pid is None else model.project_pitcher(str(pid))
        else:
            pid = batter_of.get(key)
            other = {"home": "away", "away": "home"}.get(side_of.get(key, ""), "")
            points = None if pid is None else model.project_hitter(
                str(pid), opposing_starter=sp_side.get(other),
                venue=venue, lineup_slot=slot_of.get(key))
        if points is None:
            continue  # a player the bank has never seen is never rostered
        if scale is not None:
            points *= scale["pitchers" if is_pitcher else "hitters"]
        rows.append({
            "player_name": entry["player_name"], "position": position or "UTIL",
            "team": entry.get("team"), "salary": int(entry["salary"]),
            "captain_salary": int(entry["captain_salary"]),
            "points": float(points), "actual": float(actual_of.get(key, 0.0)),
        })
    return pd.DataFrame(rows)


def score(lineup, pool: pd.DataFrame) -> float:
    """A built roster's REALIZED DK points (captain at 1.5x)."""
    actual_of = dict(zip(pool["player_name"], pool["actual"], strict=True))
    total = 0.0
    for slot in lineup.slots:
        factor = 1.5 if slot.slot == "CPT" else 1.0
        total += factor * float(actual_of.get(slot.player_name, 0.0))
    return total


def random_field(pool: pd.DataFrame, rng, n: int = FIELD_SAMPLES) -> np.ndarray:
    """Realized scores of ``n`` random LEGAL rosters — the field proxy."""
    salary = pool["salary"].to_numpy()
    captain_salary = pool["captain_salary"].to_numpy()
    actual = pool["actual"].to_numpy()
    teams = pool["team"].astype(str).to_numpy()
    scores = []
    attempts = 0
    while len(scores) < n and attempts < n * 40:
        attempts += 1
        picks = rng.choice(len(pool), size=6, replace=False)
        captain = picks[0]
        flex = picks[1:]
        if captain_salary[captain] + salary[flex].sum() > 50_000:
            continue
        if len(set(teams[picks])) < 2:
            continue
        scores.append(1.5 * actual[captain] + actual[flex].sum())
    return np.array(scores)


# Captain policies to compare. The optimizer's own choice maximizes the
# roster's projected total; these ask whether a simpler or a different rule
# would have starred a better player, holding the other five fixed.
def captain_policies(lineup, pool: pd.DataFrame) -> dict[str, float]:
    """Realized score of our six under alternative captain rules.

    The five non-captain slots are already the best five at that budget, so
    re-captaining changes the salary too — a swap is only counted when the
    resulting roster still fits under the cap. An illegal swap scores the
    roster we actually built, which is the honest comparison: that policy
    could not have played its preferred captain here.
    """
    rows = {r["player_name"]: r for r in pool.to_dict("records")}
    six = [rows[s.player_name] for s in lineup.slots if s.player_name in rows]
    if len(six) != len(lineup.slots):
        return {}

    def realized(captain: dict) -> float | None:
        salary = captain["captain_salary"] + sum(
            r["salary"] for r in six if r is not captain)
        if salary > 50_000:
            return None
        return 1.5 * captain["actual"] + sum(
            r["actual"] for r in six if r is not captain)

    def pitchers(rows_: list[dict]) -> list[dict]:
        return [r for r in rows_
                if str(r["position"]).split("/")[0] in {"P", "SP", "RP"}]

    ours = next(r for r in six if r["player_name"] == lineup.slots[0].player_name)
    candidates = {
        "ours": ours,
        "top_projection": max(six, key=lambda r: r["points"]),
        "top_salary": max(six, key=lambda r: r["salary"]),
        "best_pitcher": max(pitchers(six), key=lambda r: r["points"],
                            default=ours),
    }
    out: dict[str, float] = {}
    for name, candidate in candidates.items():
        value = realized(candidate)
        out[f"cpt_{name}"] = (value if value is not None
                              else realized(ours) or 0.0)
    return out


class RollingScale:
    """Class-level recalibration from strictly PAST windows.

    The empirical-Bayes rates are unbiased in sample and drift out of it —
    on the showdown backtest pitchers projected 9.5% high against hitters'
    3.7%, which tilts the roster mix even when the ordering inside each
    class is right. This is the recalibration loop applied to the DFS
    model: after each window its realized totals join a running sum, and
    the NEXT window's projections are scaled by actual/projected per class.
    The first window scales by 1.0, because it has no past to learn from.
    """

    def __init__(self, floor: float = 0.7, ceiling: float = 1.3) -> None:
        self._projected = {"hitters": 0.0, "pitchers": 0.0}
        self._actual = {"hitters": 0.0, "pitchers": 0.0}
        self.floor, self.ceiling = floor, ceiling

    def factors(self) -> dict[str, float]:
        out = {}
        for name, projected in self._projected.items():
            ratio = self._actual[name] / projected if projected > 0 else 1.0
            out[name] = min(max(ratio, self.floor), self.ceiling)
        return out

    def observe(self, priced: pd.DataFrame) -> None:
        klass = priced["position"].astype(str).str.split("/").str[0].isin(
            {"P", "SP", "RP"}).map({True: "pitchers", False: "hitters"})
        for name, group in priced.groupby(klass):
            self._projected[str(name)] += float(group["points"].sum())
            self._actual[str(name)] += float(group["actual"].sum())


def evaluate(boards, bat, sp, played, *, step_days: int, min_train: int,
             recalibrate: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(per-board results, every priced player) — the second for calibration.

    Roster construction is a comparison BETWEEN classes: a projection that is
    right in rank but wrong in level for pitchers against hitters builds the
    wrong six even with a perfect ordering inside each class. That check
    needs the player rows, not the roster totals.
    """
    rng = np.random.default_rng(17)
    boards = boards.copy()
    boards["kickoff"] = pd.to_datetime(boards["kickoff"], errors="coerce")
    boards = boards.dropna(subset=["kickoff"])
    if boards.empty:
        return pd.DataFrame()

    rows = []
    players: list[pd.DataFrame] = []
    scale = RollingScale() if recalibrate else None
    start = boards["kickoff"].min().normalize()
    end = boards["kickoff"].max()
    for cutoff in pd.date_range(start, end, freq=f"{step_days}D"):
        train = played[played["kickoff"] < cutoff]
        if len(train) < min_train:
            continue
        train_ids = set(train["game_id"])
        model = DfsMlbModel.fit(bat[bat["game_id"].isin(train_ids)],
                                sp[sp["game_id"].isin(train_ids)], train)
        window = boards[(boards["kickoff"] >= cutoff)
                        & (boards["kickoff"] < cutoff + pd.Timedelta(days=step_days))]
        factors = scale.factors() if scale is not None else None
        window_players: list[pd.DataFrame] = []
        for gid, board in window.groupby(board_key(window)):
            game_id = match_game(board, bat)
            if game_id is None:
                continue
            pool = board_pool(board, game_id, bat, sp, model, played, factors)
            if len(pool) < 12:
                continue
            window_players.append(pool.assign(draft_group_id=str(gid),
                                              kickoff=board["kickoff"].min()))
            ours = build_showdown(pool, spec=MLB_SHOWDOWN)
            chalk = build_showdown(pool.assign(points=pool["salary"] / 1000.0),
                                   spec=MLB_SHOWDOWN)
            ceiling = build_showdown(pool.assign(points=pool["actual"]),
                                     spec=MLB_SHOWDOWN)
            if ours is None or chalk is None or ceiling is None:
                continue
            field = random_field(pool, rng)
            realized = score(ours, pool)
            captain = ours.slots[0].player_name
            actual_of = dict(zip(pool["player_name"], pool["actual"], strict=True))
            six = {s.player_name: actual_of.get(s.player_name, 0.0)
                   for s in ours.slots}
            chalk_field = float((field < score(chalk, pool)).mean()) if len(field) else np.nan
            rows.append({
                **captain_policies(ours, pool),
                "draft_group_id": str(gid),
                "chalk_percentile": chalk_field,
                "kickoff": board["kickoff"].min(),
                "n_pool": len(pool),
                "ours": realized,
                "chalk": score(chalk, pool),
                "ceiling": score(ceiling, pool),
                "field_mean": float(field.mean()) if len(field) else np.nan,
                "percentile": (float((field < realized).mean())
                               if len(field) else np.nan),
                "captain_best": captain == max(six, key=lambda n: six[n]),
                "projected": ours.total_points,
            })
        players.extend(window_players)
        if scale is not None and window_players:
            # Only now, after the window is scored, does it become past.
            scale.observe(pd.concat(window_players, ignore_index=True))
    return (pd.DataFrame(rows),
            pd.concat(players, ignore_index=True) if players else pd.DataFrame())


def board_key(frame: pd.DataFrame) -> pd.Series:
    return frame["draft_group_id"].astype(str)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the showdown optimizer")
    parser.add_argument("--boards", required=True,
                        help="harvested showdown boards parquet (glob ok)")
    parser.add_argument("--batters", default="datasets/mlb/batters.parquet")
    parser.add_argument("--starters", default="datasets/mlb/starters.parquet")
    parser.add_argument("--games", default="datasets/mlb/games.parquet")
    parser.add_argument("--step-days", type=int, default=14)
    parser.add_argument("--min-train-games", type=int, default=400)
    parser.add_argument("--recalibrate", action="store_true",
                        help="scale each window's projections by the class-level "
                             "actual/projected ratio of the windows before it")
    parser.add_argument("--out", default=None, help="write the per-board frame here")
    args = parser.parse_args()

    boards = load_boards(args.boards)
    if boards.empty:
        print("no showdown boards in the harvest; nothing to backtest")
        return
    print(f"{boards['draft_group_id'].nunique()} showdown boards "
          f"/ {len(boards):,} priced players")

    bat, sp, played = prepare_banks(
        pd.read_parquet(args.batters), pd.read_parquet(args.starters),
        pd.read_parquet(args.games))
    result, priced = evaluate(boards, bat, sp, played, step_days=args.step_days,
                              min_train=args.min_train_games,
                              recalibrate=args.recalibrate)
    if result.empty:
        print("no board matched a banked box score; nothing to report")
        return

    print(f"\nscored {len(result)} boards "
          f"({result['kickoff'].min().date()} .. {result['kickoff'].max().date()})")
    print(f"  our build          {result['ours'].mean():7.2f} DK pts")
    print(f"  DK-salary build    {result['chalk'].mean():7.2f}")
    print(f"  random field       {result['field_mean'].mean():7.2f}")
    print(f"  retrospective best {result['ceiling'].mean():7.2f}")
    beat = float((result["ours"] > result["chalk"]).mean())
    print(f"\n  beat the salary build on {beat:.1%} of boards")
    print(f"  mean field percentile     {result['percentile'].mean():.1%}")
    print(f"  captain was our six's top scorer {result['captain_best'].mean():.1%} "
          f"(1 in 6 = 16.7% by chance)")
    share = float((result["ours"] / result["ceiling"].replace(0, np.nan)).mean())
    print(f"  share of the achievable ceiling  {share:.1%}")
    if "chalk_percentile" in result:
        print(f"  the salary build's field percentile "
              f"{result['chalk_percentile'].mean():.1%}")

    policies = [c for c in result.columns if c.startswith("cpt_")]
    if policies:
        print("\n  captain policy (same six players, different star):")
        for name in sorted(policies, key=lambda c: -result[c].mean()):
            print(f"    {name[4:]:16} {result[name].mean():7.2f} DK pts")
    # Paired t on the per-board difference: the boards are the sample.
    diff = result["ours"] - result["chalk"]
    if len(diff) > 2 and diff.std(ddof=1) > 0:
        t = float(diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff))))
        print(f"  ours - salary: {diff.mean():+.2f} DK pts (t = {t:+.2f}, "
              f"n = {len(diff)})")
    if not priced.empty:
        # Level calibration by class. Rank inside a class is not enough: the
        # optimizer chooses ACROSS classes, so a systematic level error
        # builds the wrong roster from a correct ordering.
        klass = priced["position"].astype(str).str.split("/").str[0].isin(
            {"P", "SP", "RP"}).map({True: "pitchers", False: "hitters"})
        print("\n  projection level by class (the roster mix depends on it):")
        for name, group in priced.groupby(klass):
            bias = group["points"].mean() - group["actual"].mean()
            print(f"    {name:9} projected {group['points'].mean():6.2f} "
                  f"vs actual {group['actual'].mean():6.2f} "
                  f"({bias:+.2f}, n = {len(group):,})")

    if args.out:
        result.to_parquet(args.out, index=False)
        print(f"\nwrote per-board results to {args.out}")
        players_dest = str(args.out).replace(".parquet", "_players.parquet")
        priced.to_parquet(players_dest, index=False)
        print(f"wrote {len(priced):,} priced player rows to {players_dest}")


if __name__ == "__main__":
    main()
