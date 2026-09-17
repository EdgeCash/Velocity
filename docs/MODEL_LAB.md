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

