# Hybrid architecture migration — audit and plan

Velocity becomes the **engine**, Excel becomes the **front end**:

```
ingest → ratings → Monte Carlo sim → wagering/DFS/intel
                                          ↓
                            velocity/export  →  datasets/exports/*.csv
                                          ↓
                          Excel Power Query → Dashboard · Bet Card · Games ·
                          Props · DFS Pool · DFS Optimizer · Curated Plays
```

Nothing above the export line is rebuilt. The simulation, the wagering gate,
the intel layer, the site and the workbook all keep working exactly as they
do today; the export layer is a new **read-only seam** bolted onto the frames
the pipeline already writes.

---

## 1. Current architecture

### 1.1 Layout

| Package | What it owns |
|---|---|
| `velocity/ingest/` | 22 provider adapters (network layer strictly separated from parsing) |
| `velocity/store/` | canonical pandera schema, parquet/duckdb IO, point-in-time access |
| `velocity/features/` | opponent-adjusted efficiency, pace, usage, starters, injuries, weather |
| `velocity/models/` | NFL/NCAAF game models, props, DFS projections, the shared Monte Carlo |
| `velocity/wagering/` | de-vig, edge/EV, fractional Kelly, portfolio sizing, slates, ledger |
| `velocity/intel/` | context signals → conviction → tiered, argued pick sets + publish gate |
| `velocity/dfs/` | DK salary ingest, scoring, exact optimizer, showdown, GPP, tiers |
| `velocity/backtest/` | walk-forward engine, wager lab, intel-tier backtest, archive |
| `velocity/eval/` | calibration, Brier/log-loss, ROI, CLV, ladders, correlation |
| `velocity/report/` | PNG cards, slate workbook, email, scorecards, monitors |
| `scripts/` | ~100 CLI entry points (collectors, builders, labs, the live runner) |
| `site/` | Evidence static site (Svelte components) over the built parquet |
| `app/` | Streamlit app (`app/streamlit_app.py`) |

403 Python modules, 200+ test modules, all offline and fixture-backed.

### 1.2 Ingest architecture

Every adapter splits into a **network layer** (a client) and a **pure parsing
layer** (payload → tidy frame), so the test gate never touches the network.

| Source | Module | Cost posture |
|---|---|---|
| The Odds API | `ingest/theoddsapi.py` | **paid**, 100k credits/mo, the only historical archive |
| BettingPros | `ingest/bettingpros.py` | **paid/keyed**, 5,000 calls/day, live-only (no archive) |
| FantasyPros | `ingest/fantasypros.py` | **keyed**, consensus player projections — the props prior |
| DraftKings | `dfs/salaries.py` | free-to-account, treated as licensed (artifacts only) |
| nflverse | `ingest/nfl.py` | free — NFL schedules, play-by-play with EPA |
| CFBD | `ingest/ncaaf.py` | free/keyed — college games, PBP, SP+ ratings |
| ESPN | `ingest/espn.py` | free — depth charts, injuries (all leagues) |
| Kalshi / Polymarket | `ingest/kalshi.py`, `polymarket.py` | public exchange prices |
| PrizePicks | `ingest/prizepicks.py` | pick'em lines |
| MLB Stats / Savant | `ingest/mlbstats.py`, `savant.py` | free |
| Open-Meteo | `features/weather.py`, `scripts/fetch_weather.py` | free forecast |

### 1.3 Simulation engine — the protected asset

`velocity/models/simulate.py` is the shared Monte Carlo every market prices
off. One draw of `(home_score, away_score)` per simulation feeds spread,
total, moneyline, team totals and derivatives, so every price on the board is
internally consistent by construction.

Around it sit the measured refinements, each promoted only by a lab:

* `models/level.py` — the scoring level ratings hang from (trailing weeks)
* `models/keynumbers.py` — football's margin lattice (3/7/10/14)
* `models/skew.py` — the right-skewed totals draw
* `models/drive.py` — drive-level scoring in sevens and threes
* `models/overtime.py`, `models/counts.py` — sports that cannot end level
* `models/residuals.py` — banked walk-forward residuals as the sim's shape

Entry points: `NFLGameModel` / `NCAAFGameModel` / `ScoresGameModel` →
`GameProjection` (`models/game_nfl.py`), which holds `mu_home`, `mu_away` and
the `GameSim` sample arrays, and exposes `p_home_win()`, `fair_spread()`,
`fair_total()`, `prob_home_cover()`, `prob_over()`.

Props: `models/props_football.py` / `props_ncaaf.py` / `props_mlb.py` /
`props_hr.py` decompose a consensus projection into a correlated per-game
distribution and simulate it alongside the game. Correlation is measured in
`eval/correlation.py` and applied in `models/props.py`, so a QB's yards and
his team's total move together in the same draw.

**This layer is not touched by the migration.** The export layer reads its
outputs; it does not re-simulate, re-fit or re-price anything.

### 1.4 Projection and wagering outputs

`scripts/run_live_slate.py` (4,034 lines) is the current orchestrator. Per
league per run it writes a stamped family of parquet into `--out`:

| File | Contents |
|---|---|
| `slate_{league}_{stamp}.parquet` | one row per staked bet (`game_id, market, side, point, book, price, p_model, p_fair, edge, stake, note, rule_tier, rule_record`) |
| `projections_{league}_{stamp}.parquet` | per game: `n_sims, mu_away, mu_home, p_home_win, fair_spread, fair_total` |
| `distributions_{league}_{stamp}.parquet` | pregame total/margin distributions |
| `games_{league}_{stamp}.parquet` | `game_id → home_team, away_team, kickoff` |
| `ratings_{league}_{stamp}.parquet` | the power ratings behind the fit |
| `weather_{league}_{stamp}.parquet` | what the forecast did to each total |
| `config_{league}_{stamp}.parquet` | what was live in the run |
| `publish_{league}_{stamp}.parquet` | the publish gate's verdicts |
| `portfolio_{league}_{stamp}.parquet` | correlation-aware sized stakes |
| `intel_{league}_{stamp}.parquet` | conviction tiers + rationale |
| `slate_{league}_{stamp}.xlsx` | the formatted workbook (`report/slate_xlsx.py`) |

Props ride the same run (`_prop_slate`), DFS is built by
`scripts/build_dfs_lineup.py` and `build_dfs_tiered.py` into the same folder.

`scripts/build_site_data.py` (1,248 lines) is the existing seam from *stamped
artifact families* to *stable table names* for the Evidence site. The export
layer is deliberately the same shape, targeting CSV instead of parquet.

### 1.5 Backtesting, calibration, CLV

* `backtest/engine.py` — walk-forward, point-in-time, never peeks forward
* `backtest/wagers.py` — the rule lab that promotes `wagering/tiers.py` rows
* `backtest/intel_tiers.py`, `backtest/props_football.py`, `dfs/backtest.py`
* `eval/metrics.py` — Brier, log-loss, ECE, ROI, hit rate, drawdown,
  `clv_stats`, `clv_by_tier`, `clv_by_market`
* `eval/ladders.py` — ladder calibration gate
* `intel/calibrate.py`, `scripts/calibrate_publish_gate.py`
* `backtest/archive.py` + `scripts/grade_archive.py` — the settled record chain

### 1.6 Front ends today

* **Evidence site** (`site/`) — private, behind Cloudflare Access; built by
  `build_site_data.py` and deployed from `live-slate.yml`.
* **Streamlit** (`app/streamlit_app.py`, `app/format_plays.py`) — a thin
  reader of the same slate frames. `streamlit` is **not** in
  `pyproject.toml` dependencies; it is an optional local viewer only, so
  nothing in the pipeline depends on it. Tests cover it via
  `tests/test_app_smoke.py`.
* **Workbook** (`report/slate_xlsx.py`) — a formatted, values-only `.xlsx`
  written per run. Styled for reading, *not* shaped for Power Query: stamped
  filename, display headers (`Model %`, `Stake $`), merged title rows.
* **Email** (`report/email_html.py`) and **social cards** (`report/*_png.py`).

### 1.7 Scheduling and GitHub Actions

Nineteen workflows. Cron-driven ones:

| Workflow | Cron (UTC) | Purpose |
|---|---|---|
| `collect-odds.yml` | `23 * * * *` | hourly board snapshot (9 credits/run) |
| `collect-bettingpros.yml` | `13 */3 * * *` | prop/board snapshot, ~45 calls/run |
| `collect-football-props.yml` | `19 15,22 * * *` | twice-daily prop boards |
| `collect-fantasypros.yml` | `37 12 * * 2` + weekly | consensus projections |
| `collect-dk-salaries.yml` | `31 15 * * *` | DK salary snapshot |
| `collect-injuries.yml` | `37 15 * * *`, `33 16 * * 0` | ESPN injuries |
| `collect-exchanges.yml` | `*/3h at :07` | Kalshi/Polymarket |
| `collect-kalshi-candles.yml` | `29 12 * * *` | settled candles |
| `dfs-slate.yml` | `:41`, Sat/Sun/Mon/Thu windows | DFS lineups |
| `live-slate.yml` | `53 11,14,17,20,23` Sat … | **the slate + site deploy** |
| `refresh-datasets.yml` | `29 9 * * *` | committed dataset refresh |
| `consolidate-exchanges.yml` | `47 9 * * 1` | weekly exchange roll-up |
| `model-drift.yml` | `31 6 1,15 * *` | drift check |

`live-slate.yml` runs **last** in its hour (`:53`) because it consumes every
other workflow's artifact. `tests/test_workflow_schedules.py` already pins the
minute map so a schedule edit cannot drift silently.

### 1.8 Data flow, end to end

```
providers ──collectors (Actions, cron)──► Actions artifacts (paid data)
                                        └► datasets/*.parquet (free data, in git)
                                             │
                          live-slate.yml ────┤ fit ratings → 100k-sim projections
                                             │ → de-vig → edge → Kelly → portfolio
                                             │ → intel tiers → publish gate
                                             ▼
                              artifacts/slate/*_{stamp}.parquet + .xlsx
                                             │
                      build_site_data.py ────► site/sources/.../*.parquet → Evidence
```

---

## 2. Target architecture

One new package and one new orchestrator; everything else is unchanged.

```
velocity/export/
    meta.py       generated_at / season / week stamping, CSV writer
    artifacts.py  newest-stamp-per-league artifact discovery (the read seam)
    games.py      games.csv
    props.py      props.csv
    dfs.py        dfs.csv + dfs_optimizer.csv
    plays.py      plays.csv
    dashboard.py  dashboard.csv
    workbook.py   velocity.xlsx — all six, prebuilt, for Excel without Power Query

velocity/wagering/plays.py   curated A+/A/B/Watch engine with explanations
velocity/run_weekly.py       the one-command orchestrator
docs/EXCEL_SETUP.md          Power Query setup + workbook structure
```

Output: `datasets/exports/{games,props,dfs,dfs_optimizer,plays,dashboard}.csv`
— **stable filenames, stable column order**, every row carrying
`generated_at`, `season`, `week`.

Read path: `velocity.export.artifacts` finds the newest stamp per league in
the slate artifact folder — the same rule `build_site_data.py` uses — so the
export layer never re-runs the engine and cannot change a number.

---

## 3. Gap analysis

| Need | Today | Gap |
|---|---|---|
| Excel-loadable files | stamped `.parquet` + styled `.xlsx` | stamped names break a Power Query source; merged title rows and display headers (`Model %`) are not a table |
| Stable schema | frames shift as the model does | exports need a pinned, test-enforced column contract |
| `season` / `week` on every row | only on some frames | stamp centrally in `export/meta.py` |
| Cover / over probability per game | inside `GameProjection` at run time | present in `projections_*.parquet` as `p_home_win`/`fair_*`; cover/over vs. the **market** number must be recomposed from the slate rows |
| Prop percentiles (75/90/99) | simulated, not persisted | the prop sim's percentiles are not written today — export computes them from the staked prop rows where available and leaves them null otherwise, rather than inventing numbers |
| DFS ownership / leverage / stack rating | `dfs/gpp.py` has the machinery | not exported as a flat pool |
| Curated A+/A/B/Watch | two separate tierings (`wagering/tiers.py` rule tiers A/B; `intel` A/B/C/flagged) | needs one composed public tiering with explanation text |
| One-command weekly run | ~100 scripts, orchestrated by YAML | `run_weekly.py` |
| Latency | see §5 | the binding constraint is **not** in our code |

---

## 4. Risks

1. **Licensing / ToS.** `datasets/` is explicitly opted *back into* git
   (`.gitignore`: `!datasets/**`), but exports are derived from paid feeds
   (The Odds API prices, BettingPros lines, DK salaries). Writing them under
   `datasets/exports/` without an ignore rule would commit exactly the data
   the repo quarantines to Actions artifacts.
   **Mitigation:** `datasets/exports/` is added to `.gitignore` with a
   `.gitkeep` exception, and a test asserts the ignore rule holds.
2. **Silent schema drift.** Excel breaks loudly and late.
   **Mitigation:** the column contract is a constant per exporter, asserted
   by tests; exporters emit a header-only CSV rather than a differently
   shaped one when there is no data.
3. **Fidelity.** Any rounding or re-derivation in the export layer is a second
   source of truth.
   **Mitigation:** exports are pure projections of existing frames. No
   re-simulation, no re-pricing, no re-staking. Where a requested column has
   no honest source it is emitted **empty**, never guessed.
4. **Latency expectations.** See §5 — no schedule edit removes a queue delay
   imposed by GitHub.
5. **Capability loss.** Nothing is removed. Streamlit, the site, the workbook,
   the email and the cards all keep their current entry points.

---

## 5. Latency — measured root cause

The reported 2–4 hour delay is **already measured** in
[`docs/LAUNCH.md`](LAUNCH.md) "What the schedule really does", off the Actions
run list for 2026-09-16..19:

* **`live-slate.yml` starts 1h48–3h00 after its cron.** Seven consecutive
  scheduled runs: +2:52, +2:01, +3:00, +1:56, +2:21, +1:48, +2:06.
* **`collect-odds.yml` is hourly on paper, ~4.2-hourly in practice** — 40 runs
  in seven days, gaps of 1.7–7.5 hours. Most hourly slots are *dropped*, not
  delayed.
* **`workflow_dispatch` runs start within a minute.** The delay is specific to
  the `schedule` event.

This is GitHub's documented best-effort behaviour for scheduled workflows on
a shared queue. **No cron minute, no caching change and no code optimisation
in this repository removes it.** Claiming a "15-minute market refresh" on the
`schedule` event would be a schedule that does not describe reality.

What *can* be done, and is done in Phase 7:

1. **Compensate** — windows spaced so that across the measured delay range a
   run lands before each kickoff block, with a neighbour covering a dropped
   run (already in place for `live-slate.yml`).
2. **Shorten the run** — a league with no game in its window no longer fits
   ratings (already in place; NCAAB's fit alone was 11 of 30 minutes).
3. **Make the fast path explicit and documented** — `workflow_dispatch` is
   the sub-minute path; `run_weekly.py` makes it a single command.
4. **Separate export from simulation** — the export layer reads banked
   artifacts, so refreshing the Excel-facing CSVs costs seconds, not a
   30-minute engine run.
5. **Respect the caps** — BettingPros 5,000/day (current use <8%), The Odds
   API ~38k of 100k/month. Any increase in cadence is priced against these
   before it ships.

---

## 6. Migration order

| Phase | Change | Gate | State |
|---|---|---|---|
| 1 | this document | — | done |
| 2 | read-only review of `models`/`wagering`/`backtest`/`eval` | no edits made | done |
| 3 | `velocity/export/` + `datasets/exports/` + `.gitignore` rule | `tests/test_export_*.py` | done |
| 4 | `velocity/wagering/plays.py` curated engine | `tests/test_curated_plays.py` | done |
| 5 | DFS pool + `dfs_optimizer.csv` (cash/single/GPP/ceiling) | `tests/test_export_dfs.py` | done |
| 6 | Excel compatibility contract | `tests/test_export_contract.py` | done |
| 7 | latency audit + workflow changes | `tests/test_export_workflow.py` | done |
| 8 | `velocity/run_weekly.py` | `tests/test_run_weekly.py` | done |
| 9 | `docs/EXCEL_SETUP.md` | — | done |
| 10 | full gate: `pytest`, `ruff`, `mypy` | green before every merge | enforced |

Each phase is one commit on `claude/epic-euler-fjzehn`, so any single step can
be reverted without unwinding the rest.

## 7. What Phase 2 found, and why nothing moved

The brief's instruction for the simulation engine was to review it and not
rewrite it. The review is §1.3 above. **No file under `velocity/models/`,
`velocity/backtest/` or `velocity/eval/` was modified by this migration**, and
the two edits made to `velocity/wagering/` are additive:
`tiers.rule_named()` (a lookup from a banked tier letter back to the rule's
measured numbers, so no downstream reader has to parse a record string) and
the new `wagering/plays.py`.

Two runner scripts gained one persistence step each, and both bank a frame
the run had already computed and was discarding:

* `scripts/run_live_slate.py` → `prop_dist_{league}_{stamp}.parquet`, the prop
  sim's own quantiles per player and market.
* `scripts/build_dfs_lineup.py` → `dfs_dist_{league}_{stamp}.parquet`, the
  per-sim DK-point arrays the GPP tail scorer already builds.

Both are wrapped best-effort and cannot fail their runner. Neither changes a
price, a probability or a stake.

## 8. What is deliberately still empty

Three export columns have no honest source in this repository today, and are
written blank rather than filled:

| Column | Why | What would fill it |
|---|---|---|
| `dfs.ownership` | DraftKings does not publish pre-lock ownership and nothing here models it | pass a projection into `export_dfs(..., ownership=...)` |
| `dfs.leverage_score` | leverage against an unknown field is not a quantity | the same projection |
| `props.*_percentile` (when absent) | a run that banked no `prop_dist_*` frame has no distribution to quote | re-run the slate with `--out` set |

The alternative — a fitted curve through a mean, or a flat mid-scale default —
would produce a column that always has a number and never has a source. Four
columns nobody can source beat four columns nobody can trust.
