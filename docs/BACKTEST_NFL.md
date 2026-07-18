# NFL EPA backtest — real 2021–2025 results

First real-data backtest of the full-strength NFL model: opponent-adjusted **EPA
ratings** fit on committed nflfastR play-by-play (`datasets/nfl/`), run through the
walk-forward engine over five seasons.

## Data

- `datasets/nfl/plays.parquet` — 232,322 offensive plays (2021–2025), the
  canonical `Plays` columns distilled from ~585 MB of raw nflfastR CSV.
- `datasets/nfl/games.parquet` — 1,424 games with final scores and the closing
  `spread_line` / `total_line`.
- Built by `scripts/build_nfl_pbp_datasets.py`; backtest run with
  `scripts/run_backtest_local.py --league nfl --data datasets/nfl`.

## Results (walk-forward, train on all prior weeks)

| Metric | Value | Reading |
|---|---|---|
| Games projected | 1,376 | 2021–2025, out-of-sample each week |
| Brier | **0.237** vs 0.248 baseline | projection is **informative** |
| Calibration error | **0.037** | reasonably calibrated |
| ATS vs closing spread | **48.6%** (1,280 games) | **below** 52.4% break-even |
| O/U vs closing total | **48.9%** (1,312 games) | **below** break-even |

## Reading

The EPA game model clearly beats a no-information baseline at predicting winners
(Brier 0.237 < 0.248) and is well-calibrated — it is a genuinely informative
model. But it **does not beat the NFL closing lines** on sides or totals: 48–49%
against the spread and total is a losing rate after vig.

This is the expected, honest outcome, and it matches `DESIGN.md` §9 directly:

> The market is very good. Sides and totals for NFL primetime games are
> razor-sharp; realistic early edges live in **props, NCAAF, and stale/soft
> numbers**, not marquee NFL sides.

A from-scratch power rating, however clean, runs into the wall of an efficient
market on the most-bet markets. The takeaway is not "the model is broken" — it is
"NFL sides/totals are the wrong place to look." The edge hunt belongs in:

1. **Player props** — books price them with less attention; the correlated-sim
   prop engine (`velocity/models/props.py`) is built for exactly this.
2. **NCAAF** — 130+ teams, softer numbers, priors that matter
   (`velocity/models/game_ncaaf.py`).
3. **Soft/stale lines** and line shopping, where beating a *number* (not the
   sharp consensus) is the realistic edge.

## Caveats

- No line-movement archive, so closing-line value (CLV) isn't measured here; the
  ATS record is against the single closing number.
- The ratings are plain opponent-adjusted EPA with no QB adjustment, rest/travel,
  or weather yet — real improvements the design calls for, but none likely to
  clear the ~4-point gap to the closing line on NFL sides.
