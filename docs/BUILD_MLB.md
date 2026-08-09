# Velocity — MLB Build Plan

> **DECOMMISSIONED (2026-08).** MLB served its purpose — proving the full
> ingest → model → wager → grade → report pipeline on a live daily board — and
> was retired per [`docs/FOOTBALL_CUTOVER.md`](FOOTBALL_CUTOVER.md). The last
> commit with the complete working MLB system is tagged **`mlb-final`**. This
> document is kept as the historical record of what was built and learned.

**Status:** Build/execution plan (v0.1)
**Companion to:** [`docs/BUILD.md`](BUILD.md) (the safe build loop and per-phase
gate discipline this plan inherits verbatim) and [`docs/DESIGN.md`](DESIGN.md)
(the projection → de-vig → edge → Kelly philosophy).
**Principle:** `main` stays green. Every phase is a branch → tests → real-data
verification → PR → merge, exactly as in `BUILD.md §1`. MLB is added *alongside*
NFL/NCAAF, never by destabilizing them.

---

## 0. Why MLB, in one paragraph

MLB is the most simulation-friendly major sport (a discrete sequence of near-
isolated pitcher-vs-batter matchups), it has the deepest free public data of any
sport (Statcast/pybaseball/Retrosheet/MLB StatsAPI), and it is **in season right
now** with a ~15-game daily slate — so a live board exists to test the pipeline
against today, and that same live board is the first real model target. The edge
does **not** live in the efficient full-game moneyline/total close; it lives in
the softer, lower-limit derivatives and props: **pitcher strikeouts, total bases,
team totals, and first-5-innings (F5)**. This is a thin-edge, high-turnover,
CLV-validated grind — not a quick money-maker — and the plan is scoped honestly
around that.

---

## 1. What reuses vs. what is net-new

The whole argument for MLB-in-Velocity is that the expensive machinery already
exists and is sport-agnostic. A baseball model only has to **emit the same
sampled score arrays** the football sim emits (`GameSim.home_score` /
`away_score`), and the entire wagering + backtest stack downstream is inherited.

| Layer | File(s) | MLB status |
|---|---|---|
| De-vig | `wagering/devig.py` | ♻️ **reuse unchanged** (pure odds math) |
| Edge / EV | `wagering/edge.py` | ♻️ reuse unchanged |
| Kelly staking + caps | `wagering/staking.py` | ♻️ reuse unchanged |
| Portfolio / correlation caps | `wagering/portfolio.py` | ♻️ reuse (F5 & game are correlated — add a group key) |
| Bet log / CLV | `wagering/bet_log.py` | ♻️ reuse unchanged |
| Walk-forward backtest | `backtest/engine.py` | ♻️ reuse (point-in-time guard already generic) |
| Calibration / metrics | `eval/metrics.py` | ♻️ reuse unchanged |
| The Odds API ingest | `ingest/theoddsapi.py` | ♻️ **reuse** — `normalize_odds_events` is sport-agnostic for `h2h`/`spreads`/`totals`; add one `SPORT_KEYS["mlb"]` entry |
| Slate orchestration | `wagering/live.py`, `wagering/slate.py` | ♻️ reuse the flow; **add** an MLB team-alias table (30 teams — trivial vs. NCAAF's 250+) |
| Line snapshot collectors | `.github/workflows/collect-odds.yml` | ♻️ reuse; accept `mlb` in `--leagues` |
| **Game sim** | `models/simulate.py` | ⚠️ **net-new** — the bivariate-normal (margin, total) approximation is invalid for discrete, skewed, low-scoring runs. Baseball needs a real per-inning / per-PA engine. |
| **MLB ingest** | `ingest/mlb.py` *(new)* | 🆕 schedule/scores + batter/pitcher rate inputs |
| **Canonical schema** | `store/schema.py` | 🆕 extend `LEAGUES`, `MARKETS`; new baseball stat table (`Plays` is football-shaped: down/epa/yards) |
| **MLB game model** | `models/game_mlb.py` *(new)* | 🆕 rate skills + park/weather → run distribution |
| **MLB props** | `models/props.py` | 🔧 extend the existing prop infra (Ks, TB, hits) — same decomposition pattern as football props |

**Read this as: ~70% of the value is inherited plumbing; the genuine
engineering is one new simulator, one new ingest adapter, and a schema
extension.**

---

## 2. Schema extensions (the one change that touches existing code)

`store/schema.py` today hardcodes football. Minimal, additive edits:

- `LEAGUES = ["nfl", "ncaaf", "mlb"]` — unblocks the `Games`/`Lines` `isin`
  validators so MLB frames validate (this is exactly the constraint that would
  otherwise reject an `mlb`-tagged odds snapshot).
- `MARKETS` — add the derivative markets MLB needs that football doesn't:
  `run_line` (a spread), and segment-tagged game markets for **F5**
  (`total_f5`, `moneyline_f5`, `spread_f5`). Alternatively add a `segment`
  column (`full` / `f5`) and keep the three base market names — decide in M0 and
  golden-test it.
- `Games.week` is `ge=0, le=25`; baseball has no weeks. Use an ISO-week index
  derived from `kickoff` (or `0`) — the real point-in-time anchor is
  `kickoff`/`timestamp`, which already works. No schema change needed beyond
  documenting the convention.
- **New table** `BaseballStats` (or `PlateAppearances`) — the canonical rate
  inputs the model consumes (per player-season: PA, K%, BB%, 1B/2B/3B/HR rates
  for batters; K/9, BB/9, HR/9, batted-ball for pitchers; plus park factors).
  `Plays` (down/epa/yards_gained) does not apply and is left football-only.

Every edit is additive; NFL/NCAAF golden tests must still pass byte-for-byte
(that is the regression gate that proves we didn't disturb football).

---

## 3. Data sources (all free, offline-testable)

| Source | Role | Access |
|---|---|---|
| **MLB StatsAPI** | schedules, scores, **confirmed lineups**, live PBP | free HTTP |
| **Statcast (Baseball Savant)** via **pybaseball** | pitch-level & batted-ball rate skills (2015+) | free |
| **Retrosheet** | deep historical PBP for backtesting (decades) | free |
| **Steamer / ZiPS (FanGraphs)** | ready-made per-PA rate projections (a strong prior; blend with home-grown Marcel+Statcast) | free public |
| **The Odds API** (`baseball_mlb`) | live + historical odds → `Lines` (CLV archive) | existing `THE_ODDS_API` secret |

Same discipline as every existing adapter: **pure normalizers are unit-tested
offline against frozen fixtures; network clients are out of the per-commit
gate** and verified via a manual Actions run.

---

## 4. Phase-by-phase build

Each phase is one PR into `main`, following the `BUILD.md §1` loop, with an
explicit Definition of Done (DoD) that gates the merge. Tags continue the repo's
scheme, namespaced: `v*-mlbN`.

### Phase M0 — Foundations + the in-season plumbing test
- **Build:** extend `store/schema.py` (`LEAGUES`, `MARKETS`/segment, convention
  notes); add `SPORT_KEYS["mlb"] = "baseball_mlb"` to `theoddsapi.py`; wire
  `mlb` into `collect-odds.yml`'s `--leagues`; commit small frozen MLB fixtures
  (a handful of odds events + a day of schedule) under `tests/fixtures/mlb/`.
- **Tests:** an `mlb`-tagged `Lines` frame validates; `normalize_odds_events`
  produces canonical rows from the MLB odds fixture; **all NFL/NCAAF golden
  tests unchanged**.
- **DoD:** `pytest`/`ruff`/`mypy` green; **manual dispatch of `collect-odds.yml
  --leagues mlb` returns a real non-empty board, prints credits remaining, and
  writes a private artifact** — this is the live pipeline/API-key proof, done now
  in-season, not in September. → `v*-mlb0`.

  *M0 alone is the "in-season test" already discussed — it ships value before any
  model exists.*

### Phase M1 — MLB ingest
- **Build:** `ingest/mlb.py` — pure normalizers (schedule/scores → `Games`;
  Statcast/StatsAPI stat pulls → `BaseballStats`) + network loaders (StatsAPI,
  pybaseball). Mirror the two-layer `ingest/nfl.py` pattern exactly.
- **Tests:** schema + point-in-time (a rate built "as of date d" is identical
  with or without day d+1 present); normalizers reproduce hand-computed values
  on a fixture with a known gap (messy data tolerated, not crashed).
- **DoD:** a season slice ingests to a validated store offline; loaders verified
  once against live StatsAPI/pybaseball out of the gate. → `v*-mlb1`.

### Phase M2 — Batter & pitcher rate projections
- **Build:** `features/` extension — per-player rate skills (K/BB/1B/2B/3B/HR)
  with **Bayesian shrinkage** to league/position priors (Marcel-style aging +
  regression), **park factors**, and an optional Steamer/ZiPS blend. Handedness
  splits where data supports.
- **Tests:** known-value on a fixture; rates ∈ [0,1] and sum-to-one over PA
  outcomes; shrinkage regresses small samples hard toward prior; park factor is
  applied multiplicatively and is neutral (=1) for a neutral park.
- **DoD:** projected rates are calibrated against a holdout season (a batter
  projected for a .330 OBP realizes ≈.330 in aggregate). → `v*-mlb2`.

### Phase M3 — Baseball Monte Carlo engine (the core net-new piece)
- **Build:** `models/simulate_baseball.py` — a **per-inning / per-PA** simulator.
  For each half-inning, draw PA outcomes from the batter×pitcher matchup rates
  until 3 outs, advance a base-out state machine, accumulate runs; loop 9+
  innings (extra-innings + walk-off logic; the ghost-runner rule). Emit a
  `GameSim`-**compatible** object: `home_score`/`away_score` sample arrays **plus
  per-inning run arrays** (so F5 = sum of innings 1–5) and per-player stat arrays
  (K counts, total bases) for props. Caller-supplied seeded `numpy` Generator —
  **determinism is non-negotiable**, same as `simulate.py`.
- **Tests:** determinism under seed (identical arrays); a half-inning with 100%
  strikeout rate scores 0 and records exactly 3 outs; run distribution is
  discrete, non-negative, right-skewed, and its mean matches an analytic
  expected-runs check within tolerance; calibration of simulated run totals vs.
  real games on a holdout.
- **DoD:** simulated full-game run distribution is calibrated (median total and
  win prob track reality on a holdout season) and reproducible under seed;
  golden-file locked. → `v*-mlb3`.

### Phase M4 — Game & derivative wagering integration
- **Build:** `models/game_mlb.py` — compose M2 rates + M3 sim into a
  `GameProjection`-shaped object exposing `p_home_win` / `prob_home_cover`
  (run line) / `prob_over` (total) / team totals, **plus F5 pricing** off the
  innings-1–5 marginal. Route through existing `devig`/`edge`/`staking`; add an
  MLB team-alias table to `wagering/live.py`; give game+F5 a shared
  correlation-group key in `portfolio.py`.
- **Tests:** run-line/total/ML prices are mutually consistent (all off one sim);
  F5 total < full-game total in expectation; alias table resolves all 30 clubs;
  end-to-end "projection → bet → logged CLV" on an MLB fixture slate.
- **DoD:** walk-forward backtest on ≥2 seasons produces a bankroll curve + per-
  bet CLV; **positive CLV on the soft surface (team totals / F5), not required on
  the efficient full-game close**; staking respects every cap. → `v*-mlb4`.

### Phase M5 — Player props (the softest surface)
- **Build:** extend `models/props.py` — pitcher **strikeouts** and **outs**,
  batter **total bases** / **hits** / **HR**, priced as distributions straight
  out of the M3 per-player sample arrays (no separate model). Injury/lineup
  repricing: a scratched batter reprices the lineup and the opposing pitcher's K
  prop.
- **Tests:** prop distributions integrate to 1 and match a fixture; a pitcher's
  K distribution shifts correctly against a high-K vs. low-K lineup; scratching a
  batter provably reprices; calibration per prop market.
- **DoD:** prop projections calibrated per market; **backtest CLV on a prop-line
  archive** (Odds API props where available; otherwise self-logged going
  forward). → `v*-mlb5`.

### Phase M6 — Live slate + scheduled collectors
- **Build:** add `mlb` to `run_live_slate.py` (`--league` choices) and the
  `live-slate.yml` matrix with an **MLB-appropriate cron** (daily, timed to
  lineup release ~hours before first pitch, not the football Thu/Sat/Sun/Mon
  windows). Optional: an MLB collector cadence for BettingPros props. A
  **lineup/weather repricing** step is the one real operational seam — the model
  must re-run at confirmed-lineup release to beat soft books before they move.
- **Tests:** slate runner produces a staked MLB card + skipped list on a saved
  snapshot (offline); empty/off-day board writes an empty slate and succeeds;
  cron parses.
- **DoD:** a dry-run MLB slate produces shoppable, staked recommendations from a
  live snapshot, written to a **private** artifact; off-day runs succeed empty.
  → `v*-mlb6` / `v1.x`.

---

## 5. Honest risks & how the plan handles them

- **The edge is in low-limit markets.** Full-game close is efficient; props/F5
  carry $200–$500 limits and MLB just capped pitch-microbets at $200 post-
  scandal. → Scope success as **CLV on the soft surface**, high bet count, multi-
  book line-shopping — not size through the close. The DoD for M4/M5 reflects
  this explicitly.
- **The edge is perishable and operational.** Much of it is repricing at lineup/
  weather release faster than soft books. → M6 makes lineup-release repricing a
  first-class step; backtests use historical confirmed lineups (Retrosheet/
  StatsAPI) so we don't fool ourselves with look-ahead.
- **High single-game variance.** Thin edges only show over large samples. →
  Validate on **CLV, not short-run P&L**; the ~2,430-game season is an asset for
  sample size.
- **Don't destabilize football.** → Every schema/`SPORT_KEYS` edit is additive
  and gated by the unchanged NFL/NCAAF golden tests; MLB lives behind its own
  `--league`/config path (the feature-flag discipline from `BUILD.md §5`).
- **New-simulator risk.** The baseball engine is the one genuinely new,
  correctness-critical component. → It ships with determinism, analytic-check,
  and calibration tests before any wagering trusts it (M3 gate precedes M4).

---

## 6. Minimum viable slice (if you want value fastest)

**M0 + M4-lite.** M0 alone proves the API key and pipeline against a live
in-season board today (zero model). Then the shortest path to *real
recommendations* is to skip the full per-PA sim initially and seed M3 with a
**calibrated run-distribution approximation** (e.g., a Poisson/negative-binomial
on projected team runs from M2 rates) to price team totals and F5 — enough to
stand up a staked slate and start logging CLV — then upgrade to the full per-PA
engine once it clears its calibration gate. This gets a live MLB slate and CLV
capture in weeks instead of after the whole stack lands, without ever merging an
uncalibrated model into the live path.

---

## 7. Immediate next step

Land **Phase M0**: the additive schema/`SPORT_KEYS`/workflow edits + MLB
fixtures + green tests, then manually dispatch `collect-odds.yml --leagues mlb`
to capture a live board and confirm the key end-to-end. That single PR delivers
the in-season pipeline test *and* lays the foundation every later MLB phase
builds on.
