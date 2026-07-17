# Velocity — End-to-End Build Plan

**Status:** Build/execution plan (v0.1)
**Companion to:** `docs/DESIGN.md` (what we're building and why). This doc is *how* we build it safely.
**Principle:** `main` is always green. Every phase is built on a branch, gated by tests, verified on real data, and merged only when it meets an explicit definition-of-done. Nothing half-built ever runs in the live path.

---

## 1. The safe build loop (run this for every phase)

Each phase follows the same repeatable loop. This is the core of the request — the loop is what makes "commit safely after each phase" true rather than aspirational.

```
   ┌─────────────────────────────────────────────────────────────────┐
   │  1. BRANCH      cut phase branch from latest main                 │
   │  2. SCAFFOLD    interfaces + stubs + failing tests (the contract) │
   │  3. BUILD       implement until the phase's tests pass            │
   │  4. TEST        pytest + ruff + mypy + data validation, locally   │
   │  5. REPAIR      reproduce → isolate → fix → re-run (loop to 4)    │
   │  6. VERIFY      run the real thing on a real data slice           │
   │  7. GATE        check every Definition-of-Done item for the phase │
   │  8. PR + CI      open PR → CI must be green → self/peer review     │
   │  9. MERGE + TAG  squash-merge to main, tag vX.Y-phaseN            │
   │ 10. SMOKE       pull main, run smoke test, confirm still green    │
   └─────────────────────────────────────────────────────────────────┘
```

**Repair sub-loop (step 5) — how bugs get fixed, not patched over:**
1. **Reproduce** with a *failing test first* (a bug without a regression test isn't fixed — it's postponed).
2. **Isolate** to the smallest layer (ingest/feature/model/wagering) using the layer boundaries.
3. **Fix** the root cause, not the symptom.
4. **Re-run** the full suite — confirm the new test passes and nothing else broke.
5. The regression test stays forever. The bug can't come back silently.

**Branch/main reality for this repo:** active development happens on `claude/nfl-ncaaf-projection-wagering-3gm5gs`. "Commit to main safely" = each phase is a **PR into `main`** that only merges when CI is green and the DoD is met. We never push directly to `main`; the branch protection *is* the safety mechanism.

---

## 2. Tooling & guardrails (built in Phase 0, used forever after)

| Concern | Tool | Guarantee it gives us |
|---|---|---|
| Unit/integration tests | **pytest** | Behavior is pinned; regressions fail loudly |
| Lint/format | **ruff** | Consistent style, catches dead code / bugs |
| Type checking | **mypy** (or pyright) | Interface contracts between layers hold |
| Data schema validation | **pandera** | Ingested/derived frames match canonical schema |
| Pre-commit hooks | **pre-commit** | Nothing un-linted/un-typed even reaches a commit |
| CI | **GitHub Actions** | The gate: no red PR merges to main |
| Reproducibility | **fixed seeds + pinned deps** (`uv`/`pip-tools` lockfile) | Same input → same output, always |
| Coverage floor | **pytest-cov** | Critical math (de-vig, Kelly, sim) stays covered |

**Determinism is non-negotiable.** The Monte Carlo engine takes a seed. Every model/backtest test runs with a fixed seed so results are exactly reproducible — otherwise "the test is flaky" masks real regressions.

**Offline, fast tests via frozen fixtures.** We commit *small* frozen data samples (one week of NFL, a few NCAAF games) under `tests/fixtures/`. The test suite never hits the network — it runs on the fixtures. Full-data runs are separate, on-demand, and not part of the per-commit gate.

---

## 3. Testing strategy by layer

Each layer has a distinct failure mode, so each has a distinct test type. This is what "test after each build phase" means concretely.

- **Ingest** — *schema + point-in-time.* Validate every normalized frame against its pandera schema. Assert no lookahead: a feature built "as of week t" must be byte-identical whether or not week t+1 data exists in the store. This single test class prevents the most common silent backtest cheat.
- **Features** — *known-value unit tests.* Hand-compute a feature on a tiny fixture; assert the code reproduces it. Assert monotonicity/bounds where they must hold (e.g., a share ∈ [0,1]).
- **Models** — *calibration + sanity + golden regression.* On a holdout slice: is a 60% call ~60%? Are outputs bounded (no negative pace, totals in a plausible range)? A **golden-file test** pins model output on a fixed fixture+seed, so any change to projections is a conscious, reviewed diff — not an accident.
- **Wagering** — *closed-form math tests.* De-vig, EV, and Kelly have known correct answers for textbook inputs; test those exactly. Test the guardrails: bet caps, correlation grouping, and that fractional-Kelly never exceeds its cap.
- **Backtest** — *reproducibility + point-in-time guard.* Same seed + same data → identical bankroll curve. An assertion that the engine physically cannot read a row dated ≥ kickoff.

**Acceptance vs. unit tests.** Unit tests gate every commit (fast, fixtures). *Acceptance* checks — "NFL model beats the market baseline on 2021–2023 out-of-sample on CLV and calibration" — gate a **phase**, and are the real proof a phase is done.

---

## 4. Phase-by-phase build (each is one merge to main)

Every phase lists its **build**, its **tests**, and its **Definition of Done** (the gate that must be 100% green before merge + tag).

### Phase 0 — Foundations & the safety rig
- **Build:** repo skeleton (per `DESIGN.md` §3 layout), `uv`/lockfile, pre-commit, ruff+mypy config, pytest, GitHub Actions CI, canonical `store/schema.py`, DuckDB/parquet IO, and the frozen test fixtures.
- **Tests:** CI runs on a trivial module; schema round-trips; fixtures load offline.
- **DoD:** `pytest`, `ruff`, `mypy` all green in CI on a PR; pre-commit blocks a deliberately-bad commit; a one-command `make test` works from a clean clone. → tag `v0.1-phase0`.

### Phase 1 — NFL game model
- **Build:** NFL ingest adapter → normalized store; opponent-adjusted EPA ratings; the shared Monte Carlo sim; spread/total/ML distribution outputs.
- **Tests:** ingest schema/point-in-time; ratings known-value on fixture; sim determinism under seed; golden-file projection; calibration on a holdout season.
- **DoD:** walk-forward backtest runs end-to-end on ≥2 historical seasons; model is **calibrated** (reliability within tolerance) and **beats the market baseline on CLV** on out-of-sample; golden tests locked. → `v0.2-phase1`.

### Phase 2 — Wagering core
- **Build:** de-vig, edge/EV, fractional-Kelly staking with caps, bet log, CLV tracking. Wire Phase 1 projections → historical lines → simulated bets.
- **Tests:** closed-form de-vig/EV/Kelly; guardrail caps; end-to-end "projection → bet → logged CLV" on a fixture slate.
- **DoD:** full historical dry-run produces a bankroll curve, per-bet CLV, and a calibration report; staking provably respects every cap; results reproducible under seed. → `v0.3-phase2`.

### Phase 3 — NCAAF game model
- **Build:** CFBD ingest (tolerant of messy data); preseason priors (recruiting + returning production + prior ratings); Bayesian shrinkage; connected opponent-adjustment; pace-aware totals.
- **Tests:** ingest handles missing/malformed play-by-play without crashing (fixture with known gaps); prior→posterior shrinkage behaves (regresses hard early); calibration on holdout.
- **DoD:** backtest on ≥2 seasons; calibrated on the *middle* of the distribution (we don't overclaim on blowouts); beats an NCAAF market baseline on CLV where lines exist. → `v0.4-phase3`.

### Phase 4 — Player props
- **Build:** volume×share×efficiency decomposition; in-sim correlated player outcomes; distributional pricing (neg-binomial/mixtures); injury/depth-chart repricing.
- **Tests:** share sums to team volume; distributions integrate to 1 and match fixtures; scratching a starter provably reprices teammates + QB; correlated props move together in-sim.
- **DoD:** prop projections calibrated per market (receptions, rush/rec yards, etc.); correlated-props sanity holds; backtest CLV on a prop-line archive. → `v0.5-phase4`.

### Phase 5 — Live lines & portfolio
- **Build:** swap in paid odds adapter behind the existing interface; real-time line shopping; correlation-aware portfolio staking across a slate; monitoring/dashboards.
- **Tests:** odds adapter contract tests (free and paid impls satisfy the same interface); portfolio staking never exceeds aggregate exposure caps; parity test that the paid adapter matches the free one on overlapping history.
- **DoD:** a dry-run live slate produces shoppable best-price bets with portfolio-aware stakes; kill-switch and exposure caps verified. → `v1.0`.

---

## 5. What "safe" means — rollback & failure handling

- **Main is never broken:** CI gate + branch protection. A red PR cannot merge, by construction.
- **Every phase is tagged**, so rollback is `git checkout vX.Y-phaseN` — a known-good state, not a guess.
- **Feature-flagged live path:** unfinished phases are behind config flags; the "live"/production entrypoint only calls completed, tagged capabilities. Building Phase 4 can't destabilize a Phase 2 dry-run.
- **Golden-file diffs are conscious:** if a change moves projections, the golden test fails and forces a reviewed, intentional update — never a silent drift.
- **Bug SLA within a phase:** reproduce-with-a-test *before* fixing; the regression test is part of the same PR as the fix.
- **When a phase's acceptance test fails** (e.g., NFL model doesn't beat baseline): the phase is **not done** — we do not merge a model that fails its own bar. We iterate on the branch, or explicitly de-scope with a written note in the PR. Green tests on a bad model is still a failed phase.

---

## 6. Definition of "the model is built"

Phases 0–4 merged and tagged, `main` green, and on out-of-sample history the system is: **calibrated** across markets, **positive-CLV** where we have line archives, and **reproducible** end-to-end under seed with a full bet log and bankroll curve.

At that point — and this is the next conversation — we outline **delivery & presentation of the picks**: how projections and edges surface to a human (ranked card, confidence, the "why," CLV tracking), the cadence, and the interface. We design that *after* the engine earns trust, not before.

---

## 7. Immediate next step

Kick off **Phase 0**: scaffold the repo, wire pre-commit + CI + pytest, land the canonical schema and frozen fixtures, and prove the safe build loop works by pushing a green PR to main. Everything after inherits that safety rig.
