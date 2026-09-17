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

## Addendum (2026-09-17): the calibrated total, and a cut to watch

The 2026-09-17 lab rounds (`docs/MODEL_LAB.md`) changed the NFL projection
in three ways that bear on this record: each backtest game is priced with
its announced starters, the projection's margin and total deviations are
rescaled by the slopes fitted on the residual bank (the total's about 0.50),
and rain takes a point a side off the total at a quarter-inch. On the live
chain as it now runs (wind over rest over the scaled starters fit), 2011–
2025 out of sample:

| | value |
|---|---|
| Brier | 0.2186 (the de-vigged moneyline close: 0.2102) |
| margin RMSE | 13.27 (the close: 12.97) |
| total RMSE | 13.54 (the close: 13.23) |
| O/U vs close, all games | 50.4% |
| **O/U vs close, model ≥ 4 pts from the close** | **53.7% on 869** |
| spread vs close, model ≥ 6 pts from the close | 56.2% on 210 |

After the play-context round (the turnover-EPA shrink at ×0.5 in the fit,
the residual bank rebuilt on the shrunk core; `docs/MODEL_LAB.md`), the
same chain reads Brier 0.2181, margin RMSE 13.25, total RMSE 13.52, O/U vs
close 50.7% on all games, **53.8% on 784** at the ≥4 cut, and 52.0% on 196
at the ≥6 spread cut — the totals cut holds, the thin spread cut does not,
which is the point about sample size below.

The ≥4 totals cut is the first NFL disagreement cut above break-even at a
real sample size in this lab: before the scale the same cut read 51.4% on
1,537, because half the model's total deviation was noise and the filter
selected on it. **Not promoted as a strategy** — the earlier rounds'
verdict that a disagreement filter needs a robust >52.4% across seasons
stands, and this one has been measured once. It is the thing to
re-measure each season, and the live card's NFL totals are graded against
it from here (the anchored belief still gates and sizes them).
