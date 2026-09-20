# Model Lab — NFL variant benchmarks

**Status:** living results log (v0.1)
**Harness:** `scripts/model_lab.py` over `velocity/backtest/lab.py` — every
variant runs through the identical walk-forward gate (train strictly before
each predicted week; metrics fully out-of-sample) on the committed
`datasets/nfl/` (now **2011–2025**, 4,096 games / 681k plays, 100% closing-line
coverage).
**Rule:** nothing is promoted into the live slate without winning here first.

The variant families come from the public state of the art — Elo-style recency
(nfelo/538), DVOA-style phase splits, shrinkage sweeps — plus the
market-regression insight ([nfelo's finding](https://www.nfeloapp.com/analysis/using-market-regression-to-improve-prediction-accuracy-in-the-nfl/))
that model-vs-close *disagreement thresholds*, not raw model output, are where
a bettable cut lives (this is exactly the NCAAF totals filter already shipped).

---

## Round 1 — 2021–2025 window (1,392 predicted games)

| variant | Brier ↓ | log-loss ↓ | calib. err ↓ | ATS vs close | O/U vs close |
|---|---|---|---|---|---|
| baseline (EPA, λ=200) | 0.2370 | 0.6677 | 0.0397 | 48.8% (1293) | 48.9% (1329) |
| **recency-17** (half-life 17 wks) | **0.2284** | **0.6491** | 0.0394 | 48.2% | 48.3% |
| recency-34 | 0.2317 | 0.6561 | **0.0297** | 47.7% | 47.5% |
| split-0.60 (pass/rush phases) | 0.2428 | 0.6829 | 0.0814 | 48.2% | 49.4% |
| split-0.75 | 0.2478 | 0.6983 | 0.1023 | 48.2% | 48.8% |
| ridge-100 | 0.2378 | 0.6702 | 0.0446 | 48.7% | 49.6% |
| ridge-400 | 0.2362 | 0.6650 | 0.0280 | 48.8% | 49.5% |

**Readings, honestly:**

1. **Recency weighting is a genuine forecasting improvement.** Brier drops
   ~0.009 vs baseline at half-life 17 — a large gap for this metric — with
   log-loss confirming. Recent form carries real signal a flat all-history fit
   dilutes.
2. **Phase splits (as implemented) are rejected.** Fitting pass/rush
   separately and recombining hurt both accuracy and calibration badly.
   Whatever DVOA-style value exists in phase decomposition, this simple blend
   does not capture it.
3. **λ=400 beats the λ=200 default slightly** on every probability metric —
   the default was a touch under-shrunk.
4. **Nothing beats the closing line on sides or totals.** Every ATS/O/U figure
   sits below the 52.4% break-even, and no disagreement threshold (0–6 points)
   produced a robust cut in either market. The NFL close stays efficient
   against this model family — consistent with `docs/BACKTEST_NFL.md` and with
   the plan's thesis that the edge lives in props/derivatives, not full-game
   NFL sides. Better Brier still matters: it means better win probabilities
   for moneyline pricing and Kelly staking, not better picks against the
   spread.

## Round 2 — 2014–2025 evaluation (3,295 games; trailing-4-season training)

Contenders: `baseline`, `scores` (the schedule-only fit the live slate ran for
NFL until this round), `recency-17`, `recency-34-r400`. Training capped at the
trailing four seasons for every variant (identical data horizon, constant
per-week cost); evaluation is twelve full seasons.

| variant | Brier ↓ | log-loss ↓ | calib. err ↓ | ATS vs close | O/U vs close |
|---|---|---|---|---|---|
| baseline | 0.2354 | 0.6637 | 0.0247 | 50.2% (3096) | 50.8% (3130) |
| scores (old live fit) | 0.2343 | 0.6609 | 0.0175 | 50.0% | 49.8% |
| **recency-17** | **0.2234** | **0.6379** | 0.0309 | 49.9% | 50.5% |
| recency-34-r400 | 0.2275 | 0.6464 | **0.0174** | 50.0% | 50.3% |

The Round-1 recency finding **replicates on a 12-season window**: `recency-17`
beats both the flat EPA fit and the scores fit by ~0.011 Brier — a decisive
forecasting gap, stable across the widened sample.

**Disagreement sweeps** (recency-17, per-season robustness on the same
ledger):

| cut | overall | bets | seasons > 52.4% |
|---|---|---|---|
| spread ≥ 3 | 51.5% | 1,299 | 7/12 |
| spread ≥ 6 | 55.2% | 301 | 8/12 |
| total ≥ 4 | 52.3% | 1,086 | 6/12 |
| total ≥ 6 | 53.4% | 470 | 8/12 |

Read honestly: the extreme-disagreement cuts lean the right way — and did not
in Round 1's short window — but 55.2% on 301 bets is **under one standard
error above break-even** (se ≈ 2.9% at that n), with ugly seasons mixed in
(2017: 33%, 2025: 35%). Suggestive, not proof. Contrast NCAAF's shipped
totals filter: 52.8% on 5,477 bets.

---

## Round 3 — QB adjustment + market regression (2014–2025, 3,295 games)

The dataset was rebuilt from the nflverse release parquets with
``passer_player_id`` on every dropback (bit-consistent with the previous
build), and ``fit_qb_ratings`` decomposes the passer out of the team offense
(harder ridge on QB dummies; detected starter — each team's primary passer in
its latest game — priced back in at projection time, honest in walk-forward).

| variant | Brier ↓ | log-loss ↓ | calib. err ↓ | ATS vs close | O/U vs close |
|---|---|---|---|---|---|
| recency-17 (incumbent) | 0.2233 | 0.6375 | 0.0335 | 49.9% | 50.5% |
| qb-recency-17 (λq=150) | 0.2210 | 0.6324 | **0.0131** | 48.8% | 49.6% |
| qb-recency-17-q75 | 0.2219 | 0.6352 | 0.0183 | 49.6% | 49.6% |
| **qb-recency-17-q300** | **0.2205** | **0.6313** | 0.0148 | 48.7% | 50.6% |

**Readings, honestly:**

1. **The QB adjustment is a real forecasting improvement** — Brier −0.0027
   vs the incumbent with log-loss agreeing, and **calibration error drops
   2.5×** (0.0335 → 0.0148): the QB-blind fit's biggest sin was
   overconfidence around starter changes, exactly as hypothesized. λq=300
   edges λq=150 on both probability metrics.
2. **The disagreement re-test does not clear the bar.** qb-q300 at spread
   ≥6: 55.3% on 246 bets vs the incumbent's 55.3% on 284 — same rate,
   fewer bets, still ~1 se above break-even. Sharper ratings did not
   concentrate the signal; the filter stays unpromoted.
3. **Market regression (nfelo's test, select ≤2019 / holdout 2020+):** the
   close's own Brier on the holdout is **0.2109** — better than every pure
   model (best: 0.2231) — and blended probabilities converge to the market
   rather than beating it (select-chosen w*=0.2 → holdout 0.2118). Read
   plainly: **this model family does not out-forecast the NFL close, and no
   blend weight makes it.** The model's value is (a) pricing when no line
   exists, (b) disagreement detection for leans, (c) prop/derivative
   markets. Follow-up (deliberate, not rushed): use market-anchored
   probabilities for stake sizing while keeping the pure model for the
   market-vs-model surfaces — a wagering-policy change to design carefully,
   not a ratings change.

## Promotion decisions (made this round)

- **PROMOTED (Round 3):** the live NFL slate now fits **QB-adjusted
  recency-weighted EPA ratings** (`fit_qb_ratings`, λq=300 — the Round-3
  winner; falls back to the QB-blind fit on plays data without passer
  identity). Replaces the Round-2 `recency-17` promotion.
- **PROMOTED (Round 2):** recency weighting itself (half-life 17 weeks,
  trailing four seasons; `DEFAULT_RECENCY_HALF_LIFE` in `features/team.py`).
- **NOT promoted:** an NFL spread/total disagreement filter — re-tested with
  QB-sharpened ratings in Round 3 and still ~1 se above break-even at real
  sample sizes. The burden of proof stays a robust >52.4% across seasons.
- **NOT promoted:** market-blended probabilities as a ratings change — the
  Round-3 finding is that the close is the accuracy ceiling for this model
  family; blending converges to it. Staking integration is a deliberate
  follow-up, not a fit change.
- NCAAF is untouched: the scores fit + the backtested totals filter remain.

## Backlog (next experiments, in rough order of expected value)

1. **Rest / schedule spots** — bye weeks, short weeks (Thu), divisional-game
   HFA discount; all computable from the committed schedules.
2. **Injury/availability beyond the QB** — the FantasyPros `/injuries`
   endpoint is live with our key; aggregate starter-out downgrades.
3. **Weather on totals** — wind above ~15 mph measurably depresses totals;
   Open-Meteo history is free; stadium coordinates are a small static table.
4. **Market-anchored staking** — use blend-weight probabilities for Kelly
   sizing while the pure model keeps driving leans and cards (the Round-3
   market-regression finding, applied where it belongs). *Wired: the live
   runner now anchors the NFL slate's gating/staking belief at w=0.2 (the
   select-chosen weight) via `--model-weight`; other leagues stay raw until
   their own labs argue otherwise. Still open: tuning w from live paper CLV.*
5. **Success-rate / early-down EPA blends** — alternative efficiency
   definitions per the DVOA/PFF literature, testable as drop-in `epa_col`
   variants.
6. **NCAAF lab** — port the harness to the scores model + pace variants; the
   totals filter's threshold re-validated on 2011–2025 style windows; a CFBD
   play-by-play EPA fit is the largest single accuracy jump available
   anywhere in the system.
7. **Model-model ensemble** — EPA fit × scores fit blend for the pure-model
   side (edge detection), now that the market blend answered the accuracy
   question.
8. **CLV per flagged lean** — the in-season leading indicator; the 3-hourly
   BettingPros snapshots exist precisely for this join.

---

# NCAAF lab — college variant benchmarks

## Round 1 — 2019–2024 evaluation (6,640 games; trailing-4-season training)

First college benchmark: the shipped scores fit (λ=25) against shrinkage and
recency sweeps, `model_lab.py --league ncaaf` (games-only until a college
plays dataset lands).

| variant | Brier ↓ | log-loss ↓ | calib. err ↓ | ATS vs close | O/U vs close |
|---|---|---|---|---|---|
| scores (shipped, λ=25) | 0.2076 | 0.6011 | 0.0523 | 50.0% | 52.0% |
| **ridge-10** | **0.1983** | **0.5786** | **0.0293** | 49.6% | 51.7% |
| ridge-50 | 0.2160 | 0.6206 | 0.0640 | 49.9% | 52.2% |
| recency-17 | 0.2148 | 0.6180 | 0.0813 | 50.0% | 51.9% |
| recency-34 | 0.2106 | 0.6085 | 0.0756 | 49.9% | 52.0% |
| recency-17-r50 | 0.2247 | 0.6400 | 0.0748 | 49.8% | 51.9% |

**Readings, honestly:**

1. **λ=10 is a decisive promotion** — Brier −0.0093 vs the shipped default
   with calibration error nearly halved. The old λ=25 over-shrank college
   ratings; thin schedules still carry more signal than the default trusted.
2. **Recency does not transfer to the college scores fit.** Every recency
   variant is *worse* than the flat fit — the opposite of the NFL result.
   Plausible mechanism: game-level down-weighting starves an already
   sparse, weakly-connected schedule graph. Rejected; revisit only with a
   play-level college fit.
3. The flat O/U rates (~52%) sit exactly where the shipped totals filter
   already operates (52.8% with its threshold) — consistent, no change.

**PROMOTED:** the live NCAAF slate fits at `ridge_lambda=10`
(`run_live_slate`); the NFL scores fallback keeps its default. Follow-up
queued: a finer λ sweep (5/10/15) and, longer-term, a CFBD play-by-play EPA
fit.

## Round 2 — college EPA fit + EPA×scores blend (2015–2024, 10,157 games)

The queued play-by-play round. Data: `datasets/ncaaf/plays.parquet` —
1.18M scored plays (CFBD `/plays`, `ppa` as the EPA column) across 8,534
games, backfilled by `build_ncaaf_pbp_datasets.py` and topped up weekly by
the refresh workflow. Two pieces of new machinery, both exact:

* **`compress_plays`** — plays aggregated to `(posteam, defteam, season,
  week)` cells reproduce the play-level ridge fit bit-for-bit (the one-hot
  design is constant within a cell) at ~1/70th the matrix size; a 1.4M-play
  college fit refits in well under a second per walk-forward week.
* **`ncaaf_walk_order`** — fixes a walk-forward label leak affecting every
  earlier college number: bowls are labeled POST week 1, so the engine's
  `week <` slice trained regular-season predictions on that season's
  *bowls*. POST weeks now renumber to 17 + dense rank. All Round 2 numbers
  (the re-run bar included) are leak-free; Round 1 numbers are not directly
  comparable (different eval window and ordering).

| variant | Brier ↓ | log-loss ↓ | calib. err ↓ | ATS vs close | O/U vs close |
|---|---|---|---|---|---|
| ridge-10 (promotion bar, re-run) | 0.19756 | 0.5761 | 0.0161 | 49.9% | 51.4% |
| epa-r50 (best pure EPA) | 0.19832 | 0.5772 | 0.0200 | 50.2% | 50.9% |
| epa-r100 | 0.19848 | 0.5774 | 0.0229 | 50.0% | 51.0% |
| epa-r200 | 0.19955 | 0.5803 | 0.0246 | 50.2% | 51.3% |
| epa-r400 / r800 / recency-17 | 0.2019–0.2057 | — | — | — | — |
| blend-epa30 | 0.19514 | 0.5699 | 0.0145 | 49.9% | 51.5% |
| **blend-epa50** | **0.19486** | **0.5690** | **0.0119** | 49.9% | 51.1% |
| blend-epa70 | 0.19558 | 0.5705 | 0.0144 | 50.0% | 51.2% |

**Readings, honestly:**

1. **The pure EPA fit loses.** Its ridge sweep bottoms at λ=50 (extended
   downward after the first pass came back monotone), 0.0008 Brier short of
   the scores bar with worse calibration. Play-level efficiency alone does
   not beat final scores in college.
2. **The 50/50 blend wins decisively** — Brier −0.0027 vs the bar (a larger
   gap than the one pure EPA lost by), calibration error 0.0119, the best
   recorded for college. The optimum is an interior one with both
   neighboring weights also beating the bar — a robust ensemble of two
   near-equal fits carrying different information, not a knife-edge.
3. The market close remains untouchable (blend Brier at w=0 in the
   market-regression sweep: 0.1652) — same doctrine as the NFL: the model's
   value is no-line pricing and disagreement, and ATS/O/U flat rates stay
   where the shipped totals filter already operates.

**PROMOTED (blend-epa50):** the live NCAAF slate now prices a 50/50 blend
of the EPA fit (λ=50 on compressed cells) and the scores fit (λ=10)
whenever the committed plays file is present, falling back to the pure
scores fit otherwise (`run_live_slate._build_projection`).

## Round 4 — rest spots (NFL, 2014–2025, 3,295 games)

Bye-week bonus / short-week penalty on top of the promoted QB fit
(`RestAdjustedModel`; rest days are preseason-public schedule knowledge).

| variant | Brier ↓ | log-loss ↓ | calib. err ↓ |
|---|---|---|---|
| qb-recency-17 (control) | 0.22053 | 0.63127 | 0.0148 |
| rest-1.0-1.0 | 0.22032 | 0.63072 | 0.0149 |
| rest-0.5-1.5 | 0.22040 | 0.63091 | **0.0146** |
| rest-2.0-1.0 | **0.22028** | **0.63058** | 0.0166 |

**Reading, honestly:** every grid improves Brier and log-loss over the
control — consistent direction, tiny magnitude (~0.0002 Brier, an order of
magnitude below the QB adjustment). That is exactly what a real but rare
feature looks like: byes and short weeks touch ~11% of games, so a ~1-point
correction on those games barely moves the aggregate. **PROMOTED at the
moderate 1.0/1.0 grid** (best-balanced: second-best Brier, calibration flat)
— near-free, literature-standard, and consistent across every setting
tested. The aggressive 2.0 bye bonus buys a hair more Brier at a visible
calibration cost; not worth it.

## Round 5 — wind on totals (NFL, 2014–2025)

Symmetric wind suppression past a threshold (`WeatherAdjustedModel` over the
QB fit; `datasets/nfl/weather.parquet`, Open-Meteo daily-max wind). Aggregate
Brier is structurally blind to a symmetric totals shift, so the verdict
lives in the windy-game conditional (422 eval games with max wind ≥15 mph):

| | O/U vs close, windy games only |
|---|---|
| control (no wind) | **46.3%** (406 bets) |
| wind-15-0.30 | 48.1% (395 bets) |

**Reading:** the control's windy-game deficit is the finding — the market
prices wind and the bare model demonstrably over-projects windy totals. The
adjustment recovers ~1.8 pts of that with aggregate metrics and ATS flat.
**PROMOTED (wind-15-0.30) as a bias correction, not an edge** — it makes
fair totals honest on windy days; it does not beat the close there.
*Wiring note:* the backtest prices historical actuals; the live slate needs
the Open-Meteo *forecast* endpoint at slate time — queued as the next
plumbing item, the model constants are settled.

---

# Summer lab — MLB + WNBA variant benchmarks

**Data:** the committed games frames (2024–2026) carry no lines, so the
market benchmarks come from The Odds API's historical archive — daily
closing snapshots for 2025–2026 (MLB at 17:00/23:00 UTC, WNBA at 22:30),
banked as **Actions** artifacts and joined per game by
`scripts/join_historical_closes.py` (last pre-kickoff snapshot, median
across books, doubleheader-safe nearest-kickoff match). The joined frame
lives on a private path; only the aggregated verdicts below are committed.
Coverage: MLB 4,148 spreads / 4,167 totals on 6,829 games (91% of the
collected 2025–26 window); WNBA 522/524 on 792 games (99% of 2025–26).

## MLB Round 1 — shrinkage, recency, park factors (2024–2026, 6,792 games)

The shipped hand-picked default (λ=5) against the ridge sweep (extended
twice after monotone edges), recency, and self-calibrating park factors.

| variant | Brier ↓ | log-loss ↓ | calib. err ↓ | O/U vs close |
|---|---|---|---|---|
| scores (shipped, λ=5) | 0.2492 | 0.6927 | 0.0504 | 49.7% |
| ridge-25 | 0.2472 | 0.6877 | 0.0413 | 49.4% |
| ridge-50 | 0.2466 | 0.6864 | 0.0386 | 49.5% |
| **ridge-100** | **0.2463** | **0.6856** | **0.0334** | 49.9% |
| ridge-200 | 0.2464 | 0.6859 | 0.0334 | 49.5% |
| ridge-400 | 0.2470 | 0.6870 | 0.0339 | 49.3% |
| recency-2 / 4 / 8 | 0.2518 / 0.2502 / 0.2490 | — | all ≥ 0.052 | — |
| park-fit | 0.2492 | 0.6927 | 0.0501 | 49.0% |

**Readings, honestly:**

1. **Heavy shrinkage wins decisively.** The curve bottoms at λ≈100–200 —
   MLB's true team spread is small and a lightly-shrunk fit mostly learns
   noise. λ=100: Brier −0.0029 vs the shipped default with calibration
   error cut by a third. **PROMOTED** (lab calibration + live slate).
2. **Recency is rejected** — every half-life is worse than the flat fit.
   Same mechanism as college: game-level down-weighting starves the fit,
   and in MLB the day-to-day form signal is mostly the starter, which a
   team-level weight cannot see (that's the SP round's job).
3. **Park factors (as a total bonus) add nothing** — the shrunk per-venue
   residuals are real but tiny at λ=40-game shrinkage, and O/U vs close
   actually slipped. Rejected pending a smarter (weather×park) treatment.
4. **The honest market ceiling is the closing moneyline, not the run line.**
   The archive's run lines are all ±1.5 with the information in the *price*,
   so the blend sweep's spread-probit "market" (holdout 0.2631) is
   structurally invalid for MLB, and the ~60% flat "ATS win rate" every
   variant posts is a pricing artifact (+1.5 covers often; the juice prices
   it) — not an edge, and not reported above. Devigging the closing
   moneylines directly gives **Brier 0.2491** over the 1,876 priced 2026
   games; the promoted model's 2026 holdout is **0.2485** — at the market's
   accuracy on winners, from scores alone. The select-chosen 90/10
   model/market-probit blend (0.2479) is noted but not shipped: its market
   leg is the invalid probit.

## WNBA Round 1 — shrinkage, recency, back-to-backs (2024–2026, 756 games)

| variant | Brier ↓ | log-loss ↓ | calib. err ↓ | ATS vs close | O/U vs close |
|---|---|---|---|---|---|
| scores (shipped, λ=10) | 0.2243 | 0.6412 | 0.0322 | 53.8% (506) | 47.4% |
| ridge-3 / 25 / 50 | 0.2244 / 0.2261 / 0.2288 | — | all worse | — | — |
| recency-2 | 0.2212 | 0.6331 | 0.0310 | 54.2% | 45.7% |
| recency-4 | 0.2181 | 0.6265 | **0.0204** | 54.0% | 46.5% |
| **recency-8** | **0.2165** | **0.6236** | 0.0340 | **55.4%** (504) | 49.6% |
| recency-12 / 16 / 24 | 0.2171 / 0.2181 / 0.2195 | — | — | ~55.2% | — |
| b2b-2 / b2b-3.5 | 0.2243 / 0.2244 | — | — | 53.8% | — |

**Readings, honestly:**

1. **Recency is the WNBA story — the exact opposite of MLB.** The ladder
   improves monotonically to half-life 8 buckets (~4 months) and rises
   again at 12/16/24: a bracketed interior optimum, Brier −0.0078 vs the
   flat fit (a big gap for this metric). A 13-team league where rosters
   and rotations swing inside a season rewards forgetting; **PROMOTED
   (recency-8 on the live λ=10 fit)**.
2. **The ridge sweep confirms the shipped λ=10** — 3/25/50 all lose.
3. **Back-to-back penalties are a no-op as tested** — identical Brier to
   the bar, +0.1% ATS. The market already prices fatigue; rejected pending
   a pace-aware treatment.
4. **The market is still clearly ahead in WNBA** (closing spread-probit
   holdout Brier 0.2055 vs the promoted model's 0.2241; every select-chosen
   blend lands behind pure market). The model's value here is no-line
   pricing and disagreement selectivity — and the 55.4% flat ATS over 504
   bets (~2.4σ) is the first genuinely interesting flat record the lab has
   produced; worth tracking live before anyone stakes on it.

## MLB Round 2 — starter decomposition (2024–2026, 6,792 games)

The market's dominant MLB factor, built as the QB machinery on a games-long
frame: `runs = intercept + offense[batting] + bullpen[fielding] +
starter[SP]`, fit on the banked statsapi starters (13,658 rows, 100% of the
frame) with the promoted λ=100 team ridge, priced with the evaluated game's
actual starters (probables are public pregame knowledge — the rest-spot
argument). `q` is the per-starter shrinkage, swept until bracketed.

| variant | Brier ↓ | log-loss ↓ | calib. err ↓ | 2026 holdout Brier ↓ |
|---|---|---|---|---|
| scores λ=100 (Round 1 bar) | 0.24630 | 0.68565 | 0.0334 | 0.2485 |
| sp-q5 | 0.24892 | 0.69165 | 0.0447 | 0.2524 |
| sp-q15 | 0.24647 | 0.68618 | 0.0284 | 0.2510 |
| sp-q40 | 0.24522 | 0.68348 | 0.0180 | 0.2492 |
| sp-q80 | **0.24492** | **0.68284** | 0.0147 | 0.2485 |
| **sp-q160** | 0.24493 | 0.68284 | 0.0140 | **0.2483** |
| sp-q320 | 0.24502 | 0.68304 | **0.0132** | 0.2481 |
| sp-q640 | 0.24510 | 0.68320 | 0.0139 | 0.2481 |

**Readings, honestly:**

1. **The starter term is real but must be shrunk hard.** q5 (trust a
   6-start sample nearly raw) *loses* to the team-only bar; the curve
   bottoms at q80–160 and rises again at 320/640 — a bracketed interior
   optimum. **PROMOTED (sp-q160)**: vs q80 the Brier is identical to the
   4th decimal and calibration + holdout are better.
2. **Calibration is the headline** — 0.0334 → 0.0140, the best number
   recorded for any league in this lab. The app's win probabilities and
   the pick'em EV engine consume these probabilities directly.
3. **The holdout column is the honest caveat:** sp-q160's 2026 Brier
   (0.2483) beats the bar (0.2485) and the devigged closing-moneyline
   ceiling (0.2491), but only thinly, and q320/640 tie it — most of the
   full-window gain is calibration and 2024–25 accuracy. The starter
   term's live value runs through the probables feed: it prices *tonight's
   pitching matchup*, which the team-only fit cannot see at all.
4. Live wiring: `run_live_slate` fits the decomposition on the committed
   starters bank and reads today's probables from the keyless statsapi
   schedule; a game with no announced probable prices starter-neutral
   (collapses to the team fit), and any fetch failure falls back to the
   Round 1 scores fit.

## MLB Round 3 — FIP-quality starter priors (2024–2026, 6,792 games)

The queued follow-up: shrink the starter dummy toward a defense-independent
skill estimate instead of zero. `mlb_fip_priors` turns each starter's
banked K/BB/HBP/HR into a runs-per-start delta vs league (the FIP numerator
per IP is already on the per-9 scale; the constant cancels), scaled by his
innings share and shrunk by workload; the dummy then fits the *residual*
and pricing adds the prior back — a below-floor starter prices at his
prior rather than league-average.

| variant | Brier ↓ | calib. err ↓ | 2026 holdout ↓ |
|---|---|---|---|
| sp-q160 (Round 2 promotion) | 0.24493 | **0.0140** | **0.2483** |
| sp-fip30 | 0.24547 | 0.0254 | 0.2495 |
| sp-fip60 | 0.24506 | 0.0225 | 0.2491 |
| sp-fip120 | 0.24480 | 0.0185 | 0.2487 |

**Reading, honestly: REJECTED.** The sweep is monotone toward *more* prior
shrinkage, and its limit is exactly the promoted sp-q160 — the curve is
approaching the bar, not beating it. fip120's hairline full-window Brier
edge (−0.0001) costs calibration and the holdout. Mechanism: at q160 the
dummy already sees 2+ seasons of starts for most pitchers, so FIP's
small-sample stabilization advantage only touches the thin rookie slice,
and the innings-share scaling is a blunt bullpen model. The machinery
stays for a smarter treatment (e.g. priors only below a starts floor).

*Round side-effect (live-path bugfix):* `StarterAwareModel` priced a game
with no known starter via `QBTeamRatings.starters` — the starter that team
most recently *faced* in training, a stale wrong guess. Unknown starters
now price exactly league-average; the re-run bar above is bit-identical to
Round 2's, confirming the fallback was dormant in backtest (banked
starters cover ~100% of games) and only live no-probable games were
exposed.

## WNBA Round 2 — pace×efficiency (2024–2026, 756 games)

The research list's WNBA headline: decompose scoring into possessions ×
points-per-possession. Possessions come from the wehoop team boxes
(`datasets/wnba/team_box.parquet`, the standard `FGA − ORB + TOV +
0.44·FTA` estimator, 100% of the frame covered); efficiency is the scores
machinery on points-per-100-possessions; pace is additive
`league + dev[home] + dev[away]` with shrunk per-team deviations.

| variant | Brier ↓ | log-loss ↓ | calib. err ↓ | ATS vs close | 2026 holdout ↓ |
|---|---|---|---|---|---|
| recency-8 (Round 1 promotion) | 0.21646 | 0.62359 | 0.0340 | 55.4% | 0.2241 |
| pace-eff-flat | 0.22407 | 0.64079 | 0.0282 | 53.5% | 0.2389 |
| **pace-eff-r8** | **0.21615** | **0.62289** | **0.0277** | 55.3% | **0.2235** |

**Readings, honestly:**

1. **PROMOTED (pace-eff-r8)** — better than the Round 1 promotion on every
   forecasting metric: Brier, log-loss, calibration (0.0340 → 0.0277), and
   the 2026 holdout. The margins are small on 756 games, but uniformly
   positive and structurally motivated: the decomposition prices a
   fast-pace matchup's total, which a raw points fit averages away.
2. **The flat variant loses badly** — recency does the heavy lifting
   (rosters and rotations swing inside a season); pace is a layer on top
   of that finding, not a replacement for it.
3. The market stays ahead (holdout 0.2055); the flat ATS (~55%) holds
   under the new fit — still track-live, not stake-on-it.

Live wiring: `run_live_slate` fits pace×efficiency whenever the committed
team boxes exist (the box top-up rides the daily refresh); any failure
falls back to the recency-8 scores fit.

**Backlog (summer):** NegativeBinomial run distributions,
weather-on-totals; WNBA minutes-aware availability; FIP priors gated to
below-floor starters only.

---

## Round 8 (2026-08) — the small-bye re-sweep: a clean negative

The post-2011 rest literature (docs/EDGE_RESEARCH.md §2.1: true bye effect
≈ +0.3 points while the market prices ≈ +0.97) flagged our promoted +1.0 bye
constant as suspiciously market-shaped. New variants `rest-0.3-1.0`,
`rest-0.3-0.5`, `rest-0.0-0.5` ran through the standard walk-forward gate,
2011–2025, 4,064 games:

| variant | Brier | ATS | O/U |
|---|---:|---:|---:|
| **rest-1.0-1.0 (promoted)** | **0.21945** | **49.5%** | 50.0% |
| rest-0.3-1.0 | 0.21956 | 49.2% | 50.3% |
| rest-0.3-0.5 | 0.21956 | 49.3% | 50.3% |
| rest-0.0-0.5 | 0.21963 | 49.3% | 50.3% |
| qb-recency-17 (no rest) | 0.21962 | 49.3% | 50.2% |

The differences are hair-thin (~0.0002 Brier), but the promoted +1.0 stays
best on both Brier and ATS: **the literature's +0.3 does not improve our
walk-forward accuracy, and the fade-the-rested hypothesis gets no support
from this replay.** No default change; the variants remain in the lab for
re-runs as seasons accumulate. The market-blend sweep re-confirmed Round 3's
lesson unchanged on every variant (pure market Brier 0.2109 on holdout beats
every pure model at 0.223; select-chosen w=0.2 lands at 0.2119).

## NCAAF Round 3 (2026-09) — sim dispersion, measured at last

The college sim's outcome noise (`sd_margin` 17.0, `sd_total` 16.0) had never
been walk-forward measured the way the NFL's 13.0/13.6 were. Measuring it
against the shipped `ridge-10` fit shows it was **too narrow** — the sim priced
college games as more predictable than the model actually is.

Walk-forward residual sd of (actual − model), `ridge-10`, 10k sims:

| sample | n | sd margin | sd total |
|---|---|---|---|
| 2015–2026 (all) | 12,212 | 19.06 | 17.26 |
| 2022–2025 complete | 6,030 | **18.23** | **16.71** |
| 2023–2025 complete | 4,571 | 18.11 | 16.57 |

Every individual season came in above 17.0 (17.8 – 20.3), so this is not a
sample-window artefact. Seasons improve as the training history deepens, which
is why the recent complete-season window is the one promoted.

**Promoted: `NCAAF_SD_MARGIN = 18.2`, `NCAAF_SD_TOTAL = 16.7`**, defined once in
`velocity/models/simulate.py` — the constant previously appeared verbatim in
four places and could drift.

Confirming gate (2022+ out-of-sample, mean of seeds 7 / 101 / 2027):

| sd margin / total | expected calibration error | Brier |
|---|---|---|
| 17.0 / 16.0 (was) | 0.02271 | 0.20040 |
| 18.0 / 16.5 | 0.02051 | 0.20045 |
| **18.2 / 16.7 (promoted)** | **0.02097** | 0.20062 |

Both wider settings beat the shipped one on calibration error on *every* seed;
the gap between 18.0 and 18.2 is inside seed noise, so the directly measured
value wins rather than the marginally better-scoring one. Brier is flat to the
fourth decimal, which is what should happen when only the spread of the
distribution moves — it is the calibration metric, not the discrimination one,
that this constant governs.

**The trap worth recording.** This started from an observation that NCAAF
residuals had sd 15.5 against the sim's 17.0, implying the sim was *over*
-dispersed and should shrink. That measurement was taken against the **market's
closing line**, which is far sharper than our model. Calibrating a model's own
outcome noise to the market's residuals would have shrunk these constants by
about 15% and made the sim badly overconfident — the exact opposite of the
right change. A sim's dispersion must be measured against its own projections.

## The sim-shape round (2026-09) — an empirical draw, a dispersion slope, and the level

`docs/SYSTEM_REVIEW.md` §2 named two things wrong with the engine that prices
every derivative: one `sd` per league whatever the matchup (2.1), and a normal
where E8 had measured football residuals leptokurtic in the shoulders and fat
in the tail (2.2). Both are now switches on `SimConfig` — `sd_total_slope` /
`sd_margin_slope` around `sd_anchor_total`, and a `ResidualPool` of the
shipped model's own walk-forward residuals to draw `(margin, total)` pairs
from instead of the normal — and both went through one gate
(`scripts/sim_lab.py`): every 2022+ game re-priced from the model's own μ
under normal / sloped / empirical / both, each test season's pool and slopes
fitted on the seasons before it, scored on moneyline calibration error and
Brier and on E8's yardstick — the worst |sim − real| probability at any
half-point offset from the fair line, on spreads and totals, shoulders
(≤ 13.5) and tail (14.5–28.5) apart. 10,000 sims, seeds 7 / 101 / 2027.

**What the bank found first.** Building the pool
(`scripts/build_sim_residuals.py`, `datasets/{league}/sim_residuals.parquet`)
means measuring actual − projected for every out-of-sample game, and the NFL
mean was not zero:

| season | actual total | model total | close total |
|---|---|---|---|
| 2011–2021 (mean) | 45.7 | 48.2 | 45.6 |
| 2022 | 44.0 | 47.4 | 44.2 |
| 2023 | 43.8 | 46.8 | 43.1 |
| 2024 | 46.0 | 47.2 | 44.5 |
| 2025 | 46.0 | 46.7 | 44.9 |

**The shipped NFL model projected totals 2.3 points high, in fourteen of
fifteen seasons.** The fit without the QB decomposition (`recency-17`) does
not: 45.1 against 44–46 actual. The QB fit prices each team with its
*starter's* passer effect, and starters throw above the play-weighted
intercept the offense/defense deviations were centered on — a level the
constant `base_points = 22.5` cannot see, because it is introduced by the
decomposition, not by the era. The college lab had the mirror problem: its
blend still hung from a constant 28.5 while the live runner had already moved
to the trailing two-season mean total (college scoring fell four points a
game after the 2023 clock rules), so the lab's residuals were not the live
model's. `velocity/models/level.py` fits the level *through* the model —
shift `base_points` so the training window's mean projected total is what
those games scored; margins untouched — and the lab's blend now takes the
live runner's level.

Level variants, walk-forward 2011–2025 (4,064 games, trailing-4-season
training, 4,000 sims):

| variant | Brier | calibration error | ATS vs close | O/U vs close | model − actual total | 2022+ | model − close 2022+ |
|---|---|---|---|---|---|---|---|
| `qb-recency-17-q300` (was) | 0.21955 | 0.01322 | 49.5% | 50.4% | **+2.28** | +2.06 | +2.84 |
| level on the whole window | 0.21957 | 0.01294 | 49.4% | 50.4% | +0.07 | +0.71 | +1.49 |
| **level on the trailing two seasons (promoted)** | 0.21955 | **0.01209** | 49.5% | 49.9% | +0.10 | **+0.05** | +0.83 |

The whole-window level lags the era (+0.71 in 2022+); two seasons track it.
Brier and ATS are flat, calibration error improves by a tenth, the totals
bias is gone. **Promoted: `--nfl-level fit` on the trailing two seasons**
(`NFL_LEVEL_SEASONS = 2`), and the lab's college blend on the live level.
On the banked Week-2 board the live NFL model's level came in at 21.83
points a team, 0.67 below the constant it had assumed, and the board's mean
projected total moved from 47.4 to 46.2.

**The gate, on the levelled models** (2022+ out-of-sample; NFL 1,139 games,
NCAAF 6,484; offset errors are the worst absolute probability error at any
half-point offset):

NFL:

| variant | calibration error | Brier | spread shoulder | spread tail | spread mean | total shoulder | total tail | total mean |
|---|---|---|---|---|---|---|---|---|
| normal (shipped) | 0.0512 | 0.2248 | 0.0362 | 0.0311 | 0.0210 | 0.0659 | 0.0322 | 0.0285 |
| normal, sloped sd | 0.0513 | 0.2247 | 0.0362 | 0.0311 | 0.0210 | 0.0661 | 0.0326 | 0.0288 |
| empirical draw | 0.0553 | 0.2254 | **0.0347** | **0.0300** | **0.0187** | 0.0676 | 0.0314 | 0.0281 |
| empirical, sloped | 0.0544 | 0.2256 | 0.0348 | 0.0301 | 0.0189 | 0.0676 | 0.0318 | 0.0284 |

NCAAF:

| variant | calibration error | Brier | spread shoulder | spread tail | spread mean | total shoulder | total tail | total mean |
|---|---|---|---|---|---|---|---|---|
| normal (shipped) | 0.0346 | 0.2048 | 0.0417 | 0.0377 | 0.0315 | **0.0606** | **0.0408** | **0.0370** |
| normal, sloped sd | 0.0345 | 0.2048 | 0.0415 | 0.0379 | 0.0315 | 0.0623 | 0.0449 | 0.0399 |
| empirical draw | **0.0335** | 0.2048 | **0.0396** | 0.0385 | **0.0299** | 0.0643 | 0.0439 | 0.0386 |
| empirical, sloped | 0.0338 | 0.2049 | 0.0399 | 0.0387 | 0.0302 | 0.0661 | 0.0480 | 0.0415 |

For scale, the same NFL totals shoulder error on the *unlevelled* model was
0.1024 and NCAAF's 0.0964: the level was two-thirds of the totals shape error
the review attributed to the distribution. What remains splits by league and
market:

- **The empirical draw** improves spread shape everywhere (NFL shoulders
  −0.0015, tail −0.0011; NCAAF shoulders −0.0021) and NCAAF moneyline
  calibration (−0.0011), but costs NFL moneyline calibration +0.004 on every
  seed (0.0517→0.0564, 0.0489→0.0537, 0.0531→0.0558) and Brier +0.0006, and
  costs NCAAF totals shape (+0.004 shoulder, +0.003 tail). NCAAF stakes only
  totals ≥ 6; the NFL stakes all three. **Not promoted** in either league: on
  the markets each league actually stakes it is a wash or a small loss.
- **The dispersion slope** measured cleanly in the bank (NFL `sd_total`
  13.1 at a 37-point expected total → 14.6 at 55; NCAAF 16.4 at 48 → 19.0 at
  66, +0.14 a point) and did nothing in the NFL gate and hurt NCAAF totals
  at every band (+0.002 shoulder, +0.004 tail). The aggregate offset test
  cannot reward a per-game width that is right on average; a conditional
  gate (offset error *within* expected-total buckets) is the next
  measurement, not a reason to ship it. **Not promoted.**
- **E8's definition of done** — shoulders inside the 0.02 tolerance without
  the gate — is not reached by shape alone and was not reachable: measured
  around the model's own μ rather than the market close, the shoulder error
  carries the model's aim, and the floor from that is ~0.035 on NFL spreads
  whatever the draw. The ladder gate stays.

Both switches ship (`--sim-shape empirical`, `--sim-dispersion sloped`, the
banks, the gate) so the next round re-measures instead of re-building; the
defaults are the gated sim. The residual banks are symmetric by construction
(each pair mirrored) because a raw pool's skew shifted every moneyline by a
point in the first gate for no gain in shape.

**Carried forward.** The NCAAF bank on the live level still runs 1.0 point
high on totals and 1.1 points low on home margin (actual home margin +6.8
non-neutral, model +5.75): the blend's home-field advantage is under by a
point. That is M5's "one HFA across the blend" (`docs/SYSTEM_REVIEW.md`),
with the number now attached.

## The college HFA, pace and level round (2026-09)

`docs/SYSTEM_REVIEW.md` §3.3–3.5 asked three things of the college blend:
put team pace into the EPA half instead of a 65-play constant, settle the
two home-field numbers (the EPA half assumed 2.5, the scores fit learns
~4.85), and down-weight garbage time. Garbage time waits on a plays
rebuild — the committed college plays carry no win probability, clock or
score state (1.3) — so this round ran the first two, each as the promoted
`blend-epa50` with one change, walk-forward over 12,212 games 2015–2026:

- **The home edge, fitted inside the EPA ridge.** `compress_plays` now rides
  a home flag on each cell (+½ at home, −½ away, 0 neutral) and
  `fit_ratings(…, home_col="home")` carries one unpenalized coefficient for
  it. It measures **+0.027 EPA/play** over 2015+, **+0.035** over 2022+ —
  **1.8–2.3 points at the league's pace**, *below* the 2.5 the EPA half
  assumed and well below the scores fit's 3.96 / 4.85 and the raw
  non-neutral home margin of +6.8. EPA does not see everything a home crowd
  moves (field position, special teams, the kicks).
- **Variants.** `blend-hfa-own` (EPA half at its own fitted edge, scores
  half at its learned one), `blend-hfa-epa` (both halves at the EPA-fitted
  edge), `blend-hfa-scores` (both at the scores-learned edge), and
  `blend-hfa-own-pace` (the EPA half through the pace-aware
  `NCAAFGameModel`, each team's plays per game from the training window).

| variant | Brier | log loss | calibration error | ATS vs close | O/U vs close | O/U at ≥ 6 (n) | O/U at ≥ 8 (n) |
|---|---|---|---|---|---|---|---|
| **blend-epa50 (promoted)** | **0.19752** | **0.5754** | 0.01686 | 49.95% | 51.18% | 52.6% (3,733) | 53.3% (2,432) |
| blend-hfa-own | 0.19824 | 0.5774 | 0.01699 | 49.99% | 51.08% | 52.7% (3,747) | 53.3% (2,439) |
| blend-hfa-epa | 0.19918 | 0.5801 | 0.03911 | 50.02% | 51.06% | 53.6% (3,564) | 53.6% (2,304) |
| blend-hfa-scores | 0.19856 | 0.5776 | 0.01582 | 50.01% | 51.12% | 52.7% (3,737) | 53.4% (2,435) |
| blend-hfa-own-pace | 0.19801 | 0.5766 | **0.01496** | 50.00% | 51.03% | 52.7% (3,789) | 53.1% (2,460) |

Home field does not move the money: home field is symmetric on the total,
and every HFA variant leaves the ≥ 6 and ≥ 8 totals records where they
were. On the moneyline the promoted constant beats every fitted edge on
Brier (by 0.0005–0.0017, a few seed-sds), and sharing the EPA-fitted edge
across both halves costs calibration badly (0.039): the EPA number is too
small for the scores half. Pace buys calibration (0.0169 → 0.0150) at a
small Brier cost and no totals gain. **None promoted.** The one variant
that moved the totals record — `blend-hfa-epa`, 53.6% at ≥ 6 — did it by
accident: the scores fit adds its home edge to the home team alone, so
cutting that edge from 4.85 to 2.3 lowered the scores half's total by ~2.5
and the blend's by ~1.25, and that happened to be the size of a bias the
residual bank had already flagged.

**The bias.** The promoted blend's totals against actuals, by season:

| 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|
| −0.1 | +1.5 | −0.6 | **+1.8** | **+1.2** | **+2.3** | **+1.0** | **+1.3** | **+1.4** |

The EPA half is levelled on the trailing two seasons (the NFL fix); the
scores half is not — its ridge fits one intercept over its whole history
with no recency, and college scoring fell four points a game after 2021.
In-sample on the trailing two seasons the scores half runs +0.89 a team.
`calibrate_scores_level` (velocity.models.level) shifts its `base_points`
through the model exactly as the NFL's, ratings and home edge untouched —
the `blend-level2` variant below.

**`blend-level2`** — the promoted blend with the scores half levelled on the
trailing two seasons (everything else unchanged):

| variant | Brier | log loss | calibration error | O/U vs close | O/U at ≥ 6 (n) · ROI | at ≥ 8 (n) · ROI | at ≥ 10 (n) · ROI |
|---|---|---|---|---|---|---|---|
| blend-epa50 (was) | 0.19752 | 0.5754 | 0.01686 | 51.18% | 52.6% (3,733) · +0.3% | 53.3% (2,432) · +1.8% | 53.7% (1,498) · +2.6% |
| **blend-level2 (promoted)** | 0.19753 | 0.5755 | **0.01654** | **51.22%** | **53.3% (3,605) · +1.7%** | **53.5% (2,351) · +2.1%** | **53.9% (1,418) · +2.9%** |

Brier and log loss flat to the fourth decimal (the level does not touch the
margin), calibration a shade better, and the one staked market moves at
every threshold: +0.7 points of hit rate at ≥ 6, worth 1.4% of ROI at −110.
The by-season totals bias falls from +1.0…+2.3 to −0.1…+1.6 (2021 and 2023
keep some: a two-season window lags a fast drop). At w = 0.13 the claimed
edge at ≥ 6 (0.028) now sits just under the realized one (0.033) — the
conservative side.

**Promoted: `--ncaaf-level fit`** (`DEFAULT_NCAAF_LEVEL`) in the live
runner — the scores half's `base_points` shifted through the model on the
trailing two seasons at slate time, exactly as the NFL's level is. The
college residual bank (`datasets/ncaaf/sim_residuals.parquet`) is rebuilt
from the promoted model's projections.

**Carried forward.** Garbage-time down-weighting (3.5) needs the college
plays rebuilt with win probability and score state (1.3); the fitted home
edge and the pace-aware EPA half stay in the lab as variants, measured and
unpromoted.

## MLB Round 4 — the anchoring sweep (2025–2026, 4,212 games with closes)

The number MLB was missing. It ran at anchoring `w = 1.0` — the raw model
price, no pull toward the market — with no probability shrink either, on the
league carrying the largest real exposure, and the sweep that should have
chosen it had never been run because the closes live in the private
historical-odds artifact rather than in `datasets/`.

**Data.** Every banked MLB closing moneyline from the 2025 and 2026 seasons
(runs 8 and 12 of `collect-historical-odds.yml`; 786 snapshot files). Each
book's own pair is de-vigged *first* and the fair probabilities medianed
across books — de-vigging after a price median would fold a lopsided book's
overround into the consensus instead of removing it. 4,452 provider games
carry a close; 4,212 join the committed games frame by teams and nearest
kickoff, one-shot so a doubleheader's halves each get their own.

**Model.** Walk-forward: ratings fitted only on games played before each day
(scores, ridge λ=100), projected through the **count sim**
(`velocity/models/counts.py`). Re-running was the point — the previous
verdict was produced by the rounded normal that sim replaced.

### The weight, estimated on every game

The blend is `p_belief = p_fair + w·(p_model − p_fair)`, so regressing what
happened *over the close* on the model's disagreement *with the close*,
through the origin, estimates `w` itself. That uses all 4,212 games rather
than the few hundred a 0.02 edge gate selects at a low weight.

| | n | **w** | market Brier | raw model | blend @ w | blend @ 0.20 |
|---|---|---|---|---|---|---|
| all | 4,212 | **0.245 ± 0.137** | 0.24319 | 0.24475 | 0.24301 | 0.24302 |
| 2025 | 2,310 | 0.212 ± 0.179 | 0.24114 | 0.24301 | 0.24099 | 0.24099 |
| 2026 | 1,902 | 0.292 ± 0.214 | 0.24569 | 0.24687 | 0.24545 | 0.24548 |

**The shipped 0.2 is right, and `w = 1.0` was decisively wrong** — 5.5
standard errors above the estimate. The two seasons agree. Blending at 0.2
rather than at the fitted 0.245 costs 0.00001 of Brier: indistinguishable, so
the holding position set before this sweep needs no change.

### The sim replacement earned weight

Re-scored with the rounded normal it replaced, the same regression gives
**w = 0.153 ± 0.119** and a raw-model Brier of 0.24605 against the count
sim's 0.24475. The better sim is both more informative *and* earns a higher
anchor — which is exactly why the sweep had to be re-run rather than read off
the record: the old sim would have argued for ≈0.15, a 39% smaller claim on
every MLB bet. Both seasons move the same way (2025: 0.123 → 0.212; 2026:
0.185 → 0.292).

### Two things the sweep says that are less comfortable

1. **The raw model is worse than the close.** Brier 0.24475 against the
   market's 0.24319. The lab's earlier "at the market's accuracy on winners"
   was generous to us; on two seasons of banked closes the model loses
   outright, and only the anchored blend edges ahead — by 0.00018.
2. **The edge is real but thin.** At the live 0.02 gate the gate-selected
   sweep corroborates the slope (below), with a realized edge over the
   de-vigged close of about +2.2% ± 0.9% that is roughly flat in the
   selection, while the claim at `w = 1.0` is +5.8% — the model claimed
   **2.6× what it earned**.

| w | bets | claimed | realized | ROI | |
|---|---|---|---|---|---|
| 0.20 | 310 | 0.0240 | 0.0040 | −3.35% | |
| 0.30 | 1,003 | 0.0278 | 0.0113 | −1.67% | |
| 0.50 | 1,953 | 0.0365 | 0.0263 | +1.71% | |
| 0.70 | 2,561 | 0.0446 | 0.0243 | +1.32% | |
| 1.00 | 3,018 | 0.0577 | 0.0242 | +1.55% | claims 2.4× |

**Read the gate table as corroboration only — it cannot decide the weight,
and running it twice proves that.** Before the per-game seeds were made
reproducible (they went through Python's own `hash`, which is randomized per
process), the `w = 0.20` row read n=303, realized +0.0256, ROI +2.50%; the
identical analysis with fixed seeds reads n=310, realized +0.0040, ROI
−3.35%. Monte Carlo noise alone moved the realized edge by two points,
because a 0.02 gate at a low weight selects only ~300 of 4,212 games and
which ones it selects is itself noisy. The calibration slope moved from 0.252
to 0.245 over the same re-run: that is the estimator to trust, and it is why
it uses every game.

**Practical consequence.** At `w = 0.2` and the live 0.02 gate MLB stakes
about **7% of games** (310 of 4,212) rather than the 72% raw was staking.
That is the exposure change the anchor buys, and it is the point.

Re-run with `scripts/sweep_anchoring.py --league mlb --archive <artifact folder>`.

## WNBA Round 3 — the anchoring sweep (2025–2026, 572 games with closes)

Run alongside MLB Round 4, on the same machinery
(`scripts/sweep_anchoring.py --league wnba`), against the banked WNBA closes
from both seasons. The model scored is the promoted one the live slate runs:
pace×efficiency, λ=10, recency-8, walk-forward.

**It does not support staking the league, and it is the clearest evidence yet
for the posture already in place.**

### The moneyline: nothing reliable, and nothing at all in 2026

| | n | **w** | market Brier | raw model | blend @ w | blend @ 0.20 |
|---|---|---|---|---|---|---|
| all | 572 | 0.270 ± 0.161 | 0.20862 | 0.21506 | 0.20761 | 0.20768 |
| 2025 | 307 | 0.431 ± 0.204 | 0.21462 | 0.21691 | 0.21154 | 0.21243 |
| 2026 | 265 | **−0.013 ± 0.264** | 0.20168 | 0.21292 | 0.20167 | 0.20217 |

Three things to read off it:

* The pooled weight is **1.7 standard errors from zero** — not a number you
  would stake on.
* **The two seasons do not agree.** 2025 says the model's disagreement with
  the close carries real information (w = 0.43); 2026 says it carries
  *none* (w = −0.01). Compare MLB, where 0.212 and 0.292 bracketed the pooled
  0.245.
* The raw model is worse than the close in both seasons, and in 2026 blending
  it in at 0.2 makes the forecast **worse than the closing line alone**
  (0.20217 against 0.20168).

### Against the spread: the 55.4% replicates, and still does not clear the vig

**54.2% ± 2.1% over 568 decided games.** The lab's promoted WNBA headline was
55.4% ATS over 504 games, and this is an independent read on two seasons of
banked closes: the signal is real and it reproduces. But break-even at −110
is **52.4%**, so 54.2% is 2.0 standard errors above a coin flip and only
**0.86 above the number that pays**. That is the whole WNBA case in one line
— a genuine edge against the number, not a demonstrated edge against the
price.

Note what this means for the moneyline table above: the two markets disagree,
and that is not a contradiction. A spread edge that cannot clear the vig is
exactly what a near-zero moneyline weight looks like.

### A structural flaw worth fixing before the next round

The WNBA still prices through the rounded normal, and it has a smaller
version of the problem baseball had. At the shipped `sd_margin = 12.5` the sim
puts **3.2%** of its probability on a tie; there are **zero** ties in the 875
banked games, because basketball plays overtime. Its margin dispersion is also
short — 12.5 against a realized 14.0 — and it prices the home team at 56.2%
against a realized 54.9%.

That was written as a caveat with an expectation attached — that repairing it
might move the weight the way baseball's repair did (0.153 → 0.245).
**The repair landed the same day, and the expectation was wrong**
([`docs/BUILD_WNBA_SIM.md`](BUILD_WNBA_SIM.md)). Re-scored on the corrected
sim the weight is 0.262 ± 0.159 pooled, 0.425 in 2025 and −0.023 in 2026 —
every number within noise of the ones above, and the ATS record identical at
54.2%.

Nothing moved because nothing should have: the sweep measures the moneyline,
and the tie mass was never really costing the moneyline (`p_home_win` split
ties evenly and an extra period is close to a coin flip, so the split was
right by accident). The repair bought what it was aimed at — the total's
dispersion, which was a fifth too narrow, and the short spreads, where an
impossible outcome had been worth a full 3.2 points — and the verdict here is
unchanged, now measured against a correctly-shaped sim.

### The decision

Unchanged: **WNBA stays paper** (docs/STRATEGY_REVIEW.md §1.3). It is priced,
logged and graded at stake zero, which is what a league with a replicating
but sub-vig edge has earned. The two things that would change the answer are
the sim repair above and a third season of closes.

## The parity round (2026-09-17) — the lab measures the live model, in points

`docs/PROJECTION_AUDIT.md` found three things between the lab and the model
that runs: the college blend's EPA half had never seen a 2025 play (the
backfilled 2025 games rows carried boxscore-style ids; 4 of 934 matched the
plays), the SP+ prior the live runner applies had never been through the
harness, and the gate scored win probabilities while the goal is the score.
This round fixes all three before anything else is judged.

**The gate grows a points half.** Every variant row now carries RMSE of the
projected margin and total against the actual result and against the
closing line, the market's own Brier from real moneyline closes where the
games frame has them, and the weight the result puts on (model − close) —
`info_w`, 0 meaning the close already contains everything the model knows.
The closing line is the yardstick, not a baseline.

**The incumbents, scored that way** (NFL 2011–2025 trailing-4, 4,000 sims;
NCAAF 2015–2026 all history, with the 2025 plays keyed at last):

| variant | Brier | calib. | RMSE margin (close) | RMSE total (close) | info_w margin / total |
|---|---|---|---|---|---|
| NFL `qb-recency-17-q300-level2` | 0.21953 | 0.0118 | 13.33 (12.97) | 13.90 (13.23) | +0.08 / −0.00 |
| NCAAF `blend-level2` (2025 plays keyed) | **0.1945** | 0.0192 | 18.61 (15.54) | 17.36 (16.23) | +0.01 / +0.02 |

The college number moved on the re-key alone: the level round recorded
`blend-level2` at Brier 0.19753 on the old ids; with the 2025 season in the
EPA half it is 0.1945 (the populations differ slightly — 11,943 projected
games here — so read the gap as the direction, not the fourth decimal).

**The SP+ prior in the harness** (`blend-level2-sp<K>`: last season's final
SP+ as K pseudo-games in the scores half, leak-gated on the *projected*
season, which the engine now hands every factory):

| variant | Brier | log loss | calib. | RMSE margin | RMSE total | O/U ≥6 (n) | info_w margin |
|---|---|---|---|---|---|---|---|
| blend-level2 | 0.1945 | 0.5683 | 0.0192 | 18.61 | 17.36 | 53.0% (4,312) | +0.007 |
| blend-level2-sp6 | 0.1937 | 0.5664 | 0.0195 | 18.55 | 17.35 | 52.9% (4,275) | +0.004 |
| **blend-level2-sp12** (live) | 0.1933 | 0.5655 | 0.0203 | 18.52 | 17.36 | 52.9% (4,277) | +0.003 |
| blend-level2-sp24 | **0.1929** | **0.5643** | **0.0187** | **18.48** | 17.38 | 52.7% (4,299) | +0.001 |

Brier and margin RMSE improve monotonically with K, as the 2026-08-31 sweep
found on the scores fit alone; the totals record at the filter is flat
within its standard error (~0.8pp at 4,300 bets) and the information weight
drifts toward zero — the prior moves the model toward the market, which is
what roster knowledge the market already has should do. **The lab's college
base is now `blend-level2-sp12`, the configuration the runner prices**, and
the college residual bank (`datasets/ncaaf/sim_residuals.parquet`) is
rebuilt from its projections. K=24 is the better forecaster by every
accuracy column and is left as a candidate for the next round rather than
moved on the same table that chose 12.

## The plays, scale and starters round (2026-09-17) — NFL

Three candidates from `docs/PROJECTION_AUDIT.md`, each over the promoted
`qb-recency-17-q300-level2` (2011–2025, trailing-4-season training, 4,000
sims; `close_brier` is the de-vigged moneyline close's own Brier, 0.2102 on
this window):

| variant | Brier | log loss | calib. | RMSE margin | RMSE total | info_w margin | O/U ≥4 (n) | spread ≥6 (n) |
|---|---|---|---|---|---|---|---|---|
| base (promoted) | 0.2195 | 0.6290 | **0.0118** | 13.33 | 13.90 | +0.080 | 51.4% (1,537) | 54.8% (301) |
| base-scrim | 0.2224 | 0.6365 | 0.0284 | 13.53 | 14.09 | +0.024 | 50.7% (1,762) | 51.1% (532) |
| **base-scale** | 0.2195 | 0.6289 | 0.0142 | 13.32 | **13.59** | +0.086 | 52.2% (882) | 56.2% (258) |
| base-scrim-scale | 0.2216 | 0.6339 | 0.0174 | 13.45 | 13.62 | +0.025 | 51.4% (926) | 51.1% (411) |
| **base-starters** | **0.2187** | **0.6270** | 0.0143 | **13.28** | 13.88 | **+0.099** | 51.2% (1,507) | 55.7% (246) |
| base-scrim-starters | 0.2215 | 0.6343 | 0.0293 | 13.47 | 14.07 | +0.029 | 50.9% (1,734) | 49.5% (461) |

The closing line on the same games: margin RMSE 12.97, total RMSE 13.23.

**Readings, honestly:**

1. **The scrimmage-only fit loses, and loses on every column.** The audit's
   finding was real — kneels are the winning team running out the clock at
   −0.58 EPA, and the unfiltered fit docks the teams that kneel most — but
   the remedy threw out the kicks, punts and field goals with them, and
   those carry field position the ratings want: Brier +0.003, margin RMSE
   +0.20, calibration error more than doubled, and the model's information
   beyond the close cut by two-thirds. **Not promoted.** The narrower cut
   (every live play kept, only kneels, spikes and no-plays dropped —
   `-live`) runs in the next table.
2. **The scale is a totals promotion.** Fitted on the residual bank's
   out-of-sample rows for the seasons before each projected one, the slope
   comes in near 0.50 on the total and 0.87 on the margin. Totals RMSE
   13.90 → 13.59 — a third of the way to the close — with Brier and margin
   RMSE unchanged and the margin's information weight slightly up. The
   totals sweep changes shape as it should: half as many games clear a
   four-point disagreement (882 vs 1,537) and those that do win more
   (52.2% vs 51.4%); at six points the survivors are few (276) and below
   water, which is a scaled model refusing to claim what the raw one
   claimed. **Promoted: `--nfl-scale fit`** (`DEFAULT_SCALE_BY_LEAGUE`),
   the calibration cost (0.0118 → 0.0142) noted.
3. **The announced starter is the best forecaster in the table.** With the
   schedule's `home_qb_id`/`away_qb_id` in place of "the primary passer in
   the latest training game", Brier 0.2195 → 0.2187, margin RMSE −0.06,
   and the largest information weight recorded for the NFL (+0.099). This
   is the lab catching up with the live runner, which has priced the
   depth-chart starter since 2026-09-09: **the lab's NFL base is now
   `qb-recency-17-q300-level2-starters`**, and the residual bank
   (`datasets/nfl/sim_residuals.parquet`) is rebuilt from it.

Not run here: starters and scale together, pace, and the live chain (rest
over the fit) for the incumbent and the candidate — the next table.


## The plays, scale and pace round (2026-09-17) — NCAAF

The same candidates over the college base (`blend-level2-sp12`, the live
configuration; 2015–2026, all history, 4,000 sims; the residual bank the
scale fits on was rebuilt from this base first). The close on the same
games: margin RMSE 15.54, total RMSE 16.23.

| variant | Brier | log loss | calib. | RMSE margin | RMSE total | info_w total | O/U ≥4 (n) | O/U ≥6 (n) |
|---|---|---|---|---|---|---|---|---|
| base (live) | 0.1933 | 0.5655 | 0.0203 | 18.52 | 17.36 | +0.027 | 52.1% (6,420) | 52.9% (4,277) |
| base-scrim | 0.1932 | 0.5651 | 0.0190 | 18.51 | 17.36 | +0.026 | 51.9% (6,404) | 52.7% (4,266) |
| **base-scale** | 0.1932 | 0.5646 | **0.0095** | **18.46** | **17.22** | **+0.045** | 52.0% (6,283) | **53.4% (4,104)** |
| base-scrim-scale | 0.1932 | 0.5644 | 0.0098 | 18.46 | 17.22 | +0.044 | 52.0% (6,275) | 53.4% (4,102) |
| base-scrim-pace | **0.1930** | 0.5645 | 0.0172 | 18.49 | 17.39 | +0.029 | 52.1% (6,438) | 52.7% (4,332) |

**Readings, honestly:**

1. **The scale is a promotion on every column that matters.** Calibration
   error halves, both RMSEs improve, the total's information weight rises
   by two-thirds, and the one staked market moves the right way at the
   promoted filter: 53.4% on 4,104 bets against 52.9% on 4,277 — half a
   point of hit rate, worth about a point of ROI at −110, on fewer bets,
   which is what a total that stops over-claiming its deviation should do.
   The margin slope on the rebuilt bank is above 1 (the prior-carrying
   blend under-disperses big favourites) and the total's about 0.66.
   **Promoted: `--ncaaf-scale fit`** (`DEFAULT_SCALE_BY_LEAGUE`).
2. **The scrimmage cut is a wash.** A hair better on Brier and calibration,
   identical RMSE, a hair worse at the filter — all inside one standard
   error, on a frame that is 0.9% non-scrimmage rows. Not promoted; the
   flag stays for the day the plays are rebuilt with clock and score state.
3. **Pace buys Brier and costs the total.** The best Brier in the table
   (−0.0003) and a better margin, but the totals RMSE is the one column
   that got worse (+0.03) and the filter record did not move. Not
   promoted; pace over the scaled model is the next thing to run.


## The composites round (2026-09-17) — over the promoted scale

**NCAAF**, each over `blend-level2-sp12-scale` (the promoted configuration):

| variant | Brier | calib. | RMSE margin | RMSE total | info_w total | O/U ≥6 (n) |
|---|---|---|---|---|---|---|
| sp12-scale (promoted) | 0.1932 | 0.0095 | 18.46 | 17.22 | +0.045 | 53.4% (4,104) |
| sp24-scale | **0.1929** | 0.0100 | 18.44 | 17.23 | +0.045 | 53.3% (4,092) |
| sp12-pace-scale | 0.1933 | 0.0129 | 18.48 | 17.23 | +0.049 | 53.6% (4,139) |
| **sp12-scale-phase** | 0.1931 | **0.0081** | **18.43** | **17.21** | **+0.054** | **53.6% (4,108)** |
| sp12-scale-rest | 0.1933 | 0.0117 | 18.46 | 17.23 | +0.048 | 53.3% (4,179) |

**Readings, honestly:** the phase-specific scale — fitted on the bank rows
of the phase being projected, through week 4 or after — is better than the
whole-bank scale on every column, by small amounts in the same direction:
it is the early-season slope (1.19 through week 4) being corrected where it
occurs instead of averaged with the rest of the year. **Promoted:
`--ncaaf-scale phase`** (`DEFAULT_SCALE_BY_LEAGUE`); the runner reads the
week about to be played off the current season's played games. K=24 again
edges Brier and margin and again costs the total and the information
weight; the college bye bonus and pace are washes over the scale. None of
the three promoted.

**NFL**, each over the starters base (`qb-recency-17-q300-level2-starters`):

| variant | Brier | calib. | RMSE margin | RMSE total | info_w margin | O/U ≥4 (n) |
|---|---|---|---|---|---|---|
| base-live (kicks kept, dead plays dropped) | 0.2198 | 0.0149 | 13.36 | 13.98 | +0.093 | 52.2% (1,637) |
| base-pace | 0.2215 | 0.0410 | 13.58 | 14.49 | +0.064 | 50.8% (2,149) |
| **base-starters-scale** | **0.2188** | 0.0159 | **13.27** | **13.58** | **+0.100** | 51.7% (853) |
| base-starters-pace-scale | 0.2196 | 0.0267 | 13.41 | 13.68 | +0.079 | 51.5% (1,019) |
| live-nfl-incumbent (rest over base) | 0.2194 | **0.0123** | 13.33 | 13.92 | +0.077 | 51.4% (1,559) |

**Readings, honestly:**

1. **Even the narrow plays cut loses.** Keeping the kicks and dropping only
   kneels, spikes and no-plays is still worse than the full frame on every
   column. A kneel is the winning team's fingerprint, and the ratings want
   it. The plays question is closed for the NFL: **`all`**, and the flag
   stays for a plays rebuild with clock and score state.
2. **Pace is rejected in the NFL** — worse on everything, badly on the
   total (14.49). Thirty-two teams within a few snaps of each other and a
   per-team pace map fitted on the trailing window is noise the constant
   does not carry.
3. **Starters and the scale together are the best NFL forecaster
   recorded**: Brier 0.2188, margin RMSE 13.27 (the close: 12.97), total
   13.58 (the close: 13.23) — and that is the configuration the runner
   prices, depth-chart starter plus the promoted scale.
4. The rest wrapper is a wash on accuracy (total RMSE +0.02): it stays as
   promoted history, and the live composite is scored in the next table.
   The first attempt to score it ran the scale off: a wrapper whose factory
   forwarded ``**kwargs`` hid the inner factory's ``predicting`` parameter
   from the engine, so the leak gate never fired and the scale was never
   fitted. ``functools.wraps`` on every wrapper fixes it and a test pins it.

**The live composite, scored correctly** (rest over the scaled starters
fit, the chain the runner prices minus the wind wrapper; and the phase-
specific scale for the NFL):

| variant | Brier | calib. | RMSE margin | RMSE total | info_w margin | early / late margin RMSE |
|---|---|---|---|---|---|---|
| live-nfl-starters-scale | 0.2186 | **0.0143** | 13.27 | **13.60** | +0.095 | 13.19 / 13.31 |
| live-nfl-starters-scale-phase | **0.2185** | 0.0153 | **13.26** | 13.61 | +0.114 | 13.16 / 13.31 |
| starters-scale-phase (no rest) | 0.2186 | 0.0158 | 13.26 | 13.59 | +0.118 | 13.17 / 13.31 |

The rest wrapper costs nothing over the scaled starters fit (total RMSE
+0.02 for the bye point, as before). **The phase-specific scale is a wash
in the NFL** — a hundredth on the early margin, a hundredth back on the
total, calibration a shade worse — where in college it was a clean win.
The difference is what the two phases have to correct: the college early
slope was 1.19 on a prior-less half, the NFL's 0.72 was mostly the
unscaled total, and the whole-bank scale already took that. **Not
promoted** for the NFL; the flag (`--nfl-scale phase`) stays for a season
with more early-season rows in the bank. The NFL runs `fit`.

**The early-season blend weight (NCAAF)**, over the promoted
`blend-level2-sp12-scale-phase`: the EPA half's weight through week 4 (it
has no prior; the scores half carries SP+), 0.5 after.

| early weight | Brier | calib. | RMSE margin | RMSE total | O/U ≥6 (n) | weeks 1–4 margin / total RMSE |
|---|---|---|---|---|---|---|
| 0.5 (promoted) | 0.1931 | 0.0081 | 18.43 | 17.21 | 53.6% (4,108) | 19.19 / 16.81 |
| 0.3 | 0.1933 | 0.0088 | 18.47 | **17.19** | 53.3% (4,041) | 19.31 / **16.73** |
| 0.4 | 0.1931 | 0.0087 | 18.44 | 17.20 | 53.5% (4,070) | 19.23 / 16.77 |
| 0.6 | 0.1931 | **0.0075** | **18.43** | 17.23 | 53.7% (4,155) | **19.18** / 16.87 |

A wash that teaches something: leaning on the scores half in September
buys a tenth of a point on the early total and pays it back on the early
margin; leaning on the EPA half does the reverse. The prior-carrying half
is not the better half in the early weeks — the two carry different
information and the even split is already close to the optimum. **Not
promoted**; the early-season college gap (§2.2 of the audit) wants a
prior in the EPA half, not a different weight on the one that has it.


## The situational round (2026-09-17) — NFL

Over the full live chain (`live-nfl-full`: wind over rest over the scaled
starters fit — exactly what the runner prices), the two situational
candidates the schedule columns and the weather bank made possible. 2011–
2025, trailing-4, 4,000 sims; the close's total RMSE on these games 13.23.

| variant | Brier | calib. | RMSE margin | RMSE total | info_w total | O/U ≥4 (n) | wet games: total RMSE / mean error (n=394) | div games: margin mean error |
|---|---|---|---|---|---|---|---|---|
| live-nfl-full | 0.2186 | 0.0143 | 13.27 | 13.55 | +0.054 | 53.6% (868) | 13.24 / **−2.09** | −0.02 |
| + rain 0.25 in, 0.5 a side | 0.2186 | 0.0145 | 13.27 | 13.54 | +0.075 | 53.5% (863) | 13.11 / −1.09 | −0.02 |
| **+ rain 0.25 in, 1.0 a side** | 0.2186 | 0.0143 | 13.27 | **13.54** | **+0.094** | **53.7% (869)** | **13.07 / −0.09** | −0.02 |
| + rain 0.10 in, 0.5 a side | 0.2186 | 0.0143 | 13.27 | 13.54 | +0.087 | 53.6% (862) | 13.11 / −1.09 | −0.02 |
| + divisional discount 0.5 | 0.2186 | 0.0143 | 13.27 | 13.55 | +0.054 | 53.4% (869) | 13.24 / −2.09 | +0.48 |
| + divisional discount 1.0 | 0.2187 | 0.0178 | 13.28 | 13.55 | +0.054 | 53.5% (869) | 13.24 / −2.09 | +0.97 |

**Readings, honestly:**

1. **The wind wrapper was already doing more than Round 5 could see.** With
   the scale under it, the live chain's totals RMSE is 13.55 against 13.60
   for rest-over-scale alone, and the ≥4 totals record 53.6% on 868 — the
   first NFL totals cut above break-even at a real sample size in this lab.
   Read it as the calibrated total finally selecting on real disagreement,
   not as a promoted strategy; it goes into `docs/BACKTEST_NFL.md`'s watch
   list, not the runner's defaults.
2. **Rain is a bias correction, and a clean one.** On the 394 outdoor games
   with a quarter-inch or more on the day, the projection ran 2.1 points
   high; a point a side takes that to −0.1, and the aggregate total RMSE and
   the total's information weight both improve. **Promoted: 1.0 point a
   side at 0.25 in** (`DEFAULT_NFL_PRECIP_POINTS`); the live forecast now
   fetches the day's precipitation beside the wind.
3. **The divisional discount over-corrects a bias the model does not have.**
   The raw frame's home margin is 1.9 in divisional games against 2.3
   elsewhere, but the model's divisional-game margin error is already −0.02:
   the ratings absorb the familiarity. Half a point of discount leaves +0.48
   of error, a point +0.97. **Rejected.**


**The injury burden**, over the same live chain: the share of a team's
targets and carries ruled Out or Doubtful that week (quarterbacks excluded —
the starters carry them), at a few points per whole team's worth
(`velocity/features/injuries.py`). The usage bank starts in 2020, so the
feature fires on 2020–2025 (1,709 games) and the conditional is the 223 of
those where one side is 15%+ shorter than the other.

| variant | Brier | calib. | RMSE margin | RMSE total | info_w margin | 2020+ margin RMSE | short-side games: margin RMSE / mean error toward the shorter side |
|---|---|---|---|---|---|---|---|
| live-nfl-full (no burden) | 0.2186 | 0.0143 | 13.27 | **13.55** | +0.095 | 13.08 | 13.70 / **−1.57** |
| **+ burden, 4 a unit** | **0.2184** | 0.0164 | 13.26 | 13.57 | +0.108 | 13.05 | **13.57 / −0.55** |
| + burden, 8 a unit | **0.2184** | 0.0173 | **13.26** | 13.59 | +0.119 | **13.04** | 13.53 / +0.47 |
| + burden, 16 a unit | 0.2186 | 0.0128 | 13.27 | 13.66 | +0.132 | 13.06 | 13.71 / +2.52 |

**Reading, honestly:** the bias is real — the side missing its production
ran a point and a half under the projection, and 4 a unit removes two
thirds of it, 8 slightly over-corrects, 16 is worse everywhere. The
aggregate gains are as small as a feature touching a tenth of games can
show (Brier −0.0002, margin RMSE −0.01), the total gets a hundredth worse
(the burden comes off one team's points and the total with it), and
calibration drifts up by 0.002. **Promoted at 4 a unit**
(`DEFAULT_NFL_INJURY_POINTS`) as a bias correction on the games it targets:
the live runner reads the current week's designations off the committed
bank (refreshed daily from nflverse) and says which teams are heaviest. A
week the bank has not reached yet costs nothing, which is honest and is
also the reason to refresh before the slate runs.


**Cold (NFL)**, over the promoted chain (rain at a point a side, the burden
at 4): a step on the day's mean temperature. 286 outdoor games under 32 °F.

| variant | Brier | calib. | RMSE total | info_w total | cold games: total RMSE / mean error (n=286) |
|---|---|---|---|---|---|
| promoted chain | 0.2184 | 0.0164 | 13.553 | +0.076 | 14.30 / −0.76 |
| + cold < 32 °F, 0.5 a side | 0.2184 | 0.0164 | 13.552 | +0.074 | 14.28 / +0.24 |
| + cold < 32 °F, 1.0 a side | 0.2184 | 0.0164 | 13.555 | +0.071 | 14.33 / +1.24 |
| + cold < 40 °F, 0.5 a side | 0.2184 | 0.0159 | 13.551 | +0.077 | 14.28 / +0.24 |

**Reading, honestly: not promoted.** The freezing games run three quarters
of a point under the projection, which is under one standard error on 286
games (the literature's "weaker and less reliable than wind" holds), and
the step moves the aggregate by a thousandth either way. The promoted
chain's own record on this window, for the audit's scoreboard: Brier
0.2184 against the de-vigged close's 0.2102, margin RMSE 13.26 (the close
12.97), total RMSE 13.55 (the close 13.23), information weight +0.108 on
the margin and +0.076 on the total.


## The play-context round (2026-09-17) — NFL

**What changed first.** `datasets/nfl/plays.parquet` was rebuilt 2011–2026
from the nflverse release parquets with the context the audit listed
(`PBP_CONTEXT_COLUMNS`): the pre-snap win probability (`wp`, and the
Vegas-line-anchored `vegas_wp`), quarter, clock and score state, the
turnover flags (`interception`, `fumble_lost`, `fumble`), the QB-credited
`qb_epa`, `cpoe`, and the penalty / aborted-snap markers. The twelve columns
the fit already read are byte-identical to the previous file except 2020,
where nflverse has since regenerated 1,381 plays' EPA by at most 0.17 — the
promoted chain re-run on the new file reproduces its recorded numbers to
three decimals. The four continuous columns are stored to three decimals
(the file is 14 MB, from 8; at full precision it was 25). The refresh's
current-season top-up takes the same normalization, so the schema holds.

**The candidates**, each over the whole promoted chain as it stood (level,
scale, starters, rest, the burden at 4, wind, rain at a point a side):

- *Garbage time*: plays with a pre-snap win probability within 5% of 0 or
  1 (16% of snaps; 20% on `vegas_wp`) weighted 0.5 or 0.25 in the fit, and
  a 10% band.
- *Turnover luck*: the EPA of interceptions and lost fumbles (1.5% of
  snaps, −4.4 EPA on average) scaled by 0.5 or 0.25 before the fit.
- *Tails*: EPA clipped at ±4 (1.8% of snaps) or ±3 (3.9%).
- *`qb_epa`* in place of `epa` (differs on 0.13% of plays — fumbles after
  a catch credited to the passer as passing yards would be).
- *The home edge fitted in the ridge* (`home_col` on the QB fit, priced in
  place of the 2.0-point constant): the recency-weighted fit puts it at
  0.6 points a game on 2023–24.

| variant | Brier | calib. | RMSE margin | RMSE total | info_w margin | info_w total |
|---|---|---|---|---|---|---|
| promoted chain, plays as recorded (`live-nfl-promoted-to1.0`) | 0.2184 | 0.0164 | 13.261 | 13.553 | +0.108 | +0.076 |
| garbage 0.5 at 5% | 0.2185 | 0.0154 | 13.281 | 13.555 | +0.086 | +0.069 |
| garbage 0.25 at 5% | 0.2188 | 0.0118 | 13.308 | 13.561 | +0.072 | +0.064 |
| garbage 0.5 at 10% | 0.2186 | 0.0117 | 13.282 | 13.561 | +0.091 | +0.059 |
| garbage 0.5 at 5% on `vegas_wp` | 0.2182 | 0.0137 | 13.269 | 13.560 | +0.108 | +0.063 |
| turnover EPA ×0.5 | 0.2185 | 0.0229 | 13.258 | 13.518 | +0.101 | +0.093 |
| turnover EPA ×0.25 | 0.2192 | 0.0261 | 13.288 | 13.510 | +0.084 | +0.100 |
| EPA clipped at ±4 | 0.2190 | 0.0202 | 13.264 | 13.528 | +0.099 | +0.087 |
| EPA clipped at ±3 | 0.2196 | 0.0248 | 13.284 | 13.520 | +0.095 | +0.097 |
| `qb_epa` | 0.2185 | 0.0183 | 13.269 | 13.539 | +0.086 | +0.090 |
| fitted home edge | 0.2203 | 0.0464 | 13.388 | 13.553 | +0.111 | +0.077 |
| **turnover EPA ×0.5, the bank rebuilt on its core** | **0.2181** | **0.0157** | **13.251** | **13.519** | +0.088 | **+0.087** |

**Readings, honestly:**

1. **Garbage time is not noise the fit wants removed.** Every
   down-weighting loses on the margin (13.26 → 13.27–13.31) and on both
   information weights; calibration improves because the ratings compress.
   A team running out a 24-point lead is still the team that built it.
   **Rejected**, both probability columns, both bands.
2. **The turnover shrink is the one that pays, and its cost was the
   bank's.** At ×0.5 the total improves by 0.035 RMSE and its information
   weight by 0.017 with the margin held, but calibration error jumps 0.016
   → 0.023: the scale was fitted on a residual bank built from the
   unshrunk core, and the shrunk ratings have a narrower spread. With the
   bank rebuilt on the shrunk core (`qb-recency-17-q300-level2-starters-to0.5`)
   and the scale re-fitted, every accuracy column beats the incumbent —
   Brier 0.2184 → 0.2181, calibration 0.0164 → 0.0157, margin 13.261 →
   13.251, total 13.553 → 13.519 (the close 13.23), information weight on
   the total +0.076 → +0.087. The margin's information weight comes down
   (+0.108 → +0.088): the close leans a little less on the model's margin
   even as the margin lands closer. ×0.25 over-shrinks (margin 13.29).
   **Promoted at 0.5** (`DEFAULT_NFL_TURNOVER_SHRINK`); the live runner
   scales the flagged plays' EPA before the fit, and the committed NFL bank
   is now the shrunk core's.
3. **Clipping the tails is a blunter version of the same idea** — it takes
   the total most of the way (13.53 at ±4) but pays on Brier and margin,
   because a 70-yard touchdown is not luck the way a bounce is. `qb_epa`
   changes too few plays to matter. **Rejected.**
4. **The fitted home edge is the biggest loser of the round.** The
   recency-weighted EPA edge (0.6 points a game on recent seasons) is
   under half the constant, and pricing it costs 0.13 on the margin and
   triples the calibration error: the scoring edge of home field is not
   an offensive-efficiency edge. The 2.0-point constant stays.
   **Rejected.**

The promoted chain's disagreement cut on this window: the ≥4 totals cut
reads 53.8% on 784 bets (the previous chain's 53.7% on 869); ≥6, 56.9% on
232. Still a watch item (docs/BACKTEST_NFL.md), not a strategy.


## The college QB and recency round (2026-09-17) — NCAAF

**Data first.** Every college play now carries `passer_player_id`, joined
from cfbfastR's per-play player stats (public, keyed on the same ESPN play
id as CFBD's play-by-play; `scripts/attach_ncaaf_passers.py`, topped up by
the refresh for the current season). Coverage of pass-type plays: 88–97% a
season 2015–2025, 98% of 2026 so far; 931 of 934 2025 games match. The
frame's own clock and score state (`period`, `clock`, `team_score`) are
there for a college garbage-time variant later; it carries no win
probability.

**Three questions**, every candidate over the promoted chain
(`blend-level2-sp12-scale-phase`: the 50/50 EPA×scores blend, the K=12
SP+ prior in the scores half, the phase-specific scale), on the FBS-vs-FBS
population (8,360 games, 2015–2026, the trailing-four-season window):

1. *The QB term* — `fit_qb_ratings` on passer cells (`compress_plays(...,
   by_passer=True)` with the cell counts as `count_col`, which reproduces
   the play-level QB fit exactly), the detected starter priced back in, at
   QB ridges 75 / 100 / 150 / 300 / 600.
2. *The SP+ prior in the EPA half* — last season's final SP+ offense and
   defense as week-0 pseudo-cells worth K games (`sp_pseudo_cells`; the
   scores half has carried the same prior since the sp12 round, the EPA
   half opened every season blind), K = 6 / 12.
3. *Recency on the EPA half* — the promoted fit weighed a four-season
   window flat; half-lives 51 / 34 / 17 / 12 / 8 / 6 / 4 on-field weeks.

| variant | Brier | calib. | RMSE margin | RMSE total | info_w total |
|---|---|---|---|---|---|
| promoted chain (flat EPA half, team fit) | 0.2002 | 0.0170 | 17.733 | 17.253 | +0.053 |
| QB term, q=75 | 0.1965 | 0.0141 | 17.453 | 17.307 | +0.041 |
| q=100 | 0.1966 | 0.0138 | 17.460 | 17.289 | +0.043 |
| q=150 | 0.1968 | 0.0142 | 17.475 | 17.268 | +0.046 |
| q=300 | 0.1974 | 0.0151 | 17.514 | 17.243 | +0.049 |
| q=600 | 0.1981 | 0.0158 | 17.566 | 17.235 | +0.051 |
| SP+ prior in the EPA half, K=6 | 0.2001 | 0.0176 | 17.749 | 17.293 | +0.044 |
| K=12 | 0.2005 | 0.0214 | 17.790 | 17.327 | +0.041 |
| K=12 with recency 34 | 0.1979 | 0.0184 | 17.610 | 17.246 | +0.048 |
| recency, half-life 51 | 0.1975 | 0.0143 | 17.523 | 17.159 | +0.062 |
| 34 | 0.1962 | 0.0145 | 17.426 | 17.115 | +0.067 |
| 17 | 0.1930 | 0.0172 | 17.187 | 17.006 | +0.081 |
| 12 | 0.1912 | 0.0198 | 17.050 | 16.942 | +0.092 |
| 8 | 0.1894 | 0.0259 | 16.922 | 16.874 | +0.108 |
| 6 | 0.1887 | 0.0293 | 16.878 | 16.841 | +0.119 |
| 4 | 0.1888 | 0.0312 | 16.904 | 16.824 | +0.130 |
| QB q=75 with recency 17 | 0.1911 | 0.0173 | 17.050 | 17.087 | +0.076 |
| q=150 with 17 | 0.1914 | 0.0183 | 17.071 | 17.042 | +0.080 |
| q=300 with 17 | 0.1919 | 0.0181 | 17.102 | 17.017 | +0.082 |
| q=150 with 12 | 0.1901 | 0.0225 | 16.970 | 16.982 | +0.093 |
| q=75 with 8 | 0.1886 | 0.0251 | 16.868 | 16.963 | +0.108 |
| q=100 with 8 | 0.1887 | 0.0248 | 16.871 | 16.940 | +0.110 |
| q=150 with 8 | 0.1888 | 0.0257 | 16.877 | 16.915 | +0.112 |
| **recency 6, the bank rebuilt on its core** | **0.1881** | 0.0192 | **16.880** | **16.845** | +0.094 |

The close on this population: margin RMSE 15.64, total RMSE 16.30.

**Readings, honestly:**

1. **Recency is the finding, and it is the largest single gain this lab
   has recorded.** The college EPA half had weighed a 2021 snap like last
   week's, in the transfer-portal era. Every shortening helps through 8
   weeks; 4–8 are flat on the margin (16.88–16.92) and still improving on
   the total; beyond 12 the losses are steady. **Promoted at a six-week
   half-life** (`DEFAULT_NCAAF_EPA_HALF_LIFE`; `--ncaaf-epa-half-life`):
   the interior point of the flat stretch, not its edge. The calibration
   climb through the sweep (0.017 → 0.031) was the bank's — the scale sat
   on residuals of the flat core — and with the college bank rebuilt on
   the recency core (`blend-level2-sp12-epahl6`) and the phase scale
   re-fitted, the confirmed chain reads Brier 0.2002 → **0.1881**,
   calibration 0.0170 → 0.0192, margin RMSE 17.73 → **16.88**, total RMSE
   17.25 → **16.85**, the total's information weight +0.053 → +0.094. The
   margin closed 40% of its gap to the close in one round.
2. **The QB term is real, and small next to it.** On the flat fit the
   passer decomposition takes 0.26–0.28 off the margin RMSE and 0.0034
   off Brier (the announced-starter analogue: the detected starter is
   the passer with the most dropbacks in the team's latest game, so a
   mid-season change is priced from its first game). Beside recency the
   gain shrinks — 0.05 on the margin at an eight-week half-life — and the
   total pays 0.04–0.09 for it at every ridge: a starter change moves the
   offense estimate, and the total with it, more than the games bear out.
   **Measured, not promoted**; the passer ids stay on the file, the flag
   (`--ncaaf-qb-lambda`) and the variants stay in the lab, and the day an
   announced-starter feed exists for college it is a different question.
3. **The SP+ prior does not belong in the EPA half.** K=6 is a wash, K=12
   worse, and with recency it is worse than recency alone: the scores
   half already carries the prior, and pseudo-cells at a points-per-play
   conversion pull the EPA half toward a scale it does not share.
   **Rejected**; `sp_pseudo_cells` stays for the record.
4. **The ≥6 totals cut on the promoted chain** reads 52.5% on 1,891 bets
   (the round's base: 52.8% on 2,863). A sharper model disagrees with
   the close by six points less often, and the cut's edge is unchanged
   within noise — still a watch item (docs/BACKTEST_NCAAF.md), not a
   strategy.


## The recency round (2026-09-17) — both leagues

The college finding (the round above) asked the same question of every
recency key the fits use. Three knobs, all in the lab
(`velocity.features.team.recency_weights(..., offseason_weeks=)`,
`velocity.features.scores.scores_recency_weights`,
`sp_pseudo_games(..., special_teams=True)`):

- **The offseason gap.** The recency key steps `(season, week)` on a
  contiguous key, so the offseason is the few empty week slots after a
  season's last game. `offseason_weeks` ages last season's plays by that
  many extra weeks at the turn of the season — a roster turns over in the
  offseason more than a week's play says.
- **Recency on the college scores half**, which had been flat since
  Round 1; the SP+ pseudo-games sit at week 0 of the projected season, so
  the prior counts as current under the key.
- **SP+ special teams in the prior.** The pseudo-games carried offense
  minus defense; the third component (±3 points a game at the extremes)
  folds in as half onto the team's score and half off the anchor's, so the
  pseudo-game margin is the whole SP+ rating.

### NCAAF — over the promoted six-week-recency chain (FBS vs FBS, 8,360 games)

| variant | Brier | calib. | RMSE margin | RMSE total | info_w total |
|---|---|---|---|---|---|
| promoted chain (`live-ncaaf-promoted-hl6`) | 0.1881 | 0.0192 | 16.880 | 16.845 | +0.094 |
| offseason gap 6 in the EPA key | 0.1876 | 0.0207 | 16.848 | 16.824 | +0.100 |
| gap 12 | 0.1877 | 0.0210 | 16.859 | 16.817 | +0.104 |
| SP+ special teams in the prior | 0.1879 | 0.0191 | 16.869 | 16.845 | +0.094 |
| gap 6 with special teams | 0.1875 | 0.0208 | 16.837 | 16.824 | +0.100 |
| scores half, half-life 8 | 0.1882 | 0.0435 | 16.905 | 16.824 | +0.085 |
| 17 | 0.1873 | 0.0351 | 16.836 | 16.819 | +0.095 |
| 34 | 0.1873 | 0.0297 | 16.832 | 16.821 | +0.098 |
| gap 6, special teams, scores half-life 17 | 0.1868 | 0.0359 | 16.806 | 16.801 | +0.100 |
| gap 6, special teams, scores half-life 34 | 0.1867 | 0.0306 | 16.794 | 16.801 | +0.104 |
| **the same, the bank rebuilt on its core** | **0.1866** | 0.0288 | **16.819** | **16.805** | **+0.102** |

**Readings:** each knob helps a little and they add: the offseason gap
0.03 on the margin, the special-teams component 0.01 (free — it is the
rating SP+ publishes), recency on the scores half 0.05 (34 weeks; 8 is
too short for a twelve-game season, 17 and 34 tie). **Promoted together**
(`DEFAULT_NCAAF_EPA_OFFSEASON_WEEKS` 6, `DEFAULT_NCAAF_SCORES_HALF_LIFE`
34, `DEFAULT_NCAAF_ST_PRIOR` on), the college bank rebuilt on the
combined core (`blend-level2-sp12-hl6-gap6-st-shl34`): Brier 0.1881 →
0.1866, margin RMSE 16.88 → 16.82, total RMSE 16.85 → 16.81, the ≥6
totals cut 52.7% on 1,808. **The caveat is the calibration error**, 0.019
→ 0.029 with the bank rebuilt: the scale corrects the mean deviation, not
the spread, and the sim's margin sd (NCAAF Round 3, measured on the flat
model's residuals) is now wider than a sharper model's. Brier still
improves — resolution gains more than reliability loses — and the
dispersion re-measure is the next item.

### NFL — over the promoted chain (the turnover shrink; 4,080 games)

| variant | Brier | calib. | RMSE margin | RMSE total | info_w margin | info_w total |
|---|---|---|---|---|---|---|
| promoted chain (half-life 17, no gap) | 0.2181 | 0.0157 | 13.251 | 13.519 | +0.088 | +0.087 |
| half-life 8 | 0.2183 | 0.0148 | 13.249 | 13.505 | +0.028 | +0.086 |
| 12 | 0.2177 | 0.0153 | 13.232 | 13.508 | +0.067 | +0.086 |
| 25 | 0.2191 | 0.0122 | 13.294 | 13.537 | +0.098 | +0.088 |
| 17 with offseason gap 8 | 0.2177 | 0.0135 | 13.229 | 13.510 | +0.068 | +0.083 |
| 17 with gap 16 | 0.2177 | 0.0156 | 13.229 | 13.509 | +0.043 | +0.077 |
| 12 with gap 8 | 0.2180 | 0.0175 | 13.235 | 13.507 | +0.033 | +0.079 |
| 25 with gap 16 | 0.2178 | 0.0148 | 13.236 | 13.515 | +0.078 | +0.083 |
| **17 with gap 8, the bank rebuilt on its core** | **0.2176** | **0.0141** | **13.229** | **13.510** | +0.067 | +0.082 |

**Readings:** the half-life is where Round 1 left it — 12 ties 17 on the
margin and gives up a third of the margin's information weight, 8 and 25
lose outright — and the offseason gap is the finding: eight extra weeks
of age at the turn of the season takes 0.02 off the margin and 0.01 off
the total and improves Brier and calibration, and 16 adds nothing over 8.
**Promoted at 8** (`DEFAULT_NFL_OFFSEASON_WEEKS`; `--nfl-offseason-weeks`),
the NFL bank rebuilt on the gapped core
(`qb-recency-17-q300-level2-starters-to0.5-gap8`): Brier 0.2181 →
0.2176, calibration 0.0157 → 0.0141, margin RMSE 13.25 → 13.23, total RMSE
13.52 → 13.51; the ≥4 totals cut 54.2% on 743. The margin's information
weight keeps falling as the margin lands closer (+0.088 → +0.067), the
pattern of every recency change this month: the close moves toward the
model, and the least-squares weight measures what is left.


## The home-margin round (2026-09-17) — the scale's intercept, and the college dispersion

The recency round left the college calibration error at 0.029, up from
0.019, with the bank rebuilt. The dispersion re-measure that was meant to
explain it did not: the promoted chain's walk-forward residual sd is 16.2
on the margin (2022–25) against the sim's 18.2, yet pricing the chain
through a 16.2 sim made calibration *worse* (0.031) and 17.2 slightly
better (0.027). The bias was the answer. Split by site, the chain's mean
projected home margin on home-and-away games was +6.5 against a closing
line of +4.5 and an actual of +4.4 — **two points too much home edge**,
in every season since 2015 — while the unscaled core's bias was −0.5.
The scale itself was the source: `ScaleCalibration` fitted its margin
slope with an intercept and dropped it ("a home-margin bias belongs to
the HFA parameter"), and the college slope is 1.35 — a slope that steep,
applied without the −2.2 intercept it was fitted with, inflates the home
edge along with everything else.

**The fix**: `margin_shift` — the intercept kept, fitted on the bank's
home-and-away rows only (`neutral_ids` from the games frame), applied to
home-and-away games and never to a neutral field; the walk-forward leak
gate unchanged (`scale_model(..., shift=True)`). Then the dispersion
again, measured on the shifted chain (2022–25, 3,174 games: sd 16.19
margin, 16.15 total).

| variant | Brier | calib. | RMSE margin | RMSE total | info_w total |
|---|---|---|---|---|---|
| NCAAF promoted chain (`live-ncaaf-promoted-noshift`) | 0.1866 | 0.0289 | 16.819 | 16.805 | +0.102 |
| sim sd 16.2 / 16.2 (no shift) | 0.1867 | 0.0312 | 16.819 | 16.805 | +0.102 |
| sim sd 16.7 / 16.5 (no shift) | 0.1866 | 0.0292 | 16.819 | 16.805 | +0.102 |
| sim sd 17.2 / 16.7 (no shift) | 0.1866 | 0.0274 | 16.819 | 16.805 | +0.102 |
| **the shift** | 0.1863 | 0.0190 | **16.714** | 16.805 | +0.102 |
| the shift, sim sd 16.7 / 16.5 | 0.1860 | 0.0125 | 16.714 | 16.805 | +0.102 |
| **the shift, sim sd 16.2 / 16.2 (the measured dispersion)** | **0.1860** | **0.0138** | **16.714** | 16.805 | +0.102 |
| NFL promoted chain | 0.2176 | 0.0141 | 13.229 | 13.510 | +0.082 |
| NFL with the shift | 0.2177 | 0.0221 | 13.236 | 13.510 | +0.082 |

**Readings:**

1. **The shift is a bias fix worth a tenth of a point on the college
   margin** (16.82 → 16.71 — the close is 15.64) and it returns the
   calibration error to where the flat model had it (0.029 → 0.019),
   at no cost anywhere. **Promoted for college**
   (`DEFAULT_SCALE_SHIFT_BY_LEAGUE`, `--ncaaf-scale-shift`); the NFL
   bank's intercept is noise around zero and keeping it costs the NFL
   chain calibration (0.014 → 0.022) — **off for the NFL**.
2. **With the bias gone, the dispersion re-measure lands.** The sim's
   18.2 / 16.7 was Round 3's measurement on the flat `ridge-10` fit; the
   shifted chain's own residual sd is 16.2 / 16.2, and pricing through it
   takes the calibration error to 0.014 and Brier 0.1863 → 0.1860 (16.7 /
   16.5 scores 0.0125 — inside seed noise of it, and Round 3's rule holds:
   the directly measured value wins). **Promoted: `NCAAF_SD_MARGIN =
   16.2`, `NCAAF_SD_TOTAL = 16.2`.** Round 3's trap in reverse: a sim's
   dispersion has to be measured against its own projections, and
   re-measured when the projections sharpen.
3. The confirmed college chain: Brier 0.2002 → **0.1860** and margin RMSE
   17.73 → **16.71** on the day, from the flat four-season fit the day
   began with; the ≥6 totals cut 52.7% on 1,812.


## The early-weight round, again (2026-09-17) — NCAAF

The early-season blend weight was a wash over the flat fit (the composites
round). Over the recency chain it is a different question: with a six-week
half-life and a six-week offseason gap the EPA half opens a season on about
a quarter of last season's tail, while the scores half carries the SP+
prior. The EPA half's weight through week 4 (0.5 after), over the promoted
chain with the shift and the re-measured sim:

| variant | Brier | calib. | RMSE margin | RMSE total | info_w total |
|---|---|---|---|---|---|
| promoted chain (0.5 throughout) | 0.1860 | 0.0138 | 16.714 | 16.805 | +0.102 |
| EPA half at 0.3 through week 4 | 0.1858 | 0.0124 | 16.676 | 16.810 | +0.091 |
| **0.4** | **0.1858** | **0.0120** | 16.684 | **16.805** | +0.097 |
| 0.6 | 0.1864 | 0.0140 | 16.766 | 16.811 | +0.105 |

**Reading:** small and consistent — 0.03 off the margin, Brier and
calibration better, the total unmoved at 0.4 (0.3 takes the margin a hair
further and gives it back on the total and the total's information
weight; 0.6 loses). **Promoted at 0.4** (`DEFAULT_NCAAF_EARLY_WEIGHT`,
`--ncaaf-early-weight`): the live runner prices the EPA half at 0.4 while
the week about to be played is week 4 or earlier.


## The phase round (2026-09-17) — NFL

Two of the audit's remaining NFL items, over the promoted chain (the
turnover shrink, the eight-week offseason gap, the bank rebuilt on that
core; 4,080 games):

- **The joint phase ridge** (#16): one design in place of the rejected
  two-fit split — the team columns stay the all-plays rating, and each
  team carries a pass-phase deviation on offense and on defense that fires
  on pass plays only, shrunk toward 0 at its own ridge
  (`fit_qb_ratings(phase_col="play_type", phase_lambda=)`), priced at the
  offense's pass rate like the passer is.
- **The phase scale, again** (#19's other half): on the gapped core the
  bank's margin slope reads 0.80 in weeks 1–3, 0.89 in 4–6, 1.04 in 7–10
  and 1.15 after; the scale fitted on the projected week's phase (through
  week 6, or after), for both terms and for the margin alone.

| variant | Brier | calib. | RMSE margin | RMSE total | info_w margin | info_w total |
|---|---|---|---|---|---|---|
| promoted chain | 0.2176 | 0.0141 | 13.229 | 13.510 | +0.067 | +0.082 |
| phase ridge, λ 300 | 0.2190 | 0.0160 | 13.313 | 13.545 | +0.017 | +0.054 |
| λ 1000 | 0.2181 | 0.0116 | 13.257 | 13.522 | +0.041 | +0.069 |
| λ 3000 | 0.2178 | 0.0134 | 13.238 | 13.514 | +0.056 | +0.077 |
| phase scale (margin and total) | 0.2175 | 0.0160 | 13.219 | 13.521 | +0.094 | +0.072 |
| phase scale, margin only | 0.2175 | 0.0168 | 13.219 | 13.510 | +0.094 | +0.082 |

**Readings:**

1. **The joint phase ridge loses at every ridge, monotonically.** The
   lighter the shrinkage the worse (λ 300: +0.08 on the margin, +0.03 on
   the total), and at λ 3000 it is the all-plays fit with noise added. A
   team's pass-phase deviation from its own rating is not stable enough
   to price a game with, which is what the two-fit split said in Round 1
   from the other direction. **Rejected**; the columns stay in
   `fit_qb_ratings` behind `phase_col` and the runner's
   `--nfl-phase-lambda` (default 0).
2. **The phase scale is a wash dressed as a margin gain.** Fitting the
   slopes on the projected week's phase takes 0.01 off the margin and
   puts it on the total; the margin alone takes the 0.01 and keeps the
   total, and pays 0.003 of calibration error for it — the early phase's
   slope is fitted on a third of the bank and the noise shows. The
   bank's slope pattern (0.80 early, 1.15 late) is real and the
   phase-fitted scale is still not the way to price it. **Not promoted**,
   as in the composites round; `phase_margin_only` stays on `scale_model`
   for the record.

The NFL chain, then, closes the day where the recency round left it:
Brier 0.2176, margin RMSE 13.23 (the close 12.97), total RMSE 13.51 (the
close 13.23).


## The drive round (2026-09-20) — points arriving in sevens and threes

The shipped sim draws a game's margin and total from a bivariate normal and
rounds them. Its own docstring has always named the weakness: that is "a
first-order treatment of the well-known mass at key numbers 3 and 7, [and] a
drive-level scoring sim is a later refinement". This is that refinement,
built and measured.

**The candidate** (`velocity/models/drive.py`): a team gets possessions, each
ends in a touchdown, a field goal or nothing, and the score is what that adds
up to. The extra point is its own coin, so scores land on the integer lattice
with nothing to round. Three things sit around the lattice, each earning its
place by an observable the structure alone gets wrong:

| mechanism | without it | the observable |
|---|---|---|
| overtime, 3 paired possessions | 4.3% of games end tied | the NFL's own rate is 0.33% |
| `pace_sd` — a shared scoring environment | margin and total equally dispersed | NFL residual sd is 13.01 / 13.54 |
| `strength_sd` — projection error | possessions must explain all uncertainty | NCAAF's 16.2 is wider than any lattice |

**Harness:** `scripts/sim_lab.py`, the same gate that promoted the dispersion
constants, with two variants and five columns added. `drive` is football's own
numbers with nothing fitted; `drive-fit` solves the two spread parameters on
the training seasons' residual moments. `key_*` are the errors in the mass at
each absolute margin — the half-point offset profile cannot see these, because
its offsets are measured from each game's own μ and never land on an integer.

### NFL — 3,044 games, 2015–2026, 8k sims × 3 seeds

| variant | ECE ↓ | Brier ↓ | key_mean ↓ | grid_mean ↓ | P(3) | P(7) | spread_mean ↓ | total_mean ↓ |
|---|---|---|---|---|---|---|---|---|
| normal (shipped) | 0.0576 | 0.2199 | 0.0325 | 0.0164 | 0.0542 | 0.0493 | **0.0246** | 0.0318 |
| empirical-hetero | 0.0629 | 0.2202 | 0.0321 | 0.0161 | 0.0577 | 0.0504 | 0.0237 | 0.0308 |
| drive | 0.0585 | 0.2199 | **0.0257** | **0.0128** | 0.0823 | 0.0885 | 0.0280 | **0.0300** |
| drive-fit | 0.0583 | 0.2199 | 0.0257 | 0.0128 | 0.0821 | 0.0884 | 0.0284 | 0.0300 |

*(actual: P(3) = 0.1478, P(7) = 0.0867)*

### NCAAF — 3,270 games, 2022–2026, 20k sims × 5 seeds

| variant | ECE ↓ | Brier ↓ | key_mean ↓ | grid_mean ↓ | P(3) | P(7) | spread_mean ↓ | total_mean ↓ |
|---|---|---|---|---|---|---|---|---|
| normal (shipped) | 0.0435 | 0.1901 | 0.0256 | 0.0139 | 0.0380 | 0.0363 | **0.0162** | 0.0221 |
| normal-hetero | 0.0429 | 0.1901 | 0.0255 | 0.0139 | 0.0382 | 0.0365 | 0.0165 | 0.0256 |
| drive | 0.0488 | 0.1910 | **0.0158** | 0.0101 | 0.0593 | 0.0680 | 0.0303 | 0.0361 |
| drive-fit | **0.0417** | **0.1900** | 0.0162 | **0.0094** | 0.0556 | 0.0643 | 0.0176 | **0.0200** |

*(actual: P(3) = 0.1061, P(7) = 0.0865)*

**Readings, honestly:**

1. **The structure is right, and it is not a fit.** Told only how many points
   each side expects, eleven possessions and a 0.65 field-goal mix imply an
   NFL margin sd of 13.5 against a measured 13.0 — half a point, with nothing
   tuned. The key-number mass comes out of the same structure: `grid_mean`,
   the error in the mass at every absolute margin, drops 22% in the NFL and
   32% in college. P(7) lands at 0.0885 against an actual 0.0867 where the
   normal manages 0.0493.

2. **Three is still badly short — 0.082 against 0.148 — and that is a
   finding, not a shortfall to tune away.** Independent possessions cannot
   produce football's three-point spike, because the spike is not made of
   independent possessions: it is made of a team down four kicking, a team up
   two playing for a field goal, an offense taking a knee on the 20. The
   lattice puts mass at 3 for structural reasons and real football puts
   *more* there for strategic ones. Halving the gap without modelling the
   endgame is the honest ceiling here, and this reaches it.

3. **NCAAF `drive-fit` beats the shipped sim on the gate.** Better ECE
   (0.0417 vs 0.0435), better Brier to the fourth decimal, 37% better key
   numbers, and a clean sweep of the totals profile (`total_mean` 0.0200 vs
   0.0221, `total_tail_max` 0.0247 vs 0.0279). It replicates at 20k sims over
   five seeds. It loses one column, `spread_mean`, by 0.0014.

4. **NFL `drive-fit` does not, and the reason is structural.** The NFL lattice
   is *already* wider than NFL football: 13.5 implied against a 13.0 residual
   sd, which leaves the fit no room — `strength_sd` can only widen, never
   narrow, so the fitted sim stays over-dispersed and the spread profile pays
   for it (0.0284 vs 0.0246). Read the sign: a model that certainly has
   projection error is nonetheless *less* uncertain than independent
   possessions would be. Real football compresses — clock management,
   score-aware play calling, teams that stop scoring once the game is
   decided — and eleven independent drives are worth about two possessions
   more variance than eleven real ones. College, with four points of genuine
   projection error to absorb, never hits that wall.

5. **The one that got caught.** An earlier `drive-fit` used the field-goal mix
   as its dispersion control — a coarser lattice is a wider team score, so
   solving it against the margin residuals is tempting and in the NFL it
   returned football-plausible ratios near 0.8. Against college, whose
   residual sd is wider than any lattice can reach, it ran to the bound and
   returned **a lattice with no field goals in it**: 24% of the mass on a
   margin of exactly 7 and 0.02% on a margin of 3. It matched the dispersion
   and destroyed the only thing the drive sim exists for. That is what forced
   the split into `strength_sd` and `pace_sd`, which is a better model for
   the reason it is a better model: each mechanism has its own signature
   (projection error widens the margin and leaves the total; the scoring
   environment does the reverse), so the two are separately identifiable from
   final scores alone, and neither touches the lattice.

### Promotion decision

**Neither is promoted, and nothing in the live slate changes.** NCAAF
`drive-fit` has earned a promotion *proposal*, not a promotion: rewiring the
league's pricing is a deliberate step that needs its own verification — every
derivative the ladder prices off the sim re-checked, not just the eight gate
columns — and it should not ride in on the back of a lab round. `velocity/
models/drive.py` is imported by `scripts/sim_lab.py` and by nothing else.

The NFL verdict is a genuine negative and worth keeping as one: the drive sim
is better at the thing it was built for and worse at the thing the slate
prices most.

**Next, in order of expected value:**

1. **The key-number overlay.** The two sims fail in opposite directions — the
   normal has the right spread profile and no key numbers, the drive sim the
   reverse. Re-weighting the normal's samples toward the drive sim's
   absolute-margin mass would take the lattice without the dispersion, and is
   testable on this same gate.
2. **Clock compression**, which is what reading 4 above says is missing: fewer
   effective possessions as the margin grows. It is the mechanism that would
   let the NFL lattice narrow, and it would move P(3) as well, since the
   endgame is where the three-point spike is made.
3. **The NCAAF promotion**, with the full derivative re-check.

## The lattice round (2026-09-20) — football's key numbers, measured and put back

The drive round left the two sims failing in opposite directions. The shipped
normal has the right dispersion and no key numbers: it puts 5.4% of NFL
margins on 3 where football puts 14.8%. The possession sampler has the key
numbers and, in the NFL, more dispersion than football has. This is the third
option — take the lattice from the data, leave the dispersion alone.

**The candidate** (`velocity/models/keynumbers.py`): one weight per absolute
margin, measured as how much more often football lands there than the sim
being corrected does, applied by resampling that sim's own draws. Fitted on
the training seasons and scored on the held-out ones, like every other
variant here. Two variants: `normal+keys` corrects the shipped sim,
`drive-fit+keys` corrects the possession sampler.

**Why the correction had to be global, which is the design decision worth
recording.** The obvious build is a local pull — margins landing near 3 get
snapped onto 3. One line of data rules it out. Against the shipped sim over
3,044 NFL games a margin of 3 is short by **9.4 points of probability**,
while 2 and 4 together are long by only **1.6**. The three-point spike is not
borrowed from its neighbours; it is drawn from the whole distribution,
including the margins football half-avoids — 9, 11, 12 and 15 all come in
near half the rate a normal gives them. A local kernel would have been
simpler, more explicable, and wrong.

**What it is, and what it is not.** A correction, not a model. The drive sim
explains where the mass at 7 comes from; this measures that football lands
there and puts it back. That matters for how a win here should be read, and
it matters more if the lattice ever moves — a correction fitted to a rule set
goes stale silently where a structural model adapts.

### NFL — 3,044 games, 2015–2026, 20k sims × 5 seeds

| variant | ECE | Brier | key_mean ↓ | grid_mean ↓ | P(3) | P(7) | spread_mean ↓ | total_mean ↓ |
|---|---|---|---|---|---|---|---|---|
| normal (shipped) | 0.0596 | 0.2199 | 0.0325 | 0.0164 | 0.0541 | 0.0493 | 0.0246 | 0.0317 |
| normal+keys | 0.0578 | 0.2199 | 0.0169 | 0.0101 | 0.1207 | 0.0807 | **0.0233** | 0.0315 |
| drive-fit | 0.0582 | 0.2199 | 0.0257 | 0.0128 | 0.0821 | 0.0884 | 0.0285 | 0.0301 |
| drive-fit+keys | 0.0576 | 0.2199 | **0.0162** | **0.0096** | 0.1299 | 0.0893 | 0.0244 | **0.0303** |

*(actual: P(3) = 0.1478, P(7) = 0.0867)*

### NCAAF — 3,270 games, 2022–2026, 20k sims × 5 seeds

| variant | ECE | Brier | key_mean ↓ | grid_mean ↓ | P(3) | P(7) | spread_mean ↓ | total_mean ↓ |
|---|---|---|---|---|---|---|---|---|
| normal (shipped) | 0.0435 | 0.1901 | 0.0256 | 0.0139 | 0.0380 | 0.0363 | 0.0162 | 0.0221 |
| normal+keys | 0.0428 | 0.1901 | 0.0080 | 0.0070 | 0.0908 | 0.0807 | **0.0159** | 0.0225 |
| drive-fit | 0.0417 | 0.1900 | 0.0162 | 0.0094 | 0.0556 | 0.0643 | 0.0176 | 0.0200 |
| drive-fit+keys | 0.0421 | 0.1900 | **0.0078** | **0.0065** | 0.0953 | 0.0866 | 0.0170 | **0.0197** |

*(actual: P(3) = 0.1061, P(7) = 0.0865)*

**Readings, honestly:**

1. **The key-number win is large, and it is the metric the overlay
   optimizes.** `key_mean` halves in the NFL and drops 69% in college. That
   is what the thing was built to do, fitted on other seasons, so it is a
   real out-of-sample result and not an impressive one on its own — it says
   the lattice is stable season to season, which the weight tables confirm
   directly (NFL `w(3)` sits between 2.29 and 2.46 across every training cut
   from 2019 on).
2. **The independent evidence is the spread offset profile, and it agrees.**
   That metric is measured at half-point offsets from each game's own μ and
   has nothing to do with the lattice fit, so it is free to disagree. It does
   not: `spread_mean` improves in both leagues, and season by season it
   improves in **10 of the NFL's 11 complete seasons** and 4 of college's 5.
3. **Do not claim the calibration or Brier numbers.** ECE moves by 0.002 and
   Brier not at all. Season by season the ECE difference is 6 of 12 in the
   NFL — noise, and the per-season table is why it is being called noise
   rather than a third win.
4. **The overlay is not cosmetic; it moves the rungs that matter.** On a
   3-point home favourite the push probability on exactly 3 goes from 3.1% to
   7.7%, and covering −7.5 drops 4.6 points. That is the key-number effect
   bettors buy half-points for, and the shipped sim was pricing it at about
   40% of its real size.
5. **The leagues have genuinely different lattices, which is a good sign the
   fit is measuring football rather than noise.** Six is a key number in the
   NFL (`w(6)` ≈ 1.29) and is not in college (`w(6)` ≈ 0.95), where 7 leans
   much harder instead (2.26 against the NFL's 1.68). Nobody told it that.
6. **`drive-fit+keys` is the best sim in the table, and the most expensive.**
   It is best on key numbers and totals in both leagues, and adding the
   lattice fixes the NFL spread deficit the drive round flagged (0.0285 →
   0.0244, now better than the shipped 0.0246). But it carries the whole
   possession sampler to get there, where `normal+keys` is a post-processing
   step on the sim already running.

### Promotion decision

**Still nothing is promoted, and the live slate is unchanged** — but
`normal+keys` is the strongest candidate the sim-shape work has produced, and
the remaining bar is explicit rather than vague. It changes no μ, no
dispersion and no model fit; it is a resample of draws the slate already
makes. What promotion needs before it happens is the derivative re-check the
drive round named: props, ladders, DFS and the correlation model all price
off these samples, and a change that moves the push probability on 3 by 4.6
points moves all of them. Eight gate columns are not that check.

**Next, in order of expected value:**

1. **Promote `normal+keys`**, with the full derivative re-check — the
   ladders and props repriced and compared, not just the game markets.
2. **Clock compression** (from the drive round), still the one mechanism that
   would let the NFL possession lattice narrow, and the one that would
   explain the three-point spike rather than measure it. The overlay closes
   the NFL gap at 3 from 0.094 to 0.027 and does not explain a point of it.
3. **A per-league `max_abs` and prior.** Both are currently one number for
   both leagues, chosen on the NFL and never swept.

## The derivative re-check (2026-09-20) — what else reads the sim

The lattice round won the sim-shape gate and stopped, because eight columns
scored on the game markets are not a licence to change a sim that props,
ladders, DFS and the correlation model all price off. This is that wider
check — `scripts/derivative_recheck.py`, every family scored against what
actually happened rather than against the shipped sim.

**It found something bigger than the thing it was checking, and that finding
has nothing to do with the overlay.**

### 1. The ladder gate is blocking rungs on an error the sim does not make

`velocity/eval/ladders.py` is not a consumer of the sim. It is a *hardcoded
correction for the sim's shape being wrong*: a banked table of per-offset
probability errors, used to refuse rungs the sim cannot price honestly. The
table compares the empirical tail past each offset against **a continuous
normal fitted to the residuals** — a stand-in for the sim.

The stand-in is wrong, in the expensive direction. The real sim **rounds**,
and so does football: 52% of NFL closing spreads are whole numbers, which
makes the residual at those games an integer, so the empirical tail past a
half-point offset is discrete. A rounded sim reproduces that; a continuous
normal cannot. Measured directly, the shipped sim's bias is about **0.010
smaller at every offset, on both tails**, than the table charges it.

| league / market | banked normal | shipped sim, measured | with the overlay |
|---|---|---|---|
| nfl spread | 41/58 sides open | **51/58** | 49/58 |
| nfl total | 49/58 | 52/58 | **54/58** |
| ncaaf spread | 58/58 | 58/58 | 58/58 |
| ncaaf total | 48/58 | **58/58** | 58/58 |

**Twenty ladder sides across the two leagues are refused because the gate is
measuring a sim that does not exist.** With a 0.02 tolerance, a systematic
0.010 overstatement is half the budget.

That rounding is the cause is not inferred, it is tested: re-run the same
measurement with `round_scores=False` and the error goes *up* (worst 0.0326 →
0.0387 on NFL spreads, 44 sides open → 35). The rounding is worth about nine
sides on its own.

This is **not** fixed here. Opening ladder rungs permits bets, which is the
dangerous direction, and doing it as a side effect of a re-check is exactly
the sort of change that should not ride in on another's PR. It is the top of
the backlog below.

### 2. Everything else the sim feeds, graded (three seeds each)

| | NFL shipped | NFL +keys | NCAAF shipped | NCAAF +keys |
|---|---|---|---|---|
| ladder rung calibration ↓ | 0.0249 | 0.0252 | 0.0150 | 0.0156 |
| ladder rung Brier ↓ | 0.1926 | **0.1922** | 0.2132 | **0.2130** |
| team totals calibration ↓ | 0.0313 | 0.0316 | 0.0191 | 0.0204 |
| same-game parlay calibration ↓ | 0.0151 | **0.0138** | 0.0119 | 0.0119 |
| parlay correlation error ↓ | 0.0110 | **0.0103** | 0.0106 | 0.0108 |
| **exact-margin log score** ↑ | −4.034 | **−3.964** | −4.259 | **−4.192** |
| **mass on the actual margin** ↑ | 0.0220 | **0.0277** | 0.0176 | **0.0223** |
| **modal margin hit rate** ↑ | 0.034 | **0.080** | 0.024 | **0.056** |

**Readings, honestly:**

1. **Nothing is damaged, and the joint least of all.** The same-game parlay
   columns were the real risk — a correction applied to the margin alone
   could leave both legs right and the pair wrong, and no other column would
   see it. The NFL joint calibration *improves* (0.0151 → 0.0138) and
   college's is flat. Resampling whole `(home, away)` pairs is why.
2. **The exact-score surfaces improve a lot, everywhere.** A quarter more
   probability mass on the margin that actually happened, in both leagues,
   on every seed. The modal margin — what the site's most-likely-score view
   shows — goes from right 3.4% of the time to 8.0% in the NFL, because the
   overlay's mode is 3 and football's is too.
3. **College pays a small, real cost that the NFL does not.** Ladder
   calibration is 0.0006 worse and team totals 0.0013 worse — about 7%
   relative — and unlike the NFL, where both columns flip sign across seeds
   and are therefore noise, college's are the same sign on all three. Not
   large, and not nothing; it is reported rather than averaged away with the
   NFL's.

### Verdict

The overlay survives the re-check. It is free in the NFL and costs college
about 7% of its ladder and team-total calibration for a 27% gain in the mass
it puts on the margin that happens. **`normal+keys` is cleared for promotion
on that evidence**, and the remaining objection is no longer technical.

**Next, in order of expected value:**

1. ~~**Fix the ladder gate's reference**~~ — **done**, see "the
   gate-reference round" below. The ~20 sides quoted here was measured
   without removing the level; matching it, as the old fitted normal did,
   gives 14 on the absolute bar. And it is not a pure loosening: on the
   gate's real price-scaled bar it opens 26 rungs and **closes 11** that the
   old stand-in was passing.
2. **Promote `normal+keys`**, now that the surface is measured.
3. **Clock compression** (from the drive round), still the one mechanism that
   would explain the three-point spike rather than measure it.

## The gate-reference round (2026-09-20) — measuring the sim instead of a stand-in

The derivative re-check found that `velocity/eval/ladders.py` gates the sim
against **a continuous normal fitted to the residuals**, not against the sim.
This fixes that: `residual_calibration` now takes the `SimConfig` being gated
and asks it directly, and `scripts/calibrate_ladders.py` regenerates the
banked `OFFSET_BIAS` literal from it.

**The level is removed before measuring**, as the fitted normal did by taking
the residual's own mean — `simulated_tails` shifts the sim by that mean. That
keeps this a shape gate and never a statement about the market close being a
tenth of a point off. It also means the honest headline is smaller than the
re-check's: that pass did not level-match and reported twenty sides, this one
reports fourteen on the absolute bar.

### It is not a loosening, which is the part worth reading

Under the gate's real bar — `min(tolerance, relative_tolerance × price)` —
the change moves **37 sides, 26 open and 11 closed**, for a net +15 of 232.

| league / market | old | new |
|---|---|---|
| nfl spread | 23/58 | **31/58** |
| nfl total | 26/58 | 25/58 |
| ncaaf spread | 36/58 | 32/58 |
| ncaaf total | 29/58 | **41/58** |
| **total** | 114/232 | **129/232** |

The eleven closures are almost all NCAAF spread under-tail rungs, and they
have a cause: the college sim's margin sd is 16.2 against a market-close
residual of 15.51, so it genuinely overstates the dog's deep tail by more
than the fitted stand-in suggested. **Those rungs were being passed on a
flattering proxy.** A fix that only ever opened rungs would have been a fix
to distrust.

### What the corrected measurement says about football

Two different defects, one per market, and the old reference had them
blurred together as "leptokurtosis":

1. **Spreads: the sim is too fat in the shoulders.** Real spread residuals
   are leptokurtic and close to symmetric (skew +0.10 NFL, +0.01 college), so
   a dispersion-matched sim overstates both tails at once. NFL spreads peak
   at 2.9 points of probability around 4.5 out — down from the stand-in's 3.7,
   and still past the two-point edge the slate bets on, so they stay refused
   near the line. NCAAF spreads peak at 1.7 and pass throughout.
2. **Totals: the sim is symmetric and football is not.** Total residuals are
   right-skewed in *both* leagues — **+0.33 NFL, +0.34 college** — because a
   game can run away upward and cannot run away downward. The sim is
   symmetric, so near the line it overstates the OVER tail and understates
   the under: at 4.5 out, NFL is (+0.028, −0.008) and NCAAF (+0.020, −0.020).

That second one is new, and it is a better description than what it replaces.
The old table said the *under* tail was the overstated one deep in both
leagues (+0.0105 at 20.5 in college). Measured against the sim, college's
deep tails are both inside a cent and the defect is a near-the-line skew
instead. The old NCAAF-totals deep-tail blow-up — error rising from 0.0080 at
15.5 to 0.0142 at 25.5 — was the stand-in's, not the sim's: it now falls,
0.0073 to 0.0062.

**A fatter-tailed draw would not fix the totals.** Skew is not kurtosis. That
is worth knowing before anyone reaches for the empirical residual pool again.

### Cost and hygiene

Regeneration is 21s and deterministic (a fixed seed inside `simulated_tails`,
8,000 draws a game, Monte Carlo error under 1e-4 against a 5e-4 freshness
tolerance), so `--write` is a no-op on a second run and the refresh workflow
keeps working. The freshness test now pulls its `SimConfig` from the
generator rather than restating one, so a league whose promoted sd moves
fails that test until the table is regenerated — which is the point, since
the table is a statement about a particular sim.

**Next, in order of expected value:**

1. **Promote `normal+keys`** — cleared by the derivative re-check, and now
   sitting behind a gate that measures the sim it will change.
2. **A skew-aware draw for totals.** The defect is now named and measured and
   nothing in the sim can express it; the count sim and the drive sim both
   can, which makes this the first concrete use for either on totals.
3. **Clock compression** (from the drive round).

## The skew round (2026-09-20) — the shape is right, the level is louder

The gate-reference round named a defect the old ladder table had blurred into
"leptokurtosis": **total residuals are right-skewed and the sim is
symmetric.** A game can run away upward and cannot run away downward, and
both leagues say so — skew +0.33 (NFL) and +0.34 (college) against the market
close, where the spreads are +0.10 and +0.01. This builds the draw that can
express it and measures what it is worth.

**The candidate** (`velocity/models/skew.py`): a sinh-arcsinh transform at
δ=1, where it collapses to `Y = Z·cosh(ε) + √(1+Z²)·sinh(ε)` — no special
functions, monotone in the draw it was given (so a jointly-drawn margin and
total stay paired), and with closed-form first three moments, so it is
standardized exactly rather than by the sample. `total_skew` on `SimConfig`
re-shapes the total's draw and nothing else; the margin is untouched, and a
residual pool is not skewed twice because it already carries the league's own
shape.

**The skew itself is solid.** It is stable across every training cut in both
leagues and around both reference points: +0.27 to +0.33 in the NFL and +0.33
to +0.36 in college, measured around the model's own μ, against a sampling
error of ±0.04. This is a real property of football, not a fitted wobble.

### Measured with the level removed, it is a large win

The ladder gate (`velocity/eval/ladders.py`) shifts the sim by the empirical
residual's mean before measuring, so it is a statement about shape alone.
There the skew roughly halves the totals error and opens every remaining
totals rung in both leagues:

| league | reference | worst | mean | sides open |
|---|---|---|---|---|
| nfl total | symmetric (shipped) | 0.0293 | 0.0163 | 49/58 |
| nfl total | **skew-aware** | **0.0133** | **0.0078** | **58/58** |
| ncaaf total | symmetric (shipped) | 0.0255 | 0.0115 | 53/58 |
| ncaaf total | **skew-aware** | **0.0141** | **0.0083** | **58/58** |

At 4.5 points out the NFL's `(over, under)` bias goes from `(+0.028, −0.008)`
to `(+0.007, +0.013)` and college's from `(+0.020, −0.020)` to
`(−0.004, +0.004)`. The sign of the miss the gate-reference round identified
is gone.

### Measured around the model's own μ, the answer flips with the league

`scripts/sim_lab.py` does **not** remove the level, and there the same
parameter gives opposite verdicts:

| | NFL | NCAAF |
|---|---|---|
| total_shoulder_max, symmetric | 0.0618 | 0.0406 |
| total_shoulder_max, **+skew** | **0.0655** (worse) | **0.0286** (better) |
| total_mean, symmetric | 0.0318 | 0.0221 |
| total_mean, **+skew** | 0.0314 | **0.0189** |

That is not the skew disagreeing with itself. **It is each league's totals
level wandering, and the wander is bigger than the shape.** The model's
`resid_total` mean is +0.57 in the NFL and **−0.53 in college — opposite
signs** — and per season the NFL's runs from −1.6 to +3.3. Right skew moves a
distribution's median left at fixed mean, so it compounds a positive level
error and offsets a negative one, which is exactly the pattern above.

Correcting the level with the training seasons' own mean does not rescue it,
because the level is not stably estimable: fitted at +0.279 on NFL training,
the test window came in at +0.674. Level-plus-skew improves the NFL's mean
error 0.0143 → 0.0100 and its deep tail 0.0234 → 0.0104 while still costing
the shoulder, 0.0205 → 0.0230.

**Readings, honestly:**

1. **The shape claim is established and the live claim is not.** The skew is
   the right description of football totals and, with the level held equal,
   it halves the error the ladder gate measures. Around the model's actual
   projections its effect is dominated by a level that moves ±1–3 points a
   season with no stable sign.
2. **Two gates disagreeing was the finding.** The ladder gate alone would
   have read as an unambiguous win and been promoted on that evidence. The
   only reason it did not is that `sim_lab` measures around a different
   centre, and the contradiction is what exposed the level.
3. **A fatter-tailed draw still would not fix this**, and now neither does a
   correctly-skewed one, for a reason that has nothing to do with either: on
   totals the model's aim wanders more than its shape is wrong.

### Promotion decision

**Not promoted.** Shipping it would open 14 totals ladder sides — the gate
blocks them for a shape error this genuinely fixes — at the cost of the
shoulder in whichever league's level happens to wander positive. That is a
real trade rather than a win, and it is not one to take silently.

`velocity/models/skew.py` is imported by `velocity/models/simulate.py` behind
a `total_skew` that defaults to zero, so the shipped sim is bit-identical
to what it was.

**Next, in order of expected value:**

1. **The totals level.** It is now the largest single error on totals and
   nothing in the lab has aimed at it. Per-season means of −1.6 to +3.3 in
   the NFL are not noise around a fixed number the way a shape error is —
   they look like a missing covariate (pace? weather? era scoring?), and
   that is a modelling question rather than a sim question.
2. **Promote `normal+keys`**, still cleared and still unpromoted.
3. **Re-test the skew once the level is addressed.** With the level right,
   the ladder-gate result says it is worth 14 rungs.

## The promotion round (2026-09-20) — `normal+keys` into the live sim, NFL only

Four rounds cleared the lattice and none shipped it; this one does. The
overlay the gate scored (`scripts/sim_lab.py`'s `normal+keys`) is now a
field on the sim itself — `SimConfig.lattice`, applied at the end of
`simulate_game` under the same generator as the draw, so the promoted path is
the gate's overlay draw for draw (tested) — read from a banked table,
`datasets/{league}/lattice.parquet`, that `scripts/build_lattice.py` fits off
the league's residual bank exactly as the gate and the derivative re-check
fitted theirs. Like the residual pool it is data rather than a knob: it takes
no part in config comparisons, a league without a bank simulates without a
correction and says so, and a residual bank rebuilt without its lattice fails
a freshness test rather than shipping a stale one. The switch is `--sim-keys
{none,lattice}` on the live slate.

### The banks

Fitted on every banked walk-forward game against the rounded normal at the
promoted sd, thirty pseudo-games a bin.

| | NFL (4,080 games, 2011–2026, σ 13) | NCAAF (8,360 games, 2015–2026, σ 16.2) |
|---|---|---|
| 3 | 0.144 vs 0.055 (**×2.44**) | 0.099 vs 0.041 (**×2.30**) |
| 7 | 0.087 vs 0.050 (×1.66) | 0.086 vs 0.039 (×2.11) |
| 10 | 0.050 vs 0.044 (×1.12) | 0.046 vs 0.036 (×1.25) |
| 14 | 0.050 vs 0.035 (×1.37) | 0.044 vs 0.032 (×1.36) |
| 0 | 0.003 vs 0.028 (×0.30) | 0.000 vs 0.021 (×0.15) |
| 9 / 12 | ×0.42 / ×0.54 | ×0.40 / ×0.43 |
| 21+ (the tail bin) | 17% of games, ×1.13 | **35% of games, ×1.20** |

The NFL table is the one the lattice round's tests were built from, a little
sharper for the extra seasons. The college one is the same key numbers with a
difference that matters below: a third of its games sit in the tail bin,
and that bin carries a weight of 1.2.

### What the promotion changes, measured

The ladder table (`velocity/eval/ladders.py`, regenerated by
`scripts/calibrate_ladders.py --write`) is a statement about a particular
sim, and this changes the sim, so it is the measurement that decides. Sides
are the gate's own unit — a rung passes when the sim does not overstate that
side by more than 0.02 — and the shoulder is the worst error at or inside
13.5.

| league / market | before: sides open · worst · shoulder | with the lattice | opened / closed |
|---|---|---|---|
| **nfl spread** | 50/58 · 0.0292 · 0.0292 | **58/58 · 0.0192 · 0.0192** | **+8 / 0** |
| nfl total | 49/58 · 0.0293 · 0.0293 | 49/58 · 0.0297 · 0.0297 | 0 / 0 |
| ncaaf spread | 58/58 · 0.0168 · 0.0168 | 53/58 · 0.0214 · 0.0214 | 0 / **5** |
| ncaaf total | 55/58 · 0.0255 · 0.0255 | 52/58 · 0.0264 · 0.0264 | 0 / **3** |

NFL spreads, the shoulder the gate has refused since it was built, offset by
offset (over / under bias, before → after): 0.5 out +0.022/+0.002 →
+0.010/−0.001; 2.5 +0.022/+0.005 → +0.014/+0.004; 4.5 +0.029/+0.009 →
+0.019/+0.004; 6.5 +0.025/+0.015 → +0.018/+0.009; 9.5 +0.015/+0.013 →
+0.013/+0.008. Both tails fall at every offset through 11.5. This is the
finding the lattice round predicted and could not measure: the shoulder
error was never dispersion, it was the normal putting mass on 9, 11, 12 and
15 that football puts on 3, 7 and 14, and once that mass is moved the tails
past 4.5 are thinner on both sides.

**College is the opposite, and the mechanism is visible in the table.** Its
over-bias *rises* by about 0.011 at every offset while its under-bias falls
by 0.005 — a shift, not a reshaping. The resample is symmetric in |margin|,
but a college favourite's sim is not symmetric around zero, and the tail
bin's 1.2 multiplies more mass on the favourite's blowout side than the
dog's. Fitted around the model's own projections that weight is right (the
residuals are leptokurtic and the tail really is heavier than a normal at
16.2); applied at the market's sharper numbers, as the gate measures, it
overstates blowouts by exactly the amount that closes five spread sides.
The three totals sides close by parity — resampling toward odd margins
moves totals toward odd numbers, and college's totals table was already
within 0.001 of the bar at 2.5, 4.5 and 6.5.

The derivative re-check saw this and called it "a small, real cost that the
NFL does not pay" (ladder calibration 0.0150 → 0.0156, team totals 0.0191 →
0.0204, same sign on every seed). Measured on the gate that actually
decides which rungs are bet, the cost is eight sides.

### Verdict

**Promoted in the NFL. Not promoted in college.** The two-gate rule: the
sim-shape gate and the ladder gate agree in the NFL (key numbers halved,
spread profile better, every spread side open) and disagree in college (key
numbers cut 69%, five spread and three totals sides closed), and a shape
change ships only where they agree.

- `DEFAULT_SIM_KEYS_BY_LEAGUE = {"nfl": "lattice", "ncaaf": "none"}`.
  Both banks are committed; `--sim-keys lattice` switches college on.
- The ladder table is regenerated with the NFL lattice in `SIMS["nfl"]`
  and without one for college, so the freshness test pins each league to
  the sim it runs.
- Four ladder tests that pinned "NFL spreads fail the gate where NCAAF
  spreads pass" now pin the reverse finding, with the totals shoulder as
  the case that still shows the asymmetry the signed table exists for.

What the NFL promotion does to the slate: every spread ladder rung is now
priced by a sim that lands on 3 and 7 as often as football does, and
eight rungs in the shoulders that were refused for a shape error are
bettable at the same 0.02 bar. The moneyline and main-line pricing moves
by what the lattice round measured — push probabilities on 3 and 7 at
roughly their real size, μ and sd untouched.

**Next, in order of expected value:**

1. **A per-league tail for college.** The college table's cost is the tail
   bin, not the key numbers: `max_abs` and the prior are one number for
   both leagues, chosen on the NFL and never swept. A college table with
   the tail bin at 28 or 35, or with its weight capped at 1, would keep the
   key numbers and lose the blowout shift; the ladder table decides.
2. **The totals level**, still the largest single error on totals.
3. **Re-test the skew once the level is addressed.**

## The juice round (2026-09-20) — the real close as the anchor, and the records at the real price

Two things the audit (docs/PROJECTION_AUDIT.md §7.5 #2) said the lab was
getting wrong by construction, both answerable on the banked promoted
ledger (`datasets/nfl/projections_promoted.parquet`, 2015–2026) without a
walk-forward run.

### 1. The anchor: spread-probit vs the de-vigged moneyline

The market-blend sweep chose the live slate's anchoring weight against
`Φ(spread/13.45)`, a stand-in the MLB sweep had called structurally invalid
for a fixed run line. The NFL frame carries a real moneyline close on every
game, so `market_blend_sweep(..., market="moneyline")` now sweeps against it
de-vigged multiplicatively — the same number `close_brier` already grades
the model by — and `scripts/model_lab.py` prints both.

| market leg | pure-market Brier, holdout 2020+ | select-chosen w (≤2019) | holdout at that w | pure model |
|---|---|---|---|---|
| spread probit | 0.2109 | 0.1 | 0.2112 | 0.2207 |
| **moneyline close** | **0.2102** | **0.2** | 0.2111 | 0.2207 |

On the same 3,043 games the two legs correlate at 0.994 and differ by 0.023
of probability on average; the moneyline is the better forecast by 0.0004
of Brier, and the place it earns it is the ends: in games it prices under
30% for the home side it says 0.234 where the probit says 0.263 and the
home side wins 0.201; over 80% it says 0.852 against the probit's 0.832 and
0.876 happens. The probit's constant σ is too wide at the extremes, which
is a known property of a probit on a point spread, not a defect the lab
introduced.

**Verdict: the probit was not invalid in the NFL, and the anchor does not
move.** The select window is flat from 0.1 to 0.3 on either leg (0.2150 ±
0.0001), the moneyline leg's choice is 0.2, and 0.2 is what the live slate
runs. Both sweeps stay in the output so the next model that moves the
choice is judged against the real close.

### 2. The records at the real juice

`disagreement_sweep` and `ats_ou_vs_close` now settle every bet at the
side's own closing price (`home_spread_odds` / `away_spread_odds`,
`over_odds` / `under_odds`, complete on the NFL frame) and report `units`
per unit staked beside the flat win rate; the comparison table carries
`ats_units` / `ou_units`.

| market · disagreement ≥ | bets | win rate | units / bet |
|---|---|---|---|
| spread · 0 | 2,763 | 48.5% | −0.059 |
| spread · 3 | 873 | 49.6% | −0.042 |
| spread · 6 | 114 | 54.4% | +0.052 |
| total · 0 | 2,792 | 49.7% | −0.037 |
| total · 3 | 943 | 51.5% | −0.002 |
| **total · 4** | **505** | **54.3%** | **+0.052** |
| total · 6 | 137 | 62.0% | +0.204 |

The 4-point totals bar the NFL slate runs (`DEFAULT_TOTAL_EDGE_BY_LEAGUE`)
was chosen on the flat win rate; at the real price it is +5.2 units per 100
staked over 505 bets, and the record above it is monotone in the
disagreement on both columns. Spreads are what they have always been below
6 points of disagreement — a loser at the juice, which the flat rate at
52.4% break-even was already saying.

**Nothing promoted; two measurements corrected.** The next reader of the
sweep sees the real close, and the next threshold chosen on a record is
chosen on what it pays.

## The level round (2026-09-20) — the totals level on trailing weeks

The skew round's verdict named the totals level as the largest single error
on totals: per-season means of −1.6 to +3.3 in the NFL that no shape can
express. The promoted chain fits the level on the trailing two seasons
(`velocity.models.level.calibrate_level(seasons=2)`); this round fits it on
trailing **on-field weeks** instead (`trailing_weeks`, across the season
boundary), so it can move inside a season. Full promoted chains, the
standard walk-forward, 4,000 sims.

### NCAAF (3,220 games, 2022–2026) — not promoted

| variant | Brier | calib. | O/U record | rmse_total | info_w_total | mean \|level\| by season |
|---|---|---|---|---|---|---|
| promoted (two seasons) | 0.1908 | 0.0231 | 52.2% (3,085) | 16.111 | +0.017 | 1.04 |
| lvlw6 | 0.1908 | 0.0226 | 51.8% | 16.159 | −0.025 | 1.09 |
| lvlw12 | 0.1908 | **0.0216** | 52.2% | 16.116 | +0.012 | **0.91** |
| lvlw24 | 0.1908 | 0.0235 | 52.1% | 16.115 | +0.014 | 1.00 |

The twelve-week window is better calibrated and wanders less season to
season, and it is worse on the number the totals stake on: `info_w_total`
falls from +0.017 to +0.012 and `rmse_total` does not move. What the
per-season table shows is that the college error is not a window problem
at all: 2025 (−1.0) and 2026 (−1.8 to −2.6) are over-projected under every
window, six and twenty-four weeks alike. The college level is drifting
with the game (the 2023 clock rules, and whatever 2025 is), and a window
that follows the last twelve weeks follows it no better than one that
follows the last two seasons.

### NFL (3,045 games, 2015–2026) — the bare window wins and pays in September

| variant | Brier | calib. | O/U record | rmse_total | info_w_total | mean \|level\| by season |
|---|---|---|---|---|---|---|
| promoted (two seasons) | 0.2197 | 0.0170 | 49.9% (2,802) | 13.552 | −0.018 | 1.44 |
| **lvlw8** | 0.2198 | 0.0176 | 49.8% | **13.521** | **+0.011** | **1.01** |
| lvlw12 | 0.2197 | 0.0179 | 49.6% | 13.525 | −0.000 | 1.06 |
| lvlw17 | 0.2197 | 0.0178 | 50.0% | 13.533 | −0.017 | 1.14 |
| lvlw34 | 0.2197 | 0.0175 | 50.4% | 13.554 | −0.016 | 1.39 |

Eight weeks is the window: 0.03 of totals RMSE, `info_w_total` from −0.018
to +0.011 (the model's totals now add to the close rather than subtract),
and the season-to-season wander cut by a third — 2018 from +2.0 to +0.4,
2020 from +3.3 to +1.1, 2024 from +2.4 to +1.5. Margins are untouched
(rmse_margin identical to the fourth decimal; the level shifts both teams'
points alike) and the moneyline columns move by noise.

**Where it pays for that.** By week of the season, the totals residual
(actual − projected, 2015+):

| weeks | promoted | lvlw8 |
|---|---|---|
| 1–3 | +0.37 | **+0.89** |
| 4–6 | +1.37 | +1.41 |
| 7–10 | +0.74 | **+0.40** |
| 11–14 | −0.06 | −0.19 |
| 15–18 | +0.29 | +0.47 |

An eight-week window at week 1 is the previous season's last eight weeks —
December football, played in the cold at the lowest scoring level of the
year — and it carries that level into September, where the games are
warm and the scoring is not. The window crossing the boundary is exactly
what makes it a window rather than a season fit, and exactly where it is
wrong. (2026's sixteen games read +4.8 under it for the same reason.)

### Part two, under test

`level_shift` now takes `within_season` (the window stops at the latest
played season's first week) and `shrink_games` (the trailing-week level
blended toward the two-season level by games: the window's level counts
for its own games, the season level for `shrink_games` more, so an empty or
one-week window is the season level and a full one is mostly its own).
Three variants over the promoted chain — `lvlw8s-k64`, `lvlw8s-k128`
(within the season, shrunk) and `lvlw8-k128` (crossing, shrunk) — are
running; the verdict goes below when it lands, and the skew re-test waits
on it.

## The tail round (2026-09-20) — the college lattice's cost was its tail bin

The promotion round shipped the lattice in the NFL and held it back in
college, where regenerating the ladder table with it closed eight sides
through a shift the table made visible: college's over-bias rose by 0.011
at every offset while its under-bias fell. The round's first next step was
a per-league tail. This is that sweep, run the way the gate decides —
`residual_calibration` on the committed games at the market's numbers, 4,000
sims — over the tail bin's treatment with the key numbers held exactly as
banked (×2.30 at 3 and ×2.11 at 7 in college, ×2.44 and ×1.66 in the NFL).

| table | NCAAF spread: open · worst | NCAAF total | NFL spread | NFL total |
|---|---|---|---|---|
| no lattice | 58/58 · 0.0169 | 55/58 · 0.0254 | 50/58 · 0.0292 | 49/58 · 0.0292 |
| banked (tail ×1.20 / ×1.13) | 53/58 · 0.0214 | 51/58 · 0.0264 | 58/58 · 0.0192 | 49/58 · 0.0297 |
| **tail held at 1** | **58/58 · 0.0151** | 52/58 · 0.0260 | **58/58 · 0.0136** | 50/58 · 0.0294 |
| max_abs 28, tail held at 1 | 58/58 · 0.0151 | 52/58 · 0.0259 | 58/58 · 0.0140 | 49/58 · 0.0296 |
| max_abs 35, tail held at 1 | 58/58 · 0.0157 | 53/58 · 0.0257 | — | — |
| max_abs 28, tail ×1.35 | 40/58 · 0.0261 | 51/58 · 0.0274 | — | — |
| prior 100 | 56/58 · 0.0205 | 52/58 · 0.0263 | — | — |

**Readings:**

1. **It was the tail bin, and only the tail bin.** Holding the last bin at 1
   and changing nothing else takes college spreads from five sides closed to
   every side open with a worst error *below* the no-lattice table's
   (0.0151 against 0.0169), and it improves the NFL too: the shoulder the
   promotion round brought from 0.029 to 0.019 goes to 0.014. Widening the
   table to 28 or 35 bins with the tail still corrected makes college
   worse (40/58 at 28), because the tail's ratio grows with the distance it
   covers; widening it with the tail held at 1 changes nothing the twenty-two
   bins did not already do. A bigger prior only dilutes the key numbers.
2. **Why: the tail's ratio is dispersion, not lattice.** Football's
   residuals are leptokurtic, so past 21 the game lands more often than a
   normal at the promoted σ says — ×1.13 over 17% of NFL games, ×1.20 over
   35% of college games. Around the model's own projections that ratio is
   right, and it is still not a key-number correction: it is the tail of a
   σ that could be re-fitted. Applied at the market's sharper numbers, as
   the gate measures and the slate prices, it overstates the favourite's
   blowouts by exactly the amount that closed the college sides.
3. **Totals move by parity and by noise.** Resampling toward odd margins
   moves totals toward odd numbers; the college totals shoulder sits within
   0.0006 of the bar at 2.5, 4.5 and 6.5 and lands on either side of it at
   4,000 sims. The regenerated table below is the 8,000-sim answer.

### Verdict

**The tail bin is left at 1 by default** (`fit_lattice_weights(...,
correct_tail=False)`, `scripts/build_lattice.py --correct-tail` to put it
back), both banks are rebuilt that way, and **college is switched on**
(`DEFAULT_SIM_KEYS_BY_LEAGUE = {"nfl": "lattice", "ncaaf": "lattice"}`).
The lattice corrects the lattice and leaves the dispersion to σ. The
sim-shape gate's `normal+keys` variant follows the same default, so the
gate measures what ships.

### The regenerated table (8,000 sims, `scripts/calibrate_ladders.py --write`)

| league / market | no lattice | promotion round (NFL, tail corrected) | **tail round (both, tail at 1)** |
|---|---|---|---|
| nfl spread | 50/58 · 0.0292 | 58/58 · 0.0192 | **58/58 · 0.0136** |
| nfl total | 49/58 · 0.0293 | 49/58 · 0.0297 | 49/58 · 0.0296 |
| ncaaf spread | 58/58 · 0.0168 | 58/58 · 0.0168 (no lattice) | **58/58 · 0.0150** |
| ncaaf total | 55/58 · 0.0255 | 55/58 · 0.0255 (no lattice) | 52/58 · 0.0259 |

The one cost, stated: college's totals shoulder sat on the bar before the
lattice (the over side at 2.5, 4.5 and 6.5 read 0.0198–0.0200 against a
0.02 tolerance) and the parity shift puts it a few ten-thousandths over
(0.0202–0.0205), so three totals sides close. That is the bar being where
that shape's error is, not a shape the lattice made worse; the sim-shape
gate's totals columns for college were flat under the overlay (0.0221 →
0.0225), and its key-number error fell 69%. Taken.

## The situational round, part two (2026-09-20) — surface and body clock

Two of nfelo's home-field findings that the schedule columns can price
(docs/PROJECTION_AUDIT.md §7): a team a point worse on a surface unlike its
own home's (`SurfaceMismatchModel`, grass vs turf from the games frame's
surface strings, the away side's home surface taken as its season mode),
and a Pacific-time team two points worse at an Eastern early kickoff
(`BodyClockModel`, gametime hour ≤ 13). Over the full promoted chain, the
standard NFL walk-forward (3,045 games, 2015–2026), 4,000 sims.

| variant | Brier | calib. | rmse_margin | info_w_margin | games moved | margin residual on them, before → after |
|---|---|---|---|---|---|---|
| promoted | 0.2197 | 0.0170 | 13.008 | 0.034 | — | — |
| surf0.5 | 0.2198 | 0.0225 | 13.012 | 0.042 | 1,538 | |
| surf1.0 | 0.2202 | 0.0293 | 13.026 | 0.050 | 1,538 | **+0.02 → −0.98** |
| clock1.0 | 0.2199 | 0.0180 | 13.010 | 0.036 | 253 | |
| clock2.0 | 0.2202 | 0.0198 | 13.019 | 0.037 | 253 | **+0.07 → −1.93** |
| surf1.0-clock2.0 | 0.2208 | 0.0336 | 13.044 | 0.052 | | |

**Not promoted, and the last column says why there was nothing to promote.**
On the games each wrapper moves, the promoted chain's margin residual is
already centred — +0.02 across the 1,538 surface mismatches, +0.07 across
the 253 early Pacific kickoffs — so a one-point surface term and a
two-point body-clock term each install a bias of exactly their own size,
and every column pays for it: Brier, calibration, margin RMSE, all worse in
proportion to the points added. (`info_w_margin` rises because the
residual now carries something the close does not; that is the weight
measuring a new error, not new information.) nfelo's findings were made on
nfelo's residuals; a ridge fitted on plays with a fitted home edge, an
announced-starter QB term and a rest wrapper has nothing left on these two
columns. Neither wrapper is worth a smaller size either — the bias to
correct is a fiftieth of a point.

Both wrappers stay in `velocity/backtest/lab.py` as variants and are
applied nowhere; the live slate is unchanged.

## The SP+ blend round (2026-09-20) — success rate and explosiveness beside EPA

SP+ decomposes a team into efficiency (success rate) and explosiveness (EPA
on successful plays), and the audit's first item was that the plays frame
has carried a `success` column since Round 1 without a variant reading it.
`blend_team_components` blends the QB fit's team sides toward a
success-rate ridge (converted to EPA units by the plays' own EPA-per-success
gap, 1.96) and toward a ridge on successful plays only; the QB term, the
starters and the pass rate are untouched. Over the full promoted chain,
3,045 games, 2015–2026, 4,000 sims.

| variant | Brier | log loss | calib. | rmse_margin | info_w_margin | rmse_total | info_w_total | ATS (flat) |
|---|---|---|---|---|---|---|---|---|
| promoted | 0.2197 | 0.6290 | 0.0170 | 13.008 | 0.034 | 13.552 | −0.018 | 48.5% |
| succ0.25 | **0.2193** | **0.6281** | **0.0148** | **13.005** | 0.049 | 13.552 | −0.024 | 49.0% |
| succ0.5 | 0.2194 | 0.6283 | 0.0152 | 13.025 | 0.060 | 13.559 | −0.029 | 49.6% |
| expl0.25 | 0.2213 | 0.6325 | 0.0187 | 13.064 | −0.016 | **13.529** | **+0.015** | 49.5% |
| succ0.25-expl0.25 | 0.2209 | 0.6316 | 0.0207 | 13.061 | −0.003 | 13.528 | +0.011 | 50.2% |

The table reads like a small win for a quarter of success rate. The paired
test does not:

| vs promoted | ΔBrier per game | seasons better (of 12) | Δ squared margin error | seasons better | spread ≥4: record · units |
|---|---|---|---|---|---|
| succ0.25 | −0.00040 ± 0.00026 (t −1.5) | 6 | −0.06 ± 0.25 | 6 | 50.8% · −0.020 (vs 47.2% · −0.087) |
| succ0.5 | −0.00032 ± 0.00052 (t −0.6) | 6 | +0.45 ± 0.50 | 6 | 52.5% · +0.015 |
| expl0.25 | **+0.00157 ± 0.00052 (t +3.0)** | 4 | **+1.46 ± 0.53** | **2** | — |

**Readings:**

1. **Success rate is a coin flip beside EPA.** A quarter of it moves the
   projected margin by 0.43 points a game on average and improves the
   Brier in six seasons of twelve, the margin error in six of twelve, with
   the paired difference a standard error and a half from zero. The
   spread record at ≥4 points of disagreement improves (47.2% → 50.8%,
   465 bets) and at ≥6 worsens (54.4% → 52.7%, 131 bets); both are inside
   the noise of a hundred-odd bets. Nothing here is the consistent
   per-season sign the promoted rounds had.
2. **Explosiveness hurts the margin, clearly.** Three standard errors on
   the Brier, worse margin error in ten seasons of twelve. A ridge on
   successful plays only rates the offense that had its big plays go
   in, and big plays are the least repeatable part of EPA — the same
   reason the turnover shrink won. Its one gain is on totals (rmse_total
   −0.024, `info_w_total` from −0.018 to +0.015), which says a team's
   explosiveness carries information about how many points a game will
   have that its margin rating does not; that is a totals question and
   the level rounds are where it belongs.
3. **Why EPA already has it.** Success rate is a coarsening of EPA — a
   play succeeds when its EPA is positive-enough — and a ridge on EPA over
   four seasons of plays is not short of information about which teams
   sustain drives. SP+ needs the decomposition because it is built on
   drive-level and game-level inputs; a play-level ridge does not.

**Not promoted.** `blend_team_components` and `epa_per_success` stay in
`velocity/features/team.py`, the variants in the lab. The audit's first
item is answered rather than adopted.

### Part two — the shrunk window, measured (promoted)

Three variants over the promoted chain, same walk-forward, 3,045 games.
`k` is the shrink in games: the window's level counts for its own games,
the two-season level for `k` more.

| variant | rmse_total | info_w_total | O/U (flat) · units | mean \|level\| by season | weeks 1–3 | Δ sq. total error vs promoted | seasons better |
|---|---|---|---|---|---|---|---|
| promoted (two seasons) | 13.552 | −0.018 | 49.9% · −0.034 | 1.44 | +0.37 | — | — |
| lvlw8 (bare) | 13.521 | +0.011 | 49.8% | 1.01 | +0.89 | −0.91 ± 0.78 | 5/12 |
| lvlw8s-k64 (within season) | **13.513** | −0.021 | 50.0% · −0.032 | 1.02 | +0.57 | −1.15 ± 0.51 | 6/12 |
| lvlw8s-k128 (within season) | 13.518 | −0.021 | 50.2% · −0.029 | 1.12 | +0.52 | −0.99 ± 0.39 | 6/12 |
| **lvlw8-k128** (crossing, shrunk) | 13.517 | −0.009 | 50.3% · −0.026 | 1.16 | +0.56 | **−1.10 ± 0.38** | **8/12** |

Margins and the moneyline columns are identical across the row (the level
shifts both teams alike). The totals record at the slate's own 4-point bar:
53.3% · +0.034 per unit (523 bets) on the two-season level, **53.8% ·
+0.043 (457 bets)** on the shrunk window.

**Readings:**

1. **The shrink does what it was for.** September's over-projection under
   the bare window (+0.89) comes back to +0.56, and the window keeps its
   gains where the drift is: 2018 from +2.0 to +1.3, 2020 from +3.3 to
   +2.3, 2021 from −1.6 to −0.7, 2022 from −0.6 to −0.2. Three of the four
   variants improve the squared total error by more than two standard
   errors; the bare window is the only one whose paired difference is
   inside one.
2. **Crossing the boundary, shrunk, beats stopping at it.** The
   within-season windows are empty at week 1 and one week deep at week 2,
   so through October they are mostly the two-season level with the
   window's noise on top; the crossing window always holds 128 games and
   the shrink halves December's pull rather than deferring it. Eight
   seasons of twelve better, the most consistent of the four, and the
   best `info_w_total` of the shrunk rows.
3. **2026 is sixteen games and stays a level problem** (+3.9 either way):
   nothing fitted on 2025 knows what September 2026 is scoring, and the
   window will know by week 9.

### Promotion decision

**Promoted: `lvlw8-k128`** — the trailing eight on-field weeks across the
season boundary, blended toward the trailing two seasons by 128 games.
`NFL_LEVEL_WEEKS = 8`, `NFL_LEVEL_SHRINK_GAMES = 128.0` in the live runner
beside `NFL_LEVEL_SEASONS = 2`; `promoted()` in the lab defaults to the
same, with `live-nfl-promoted-level2s` keeping the previous chain for the
record. The residual bank and the promoted ledger are rebuilt from the new
chain (the scale reads the bank), and the skew re-test the skew round
deferred runs on it below.

College is unchanged: its level is drifting with the game, not within the
season, and no window followed it (part one).

### The wager lab on the new ledger — NFL totals go under-only

The promoted ledger was rebuilt from the new chain (4,081 games), and the
curated list's rule tiers (`velocity/wagering/tiers.py`) are pinned to it,
so the wager lab (`scripts/wager_lab.py`) re-scored every rule:

| rule | all seasons: bets · win · ROI · seasons above | 2015+: bets · win · ROI |
|---|---|---|
| NFL totals, unders 4+ | 299 · **56.2%** · **+9.4%** · 11 of 15 | 213 · 56.8% · +10.2% |
| NFL totals, overs 4+ | 291 · 49.8% · −3.3% · 7 of 15 | 164 · 49.4% · −4.5% |
| NFL totals, either 4+ | 590 · 53.1% · +3.2% · 7 of 16 | 377 · 53.6% · +3.8% |
| NFL totals, either 6+ | 165 · 55.2% · +7.3% · 5 of 11 | 90 · 62.2% · +20.8% |

Unders at 4+ improve under the new level (55.6% → 56.2%, 9 → 11 seasons
above break-even); overs at 4+ fall from 52.8% to 49.8% — the level lifts
the projected total in the seasons that were scoring more, so the
disagreements that read "over" are now the ones the market already priced.
52.8% was a coin flip against a 52.4% break-even and 49.8% is one too. The
slate takes NFL totals on the under side only from here, as it has taken
college's (`DEFAULT_TOTAL_SIDES_BY_LEAGUE`), and the NFL tier table has one
row: A = unders at 4+, 56.2% over 299, 11 of 15.

## The skew re-test (2026-09-20) — the shape is right, the centre is the level's

The skew round deferred the totals skew to after the level; the level round
landed a third of it. This re-runs both gates on the new promoted ledger
and adds the measurement the two gates were missing between them.

**The ladder gate, at the market's numbers, with the promoted lattice**
(`residual_calibration`, NFL totals, 4,000 sims):

| sim | sides open | worst | 4.5 over / under bias |
|---|---|---|---|
| shipped (lattice) | 50/58 | 0.0295 | +0.028 / −0.009 |
| + skew at the bank's own ε (+0.18) | **58/58** | **0.0129** | +0.008 / +0.012 |
| + skew ε = 0.25 | 58/58 | 0.0191 | +0.002 / +0.019 |
| + skew ε = 0.33 (the close's skew) | 52/58 | 0.0278 | −0.006 / +0.028 |

**The sim-shape gate, at the model's μ** (`sim_lab`, 2015+, 10k sims × 3
seeds): `normal+skew` improves the totals tail (0.0327 → 0.0295) and the
totals mean error (0.0266 → 0.0261) and worsens the totals shoulder (0.0524
→ 0.0555); ECE 0.0575 → 0.0571; every margin column unchanged. The same
disagreement as the skew round, smaller.

**Why they disagree, exactly.** On the 2015+ bank at the model's μ the
totals residual has **mean +0.58 and median 0.00**. The level calibration
centres the *mean* projected total on the mean actual one; the slate and
the market price the *median* (`fair_total` is the sim's median); and
football's right skew puts those two about 0.6 points apart. So the
symmetric sim's median sits on reality's median today — by that
cancellation — and a mean-preserving skew moves the sim's median a point
below it (at μ 45: median 45 → 44, P(total > 45.5) 0.486 → 0.459). Graded
on every totals rung μ_t ± 0.5 … 14.5 across the 3,045 games:

| sim | rung Brier | rung calibration | under-tail calib. | over-tail calib. | mean (quoted − happened) |
|---|---|---|---|---|---|
| shipped (lattice) | 0.19582 | 0.00811 | 0.00971 | 0.00671 | −0.003 |
| + skew, mean kept | 0.19607 | 0.01526 | 0.02299 | 0.00925 | **−0.015** |
| + skew, median kept | **0.19578** | **0.00759** | **0.00340** | 0.01126 | +0.006 |

The mean-kept skew doubles the rung calibration error where the slate bets;
the ladder gate could not see that because it level-matches on the
empirical *mean*, which is the same convention. Re-centred on the median
(the standardized transform's median is −0.064 sd, 0.87 points at σ 13.6),
the skew is a small net gain — the under tail's calibration cut by two
thirds, the over tail's worse by half, Brier flat — which is what a shape
correction with the centre right looks like: real, and small.

### Verdict

**Not promoted, and closed as a shape question.** The skew is right about
the tails and cannot ship as a mean-preserving draw while the level is a
mean; the pair that would work — a level that centres the *median* and a
skew that keeps it — is one design, not two switches, and its gain at the
rungs is half a point of calibration on top of what the lattice already
did. It goes behind the level's remaining question (the +0.58 out-of-sample
mean: scoring rising year over year faster than a trailing window
follows), which is worth more and would move the same rungs.

`--sim-skew fit` stays on the live slate as a switch, off in both leagues;
`fit_epsilon` on the bank reads +0.18 for the NFL.

## The wepa round (2026-09-20) — play-context knobs chosen inside the window

nfelo's wepa fits its play-context weights to predictiveness rather than
setting them. The lab's version (`select_by_margin`, `context_fitted`):
inside each training window, every candidate on a seven-entry grid — the
turnover shrink at 0.25 / 0.5 / 0.75, garbage time, the money downs
(third and fourth) at ×1.5 and ×0.75, and combinations — is fitted on the
window's earlier seasons and scored by margin RMSE on its last complete
season; the argmin is refitted on the whole window, chosen once per
season. Beside it, the new money-downs knob alone at a fixed weight. Over
the promoted chain, 3,045 games, 2015–2026.

| variant | Brier | calib. | rmse_margin | info_w_margin | ATS (flat) | ΔBrier vs promoted | seasons | Δ sq. margin error | seasons |
|---|---|---|---|---|---|---|---|---|---|
| promoted | 0.2197 | 0.0170 | 13.008 | 0.034 | 48.5% | — | — | — | — |
| late1.5 | 0.2210 | 0.0171 | 13.097 | −0.004 | 48.1% | +0.00130 ± 0.00042 | 2/12 | +2.33 ± 0.44 | 1/12 |
| late0.75 | 0.2196 | 0.0176 | **12.987** | 0.063 | 48.2% | −0.00018 ± 0.00025 | 6/12 | **−0.54 ± 0.26** | **9/12** |
| wepa (chosen per window) | 0.2197 | 0.0178 | 12.999 | 0.049 | 48.2% | −0.00001 ± 0.00025 | 3/12 | −0.23 ± 0.25 | 7/12 |

**Readings:**

1. **Choosing per window adds nothing.** One check season is ~270 games,
   and a margin RMSE on 270 games cannot tell 0.25 from 0.5 on the
   turnover shrink or ×0.75 from ×1 on the money downs; the selector's
   choice moves season to season and its result is the grid's average —
   the paired difference against the promoted chain is zero to the
   fourth decimal. The mechanism works (tested, cached, walk-forward
   honest) and is the wrong size for this data.
2. **Up-weighting the money downs is clearly wrong** (margin error worse in
   eleven seasons of twelve, three standard errors on the Brier): third
   and fourth downs are the highest-leverage and least repeatable plays,
   which is the turnover shrink's argument again.
3. **Down-weighting them is a small, consistent margin gain** — 0.02 of
   margin RMSE, better in nine seasons of twelve, two standard errors —
   and nothing the slate stakes sees it: the moneyline Brier is flat, the
   totals untouched, the flat ATS record 48.5% → 48.2% (a loser either
   way, and unstaked). A finer sweep (×0.5–×0.9) belongs to a round where
   the margin is a staked market.

**Not promoted.** `select_by_margin` and the `late_down` knob stay in the
lab; the live chain is unchanged.
