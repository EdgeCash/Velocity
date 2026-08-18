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
   market-regression finding, applied where it belongs).
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
banked as **private** Actions artifacts and joined per game by
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

**Backlog (summer):** FIP-quality starter priors (the banked K/BB/HBP/HR
lines are already in `starters.parquet`), NegativeBinomial run
distributions, weather-on-totals; WNBA pace×efficiency from box scores,
minutes-aware availability.
