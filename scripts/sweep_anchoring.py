"""The summer-league staking sweep: does the claimed edge match the realized one?

    python scripts/sweep_anchoring.py --league mlb --archive artifacts/hist
    python scripts/sweep_anchoring.py --league wnba --archive artifacts/hist

Both summer leagues ran with no market anchor and no probability shrink —
both levers raw — and the number that should have settled it never had the
data to be chosen from, because the closes live in the private
historical-odds artifact rather than in ``datasets/``. This is that sweep.

**The market is the closing moneyline.** The lab established that for
baseball: the archive's run lines are all ±1.5 with the information in the
*price*, so a spread-probit "market" is structurally invalid there. The
moneyline is also the market both leagues' sims price most directly — a
probability of winning, against a probability of winning.

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


# Per-league: the ratings fit, the sim dispersion, and the ridge the live
# slate runs. Kept here rather than imported so the sweep states exactly what
# it scored — a sweep that silently tracks a config change is unreadable later.
LEAGUES = {
    "mlb": {"ridge": 100.0, "half_life": None, "sd_margin": 4.5, "sd_total": 4.5,
            "counts": True, "min_train": 400},
    # The promoted WNBA configuration: pace×efficiency, λ=10, recency-8
    # (docs/MODEL_LAB.md WNBA Round 2), on the rounded normal it still uses.
    "wnba": {"ridge": 10.0, "half_life": 8.0, "sd_margin": 12.5, "sd_total": 15.0,
             "counts": False, "min_train": 150},
}


class _Ratings:
    """One interface over the two fits: ``(mu_home, mu_away)`` and a team set."""

    def __init__(self, teams: set[str], expected) -> None:  # noqa: ANN001
        self.teams = teams
        self._expected = expected

    def expected_points(self, home: str, away: str) -> tuple[float, float]:
        return self._expected(home, away)


def _fit(  # pragma: no cover - exercised by the CLI
    train: pd.DataFrame, pace: pd.DataFrame | None, ridge_lambda: float,
    half_life: float | None, config,  # noqa: ANN001
) -> _Ratings | None:
    """The league's promoted fit: pace×efficiency when a pace frame is given."""
    from velocity.features.scores import fit_scores_ratings

    if pace is not None:
        from velocity.backtest.lab import fit_pace_efficiency

        try:
            model = fit_pace_efficiency(train, pace, config,
                                        ridge_lambda=ridge_lambda,
                                        half_life=half_life)
        except Exception:  # noqa: BLE001 - an unfittable early window is skipped
            return None
        def expected(home: str, away: str) -> tuple[float, float]:
            eff_home, eff_away = model.eff_model.expected_points(home, away)
            poss = (model.pace_league + model.pace_dev.get(home, 0.0)
                    + model.pace_dev.get(away, 0.0))
            return poss * eff_home / 100.0, poss * eff_away / 100.0
        return _Ratings(set(model.eff_model.ratings.teams), expected)

    ratings = fit_scores_ratings(train, ridge_lambda=ridge_lambda)
    return _Ratings(
        set(ratings.teams),
        lambda home, away: (ratings.expected_points(home, away, at_home=True),
                            ratings.expected_points(away, home, at_home=False)),
    )


def walk_forward_probabilities(  # pragma: no cover - slow, exercised by the CLI
    games: pd.DataFrame, *, ridge_lambda: float = 100.0, n_sims: int = 20_000,
    min_train: int = 400, counts: bool = True, pace: pd.DataFrame | None = None,
    half_life: float | None = None, sd_margin: float = 4.5, sd_total: float = 4.5,
) -> pd.DataFrame:
    """P(home win) per game, fitted only on games played before its own day.

    ``counts`` picks the baseball sim: the promoted one
    (:mod:`velocity.models.counts`) or the rounded normal it replaced, so the
    sweep can be run against either and the difference read off.

    ``pace`` is the WNBA's possessions frame. When it is supplied the fit is
    the promoted pace×efficiency one the live slate runs rather than the plain
    scores fit — the sweep has to score what production actually prices.
    """
    from velocity.models.counts import MLB_COUNTS
    from velocity.models.simulate import SimConfig, simulate_game
    from velocity.util.seed import make_rng

    config = SimConfig(sd_margin=sd_margin, sd_total=sd_total,
                       n_sims=n_sims, counts=MLB_COUNTS if counts else None)
    frame = games.dropna(subset=["home_score", "away_score"]).copy()
    frame["_day"] = pd.to_datetime(frame["kickoff"]).dt.normalize()
    rows = []
    for day in sorted(frame["_day"].unique()):
        train = frame[frame["_day"] < day]
        if len(train) < min_train:
            continue
        ratings = _fit(train, pace, ridge_lambda, half_life, config)
        if ratings is None:
            continue
        for record in frame[frame["_day"] == day].to_dict("records"):
            home, away = record["home_team"], record["away_team"]
            if home not in ratings.teams or away not in ratings.teams:
                continue
            mu_home, mu_away = ratings.expected_points(home, away)
            sim = simulate_game(
                mu_margin=mu_home - mu_away, mu_total=mu_home + mu_away,
                # A per-game seed, from a STABLE hash: Python's own is
                # randomized per process, so a promoted number taken with it
                # could never be reproduced.
                rng=make_rng(zlib.crc32(str(record["game_id"]).encode())),
                config=config,
            )
            rows.append({"game_id": str(record["game_id"]),
                         "p_model": sim.p_home_win(),
                         "mu_margin": float(mu_home - mu_away)})
    return pd.DataFrame(rows)


def ats_record(priced: pd.DataFrame) -> dict[str, float]:
    """The model's record against the CLOSING SPREAD — the other claim.

    The WNBA headline that put the league on a watch-it posture was 55.4%
    against the spread, not a moneyline number, so a moneyline sweep alone
    cannot speak to it. This is the direct test: back the side the projected
    margin favours against the closing number, and count covers. Pushes are
    excluded, as a book excludes them.
    """
    frame = priced.dropna(subset=["mu_margin", "spread_line", "home_score",
                                  "away_score"])
    if frame.empty:
        return {"n": 0.0, "cover_rate": float("nan"), "se": float("nan")}
    margin = (frame["home_score"].to_numpy(dtype=float)
              - frame["away_score"].to_numpy(dtype=float))
    # ``spread_line`` is positive when home is favored (the datasets' own
    # convention), so the home side covers when it wins by more than that.
    line = frame["spread_line"].to_numpy(dtype=float)
    on_home = frame["mu_margin"].to_numpy(dtype=float) > line
    decided = margin != line
    if not decided.any():
        return {"n": 0.0, "cover_rate": float("nan"), "se": float("nan")}
    covered = np.where(on_home, margin > line, margin < line)[decided]
    n = int(covered.sum() + (~covered).sum())
    rate = float(covered.mean())
    return {"n": float(n), "cover_rate": rate,
            "se": float(np.sqrt(rate * (1 - rate) / n))}


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
    parser = argparse.ArgumentParser(
        description="Anchoring sweep on banked closing moneylines")
    parser.add_argument("--league", default="mlb", choices=sorted(LEAGUES))
    parser.add_argument("--archive", required=True,
                        help="downloaded historical-odds artifact folder")
    parser.add_argument("--games", default=None,
                        help="the committed games frame (default: the league's)")
    parser.add_argument("--pace", default=None,
                        help="the team-box frame for a pace×efficiency league "
                             "(default: the league's, when it has one)")
    parser.add_argument("--min-edge", type=float, default=DEFAULT_MIN_EDGE)
    parser.add_argument("--n-sims", type=int, default=20_000)
    parser.add_argument("--ridge", type=float, default=None)
    parser.add_argument("--old-sim", action="store_true",
                        help="score with the rounded normal the count sim replaced "
                             "(baseball only; the others never had one)")
    parser.add_argument("--probabilities", default=None,
                        help="cache the walk-forward probabilities here")
    args = parser.parse_args()

    cfg = LEAGUES[args.league]
    games_path = args.games or f"datasets/{args.league}/games.parquet"

    from join_historical_closes import load_archive

    lines, events = load_archive(Path(args.archive), args.league)
    closes = closing_moneylines(lines, events)
    print(f"closes: {len(closes)} provider games with a de-vigged moneyline "
          f"({lines['game_id'].nunique()} in the archive)")

    games = pd.read_parquet(games_path)
    priced = attach_closes(games, closes)
    # The closing spread rides along from the same archive, through the
    # existing consensus, so the ATS claim can be tested on the same games.
    from join_historical_closes import closing_consensus, join_closes

    priced = join_closes(priced, closing_consensus(lines, events))
    matched = int(priced["p_home_fair"].notna().sum())
    print(f"joined: {matched} of {len(games)} committed games carry a close")

    cache = Path(args.probabilities) if args.probabilities else None
    if cache is not None and cache.exists():
        probs = pd.read_parquet(cache)
        print(f"probabilities: {len(probs)} read from {cache}")
    else:
        pace = None
        if cfg["half_life"] is not None:
            box = Path(args.pace or f"datasets/{args.league}/team_box.parquet")
            if box.exists():
                from velocity.backtest.lab import wnba_pace_frame

                pace = wnba_pace_frame(pd.read_parquet(box))
            else:
                print(f"no team box at {box}; falling back to the scores fit")
        probs = walk_forward_probabilities(
            priced, ridge_lambda=args.ridge if args.ridge is not None else cfg["ridge"],
            n_sims=args.n_sims, min_train=int(cfg["min_train"]),
            counts=cfg["counts"] and not args.old_sim, pace=pace,
            half_life=cfg["half_life"], sd_margin=cfg["sd_margin"],
            sd_total=cfg["sd_total"])
        fit = ("pace×efficiency" if pace is not None else "scores")
        sim_name = ("count sim" if cfg["counts"] and not args.old_sim
                    else "rounded normal")
        print(f"probabilities: {len(probs)} walk-forward games scored "
              f"({fit} fit, {sim_name})")
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

    ats = ats_record(scored)
    if ats["n"]:
        print(f"\nagainst the closing spread: {ats['cover_rate']:.1%} "
              f"± {ats['se']:.1%} over {int(ats['n'])} decided games "
              f"(52.4% is the break-even at −110)")

    table = sweep(scored, min_edge=args.min_edge)
    print(f"\ngate-selected sweep at min-edge {args.min_edge:.3f} — what the "
          f"live slate would have bet (thin at low weights, so it corroborates "
          f"the slope rather than deciding):")
    print(table.to_string(index=False))


if __name__ == "__main__":
    main()
