"""Does knowing the starting goalie help, and does GUESSING him help? (docs/BUILD_NHL.md)

`BUILD_NHL.md` H2 promoted the goalie decomposition at Brier **0.24258**, and
recorded that live pricing is **goalie-neutral** because no free confirmed-
starter feed exists pregame. Both statements are true, and together they hide a
third: the promoted number was measured with the evaluated game's *actual*
goalie plugged into the lookup, and production ships an empty lookup. **The
configuration that ships was never the configuration that was measured.**

H2's open items then propose plugging a starter feed in, on the reasoning that
it "only adds signal". A guess is not signal. ESPN's depth chart names a team's
nominal #1, and over the banked 2023–25 seasons that goalie starts about **60%**
of the time — so a depth-chart lookup is confidently wrong in two games of
five, and a wrong goalie does not merely fail to help: it prices the game as
someone else.

So this measures three lookups on one walk-forward, identical in every other
respect:

* ``neutral`` — the empty lookup. **What production actually does**, and the
  number `BUILD_NHL.md` does not have.
* ``depth-N`` — a point-in-time depth chart: the goalie with the most starts in
  that team's trailing N games, computed only from games already played. This
  is the ESPN depth chart's shape, and is if anything generous to it — it
  cannot be stale, and it is fitted to this exact rotation.
* ``oracle`` — the actual starter. Not shippable; it is the ceiling, and it is
  what the promoted 0.24258 measured.

The decision rule is the obvious one. If ``depth-N`` does not beat ``neutral``,
a depth-chart feed should not be wired, whatever the oracle says — the oracle
gap would then be the value of *confirmed* goalies, which is a different feed
and a different question.

    python scripts/nhl_goalie_lab.py --n-sims 4000
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from velocity.backtest.engine import BacktestConfig, walk_forward
from velocity.backtest.lab import (
    INSEASON_CALIBRATION,
    StarterAwareModel,
    mlb_starter_frame,
)
from velocity.features.team import fit_qb_ratings
from velocity.models.simulate import SimConfig

LEAGUE = "nhl"
Lookup = dict[tuple[str, str, object], tuple[str | None, str | None]]


def _empty_lines() -> pd.DataFrame:
    """The shaped-but-empty lines frame the engine slices; no wagering here."""
    cols = ["line_id", "game_id", "book", "market", "side",
            "price", "point", "timestamp", "is_closing"]
    frame = pd.DataFrame(columns=cols)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    return frame


def team_game_log(games: pd.DataFrame, starters: pd.DataFrame) -> pd.DataFrame:
    """One row per (team, game) in kickoff order, carrying that team's starter."""
    frame = games[["game_id", "season", "kickoff", "home_team", "away_team"]].copy()
    frame["kickoff"] = pd.to_datetime(frame["kickoff"])
    rows = []
    for game in frame.to_dict("records"):
        for side, team in (("home", game["home_team"]), ("away", game["away_team"])):
            rows.append({
                "game_id": game["game_id"], "kickoff": game["kickoff"],
                "season": game["season"], "team": str(team), "side": side,
            })
    long = pd.DataFrame(rows).merge(
        starters.drop_duplicates(subset=["game_id", "side"])[
            ["game_id", "side", "starter_id"]
        ],
        on=["game_id", "side"], how="left",
    )
    long["starter_id"] = long["starter_id"].astype("string")
    return long.sort_values(["kickoff", "game_id", "side"]).reset_index(drop=True)


def nominal_starters(long: pd.DataFrame, window: int) -> pd.DataFrame:
    """Each team-game's depth-chart #1, from that team's PRIOR games only.

    Point-in-time by construction: the count that names the nominal starter
    for game *i* is taken over games ``[i-window, i)``, so no game can inform
    its own prediction. A team with no prior banked start has no nominal
    starter and prices neutral, exactly as a missing depth chart would.
    """
    out = long.copy()
    out["nominal_id"] = pd.NA
    for _team, group in long.groupby("team", sort=False):
        ids = group["starter_id"].tolist()
        index = group.index.tolist()
        for i, row_index in enumerate(index):
            prior = [x for x in ids[max(0, i - window):i] if pd.notna(x)]
            if prior:
                out.at[row_index, "nominal_id"] = (
                    pd.Series(prior).value_counts().idxmax()
                )
    out["nominal_id"] = out["nominal_id"].astype("string")
    return out


def hit_rate(long: pd.DataFrame) -> tuple[float, int]:
    """How often the nominal #1 is the goalie who actually started."""
    scored = long.dropna(subset=["starter_id", "nominal_id"])
    if scored.empty:
        return float("nan"), 0
    hits = (scored["nominal_id"] == scored["starter_id"]).sum()
    return float(hits) / len(scored), len(scored)


def build_lookup(long: pd.DataFrame, games: pd.DataFrame, column: str) -> Lookup:
    """``(home, away, kickoff) -> (home goalie, away goalie)`` from one column."""
    wide = long.pivot_table(
        index="game_id", columns="side", values=column, aggfunc="first"
    )
    merged = games.merge(wide, left_on="game_id", right_index=True, how="left")
    kick = pd.to_datetime(merged["kickoff"])
    lookup: Lookup = {}
    for game, when in zip(merged.to_dict("records"), kick, strict=True):
        home, away = game.get("home"), game.get("away")
        lookup[(str(game["home_team"]), str(game["away_team"]), pd.Timestamp(when))] = (
            None if pd.isna(home) else str(home),
            None if pd.isna(away) else str(away),
        )
    return lookup


def variant(lookup: Lookup, starters: pd.DataFrame, sim: SimConfig, qb_lambda: float):  # type: ignore[no-untyped-def]
    """The promoted H2 fit, priced through ``lookup``. Only the lookup varies."""
    cal = INSEASON_CALIBRATION[LEAGUE]

    def factory(train: pd.DataFrame) -> StarterAwareModel:
        ratings = fit_qb_ratings(
            mlb_starter_frame(train, starters),
            ridge_lambda=cal["ridge"], qb_lambda=qb_lambda, min_dropbacks=6,
        )
        return StarterAwareModel(ratings, lookup, sim, hfa_points=0.15)

    return factory


def main() -> None:
    parser = argparse.ArgumentParser(description="NHL goalie-lookup walk-forward")
    parser.add_argument("--data", default="datasets/nhl")
    parser.add_argument("--n-sims", type=int, default=4000)
    parser.add_argument("--qb-lambda", type=float, default=40.0,
                        help="the promoted H2 goalie-dummy shrinkage")
    parser.add_argument("--windows", type=int, nargs="+", default=[10, 20],
                        help="trailing team-games the depth chart is read over")
    parser.add_argument("--min-train-games", type=int, default=200)
    parser.add_argument("--seeds", type=int, nargs="+", default=[0],
                        help="repeat every lookup under each seed. The variants are "
                             "paired (same RNG stream per week), but a Brier gap of "
                             "1e-4 is meaningless unless it clears the simulation's "
                             "own spread — so measure that spread rather than assume it.")
    parser.add_argument("--out", help="folder to write the comparison table to")
    args = parser.parse_args()

    folder = Path(args.data)
    games = pd.read_parquet(folder / "games.parquet")
    starters = pd.read_parquet(folder / "starters.parquet")
    cal = INSEASON_CALIBRATION[LEAGUE]
    sim = SimConfig(sd_margin=cal["sd_margin"], sd_total=cal["sd_total"],
                    n_sims=args.n_sims)

    long = team_game_log(games, starters)
    print(f"NHL goalie lab: {len(games)} games, {len(long)} team-games, "
          f"{long['starter_id'].nunique()} goalies")

    lookups: dict[str, Lookup] = {"neutral": {}}
    print("\n--- how often is the nominal #1 the actual starter? ---")
    for window in args.windows:
        scoped = nominal_starters(long, window)
        rate, n = hit_rate(scoped)
        print(f"  depth-{window:<3} {rate:.1%} of {n} team-games")
        lookups[f"depth-{window}"] = build_lookup(scoped, games, "nominal_id")
    lookups["oracle"] = build_lookup(long, games, "starter_id")

    rows = []
    for seed in args.seeds:
        for name, lookup in lookups.items():
            result = walk_forward(
                games, games, _empty_lines(),
                variant(lookup, starters, sim, args.qb_lambda),
                BacktestConfig(min_train_games=args.min_train_games, seed=seed),
            )
            row: dict[str, object] = {"lookup": name, "seed": seed}
            for key in ("n_games", "brier", "log_loss", "calibration_error"):
                if key in result.metrics:
                    row[key] = result.metrics[key]
            rows.append(row)
            print(f"  seed {seed} · {name}: done ({int(row.get('n_games', 0))} games)")

    raw = pd.DataFrame(rows)
    # Paired against neutral WITHIN each seed: the variants share an RNG
    # stream, so the per-seed difference is the signal and the spread of that
    # difference across seeds is the noise it has to clear.
    neutral = raw[raw["lookup"] == "neutral"].set_index("seed")["brier"]
    raw["brier_vs_neutral"] = raw.apply(
        lambda r: r["brier"] - neutral.loc[r["seed"]], axis=1
    )
    table = (raw.groupby("lookup", sort=False)
             .agg(n_games=("n_games", "first"),
                  brier=("brier", "mean"),
                  brier_sd=("brier", "std"),
                  log_loss=("log_loss", "mean"),
                  calibration_error=("calibration_error", "mean"),
                  vs_neutral=("brier_vs_neutral", "mean"),
                  vs_neutral_sd=("brier_vs_neutral", "std"))
             .reset_index())
    with pd.option_context("display.width", 200, "display.max_columns", None):
        print(f"\n=== Goalie lookup comparison ({len(args.seeds)} seed(s), "
              "walk-forward, out-of-sample) ===")
        print(table.to_string(index=False))
        if len(args.seeds) > 1:
            print("\n--- per seed ---")
            print(raw.to_string(index=False))
    print("\nNegative vs_neutral beats what production ships today — but only if it "
          "clears vs_neutral_sd.")

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        table.to_parquet(out / "nhl_goalie_lookup.parquet", index=False)
        print(f"wrote {out / 'nhl_goalie_lookup.parquet'}")


if __name__ == "__main__":
    main()
