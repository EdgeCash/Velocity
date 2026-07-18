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
