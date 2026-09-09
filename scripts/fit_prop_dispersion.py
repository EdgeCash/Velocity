"""Fit the football prop sim's dispersion from the banked player-weeks.

    python scripts/fit_prop_dispersion.py            # print the table
    python scripts/fit_prop_dispersion.py --bank     # and write the rush pool

``FootballPropConfig`` shipped with honest priors, not fits: lognormal volume
σ 0.18 / 0.25, a per-catch yardage sd of 6.0, rushing yards as a normal at
45% of the mean (docs/SYSTEM_REVIEW.md §5.2). ``datasets/nfl/player_weeks.parquet``
holds 112,450 player-weeks (2020–2025) to fit them from. Every number here is
a *within player-season* measurement — each player's own season is the
"projection" and the game-to-game spread around it is the outcome noise the
sim owes — decomposed the way the sim generates:

* **Team volume.** A team's receptions and carries per game vary week to
  week by a Poisson part (the count) and a multiplier part (the game
  script). ``σ² = CV² − 1/μ`` on the team totals isolates the multiplier the
  sim draws (``pass_volume_sigma`` / ``rush_volume_sigma``).
* **Receptions.** Negative-binomial overdispersion φ per position from
  ``(var − mean) / mean²``, net of the team multiplier's own
  ``exp(σ²) − 1`` — the per-player share of the extra variance.
* **Receiving yards.** The per-catch noise: sd of
  ``(yards − receptions × season ypr) / √receptions`` per position. The sim's
  ``√receptions`` scaling is structural and holds (the implied receiving-yard
  CV matches the measured one once this sd is right).
* **Rushing yards.** The relative deviation ``(yards − μ) / μ`` per position:
  its sd net of the rush multiplier is the player CV; its *shape* is banked
  as a standardized pool (``datasets/nfl/prop_residuals.parquet``) because
  the measured right skew sits between a normal (too light above 1.5μ) and
  a gamma (too light below 0.25μ) — the pool matches both tails by
  construction, the same mechanism as the game sim's residual draw.

Derived from public box-score stats; no odds data, safe to commit.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

POSITIONS = ("WR", "TE", "RB", "QB")
MIN_GAMES = 8


def _regular(pw: pd.DataFrame) -> pd.DataFrame:
    if "season_type" in pw.columns:
        kind = pw["season_type"].astype(str).str.upper()
        return pw[kind.isin(["REG", "REGULAR"]) | pw["season_type"].isna()]
    return pw


def team_volume_sigma(pw: pd.DataFrame, col: str) -> float:
    """The lognormal σ of the team-level multiplier on ``col`` (a count)."""
    team = pw.groupby(["season", "week", "team"])[col].sum().reset_index()
    stats = team.groupby(["team", "season"])[col].agg(["mean", "var", "size"])
    stats = stats[(stats["size"] >= MIN_GAMES) & (stats["mean"] > 0)]
    excess = (stats["var"] / stats["mean"] ** 2) - 1.0 / stats["mean"]
    sigma2 = float(excess.median())
    return float(np.sqrt(max(sigma2, 0.0)))


def receptions_phi(pw: pd.DataFrame, position: str, *, min_mean: float = 1.5) -> float:
    """Negative-binomial overdispersion of receptions within player-seasons."""
    stats = (pw[pw["position"] == position]
             .groupby(["player_id", "season"])["receptions"]
             .agg(["mean", "var", "size"]))
    stats = stats[(stats["size"] >= MIN_GAMES) & (stats["mean"] >= min_mean)]
    phi = (stats["var"] - stats["mean"]) / stats["mean"] ** 2
    return float(max(phi.median(), 0.0))


def per_catch_sd(pw: pd.DataFrame, position: str) -> float:
    """sd of (receiving yards − receptions × season ypr) / √receptions."""
    d = pw[pw["position"] == position]
    key = d.groupby(["player_id", "season"]).agg(
        yds=("receiving_yards", "sum"), rec=("receptions", "sum"), n=("receptions", "size"))
    key = key[(key["rec"] >= 20) & (key["n"] >= MIN_GAMES)]
    key["ypr"] = key["yds"] / key["rec"]
    d = d.merge(key[["ypr"]], left_on=["player_id", "season"], right_index=True)
    d = d[d["receptions"] > 0]
    z = (d["receiving_yards"] - d["receptions"] * d["ypr"]) / np.sqrt(d["receptions"])
    return float(z.std())


def rush_deviations(pw: pd.DataFrame, position: str, *, min_mean: float) -> pd.DataFrame:
    """Per game: relative deviation and standardized residual of rushing yards."""
    d = pw[pw["position"] == position]
    key = d.groupby(["player_id", "season"])["rush_yards"].agg(["mean", "std", "size"])
    key = key[(key["size"] >= MIN_GAMES) & (key["mean"] >= min_mean) & (key["std"] > 0)]
    d = d.merge(key.rename(columns={"mean": "mu", "std": "sd"}),
                left_on=["player_id", "season"], right_index=True)
    return pd.DataFrame({
        "position": position,
        "relative": (d["rush_yards"] - d["mu"]) / d["mu"],
        "z": (d["rush_yards"] - d["mu"]) / d["sd"],
    })


def fit(pw: pd.DataFrame) -> tuple[dict[str, object], pd.DataFrame]:
    """The fitted constants and the rush residual pool."""
    pw = _regular(pw)
    pass_sigma = team_volume_sigma(pw, "receptions")
    rush_sigma = team_volume_sigma(pw, "carries")
    mult_phi = float(np.exp(pass_sigma**2) - 1.0)
    out: dict[str, object] = {
        "pass_volume_sigma": pass_sigma,
        "rush_volume_sigma": rush_sigma,
        "receptions_phi": {p: max(receptions_phi(pw, p) - mult_phi, 0.0)
                           for p in ("WR", "TE", "RB")},
        "yards_sd_per_reception": {p: per_catch_sd(pw, p) for p in ("WR", "TE", "RB")},
    }
    pools = []
    cv: dict[str, float] = {}
    for position, min_mean in (("RB", 15.0), ("QB", 8.0)):
        dev = rush_deviations(pw, position, min_mean=min_mean)
        total_cv = float(dev["relative"].std())
        cv[position] = float(np.sqrt(max(total_cv**2 - rush_sigma**2, 0.0)))
        z = dev["z"].to_numpy(dtype=float)
        z = (z - z.mean()) / z.std(ddof=1)
        pools.append(pd.DataFrame({"position": position, "market": "rush_yards", "z": z}))
        out[f"rush_skew_{position}"] = float(dev["relative"].skew())
    out["rush_cv"] = cv
    return out, pd.concat(pools, ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fit the football prop dispersion")
    parser.add_argument("--player-weeks", default="datasets/nfl/player_weeks.parquet")
    parser.add_argument("--bank", action="store_true",
                        help="write datasets/nfl/prop_residuals.parquet (the rush pool)")
    parser.add_argument("--out", default="datasets/nfl/prop_residuals.parquet")
    args = parser.parse_args()

    pw = pd.read_parquet(args.player_weeks)
    fitted, pool = fit(pw)
    print(f"player-weeks: {len(pw)} rows, seasons "
          f"{int(pw['season'].min())}–{int(pw['season'].max())}\n")
    print(f"team volume σ (lognormal, net of Poisson): pass {fitted['pass_volume_sigma']:.3f} "
          f"(was 0.18) · rush {fitted['rush_volume_sigma']:.3f} (was 0.25)")
    print("receptions overdispersion φ (per player, net of the team multiplier):",
          {k: round(v, 3) for k, v in fitted["receptions_phi"].items()})  # type: ignore[union-attr]
    print("per-catch yards sd (was 6.0):",
          {k: round(v, 2) for k, v in fitted["yards_sd_per_reception"].items()})  # type: ignore[union-attr]
    skew_rb, skew_qb = fitted["rush_skew_RB"], fitted["rush_skew_QB"]
    print("rushing CV (player noise, net of the team multiplier; was 0.45):",
          {k: round(v, 2) for k, v in fitted["rush_cv"].items()},  # type: ignore[union-attr]
          f"· relative-deviation skew RB {skew_rb:.2f} / QB {skew_qb:.2f}")  # type: ignore[str-bytes-safe]
    print(f"rush residual pool: {len(pool)} standardized game residuals "
          f"({pool['position'].value_counts().to_dict()})")
    if args.bank:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        pool.to_parquet(args.out, index=False)
        print(f"wrote {len(pool)} rows to {args.out}")


if __name__ == "__main__":
    main()
