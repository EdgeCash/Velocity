# NCAAF backtest — real results, and the first real edge

Where the NFL was a wall (a from-scratch model can't beat razor-sharp sides),
NCAAF is where the design said edges live — and the real data agrees.

## Data

- `datasets/ncaaf/games.parquet` — 10,205 games **with closing betting lines**,
  2015–2024, pulled from CollegeFootballData (`scripts/pull_cfbd_lines.py`;
  consensus spread/total across providers, spread flipped to the nflverse
  convention). CFBD data used with attribution.
- `datasets/ncaaf/boxscores_2002_2025.parquet` — 19,843 games, 2002–2025, from the
  uploaded box-score CSV (`scripts/build_ncaaf_boxscores.py`). No lines; used for
  the longer projection-only history.
- Model: the schedule-only **scores** rating (`fit_scores_ratings`) →
  `ScoresGameModel`, run through the walk-forward engine
  (`scripts/run_backtest_local.py --league ncaaf --rating scores`).

## Projection quality (walk-forward, 2015–2024)

| Metric | Value | vs NFL |
|---|---|---|
| Brier | **0.203** vs 0.238 baseline (**+0.035**) | ~3× the NFL edge (+0.011) |
| Calibration error | 0.039 | comparable |

College outcomes are far more predictable than the NFL's (bigger talent gaps),
and the model captures it.

## The market test — sides vs the closing line

Flat, every game: **50.1% ATS** on 9,518 games. College sides are efficient too;
no edge on spreads at any disagreement threshold (~50% throughout).

## The market test — totals vs the closing line (the edge)

Flat: **51.5% O/U** on 9,363 games — already the closest to break-even we've seen.
And it **improves monotonically with disagreement**, clearing the 52.4% break-even
when we bet only where the model differs from the market:

| Bet only when \|model − market total\| ≥ | O/U win rate | Bets |
|---|---|---|
| 0 (all) | 51.6% | 9,531 |
| 3 pts | 52.4% | 6,448 |
| 4 pts | **52.8%** | 5,477 |
| 6 pts | **53.4%** | 3,776 |
| 8 pts | **53.4%** | 2,347 |

A win rate that rises with edge — from 51.6% flat to 53.4% on the biggest
disagreements — is signal, not a lucky cut. Selective NCAAF **totals** clear the
vig. This is the design's thesis made concrete (§9): *edges live in NCAAF and soft
totals, not marquee sides.*

## Robustness — is the totals edge consistent across seasons?

Splitting the selective totals bets (|edge| ≥ 4) by season, the edge is **real but
not bulletproof — positive in 7 of 10 seasons**, and not driven by one lucky year:

| Season | O/U win rate | Bets | | Season | O/U win rate | Bets |
|---|---|---|---|---|---|---|
| 2015 | 54.5% ✅ | 253 | | 2020 | 55.6% ✅ | 340 |
| 2016 | 53.4% ✅ | 386 | | 2021 | 53.6% ✅ | 504 |
| 2017 | 49.0% ❌ | 455 | | 2022 | 51.9% ❌ | 903 |
| 2018 | 48.6% ❌ | 484 | | 2023 | 53.5% ✅ | 860 |
| 2019 | 53.0% ✅ | 534 | | 2024 | 55.2% ✅ | 752 |

Overall 52.8% on 5,471 bets. The season-to-season swing (48.6% → 55.6%) is the
honest reality: even a genuine edge has losing stretches, which is exactly why the
staking discipline (fractional Kelly, caps) exists. 7-of-10 positive across a
decade — including four of the last five seasons — is a credible signal worth
sharpening, not a finished bankroll.

## Re-verification (the filter as now wired live)

The strategy is no longer analysis-only: `SlateConfig.min_total_disagreement`
implements it in the live slate path, and
`run_backtest_local.py --totals-sweep` re-measures it on demand. Re-running the
full 10 seasons (`--rating scores --n-sims 10000`) reproduces the aggregate edge:

| Threshold | This run | Original table |
|---|---|---|
| ≥ 0 pts | 51.6% (9,567) | 51.6% (9,531) |
| ≥ 3 pts | 52.3% (6,605) | 52.4% (6,448) |
| ≥ 4 pts | **52.6%** (5,630) | 52.8% (5,477) |
| ≥ 6 pts | **53.4%** (3,894) | 53.4% (3,776) |
| ≥ 8 pts | 53.0% (2,461) | 53.4% (2,347) |

The shape holds exactly — flat totals at break-even-minus, rising monotonically
with disagreement, clearing 52.4% from 4 points on.

**Where it differs, honestly:** bet counts run ~3% higher than the original
table throughout (a small definitional difference in the earlier ad-hoc
analysis), and the per-season robustness comes out **6 of 10 seasons above
break-even, not 7** — 2015 lands at 50.7% here versus 54.5% as first reported.
2017, 2018 and 2022 are the losing seasons in both runs. The reproducible
number to carry forward is **6/10**; treat the edge as real but thinner than
the original write-up implied, which is exactly why the points threshold is a
CLI knob (`--ncaaf-total-edge`) rather than a constant — ≥6 points buys a
clearly better rate for ~30% fewer bets.

## Honest caveats

- The edge is **thin** (53% vs 52.4% break-even) and measured with **fixed sim
  variance** (`sd_total = 16`, chosen to fit college totals); it needs
  out-of-sample confirmation on a season held out entirely, plus real-world
  friction (juice beyond −110, limits, availability).
- No **closing-line value** yet — CFBD gives one consensus number, not the line
  history. CLV is the sharper skill signal and the next thing to measure (the
  betting-lines *timestamps* endpoint, or a line-movement archive).
- The rating is plain opponent-adjusted points. **EPA ratings** (from CFBD
  play-by-play `ppa`) and **preseason priors** (recruiting) are built and should
  sharpen this further.

## Next

1. Pull CFBD **play-by-play** (`ppa`) → EPA ratings for NCAAF, re-run the totals
   test — the richer signal should widen the edge.
2. Add **recruiting priors** so early-season weeks (where the model is weakest)
   regress sensibly.
3. Measure **CLV** against line movement, the real proof of skill.
4. Formalize the **totals strategy**: edge threshold → fractional-Kelly stake
   through the existing wagering stack.

## Addendum (2026-08): team totals — the censored-score derivative

Arscott 2023 (J. Sports Economics) showed NCAAF **team-total** lines carry a
censoring bias — books derive them linearly from the game total and spread
(`home ≈ (total + spread) / 2`), ignoring that scores are floored at zero —
and a lines-only strategy won >55% over two decades. We reproduced the
measurement on the committed closes (`backtest/lab.py
team_total_censoring_study`, runnable via `run_backtest_local.py
--team-totals-study`):

| implied team total | n (sides) | bias (realized − implied) | over rate |
|---|---:|---:|---:|
| ≤ 14 | 1,298 | **+0.97** | 52.0% |
| 14–17 | 1,101 | +0.64 | 45.6% |
| 17–21 | 2,415 | +0.33 | 49.4% |
| 21–28 | 6,120 | +0.10 | 48.2% |
| > 28 | 8,901 | +0.25 | 50.1% |

The honest read: the **mean** bias is exactly where censoring predicts
(≈ +1 point at low implied totals; NFL shows the same +0.73 at ≤14), but the
**over rate at the derived number stays ~50–52%** — censoring moves the mean,
not the median, so the paper's >55% must live in how *posted* team-total
lines deviate from the derivation, which our archive doesn't carry yet.

What shipped on this evidence:

- Team totals are now a **first-class market** end-to-end: The Odds API
  ingest (`team_totals` per-event key → `team_total_home`/`_away`), sim
  pricing (the sim's floor-at-zero carries the censoring correction the
  linear derivation misses), the EV gate, staking, grading, CLV, and parlay
  legs.
- A `min_team_total_disagreement` gate mirrors the totals filter but
  **defaults to off** — no >52.4% cut exists on derived numbers, so the
  threshold waits for banked *posted* team-total closes to calibrate against,
  exactly how the full-game totals filter was promoted.

**Update (2026-08):** banking is live. The props collector's per-event calls
now carry `team_totals` (`DEFAULT_EVENT_MARKETS`), writing
`team_totals_{league}_{tag}.parquet` beside the props archive twice daily.
The calibration harness is ready to run as closes accumulate:
`run_backtest_local.py --team-totals-study --team-total-lines <banked>`
(`backtest/lab.py posted_team_total_study`) measures posted closes vs
realized scores *and* vs the linear derivation — the over-rate table that
will finally set the `min_team_total_disagreement` default, or keep the
gate honestly off.

## Addendum (2026-08-31): the 2025 gap, the trailing-window negative, and the base-points fix

Three findings from putting the fit next to a public yardstick (Bill
Connelly's posted SP+ Week-1 2026 card):

1. **The 2025 season was missing from `games.parquet` entirely.** The CFBD
   lines pull last ran through 2024 and the in-season refresher only appends
   current rows, so 2026 pricing was fit on ratings that ended in January
   2025. `scripts/backfill_games_from_boxscores.py` now lifts a season of
   finals from the banked boxscores into the games schema — **fit-only rows**:
   they carry no closing lines, so the ratings see them and the market
   backtest (which drops lineless rows) does not. Backfilled: 934 games of
   2025. Lines for 2025 still want a keyed `pull_cfbd_lines.py` run.

2. **A trailing-4-season window was tested and NOT promoted.** Same
   walk-forward, same sweep as above, scores rating: ≥4 pts 52.3% on 5,338
   (vs 52.4% on 5,657 expanding), and *worse* where the edge is fattest —
   ≥6 pts 52.2% vs 53.4%. Re-run it with
   `run_backtest_local.py --league ncaaf --rating scores --trailing-seasons 4
   --totals-sweep`; the flag exists so this stays an executable negative.

3. **The live blend's `base_points=28.5` was a stale regime constant.**
   College totals dropped ~4 points per game after the 2023 clock rules
   (57–58 through 2018 → ~53 in 2023–25), and the hardcoded level pushed
   every projected total high — on the 2026 Week-1 board the ≥4-point filter
   would have fired 22 overs of 25 picks, +4.6 points of pure baseline bias
   against the posted O/Us. The runner now derives the level from the
   trailing two seasons (`ncaaf_base_points`), which balances the fired card
   (15 over / 6 under, +2.6 residual). The scores half of the blend — the
   configuration the totals sweep above actually validated — is untouched.

Yardstick context, for calibration expectations: against the 39 shared
FBS-vs-FBS games of that SP+ card, the corrected fit correlates 0.86 on
margins (MAE ~7.7 — SP+ carries roster/portal priors we don't) and sits
+1 to +3 on totals vs the posted O/Us depending on configuration, versus
SP+'s −1.2. Week-1 college numbers are the year's least informed; treat
opening-week totals as paper-tracking, not proof either way.

## Addendum (2026-08-31, later): 2025 verified, the threshold moves, and the SP+ prior

The keyed CFBD pulls ran (workflow one-offs): 2025's consensus closes are
attached (930 backfilled rows lined, 667 CFBD-only games appended, 4 left
fit-only) and `sp_ratings.parquet` carries SP+ 2014–2025.

**1. The 2025 market test softens the ≥4 cut below its own bar.** Same
walk-forward, scores rating, now through 2025:

| cut | all-history | 2025 alone |
| --- | --- | --- |
| ≥4 pts | **52.3%** on 5,657 → 6,451 bets | 51.2% on 781 |
| ≥6 pts | **53.0%** on 4,398 | 50.6% on 496 |

2025 was below water at both cuts (one season, ~1σ — a warning, not a
verdict), and the aggregate ≥4 rate no longer clears the 52.4% break-even.
The burden of proof stays a robust >52.4%: the live default moved to
`--ncaaf-total-edge 6`, and early-2026 college totals are paper-tracked
until live CLV says otherwise.

**2. SP+ as a previous-season prior — tested and PROMOTED (K=12).** The
Torvik pseudo-games pattern ported to football (`sp_pseudo_games` in
`ingest/ncaaf.py`): the latest *finished* season's final SP+ ratings become
K neutral pseudo-games per team in the following season (leak-gated —
CFBD serves final ratings, so a season may only ever inform the next one).
Walk-forward, 2015–2025:

| variant | Brier | Brier wks ≤4 | totals ≥4 | totals ≥6 | 2025 ≥4 |
| --- | --- | --- | --- | --- | --- |
| stock | 0.2038 | 0.1862 | 52.3% | 53.0% | 51.2% |
| K=3 | 0.2027 | 0.1849 | 52.4% | 52.9% | 51.3% |
| K=6 | 0.2019 | 0.1840 | 52.6% | 52.9% | 51.5% |
| **K=12** | **0.2009** | **0.1830** | 52.4% | **53.1%** | **51.9%** |
| K=24 | 0.2000 | 0.1823 | 52.1% | 53.0% | 51.7% |

Brier improves monotonically with K but the ≥4 totals rate breaks down at
K=24 — the prior starts anchoring the model to the consensus SP+ tracks.
K=12 takes the best ≥6 totals and 2025 numbers with near-best Brier. The
live runner applies the prior to the scores fit only (the EPA half,
`ncaaf_base_points`, and the ratings table see real games; the
`__SP_PRIOR__` anchor is filtered from display) whenever
`sp_ratings.parquet` is present.

What SP+ is and is not, recorded once: it is roster knowledge the
results-only fit lacks (returning production, recruiting, transfers) —
worth points of early-season calibration. It is **not** an edge source:
its own public record tracks the closing line (~53% ATS), and blending
toward it moves us toward the market, not past it.
