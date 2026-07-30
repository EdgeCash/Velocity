# Velocity — Project Status & Handoff

**Purpose of this file:** a self-contained snapshot of *what this project is, what's been built, what we've actually learned, and where the open decisions are* — written so a fresh session (human or model) with **no prior context** can read it cold and reason about direction. If you're picking this up to think about where it should go, start here.

**Last updated:** 2026-07-26.
**Companion docs:** `DESIGN.md` (modelling philosophy), `WAGERING_SYSTEM_PLAN.md` (the forward, real-money plan), `LAUNCH.md` (operator runbook).

---

## 1. What this project is

Velocity projects sports outcomes as **full probability distributions** (via Monte Carlo simulation), prices every market off those distributions (de-vig → edge → fractional-Kelly stake), and grades itself primarily on **closing-line value (CLV)** — beating the closing line — because CLV predicts long-run profit far more reliably than a small realized-ROI sample.

It began as an **NFL/NCAAF** system (`DESIGN.md`). We then built a full **MLB vertical** on the same wagering stack, deliberately, as a **fast, in-season test rig**: MLB plays every day, so the CLV/calibration feedback loop that would take an NFL season to run takes days. The MLB work is the proving ground; the *process* is meant to transfer back to football.

## 2. Goals (the thesis)

1. **Make projections/plays as accurate as possible, validated by CLV + calibration**, not short-run P&L.
2. **Use MLB as a transferable rig** to develop and prove the method (sim → price → backtest → calibrate → exclude no-edge markets), then port it to NFL/NCAAF.
3. **Build toward a real-money operating system** — direction confirmed. See `WAGERING_SYSTEM_PLAN.md` for the phased, gated plan.

## 3. Architecture in one paragraph

A per-PA (baseball) / per-play (football, planned) Monte Carlo sim emits **GameSim-compatible sample arrays**, so a single wagering stack — de-vig, edge, Kelly, CLV, calibration — prices *any* market (moneyline, spread, total, team totals, F5, player props) off the same simulation. Point-in-time discipline (as-of stats, no leakage) makes the **walk-forward backtest** honest. Confidence calibration (probability shrinkage toward 0.5) corrects overconfidence, tuned against the backtest's calibration table. Paid odds live only in private artifacts, never the public repo.

---

## 4. What's been built (capability inventory)

| Capability | State | Where |
|---|---|---|
| Per-PA baseball Monte Carlo sim (TTO, park, bullpen, platoon, workload cap) | **Done, tested** | `velocity/models/simulate_baseball.py`, `game_mlb.py` |
| Live MLB model build (StatsAPI stats + lineups → model) | **Done** | `velocity/models/mlb_build.py` |
| Point-in-time as-of model build (no leakage) | **Done** | `build_mlb_asof` |
| Wagering stack (de-vig, edge, fractional Kelly, group caps) | **Done, tested** | `velocity/wagering/` |
| Player-prop pricing off the sim arrays | **Done, tested** | `velocity/models/props_mlb.py`, `wagering/props_slate.py` |
| CLV + calibration scorecard (ECE) | **Done, tested** | `velocity/report/scorecard.py` |
| Walk-forward CLV backtest (game markets) | **Done, ran** | `velocity/backtest/mlb.py`, `scripts/backtest_mlb.py` |
| Walk-forward **prop** CLV backtest (+ grading) | **Done, ran** | `velocity/backtest/props_mlb.py`, `scripts/backtest_props_mlb.py` |
| Box-score grading (game_id→gamePk fetch) → ROI + calibration | **Done, ran** | `results.gamepks_for_slate`, `props_mlb.load_archive_boxscores` |
| Confidence-shrink sweep (global + **per-market**) | **Done, ran** | `run_prop_shrink_sweep`, `prop_market_sweep` |
| Market exclusion (drop no-edge markets) | **Done** | `SlateConfig.exclude_markets` |
| Odds/props CLV backfill collectors | **Done** | `scripts/collect_historical_*.py`, workflows |
| Live slate (game + props), calibrated | **Wired** | `scripts/run_live_slate.py` |
| Football (NFL/NCAAF) vertical | **Not started** (only the original NFL scaffolding in `DESIGN.md`) | — |
| Execution, persistent bankroll, monitoring | **Not started** | see plan |

## 5. Key empirical findings (the evidence — this is what to reason from)

All results are **one ~15-day window (2026-07-10 → 07-24), one season, in-sample.** Treat every number as a **hypothesis**, not a settled fact (see §7).

### Game markets (walk-forward)
- Markets are ~efficient: **ROI −3.9%, but CLV +3.6%** — the model has edge vs the close but was **overconfident** (over-staking).
- **Shrink sweep:** shrinking model prob toward 0.5 improved both ROI and CLV; optimum ~0.30 (≈break-even ROI, +9.2% CLV, ECE degrades below 0.30). **Chose 0.35** live (between the break-even knee and a conservative hedge).

### Player props (grade-free CLV, then graded)
- **Grade-free CLV** (3,806 model bets): mean price CLV −0.024, mean line CLV +0.137, **60.4% beat the close.** By market — pitcher strikeouts **+0.049 CLV / 80% positive**, pitcher outs +0.046 / 75%, hits +0.022 / 68%, **total_bases −0.094 / 48%** (the drag).
- **Graded** (box scores, 174/179 games): record **1,902–1,744, ROI −5.2%, ECE 0.105**, calibration **overconfident in every bin** — the 0.9–1.0 bucket realized only **0.555** (near-certainty bets that are coin flips; almost all `total_bases`).

### Prop shrink sweep (graded, aggregate)
| shrink | n_bets | ROI | mean CLV | ECE |
|--:|--:|--:|--:|--:|
| 1.00 | 3,806 | −5.19% | −0.024 | 0.105 |
| 0.70 | 3,496 | −3.75% | +0.062 | 0.072 |
| **0.50** | 4,180 | **−3.35%** | +0.091 | **0.067** |
| 0.35 | 4,656 | −3.97% | +0.093 | 0.070 |
| 0.25 | 4,882 | −4.42% | +0.093 | 0.073 |
| 0.15 | 5,074 | −4.80% | +0.095 | 0.083 |

ROI is U-shaped, best at **0.50**; CLV plateaus positive; ECE best at 0.50.

### Per-market ROI by shrink (the decisive read)
| market | 0.15 | 0.25 | 0.35 | 0.50 | 0.70 | 1.00 |
|---|--:|--:|--:|--:|--:|--:|
| hits | −.015 | −.014 | −.015 | **−.012** | −.051 | −.067 |
| pitcher_outs | −.155 | −.057 | −.000 | +.028 | **+.059** | +.043 |
| pitcher_strikeouts | −.009 | +.025 | +.037 | **+.038** | +.011 | −.012 |
| total_bases | −.087 | −.091 | −.097 | −.103 | −.096 | −.087 |

**Interpretation that drives everything:** pitcher strikeouts/outs and hits carry real edge and calibrate around 0.5; **`total_bases` loses at *every* shrink** (shrinking toward 0.5 makes it *worse*) — the sim can't predict the extra-base (single/double/triple/HR) distribution game-to-game. No calibration lever rescues a no-edge market → the honest treatment is **exclusion**, not tuning.

### The one big caveat, restated
**Positive CLV, flat-to-negative ROI.** That's the *expected* signature of a real-but-thin edge fighting vig + variance over a tiny sample — promising, but **not yet "this makes money."** Everything is in-sample; nothing has seen out-of-sample data.

## 6. Current live configuration

`scripts/run_live_slate.py` as it stands:
- **Game markets:** confidence shrink **0.35** (`--mlb-shrink`).
- **Player props:** confidence shrink **0.50** (`--mlb-prop-shrink`); **`total_bases` excluded** (`--mlb-exclude-props`).
- Odds/stats: hourly GitHub Actions collection → private artifacts; paid data never committed.

## 7. Proven vs. unproven (the honest line)

- **Proven:** the pipeline works end-to-end and is point-in-time correct; there is a **positive CLV signal** on the tradeable markets; calibration discipline meaningfully improves both ROI and ECE.
- **Unproven:** **realized profitability** (best prop ROI is still −3.35%), and **out-of-sample durability** (all settings tuned on one 15-day window). The `WAGERING_SYSTEM_PLAN.md` defines **three gates** before real money: (1) out-of-sample validation with frozen settings, (2) forward paper-trading CLV, (3) small-stake execution proof.

## 8. Resources & constraints

- **Odds API budget:** ~**65,000 credits remaining** (started ~86,647; the props backfill spent ~21,400). Historical pulls carry a **10× multiplier**, so backfills are the expensive operation — budget them.
- **Banked archives (private artifacts, 90-day retention):** game-odds CLV archive; **prop CLV archive** = run `30181389616`, **136,418 rows**, 45 snapshots over the 15-day window.
- **Data window is small and singular:** 15 days, one season. The single biggest lever on confidence is *more, out-of-sample* data.
- **Football clock:** NFL regular season is ~6 weeks out (as of late July 2026) — the football port is deadline-driven.

## 9. Repo map (where to look)

- **Modelling:** `velocity/models/` (`simulate_baseball.py`, `game_mlb.py`, `props_mlb.py`, `mlb_build.py`)
- **Wagering:** `velocity/wagering/` (`slate.py`, `props_slate.py`, `bet_log.py`, `devig.py`, `edge.py`, `staking.py`, `live.py`)
- **Backtest:** `velocity/backtest/` (`mlb.py`, `props_mlb.py`, `archive.py`)
- **Scoring:** `velocity/report/` (`scorecard.py`, `results.py`)
- **Ingest:** `velocity/ingest/` (`mlb.py`, `theoddsapi.py`, `mlb_people.py`, `mlb_bullpen.py`)
- **Scripts:** `scripts/` (`run_live_slate.py`, `backtest_mlb.py`, `backtest_props_mlb.py`, `collect_historical_*.py`)
- **Workflows:** `.github/workflows/` (`backtest-mlb.yml`, `backtest-props-mlb.yml`, `collect-*.yml`)
- **Docs:** `docs/` (`DESIGN.md`, `WAGERING_SYSTEM_PLAN.md`, `PROJECT_STATUS.md` (this), `LAUNCH.md`, `BUILD*.md`, `DATA_*.md`)

Recent merged PRs (the prop-track arc): **#54** prop backtest CLI/workflow · **#55** pricer robustness (skip unsimulated players) · **#56** box-score grading · **#57** shrink sweep · **#58** per-market shrink + breakdown · **#59** live prop calibration (shrink 0.5 + exclude `total_bases`).

## 10. Open decisions & where fresh thinking helps most

These are the genuine forks — the highest-leverage places to think before jumping in:

1. **Is the edge real, or overfit?** Nothing is out-of-sample yet. The cheapest, highest-value next experiment is a **second-window walk-forward with frozen settings** (Gate 1). Everything else is speculative until this passes.
2. **Where to spend modelling effort:** fix the extra-base distribution so `total_bases` becomes tradeable, or accept exclusion and pour effort into markets that already show edge (pitcher props)? The per-market data says the pitcher side is where the model is genuinely good.
3. **MLB depth vs. football breadth:** keep hardening MLB (in-season, fast feedback), or start the NFL/NCAAF port now (deadline, original goal)? Current direction is **both in parallel**.
4. **What "real money" requires that we don't have:** execution/slippage, persistent bankroll state, monitoring/kill-switches, account-longevity strategy, compliance. The model is ~40% of a system (see plan §3, §11).
5. **Staking philosophy under thin, uncertain edge:** fraction of Kelly, caps, drawdown kill-switch, and how hard to haircut the (in-sample) edge until forward data justifies sizing up.

### The recommended first move
Run **Gate 1 (out-of-sample validation)** and **start the football sim port** in parallel: the first is the honest go/no-go on the whole thesis; the second is on a clock. Don't scale staking or expand markets before Gate 1 passes.

---

*Method note for whoever continues this: the discipline that got us here is worth keeping — point-in-time correctness (no leakage), CLV-first validation, calibration measured not assumed, and no setting trusted on data it was tuned on. Small, gated PRs; paid data stays in private artifacts.*
