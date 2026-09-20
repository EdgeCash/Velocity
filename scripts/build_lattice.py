"""Bank football's margin lattice — the key-number correction the live sim reads.

    python scripts/build_lattice.py --league nfl
    python scripts/build_lattice.py --league ncaaf

Reads the league's banked walk-forward residuals
(``datasets/{league}/sim_residuals.parquet``, written by
``scripts/build_sim_residuals.py``), measures how much more often football
lands on each absolute margin than the shipped rounded normal does around
those same projections, and writes ``datasets/{league}/lattice.parquet``
(:data:`velocity.models.keynumbers.LATTICE_COLUMNS`). The live sim resamples
its draws by this table (``SimConfig.lattice``); ``scripts/sim_lab.py`` and
``scripts/derivative_recheck.py`` are the gates that decided it should
(docs/MODEL_LAB.md, the lattice round through the promotion round).

Rebuild it whenever the residual bank is rebuilt: the freshness test in
``tests/test_key_numbers.py`` compares the committed table with a fresh fit
off the committed bank, so a bank that moves without its lattice fails CI
rather than shipping a stale correction. Then regenerate the ladder table
(``scripts/calibrate_ladders.py --write``), which is a statement about the
sim this changes.

Prints the table — observed share, the normal's share, the weight — so the
promotion record has its numbers. Derived from public final scores and our
own projections: no odds data, so the bank is safe to commit.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from velocity.models.keynumbers import (
    DEFAULT_MAX_ABS,
    DEFAULT_PRIOR,
    LATTICE_COLUMNS,
    fit_lattice_from_residuals,
    rounded_normal_mass,
)
from velocity.models.residuals import RESIDUALS_FILE
from velocity.models.simulate import DEFAULT_SD_MARGIN, NCAAF_SD_MARGIN

SD_MARGIN = {"nfl": DEFAULT_SD_MARGIN, "ncaaf": NCAAF_SD_MARGIN}


def describe(residuals: pd.DataFrame, sd_margin: float, max_abs: int, prior: float,
             correct_tail: bool = False) -> pd.DataFrame:
    """One row per absolute margin: what football did, what the normal says, the ratio."""
    mu = residuals["mu_margin"].to_numpy(dtype=float)
    actual = np.abs(mu + residuals["resid_margin"].to_numpy(dtype=float))
    index = np.clip(np.rint(actual).astype(np.int64), 0, max_abs)
    observed = np.bincount(index, minlength=max_abs + 1) / float(len(residuals))
    normal = rounded_normal_mass(mu, sd_margin, max_abs=max_abs)
    weights = fit_lattice_from_residuals(
        residuals, sd_margin, max_abs=max_abs, prior=prior, correct_tail=correct_tail)
    return pd.DataFrame({
        "abs_margin": np.arange(max_abs + 1),
        "games": np.bincount(index, minlength=max_abs + 1),
        "observed": observed,
        "normal": normal,
        "weight": weights.weights,
    })


def main() -> None:
    parser = argparse.ArgumentParser(description="Bank the sim's margin lattice")
    parser.add_argument("--league", required=True, choices=sorted(SD_MARGIN))
    parser.add_argument("--residuals", default=None,
                        help=f"a residual bank (default datasets/{{league}}/{RESIDUALS_FILE})")
    parser.add_argument("--seasons-from", type=int, default=None,
                        help="fit on residuals from this season on (thin early training)")
    parser.add_argument("--max-abs", type=int, default=DEFAULT_MAX_ABS)
    parser.add_argument("--prior", type=float, default=DEFAULT_PRIOR)
    parser.add_argument("--correct-tail", action="store_true",
                        help="also weight the tail bin (at or beyond --max-abs); off by "
                             "default — the tail round found that a dispersion correction "
                             "in a key-number tool, and it closed ladder sides")
    parser.add_argument("--out", default=None,
                        help="default datasets/{league}/lattice.parquet")
    args = parser.parse_args()

    source = Path(args.residuals or f"datasets/{args.league}/{RESIDUALS_FILE}")
    out = Path(args.out or f"datasets/{args.league}/lattice.parquet")
    residuals = pd.read_parquet(source)
    if args.seasons_from is not None:
        residuals = residuals[residuals["season"] >= args.seasons_from]
    residuals = residuals.dropna(subset=["mu_margin", "resid_margin"]).reset_index(drop=True)
    if residuals.empty:
        raise SystemExit(f"no residuals in {source}")

    sd_margin = SD_MARGIN[args.league]
    table = describe(residuals, sd_margin, args.max_abs, args.prior, args.correct_tail)
    print(f"{args.league}: {len(residuals)} walk-forward games, seasons "
          f"{residuals['season'].min()}–{residuals['season'].max()}, "
          f"rounded normal at σ {sd_margin:g}, prior {args.prior:g} games a bin, "
          f"tail {'corrected' if args.correct_tail else 'left alone'}")
    print(table.to_string(index=False, formatters={
        "observed": "{:.4f}".format, "normal": "{:.4f}".format, "weight": "{:.3f}".format,
    }))
    keys = table.set_index("abs_margin")
    print("\n  key numbers: " + ", ".join(
        f"{k}: {keys.loc[k, 'observed']:.3f} vs {keys.loc[k, 'normal']:.3f} "
        f"(×{keys.loc[k, 'weight']:.2f})" for k in (3, 7, 10, 14)))

    out.parent.mkdir(parents=True, exist_ok=True)
    table[LATTICE_COLUMNS].to_parquet(out, index=False)
    print(f"\nwrote {len(table)} lattice rows to {out}")


if __name__ == "__main__":
    main()
