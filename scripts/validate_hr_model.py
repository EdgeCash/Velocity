"""Walk-forward validation for the home-run model — does it beat the naive read?

The gate every Velocity model clears before a dollar rides on it. For each
prediction window, the model is fit ONLY on games that finished before that
window opened, then asked for P(at least one HR) for every batter who
started. Predictions are scored against what actually happened:

* **Brier score** (lower is better) against two honest baselines — the league
  base rate (knows nothing) and the batter's own raw HR/PA (knows only the
  counting stat, which is what the market anchors on).
* **A calibration table** — when the model says 12%, do 12% of them go deep?
  A home-run model that is merely *sharp* is a tout sheet; one that is
  *calibrated* can be priced against a board.

    python scripts/validate_hr_model.py --statcast artifacts/statcast/<file>.parquet
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from velocity.models.props_hr import HomeRunModel


def brier(predicted: np.ndarray, actual: np.ndarray) -> float:
    """Mean squared error of a probability forecast (0 = perfect)."""
    return float(np.mean((predicted - actual) ** 2))


def auc(predicted: np.ndarray, actual: np.ndarray) -> float:
    """Probability a random home run outranks a random non-home-run.

    The metric that matters for a RANKING contest (and the one Brier hides
    on a rare event): 0.5 is a coin flip, 1.0 is perfect ordering. Computed
    from rank statistics, ties averaged.
    """
    order = np.argsort(predicted)
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(predicted) + 1)
    # Average ranks within ties so a flat forecast scores 0.5, not more.
    frame = pd.DataFrame({"p": predicted, "r": ranks})
    ranks = frame.groupby("p")["r"].transform("mean").to_numpy()
    positives = actual > 0
    n_pos, n_neg = int(positives.sum()), int((~positives).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    return float((ranks[positives].sum() - n_pos * (n_pos + 1) / 2)
                 / (n_pos * n_neg))


def top_k_by_slate(graded: pd.DataFrame, column: str, k: int) -> float:
    """Mean realized HR rate among each SLATE's top-k ranked bats.

    The DK single-stat contest is a per-day question — "pick the three most
    likely to go deep tonight" — so the honest measurement ranks within a
    slate and averages over days, never pools every prediction into one pile.
    """
    rates = []
    for _day, slate in graded.groupby(graded["kickoff"].dt.date):
        if len(slate) < k:
            continue
        top = slate.nlargest(k, column)
        rates.append(float((top["went_deep"] > 0).mean()))
    return float(np.mean(rates)) if rates else float("nan")


def calibration_table(
    predicted: np.ndarray, actual: np.ndarray, bins: int = 8
) -> pd.DataFrame:
    """Realized HR frequency vs mean predicted probability, per bucket."""
    if predicted.size == 0:
        return pd.DataFrame(columns=["bucket", "n", "predicted", "realized"])
    edges = np.quantile(predicted, np.linspace(0, 1, bins + 1))
    edges = np.unique(edges)
    idx = np.clip(np.digitize(predicted, edges[1:-1]), 0, len(edges) - 2)
    rows = []
    for b in range(len(edges) - 1):
        mask = idx == b
        if not mask.any():
            continue
        rows.append({
            "bucket": f"{edges[b]:.3f}-{edges[b + 1]:.3f}",
            "n": int(mask.sum()),
            "predicted": round(float(predicted[mask].mean()), 4),
            "realized": round(float(actual[mask].mean()), 4),
            "gap": round(float(actual[mask].mean() - predicted[mask].mean()), 4),
        })
    return pd.DataFrame(rows)


def walk_forward(
    batters: pd.DataFrame,
    games: pd.DataFrame,
    starters: pd.DataFrame,
    statcast: pd.DataFrame | None,
    *,
    min_train_games: int = 400,
    step_days: int = 14,
    season: int | None = None,
) -> pd.DataFrame:
    """Fit-then-predict forward through the schedule; return per-batter rows."""
    games = games.copy()
    games["game_id"] = games["game_id"].astype(str)
    games["kickoff"] = pd.to_datetime(games["kickoff"], errors="coerce")
    games = games.dropna(subset=["kickoff"]).sort_values("kickoff")
    played = games[games["home_score"].notna()] if "home_score" in games else games

    bank = batters.copy()
    bank["game_id"] = bank["game_id"].astype(str)
    bank["batter_id"] = bank["batter_id"].astype(str)
    bank = bank.merge(played[["game_id", "kickoff", "season", "home_team"]],
                      on="game_id", how="inner")
    if "started" in bank.columns:
        bank = bank[bank["started"].astype(bool)]

    sp = starters.copy()
    sp["game_id"] = sp["game_id"].astype(str)
    # Each batter faces the OTHER side's starter.
    opposing = sp.assign(
        side=sp["side"].map({"home": "away", "away": "home"}))[
            ["game_id", "side", "starter_id"]]
    bank = bank.merge(opposing, on=["game_id", "side"], how="left")

    if season is not None:
        # Predict only this season's windows. The Statcast frame must then
        # carry ONLY prior seasons, or the prior leaks the outcome it is
        # being asked to predict.
        predict_from = bank[bank["season"] == season]["kickoff"].min()
    else:
        predict_from = bank["kickoff"].min()
    dates = pd.date_range(predict_from, bank["kickoff"].max(),
                          freq=f"{step_days}D")
    rows: list[pd.DataFrame] = []
    for cutoff in dates:
        train = bank[bank["kickoff"] < cutoff]
        window = bank[(bank["kickoff"] >= cutoff)
                      & (bank["kickoff"] < cutoff + pd.Timedelta(days=step_days))]
        if window.empty or train["game_id"].nunique() < min_train_games:
            continue
        train_ids = set(train["game_id"])
        model = HomeRunModel.fit(
            train, played[played["game_id"].isin(train_ids)],
            sp[sp["game_id"].isin(train_ids)], statcast)
        # The two baselines: knowing nothing, and knowing only the counting stat.
        raw = train.groupby("batter_id").agg(hr=("hr", "sum"), pa=("pa", "sum"))
        raw_rate = (raw["hr"] / raw["pa"]).to_dict()

        predicted, naive, counting = [], [], []
        for row in window.to_dict("records"):
            pid = str(row["batter_id"])
            p = model.probability(
                pid,
                opposing_starter=None if pd.isna(row.get("starter_id"))
                else str(row["starter_id"]),
                venue=str(row["home_team"]),
                lineup_slot=int(row["lineup_slot"]) or None,
            )
            # Both baselines get the SAME projected plate appearances the
            # model gets. Handing them the realized PA would leak the
            # outcome (a batter who came up five times homers more often)
            # and quietly rig the comparison against the model.
            pa = model.expected_pa(int(row["lineup_slot"]) or None)
            predicted.append(np.nan if p is None else p)
            naive.append(1 - (1 - model.league_rate) ** pa)
            r = raw_rate.get(pid)
            counting.append(np.nan if r is None else 1 - (1 - r) ** pa)
        rows.append(window.assign(
            p_model=predicted, p_league=naive, p_counting=counting,
            went_deep=(window["hr"] > 0).astype(float), cutoff=cutoff))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward the HR model")
    parser.add_argument("--batters", default="datasets/mlb/batters.parquet")
    parser.add_argument("--games", default="datasets/mlb/games.parquet")
    parser.add_argument("--starters", default="datasets/mlb/starters.parquet")
    parser.add_argument("--statcast", default=None,
                        help="Statcast snapshot parquet (omit to fit without it)")
    parser.add_argument("--step-days", type=int, default=14)
    parser.add_argument("--season", type=int, default=0,
                        help="predict only this season's windows (0 = all)")
    args = parser.parse_args()

    batters = pd.read_parquet(args.batters)
    games = pd.read_parquet(args.games)
    starters = pd.read_parquet(args.starters)
    statcast = pd.read_parquet(args.statcast) if args.statcast else None
    if statcast is not None and args.season and "season" in statcast.columns:
        # Only seasons that FINISHED before the one being predicted.
        statcast = statcast[statcast["season"] < args.season]
        print(f"statcast restricted to seasons < {args.season}: {len(statcast)} rows")
    print(f"{len(batters):,} batter-games | {len(games):,} games "
          f"| statcast: {'yes' if statcast is not None else 'no'}")

    graded = walk_forward(batters, games, starters, statcast,
                          step_days=args.step_days,
                          season=args.season or None)
    if graded.empty:
        raise SystemExit("no prediction windows — not enough history")
    graded = graded.dropna(subset=["p_model", "p_counting"])
    actual = graded["went_deep"].to_numpy()
    print(f"\n{len(graded):,} predictions over {graded['cutoff'].nunique()} windows"
          f" | realized HR rate {actual.mean():.4f}")

    print("\n--- Brier score (lower is better) ---")
    scores = {
        "league base rate": brier(graded["p_league"].to_numpy(), actual),
        "batter raw HR/PA": brier(graded["p_counting"].to_numpy(), actual),
        "model": brier(graded["p_model"].to_numpy(), actual),
    }
    for name, value in scores.items():
        print(f"  {name:20s} {value:.6f}")
    lift = (scores["batter raw HR/PA"] - scores["model"]) / scores["batter raw HR/PA"]
    print(f"\n  model vs counting-stat baseline: {lift:+.2%} Brier improvement")
    print("  (Brier is nearly blind on a rare event — the base rate dominates.")
    print("   Ranking power is what the contest and the price both turn on.)")

    print("\n--- Discrimination: AUC (0.5 = coin flip) ---")
    for name, column in (("batter raw HR/PA", "p_counting"), ("model", "p_model")):
        print(f"  {name:20s} {auc(graded[column].to_numpy(), actual):.4f}")

    days = graded["kickoff"].dt.date.nunique()
    print(f"\n--- Contest view: top-k per SLATE, averaged over {days} days ---")
    print(f"  field average            {actual.mean():.4f}")
    for k in (3, 5, 10, 20):
        model_rate = top_k_by_slate(graded, "p_model", k)
        raw_rate = top_k_by_slate(graded, "p_counting", k)
        edge = model_rate - raw_rate
        print(f"  top {k:<3d}  model {model_rate:.4f}   raw HR/PA {raw_rate:.4f}"
              f"   ({edge:+.4f})")

    print("\n--- Calibration (model) ---")
    print(calibration_table(graded["p_model"].to_numpy(), actual).to_string(index=False))


if __name__ == "__main__":
    main()
