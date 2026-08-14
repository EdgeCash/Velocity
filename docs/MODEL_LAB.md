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
