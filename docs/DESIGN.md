# Velocity — NFL & NCAAF Projection and Wagering System

**Status:** Design outline (v0.1)
**Scope:** Project game outcomes and player props for NFL and NCAAF, then convert projections into disciplined, positive-expected-value wagers.
**Stack:** Python (pandas, numpy, scikit-learn, PyMC), free public data first (nflverse, CollegeFootballData), paid odds feed added later for live lines.

---

## 1. Philosophy & goals

The system exists to answer two questions on every game and every market:

1. **What is the true distribution of the outcome?** (Not a point estimate — a full distribution. Spreads, totals, and props are all bets on *tails* and *quantiles*, not means.)
2. **Is the market price wrong enough to bet?** A projection is only useful relative to a price. Edge = our probability − the price's implied probability, after removing the vig.

Two design commitments follow from this:

- **Probabilistic, not deterministic.** Every projection is a distribution (or a Monte Carlo sample), so we can price any derivative market — spread, total, moneyline, team totals, alternate lines, and player props — from the same simulation.
- **The market is the benchmark, not the target.** The closing line is the sharpest widely available forecast. Our primary success metric is **beating the closing line (CLV)**, because CLV predicts long-run profit far more reliably than a small realized-ROI sample.

**Non-goals for v1:** live/in-game betting, exotic parlays as a primary strategy, and any model that can't be backtested against historical closing lines.

---

## 2. Data sources (free tier)

### NFL — nflverse
- **`nfl_data_py`** (Python port of nflfastR data): play-by-play back to 1999 with EPA, win probability, CPOE, air yards, and pre-computed advanced metrics.
- Weekly rosters, depth charts, snap counts, injuries, schedules, and Next Gen Stats aggregates.
- Player IDs are stable and cross-linked (GSIS ↔ PFR ↔ ESPN), which matters for joining props to performance.

### NCAAF — CollegeFootballData (CFBD)
- **`cfbd` Python package** + free API key. Play-by-play, drives, box scores, team/player season and game stats, recruiting rankings, returning-production, SP+/FPI/ELO ratings, betting lines (historical), and the venue/weather metadata.
- Coverage is thinner and messier than NFL: ~130+ FBS teams, huge talent disparity, non-conference cupcake games, and inconsistent play-by-play parsing for smaller programs. The ingestion layer must tolerate this.

### Odds & lines
- **Historical (free-ish):** CFBD carries historical NCAAF lines; for NFL, backfill closing lines from public archives / Kaggle dumps for backtesting.
- **Live (paid, later):** The Odds API or OddsJam for real-time multi-book lines. The architecture treats the odds provider as a swappable adapter so this is a config change, not a rewrite.

### Supporting free data
- Weather (Open-Meteo historical + forecast API), stadium metadata (dome/turf/altitude), and schedule/rest (bye weeks, days of rest, travel distance, time-zone shifts).

---

## 3. System architecture

```
                    ┌────────────────────────────────────────────┐
                    │                  velocity/                  │
                    └────────────────────────────────────────────┘

  ingest ─────────► store ─────────► features ─────────► models ─────────► projections
 (adapters)      (parquet/DB)      (feature store)    (game + props)      (distributions)
                                                                                │
                                                                                ▼
   odds ─────────► devig ─────────► edge/pricing ─────────► staking ─────────► bet log
 (adapters)     (fair prob)      (EV per market)        (Kelly/bankroll)   (CLV tracking)
                                                                                │
                                                                                ▼
                                                        backtest / evaluation / dashboards
```

**Layering principle:** each arrow is a clean interface. Ingest adapters normalize different providers into one schema; the odds layer is fully decoupled from the projection layer so we can swap free → paid without touching models.

### Proposed repository layout
```
velocity/
  ingest/
    nfl.py            # nfl_data_py adapters → normalized parquet
    ncaaf.py          # cfbd adapters → normalized parquet
    odds.py           # odds provider adapter (interface + free/paid impls)
    weather.py
  store/
    schema.py         # canonical table definitions (games, plays, players, lines)
    io.py             # parquet/duckdb read/write helpers
  features/
    team.py           # opponent-adjusted efficiency, pace, situational splits
    player.py         # usage, target/carry share, matchup adjustments
    context.py        # rest, travel, weather, injuries, home field
  models/
    game_nfl.py       # NFL game model (ratings + simulation)
    game_ncaaf.py     # NCAAF game model (ratings + priors + simulation)
    props.py          # player prop distributions
    simulate.py       # Monte Carlo engine (shared)
  wagering/
    devig.py          # remove vig → fair probabilities
    edge.py           # EV, implied vs. model probability
    staking.py        # Kelly, fractional Kelly, bankroll constraints
    portfolio.py      # correlation-aware bet sizing across a slate
  backtest/
    engine.py         # walk-forward, point-in-time correctness
    metrics.py        # CLV, Brier, log-loss, calibration, ROI, drawdown
  eval/
    calibration.py    # reliability diagrams, per-market calibration
    reports.py
  config/
  notebooks/          # research only, never in the production path
```

**Golden rule enforced by the store layer: point-in-time correctness.** Every feature must be computable using *only* information available before kickoff. No lookahead. This is the single most common way sports models silently cheat in backtests.

---

## 4. Game projection models

The core object is a **team strength model** that produces, for any matchup, a distribution over the final score of each team. Everything else (spread, total, moneyline, team totals) is derived from that joint score distribution via simulation.

### 4.1 Two complementary layers

**Layer A — Ratings (fast, robust, the backbone).**
A power-rating system that yields an expected margin and expected total for any matchup.

- Start with **efficiency-based ratings** rather than raw points. The unit of value in football is **EPA (expected points added) per play**, split into offense and defense, further split into pass/rush and early-down/late-down. Opponent-adjust these (a ridge regression / adjusted-plus-minus style fit) so beating a weak schedule doesn't inflate a rating.
- Convert opponent-adjusted offensive and defensive efficiency + expected pace (plays per game) into **expected points for each team**, then into an expected margin and total.
- Maintain a **dynamic rating** (Elo-style or a Kalman/state-space update) so ratings move week to week and we get sensible early-season behavior via priors.

**Layer B — Simulation (turns ratings into any market).**
Given each team's expected scoring distribution, run a Monte Carlo:
- Model **drive-level or possession-level scoring** so totals and spreads share a coherent joint distribution (margin and total are correlated — a shootout is high-total *and* can be either sign).
- Sample game-to-game variance calibrated to historical residuals (football final margins have a well-known distribution with mass near key numbers 3 and 7 — the sim must reproduce that, not a smooth normal).
- Output: P(win), full margin distribution, full total distribution, and every alternate line, priced consistently.

### 4.2 NFL specifics
- 32 teams, deep and stable play-by-play → data-rich. Ratings can lean heavily on EPA/CPOE with modest priors.
- Key features: opponent-adjusted EPA/play (off & def, pass & rush), success rate, pace/neutral-pace, red-zone and third-down efficiency, pressure/sack rates, explosive-play rates, QB-adjusted ratings (a rating must degrade instantly when the starting QB changes — QB is the largest single factor), rest/bye, travel, dome/outdoor + weather (wind is the dominant weather effect on passing and kicking), and home field (~2 points, shrinking historically — estimate it, don't assume it).

### 4.3 NCAAF specifics (materially different modeling problem)
- **~130 FBS teams + talent chasm.** A top team may be 40+ points better than a bad one; the model must handle blowouts and heteroscedastic variance (bad teams are also *less predictable*).
- **Priors dominate early.** With only ~12 games/season and huge roster turnover, preseason priors carry real weight. Blend **recruiting rankings (247/Blue-chip ratio), returning production, and prior-year adjusted ratings** into a preseason prior, then let in-season results update it (Bayesian shrinkage — regress hard early, trust data later).
- **Schedule is wildly unbalanced.** Opponent adjustment is essential *and* fragile (many teams never share opponents). A connected ratings solve (like SP+/massey-style) is required; consider blending with public advanced ratings (SP+, FPI) as features or priors rather than reinventing them.
- **Pace and style vary enormously** (tempo offenses vs. ground-and-pound) → totals modeling needs team-specific pace, not a league constant.
- **Motivation/situational noise:** rivalry games, bowl opt-outs, playoff-lock resting, coaching changes. Flag these; don't pretend the base model captures them.
- **Home field is larger and more variable** than the NFL and altitude/environment matters more.

### 4.4 What the game model outputs
For each game: `P(home win)`, margin distribution, total distribution, and the fair line for spread / total / moneyline / team totals / alternates — all from one simulation so they're internally consistent.

---

## 5. Player prop models

Props are a **usage × efficiency × game-script** problem, and they are where a free-data model can find the most soft lines (books price props with less attention than sides/totals).

### 5.1 Decomposition
Project each stat as a chain, each factor modeled separately so we can reason about it:

```
Player stat  =  Team volume  ×  Player share  ×  Efficiency  ×  Game-script adjustment
```

Example — receiving yards:
- **Team pass attempts** (from the game sim's expected plays × pass rate, itself game-script dependent — trailing teams pass more).
- **Player target share** (recent usage, role, injuries to teammates redistributing targets).
- **Yards per target / catch rate** (player skill × opponent pass-defense by area of field).
- **Game-script feedback:** the game sim already produces win-probability paths, so pass/rush volume adjusts endogenously (a team projected to trail throws more).

### 5.2 Distributions, not points
Books offer over/unders, so we need the **full distribution** of each stat to price the line and alternates:
- Counts (receptions, completions, carries): negative binomial / Poisson-mixture.
- Yards: often a mixture (mass near zero for a quiet game + a right-skewed body) — model the shape, don't assume normal.
- Anytime TD / props with a binary core: model the scoring-event probability directly (red-zone usage × team red-zone rate).
- **Correlation matters** (a QB's passing yards, his WR1's receiving yards, and the team total are correlated). Simulate players *within* the game sim so these move together — this is what makes same-game parlays and correlated props tractable and honest.

### 5.3 Priors, sample size, and injuries
- Small samples: shrink player rates toward position/role baselines (empirical-Bayes).
- **Usage is stickier and more predictive than efficiency** — weight recent role heavily, regress efficiency hard.
- Injury/inactive handling is make-or-break: a scratched WR1 reprices his backups and the QB. Ingest injury reports and depth charts; recompute shares on inactives.
- NCAAF props: fewer offered, noisier data, thinner injury reporting — treat with wider uncertainty and a higher edge threshold.

---

## 6. Wagering system

Projections become bets through four steps: **de-vig → edge → stake → log**.

### 6.1 De-vigging (fair probability from a price)
A quoted price includes the book's margin. Remove it to get the market's fair probability, using a principled method (multiplicative/Shin/logit — not naive normalization, which mis-attributes vig on lopsided markets). This gives an apples-to-apples comparison to our model probability and is also how we measure CLV.

### 6.2 Edge & expected value
For each market: `EV = p_model × payout − (1 − p_model)`, and compare `p_model` to the de-vigged `p_market`.
- Only bet when the edge clears a **threshold** that accounts for our own estimation error (more uncertainty in NCAAF and props → higher required edge).
- **Shop lines** across books; bet the best available number. Buying half a point through a key number (3, 7) is a real, quantifiable edge.

### 6.3 Staking — fractional Kelly with guardrails
- Size with **Kelly**, but use **fractional Kelly (¼–½)** because our probability estimates are themselves uncertain; full Kelly on a mis-estimated edge overbets and courts ruin.
- Hard caps: max % of bankroll per bet, per game, and per correlated group.
- **Correlation-aware portfolio sizing** across the slate: a spread, its total, and a same-game prop are not independent; naive per-bet Kelly across correlated bets massively overstakes. Size the *portfolio*, not each ticket in isolation.

### 6.4 Bet logging & closing-line value
Every bet records: timestamp, market, our price, our probability, stake, and — critically — the **closing line**. CLV is the leading indicator of skill. A model can be up or down over 200 bets by luck; consistent positive CLV is the real signal that the edge is genuine.

---

## 7. Backtesting & evaluation

A model is worthless until it's been tested the way it will be used.

### 7.1 Walk-forward, point-in-time
- **Walk-forward** only: train on weeks ≤ t, predict week t+1, roll forward. Never fit on the future.
- **Point-in-time features** enforced by the store layer (Section 3). If a feature can't be reconstructed as-of kickoff, it can't be in the model.
- Backtest against **actual historical closing lines**, including realistic vig, and simulate line shopping only across books we'd actually have.

### 7.2 Metrics (in priority order)
1. **Calibration** — when we say 60%, does it hit ~60%? Reliability diagrams per market. A well-calibrated model is a prerequisite for Kelly to work at all.
2. **CLV** — do we consistently beat the closing line? The primary skill metric.
3. **Log-loss / Brier** — proper scoring rules on our probabilities vs. outcomes.
4. **ROI, hit rate vs. break-even, drawdown, and bankroll trajectory** under the actual staking plan.
- Always report against a **market baseline** (bet nothing / bet the closing favorite) — beating a naive model means little; beating the market is the bar.

### 7.3 Guarding against self-deception
- Out-of-sample seasons held out entirely.
- Multiple-comparisons discipline: testing 50 prop angles guarantees some look great by chance — correct for it.
- Track model vs. market disagreement: large disagreements are either edges or bugs. Investigate before trusting.

---

## 8. Build roadmap

**Phase 0 — Foundations**
Ingest adapters (NFL + NCAAF), canonical schema, point-in-time store, parquet/duckdb. Prove we can reconstruct any week's features as-of kickoff.

**Phase 1 — NFL game model**
Opponent-adjusted EPA ratings → simulation → spread/total/ML distributions. Backtest CLV and calibration vs. historical lines. NFL first because the data is cleanest.

**Phase 2 — Wagering core**
De-vig, edge, fractional-Kelly staking, bet log, CLV tracking. Wire against historical lines end-to-end.

**Phase 3 — NCAAF game model**
Priors (recruiting + returning production + prior ratings), Bayesian shrinkage, connected opponent-adjustment, pace-aware totals. Handle the messy-data realities.

**Phase 4 — Player props**
Volume × share × efficiency decomposition, in-sim correlated player outcomes, distributional pricing, injury/depth-chart repricing.

**Phase 5 — Live lines & portfolio**
Swap in the paid odds adapter, real-time line shopping, correlation-aware portfolio staking across the slate, monitoring/dashboards.

---

## 9. Key risks & honest caveats

- **The market is very good.** Sides and totals for NFL primetime games are razor-sharp; realistic early edges live in **props, NCAAF, and stale/soft numbers**, not marquee NFL sides.
- **Free data has gaps** (no snap-level tracking, limited injury granularity, messy NCAAF play-by-play). We model uncertainty honestly rather than pretending precision.
- **Calibration > cleverness.** A simple, well-calibrated model with correct staking beats a fancy, overconfident one that breaks Kelly.
- **Variance is large.** Even a genuine 55% edge has long losing stretches; bankroll rules and CLV tracking are what keep us honest through them.
- **Limits & availability.** Real edges get bet into or limited; this is a modeling *and* an execution problem.

---

## 10. Open decisions (to resolve before Phase 1)

- Storage engine: flat parquet vs. DuckDB vs. Postgres (leaning DuckDB — fast, local, SQL, zero-ops).
- Ratings method: Elo/state-space vs. batch ridge-adjusted efficiency vs. a hierarchical Bayesian (PyMC) model — likely start batch-ridge, add state-space for in-season updating.
- How much to lean on public advanced ratings (SP+/FPI) as NCAAF priors vs. building fully in-house.
- Backtest depth: how many historical seasons and which line archives we can source for free.
