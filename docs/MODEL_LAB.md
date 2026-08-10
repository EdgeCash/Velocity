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

## Round 2 — 2011–2025 window (validation + the live-model bar)

Contenders: `baseline`, `scores` (the schedule-only fit the live slate
currently runs for NFL), `recency-17`, `recency-34-r400`.

<!-- RESULTS-15 -->

---

## Promotion decisions

- Pending Round 2: if `recency-17` (or `-34-r400`) beats both `baseline` and
  `scores` on the long window, the live NFL slate switches its ratings fit to
  the recency-weighted EPA model.
- The disagreement-sweep machinery stays the gate for any future "bet NFL
  sides/totals" proposal — the burden of proof is a robust >52.4% cut across
  seasons, which no current variant provides.

## Backlog (next experiments, in rough order of expected value)

1. **QB adjustment** — the single feature every successful public NFL model
   carries (nfelo, 538 Elo). Needs passer identity on the canonical plays
   (nflverse carries it; a dataset rebuild adds `passer_player_id`), then a
   starter-change adjustment at projection time.
2. **Rest / schedule spots** — bye weeks, short weeks (Thu), divisional-game
   HFA discount; all computable from the committed schedules.
3. **Weather on totals** — wind above ~15 mph measurably depresses totals;
   Open-Meteo history is free; stadium coordinates are a small static table.
4. **Success-rate / early-down EPA blends** — alternative efficiency
   definitions per the DVOA/PFF literature, testable as drop-in `epa_col`
   variants.
5. **NCAAF lab** — port the harness to the scores model + pace variants; the
   totals filter's threshold re-validated on 2011–2025 style windows.
