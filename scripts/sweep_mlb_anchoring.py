"""The MLB staking sweep: does the claimed edge match the realized one?

    python scripts/sweep_mlb_anchoring.py --archive artifacts/hist \\
        --games datasets/mlb/games.parquet

MLB ran with no market anchor and no probability shrink — both levers raw, on
the league carrying the largest real exposure — and the number that should
have settled it never had the data to be chosen from. This is that sweep.

**The market is the closing moneyline.** The lab established that: the
archive's run lines are all ±1.5 with the information in the *price*, so a
spread-probit "market" is structurally invalid for baseball, while the
de-vigged closing moneyline is a real, sharp forecast of the same quantity the
sim produces.

The method is the college sweep's (``sweep_ncaaf_anchoring.py``), and it
turns on one property: the anchoring weight sets the *claimed* probability —
what Kelly stakes on — but **not which side is picked**. Since
``belief − fair = w·(p_model − fair)``, every positive weight picks the same
side; only the size of the claim moves. So for each weight the sweep reports
the mean claimed edge beside the realized one, out of sample by construction
(walk-forward projections against banked closes the fit never saw). The right
weight is where the claim and the realization agree: any higher over-stakes,
any lower under-stakes.

The closes are paid data and live only in the private historical-odds
artifact. Nothing here writes them into the repo — the script reads a
downloaded artifact folder and prints a table.
"""

from __future__ import annotations

import argparse
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

# The live gate: a bet needs this much claimed edge before it is staked at all.
DEFAULT_MIN_EDGE = 0.02
WEIGHTS = (0.05, 0.1, 0.13, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0)


def closing_moneylines(lines: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Per provider game: the de-vigged consensus close on the home team.

    Returns ``game_id, home_team, away_team, kickoff, p_home_fair,
    home_price, away_price, n_books``. Each book's own pair is de-vigged
    first and the **fair probabilities** are then medianed across books —
    de-vigging after a price median would fold one book's lopsided pair into
    the consensus rather than removing its vig.
    """
    columns = ["game_id", "home_team", "away_team", "kickoff", "p_home_fair",
               "home_price", "away_price", "n_books"]
    if lines.empty or events.empty:
        return pd.DataFrame(columns=columns)

    from velocity.wagering.odds import american_to_prob

    kick = events.set_index("game_id")["kickoff"].map(pd.Timestamp).to_dict()
    home_of = events.set_index("game_id")["home_team"].astype(str).to_dict()
    away_of = events.set_index("game_id")["away_team"].astype(str).to_dict()

    df = lines[lines["market"].astype(str) == "moneyline"].copy()
    if df.empty:
        return pd.DataFrame(columns=columns)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["_kick"] = df["game_id"].map(kick)
    df = df[df["_kick"].notna() & (df["timestamp"] <= df["_kick"])]
    if df.empty:
        return pd.DataFrame(columns=columns)
    # The last snapshot before first pitch is the close.
    last = df.groupby("game_id")["timestamp"].transform("max")
    df = df[df["timestamp"] == last]

    rows = []
    for game_id, group in df.groupby("game_id"):
        home_name, away_name = home_of.get(game_id, ""), away_of.get(game_id, "")
        fair: list[float] = []
        home_prices: list[float] = []
        away_prices: list[float] = []
        for _book, pair in group.groupby("book"):
            sides = dict(zip(pair["side"].astype(str), pair["price"], strict=True))
            if home_name not in sides or away_name not in sides:
                continue
            try:
                p_home = american_to_prob(float(sides[home_name]))
                p_away = american_to_prob(float(sides[away_name]))
            except Exception:  # noqa: BLE001 - one malformed pair never blocks a game
                continue
            overround = p_home + p_away
            if overround <= 0:
                continue
            fair.append(p_home / overround)
            home_prices.append(float(sides[home_name]))
            away_prices.append(float(sides[away_name]))
        if not fair:
            continue
        rows.append({
            "game_id": str(game_id),
            "home_team": home_name,
            "away_team": away_name,
            "kickoff": pd.Timestamp(kick[game_id]),
            "p_home_fair": float(np.median(fair)),
            # The best number a shopper could have taken at the close.
            "home_price": float(max(home_prices, key=_payout)),
            "away_price": float(max(away_prices, key=_payout)),
            "n_books": len(fair),
        })
    return pd.DataFrame(rows, columns=columns)


def _payout(price: float) -> float:
    """Profit per unit staked at American ``price`` — the shopper's ranking."""
    from velocity.wagering.odds import net_payout

    return float(net_payout(price))


def attach_closes(games: pd.DataFrame, closes: pd.DataFrame,
                  *, tolerance_hours: float = 6.0) -> pd.DataFrame:
    """Join the closes onto the committed games by teams + nearest kickoff.

    One-shot per provider game, so a doubleheader's two halves each get their
    own close or none — the same rule ``join_historical_closes.py`` uses, and
    for the same reason.
    """
    out = games.copy()
    for column in ("p_home_fair", "home_price", "away_price"):
        out[column] = np.nan
    if closes.empty:
        return out
    tol = pd.Timedelta(hours=tolerance_hours)
    used: set[str] = set()
    closes = closes.assign(_kick=pd.to_datetime(closes["kickoff"]))
    grouped = dict(iter(closes.groupby(["home_team", "away_team"])))
    kick = pd.to_datetime(out["kickoff"])
    for idx in out.sort_values("kickoff").index:
        group = grouped.get((out.at[idx, "home_team"], out.at[idx, "away_team"]))
        if group is None:
            continue
        candidates = group[~group["game_id"].isin(used)]
        if candidates.empty:
            continue
        deltas = (candidates["_kick"] - kick[idx]).abs()
        best = deltas.idxmin()
        if deltas[best] > tol:
            continue
        used.add(str(candidates.at[best, "game_id"]))
        for column in ("p_home_fair", "home_price", "away_price"):
            out.at[idx, column] = float(candidates.at[best, column])
    return out


def walk_forward_probabilities(  # pragma: no cover - slow, exercised by the CLI
    games: pd.DataFrame, *, ridge_lambda: float = 100.0, n_sims: int = 20_000,
    min_train: int = 400, counts: bool = True,
) -> pd.DataFrame:
    """P(home win) per game, fitted only on games played before its own day.

    ``counts`` picks the sim: the promoted baseball one
    (:mod:`velocity.models.counts`) or the rounded normal it replaced, so the
    sweep can be run against either and the difference read off.
    """
    from velocity.features.scores import fit_scores_ratings
    from velocity.models.counts import MLB_COUNTS
    from velocity.models.simulate import SimConfig, simulate_game
    from velocity.util.seed import make_rng

    config = SimConfig(sd_margin=4.5 if counts else 3.2,
                       sd_total=4.5 if counts else 4.6,
                       n_sims=n_sims, counts=MLB_COUNTS if counts else None)
    frame = games.dropna(subset=["home_score", "away_score"]).copy()
    frame["_day"] = pd.to_datetime(frame["kickoff"]).dt.normalize()
    rows = []
    for day in sorted(frame["_day"].unique()):
        train = frame[frame["_day"] < day]
        if len(train) < min_train:
            continue
        ratings = fit_scores_ratings(train, ridge_lambda=ridge_lambda)
        for record in frame[frame["_day"] == day].to_dict("records"):
            home, away = record["home_team"], record["away_team"]
            if home not in ratings.teams or away not in ratings.teams:
                continue
            mu_home = ratings.expected_points(home, away, at_home=True)
            mu_away = ratings.expected_points(away, home, at_home=False)
            sim = simulate_game(
                mu_margin=mu_home - mu_away, mu_total=mu_home + mu_away,
                # A per-game seed, from a STABLE hash: Python's own is
                # randomized per process, so a promoted number taken with it
                # could never be reproduced.
                rng=make_rng(zlib.crc32(str(record["game_id"]).encode())),
                config=config,
            )
            rows.append({"game_id": str(record["game_id"]),
                         "p_model": sim.p_home_win()})
    return pd.DataFrame(rows)


def calibration_slope(priced: pd.DataFrame) -> tuple[float, float, int]:
    """The anchoring weight estimated directly, on every game. ``(w, se, n)``.

    The better estimator, and the one the verdict rests on. The blend is
    ``p_belief = p_fair + w·(p_model − p_fair)``, so regressing what actually
    happened *over the close* on the model's disagreement *with the close*,
    through the origin, estimates ``w`` itself:

        (home_won − p_fair)  =  w · (p_model − p_fair)  +  ε

    The gate-selected sweep below answers the same question, but only on the
    few hundred games a 0.02 edge selects at a low weight — far too thin to
    locate a crossing point (the two banked seasons disagree by 0.1 there with
    standard errors of 0.04). This uses all of them.
    """
    frame = priced.dropna(subset=["p_model", "p_home_fair", "home_score",
                                  "away_score"])
    if len(frame) < 3:
        return float("nan"), float("nan"), len(frame)
    won = (frame["home_score"].to_numpy(dtype=float)
           > frame["away_score"].to_numpy(dtype=float)).astype(float)
    fair = frame["p_home_fair"].to_numpy(dtype=float)
    disagreement = frame["p_model"].to_numpy(dtype=float) - fair
    excess = won - fair
    denom = float(disagreement @ disagreement)
    if denom <= 0:
        return float("nan"), float("nan"), len(frame)
    w = float(disagreement @ excess) / denom
    resid = excess - w * disagreement
    se = float(np.sqrt((resid @ resid) / (len(frame) - 1) / denom))
    return w, se, len(frame)


def brier(probabilities: np.ndarray, outcomes: np.ndarray) -> float:
    """Mean squared error of a probability forecast."""
    return float(np.mean((probabilities - outcomes) ** 2))


def sweep(priced: pd.DataFrame, *, weights=WEIGHTS,
          min_edge: float = DEFAULT_MIN_EDGE) -> pd.DataFrame:
    """Claimed edge against realized edge, per anchoring weight.

    ``priced`` needs ``p_model``, ``p_home_fair``, ``home_price``,
    ``away_price``, ``home_score``, ``away_score``.
    """
    frame = priced.dropna(subset=["p_model", "p_home_fair", "home_score",
                                  "away_score"]).copy()
    if frame.empty:
        return pd.DataFrame()
    p_model = frame["p_model"].to_numpy(dtype=float)
    p_fair = frame["p_home_fair"].to_numpy(dtype=float)
    home_won = (frame["home_score"].to_numpy(dtype=float)
                > frame["away_score"].to_numpy(dtype=float))

    # The side is the same at every positive weight — only the claim moves.
    on_home = p_model > p_fair
    won = np.where(on_home, home_won, ~home_won)
    market = np.where(on_home, p_fair, 1.0 - p_fair)
    model = np.where(on_home, p_model, 1.0 - p_model)
    payout = np.array([_payout(p) for p in np.where(
        on_home, frame["home_price"], frame["away_price"])])

    rows = []
    for w in weights:
        belief = market + w * (model - market)
        claimed = belief - market
        staked = claimed >= min_edge
        n = int(staked.sum())
        if n == 0:
            rows.append({"weight": w, "n": 0})
            continue
        realized = float(won[staked].mean() - market[staked].mean())
        roi = float(np.where(won[staked], payout[staked], -1.0).mean())
        rows.append({
            "weight": w,
            "n": n,
            "claimed_edge": float(claimed[staked].mean()),
            "realized_edge": realized,
            "hit_rate": float(won[staked].mean()),
            "market_rate": float(market[staked].mean()),
            "roi": roi,
        })
    return pd.DataFrame(rows)


def main() -> None:  # pragma: no cover - CLI over private data
    parser = argparse.ArgumentParser(description="MLB anchoring sweep on closing moneylines")
    parser.add_argument("--archive", required=True,
                        help="downloaded historical-odds artifact folder")
    parser.add_argument("--games", default="datasets/mlb/games.parquet")
    parser.add_argument("--min-edge", type=float, default=DEFAULT_MIN_EDGE)
    parser.add_argument("--n-sims", type=int, default=20_000)
    parser.add_argument("--ridge", type=float, default=100.0)
    parser.add_argument("--old-sim", action="store_true",
                        help="score with the rounded normal the count sim replaced")
    parser.add_argument("--probabilities", default=None,
                        help="cache the walk-forward probabilities here")
    args = parser.parse_args()

    from join_historical_closes import load_archive

    lines, events = load_archive(Path(args.archive), "mlb")
    closes = closing_moneylines(lines, events)
    print(f"closes: {len(closes)} provider games with a de-vigged moneyline "
          f"({lines['game_id'].nunique()} in the archive)")

    games = pd.read_parquet(args.games)
    priced = attach_closes(games, closes)
    matched = int(priced["p_home_fair"].notna().sum())
    print(f"joined: {matched} of {len(games)} committed games carry a close")

    cache = Path(args.probabilities) if args.probabilities else None
    if cache is not None and cache.exists():
        probs = pd.read_parquet(cache)
        print(f"probabilities: {len(probs)} read from {cache}")
    else:
        probs = walk_forward_probabilities(
            priced, ridge_lambda=args.ridge, n_sims=args.n_sims,
            counts=not args.old_sim)
        print(f"probabilities: {len(probs)} walk-forward games scored "
              f"({'rounded normal' if args.old_sim else 'count sim'})")
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            probs.to_parquet(cache, index=False)

    priced["game_id"] = priced["game_id"].astype(str)
    scored = priced.merge(probs, on="game_id", how="inner").dropna(
        subset=["p_model", "p_home_fair", "home_score", "away_score"])

    # The verdict: the weight estimated on every game.
    print("\ncalibration slope — the weight that makes the claim honest:")
    for label, frame in [("all", scored),
                         *((f"season {int(s)}", g) for s, g in
                           scored.groupby("season"))]:
        w, se, n = calibration_slope(frame)
        won = (frame["home_score"].to_numpy(dtype=float)
               > frame["away_score"].to_numpy(dtype=float)).astype(float)
        fair = frame["p_home_fair"].to_numpy(dtype=float)
        model = frame["p_model"].to_numpy(dtype=float)
        print(f"  {label:12} n={n:5d}  w = {w:.3f} ± {se:.3f}   "
              f"Brier: market {brier(fair, won):.5f} · "
              f"raw model {brier(model, won):.5f} · "
              f"blend@{w:.2f} {brier(fair + w * (model - fair), won):.5f} · "
              f"blend@0.20 {brier(fair + 0.2 * (model - fair), won):.5f}")

    table = sweep(scored, min_edge=args.min_edge)
    print(f"\ngate-selected sweep at min-edge {args.min_edge:.3f} — what the "
          f"live slate would have bet (thin at low weights, so it corroborates "
          f"the slope rather than deciding):")
    print(table.to_string(index=False))


if __name__ == "__main__":
    main()
