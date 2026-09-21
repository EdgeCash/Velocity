# The workflow — how Velocity runs, end to end

What the system does, in what order, on whose clock, and what you do about
it. This is the operating manual; the deep dives are linked from each
section.

One sentence: **Velocity simulates, the export layer writes flat files, and
Excel is the front end — with every run stating whether the board it
produced is actually usable.**

```
providers ──collectors (cron)──► Actions artifacts (paid data, never in git)
                               └► datasets/*.parquet (free data, committed)
                                        │
                     live-slate.yml ────┤ fit ratings → 100k-sim projections
                                        │ → de-vig → edge → Kelly → portfolio
                                        │ → intel tiers → publish gate
                                        ▼
                          artifacts/slate/*_{stamp}.parquet
                                        │
                        velocity/export ┤ (read-only; re-simulates nothing)
                                        ▼
                    datasets/exports/*.csv  +  velocity.xlsx  +  readiness.csv
                                        │
                        ┌───────────────┴───────────────┐
                        ▼                               ▼
              Excel (desktop, Power Query)      Excel (iPad, one workbook)
```

---

## 1. What changed

| | Before | Now |
|---|---|---|
| Output | stamped parquet + a styled per-league workbook, inside Actions artifacts | the same, **plus** six stable-named CSVs and one complete workbook |
| Front end | the Evidence site (private, behind Access) | the site, **plus** Excel on desktop or tablet |
| Running a week | ~100 scripts orchestrated by YAML | `python -m velocity.run_weekly` |
| "Did it work?" | exit code 0 | **READY / DEGRADED / NOT READY**, in three places |
| Curated card | two internal tierings (rule tiers, intel tiers) | one public **A+ / A / B / Watch**, each play stating its case |

Nothing was removed. The simulation, wagering gate, intel layer, site,
email and social cards all keep their existing entry points.

---

## 2. The engine (unchanged)

`live-slate.yml` is the run that matters. Per league it fits ratings on
committed history, simulates every game 100,000 times, prices every market
off the same draws, de-vigs, measures edge, stakes with fractional Kelly,
sizes the portfolio, applies the intel layer and the publish gate — then
banks a stamped family of parquet:

| Family | Holds |
|---|---|
| `games_{lg}_{stamp}` | `game_id → teams, kickoff` |
| `projections_{lg}_{stamp}` | `mu_away/mu_home, p_home_win, fair_spread, fair_total` |
| `distributions_{lg}_{stamp}` | the **exact pmf** of each game's total and margin over all 100k draws |
| `slate_{lg}_{stamp}` | one row per staked bet |
| `slate_{lg}_props_{stamp}` | one row per staked prop |
| `prop_dist_{lg}_{stamp}` | per-player prop quantiles (mean, median, 75/90/99) |
| `dfs_pool_{lg}_{stamp}` | every priced DK player |
| `dfs_dist_{lg}_{stamp}` | per-player DK-point quantiles |
| `intel_{lg}_{stamp}` | conviction, tier, rationale |
| `weather_{lg}_{stamp}` | the forecast and what it did to each total |
| `record_{lg}_{stamp}` | the settled bet record |

**None of this is touched by the export layer.** No file under
`velocity/models/`, `velocity/backtest/` or `velocity/eval/` was modified by
the migration.

---

## 3. The export layer

`velocity/export/` is a **read-only projection** of the frames above. It
fits nothing, simulates nothing, re-prices nothing. Two consequences worth
knowing:

* **The same numbers reach every surface.** `cover_probability` and
  `over_probability` are counted off the persisted pmf, pushes excluded from
  the numerator exactly as the grader does — the sim's own numbers, not an
  approximation of them. A test asserts the two routes agree to 1e-12.
* **Re-exporting is seconds, not a run.** `--steps export` rebuilds every
  file from banked artifacts without touching the engine, which is why the
  export sits inside the run that made the numbers rather than in its own
  scheduled workflow.

### What it writes, into `datasets/exports/`

| File | One row per | Feeds |
|---|---|---|
| `games.csv` | game on the board | Games |
| `props.csv` | staked/papered prop | Props |
| `team_totals.csv` | **side** (2 per game) | Team Totals |
| `dfs.csv` | DK player | DFS Pool |
| `dfs_optimizer.csv` | DK player, one column per contest type | DFS Optimizer |
| `plays.csv` | curated play (A+/A/B/Watch) | Curated Plays |
| `dashboard.csv` | one metric (long format) | Dashboard |
| `readiness.csv` | one surface | the run's verdict |
| `velocity.xlsx` | — | **all of the above, prebuilt** |

Contract: stable filenames, stable column order, `lower_snake_case` headers,
`generated_at`/`season`/`week` on every row, a header-only file when there
is no data, UTF-8 BOM, LF endings, byte-stable across identical runs.
Enforced by `tests/test_export_contract.py`.

**Blank means unknown, never zero.** A column with no honest source exports
empty — `ownership` always, unless you supply a projection.

### Sign conventions

* `spread_edge` positive = value on the **home** side.
* `total_edge` positive = value on the **over**.
* `cover_probability` = P(**home** covers `market_spread`).
* `team_total_edge` positive = value on that side's **over**.

---

## 4. The curated card

`velocity/wagering/plays.py` composes the two tierings that already existed
rather than adding a third:

* **Rule tiers** — the wager lab's walk-forward record of the *rule*
  (e.g. NFL unders at 4+: 56.2% over 299 bets, 11 of 15 seasons).
* **Intel tiers** — this game's context (matchups, form, rest, injuries).
  Can veto; never promotes.

| Tier | Meaning |
|---|---|
| **A+** | high confidence **and** a rule with a measured record behind it |
| **A** | high confidence, no promoted rule for that market yet |
| **B** | staked, positive edge, lower confidence |
| **Watch** | seen and **not bet** — vetoed, papered, zero-staked, or no positive edge |

`Watch` is not a failure state. A play the system looked at and declined is
more use than a silent absence.

`confidence` (0–10) blends edge, rule record (win rate scaled by seasons
cleared) and intel conviction. Absent components are dropped and the
remaining weights renormalized — a play with no context read is scored on
what *is* known, not penalized for the silence. The weights and tier lines
live in one frozen `PlaysConfig`; they are **policy, not measurement**.

Every play carries its argument on one line:

> Under 41.5 — Model total 37 · +4.5 points of disagreement · model 48.8% ·
> fair 43.6% · Kelly 0.17% · rule A (unders 4+): 56.2% over 299 bets, 11 of
> 15 seasons · confidence 5.8 · outs lean under (…)

---

## 5. Readiness — the part that makes a run trustworthy

A run that **finished** and a board you can **bet before kickoff** are
different claims. Until recently only the first was ever stated: a run could
exit 0, go green and upload an artifact with no props, no DFS pool and no
weather, and nothing said so.

`velocity/export/readiness.py` judges each surface *and the timing*:

| Verdict | Meaning | Job |
|---|---|---|
| **READY** | every surface present, numbers fresh | green |
| **DEGRADED** | usable card, something optional missing | green |
| **NOT READY** | a required surface missing, **or** numbers stale with too little time to redo | **red** |

Required = `games`, `projections`, `market lines`. Optional = props, team
totals, DFS pool, weather, curated plays, settled record.

The timing distinction is the point. A five-hour-old board with ten hours to
kickoff is DEGRADED — redo it. The same board forty minutes out is NOT
READY, because a scheduled rerun starts 1h48–3h00 late plus 20–30 minutes to
run, so there is nothing left to do but know. Stale and missing stay
separate complaints for the same reason.

Stated in three places, because you are not always in the same one:

1. **`readiness.csv`** — one row per surface, with why.
2. **The workbook's first tab** — a colour-coded `Run status` block leading
   the Dashboard, and a line on Read Me.
3. **A CI gate step** — fails the job on NOT READY. The failing job *is* the
   alert: GitHub emails the repository owner on a failed run by default. It
   runs **after** the artifact upload, so a red gate never costs the
   artifact it is complaining about.

Thresholds: 240 minutes staleness, 90 minutes kickoff window. Both are
judgement, in one place, with tests pinning the behaviour. Retune once real
game days tell you what is actually too old.

---

## 6. The clocks

**Every scheduled workflow starts late.** Measured across seven consecutive
runs: **+1h48 to +3h00**. `collect-odds.yml` is hourly on paper and roughly
4.2-hourly in practice because GitHub drops most slots outright.
`workflow_dispatch` starts **within a minute**. See
[`docs/LATENCY_AUDIT.md`](LATENCY_AUDIT.md).

| Workflow | Cron (UTC) | Produces |
|---|---|---|
| `collect-odds.yml` | `23 * * * *` | the board archive — **the market side of the card** |
| `collect-bettingpros.yml` | `13 */3 * * *` | line/prop snapshots |
| `collect-football-props.yml` | `:19`, **every slate window** | prop boards |
| `collect-fantasypros.yml` | `37 12` Tue/Thu/Sat/Sun | consensus projections |
| `collect-dk-salaries.yml` | `31 15 * * *` | DK salaries |
| `collect-injuries.yml` | `37 15 * * *`, `33 16 * * 0` | ESPN injuries |
| `collect-exchanges.yml` | `7 */3 * * *` | Kalshi/Polymarket |
| `dfs-slate.yml` | `:41` Sat/Sun/Mon/Thu | DFS lineups |
| **`live-slate.yml`** | `:53` Sat 11,14,17,20,23 · Sun 11,14,17,20 · Mon/Thu/Fri 17,20 · Tue/Wed 17 | **the slate, the exports, the site** |
| `refresh-datasets.yml` | `29 9 * * *` | committed datasets |
| `model-drift.yml` | `31 6 1,15 * *` | drift check |

Props collect on the **same windows as the slate, 34 minutes ahead** —
both queue on the same platform, so riding the same delay lands the board
inside the 75-minute freshness bar rather than at a wall-clock time the
slate has drifted away from ([`docs/DECISIONS.md`](DECISIONS.md) D1).

`live-slate.yml` runs **last in its hour** (`:53`) because it consumes every
other workflow's artifact. `tests/test_workflow_schedules.py` pins the
minute map so an edit cannot drift silently.

**`refresh-exports.yml` has no cron on purpose.** It rebuilds the CSVs and
workbook from banked artifacts in ~2 minutes. A scheduled version would
start up to three hours late and hand back a board *older* than the one
already in the last run's artifact — worse than not refreshing. Its triggers
are `workflow_dispatch` and `workflow_run` on the DFS build.

---

## 7. The game-day routine

**~3 hours before the first kickoff you care about:**

1. **Tap the "Velocity — Run" shortcut** (or Actions → Live slate → Run
   workflow). Dispatched runs start within a minute; scheduled ones do not.
   Shortcut setup: [`IOS_SHORTCUTS.md`](IOS_SHORTCUTS.md).
2. Wait ~10–15 minutes.
3. Check the **Board readiness gate** step: green means usable.
4. Download the artifact → open `velocity.xlsx` → read **Run status** at the
   top of the Dashboard.

**If you only do one thing, do step 1.** Every measured failure so far
traces to a scheduled run starting late or being dropped.

To check a board you already have without spending a run: **Actions →
Refresh Excel exports → Run workflow** (~2 minutes, no re-simulation).

Full routine, including what to do per missing surface:
[`docs/GAME_DAY.md`](GAME_DAY.md).

---

## 8. Getting it onto a device

**iPad / iPhone / Android** — these have **no Power Query**, so the CSV
route cannot be assembled there. Use `velocity.xlsx`: one file, seven tabs,
already populated. Three routes — email (set `MAIL_USERNAME`,
`MAIL_PASSWORD`, `MAIL_TO` once and every run arrives in your inbox),
Actions artifact download, or a dispatched refresh.
[`docs/EXCEL_IPAD.md`](EXCEL_IPAD.md).

**Desktop Excel** — connect the six CSVs once via Power Query; every later
refresh is a button. [`docs/EXCEL_SETUP.md`](EXCEL_SETUP.md).

Both read the same numbers from the same run. Neither is a fork of the other.

---

## 9. Running it yourself

```bash
python -m velocity.run_weekly --slate-dir artifacts/slate            # the week
python -m velocity.run_weekly --steps export --odds-dir artifacts/odds
python -m velocity.run_weekly --dry-run                              # the plan
```

Four steps — `refresh`, `slate`, `dfs`, `export` — each logging one JSON
object. A failing step does not take the rest with it: a props outage should
cost the props, not the board. Exit non-zero if any step failed.

Key flags: `--leagues`, `--bankroll`, `--odds-dir` (**without it the card
can only show market numbers for games that earned a bet**), `--salaries`
and `--fp` (the DFS step needs both), `--strict`.

---

## 10. The data posture

`datasets/exports/` is **gitignored**. The CSVs derive from paid and
licensed feeds — The Odds API prices, BettingPros lines, DraftKings salaries
— which the repo quarantines to Actions artifacts. `datasets/` is otherwise
opted back into git, so the ignore sits *after* that opt-in and is written
`exports/*` rather than `exports/` (excluding the directory would stop git
descending into it). A test holds both facts.

Keep the workbook local. Do not commit it; do not publish the CSVs.

---

## Related

| | |
|---|---|
| [`GAME_DAY.md`](GAME_DAY.md) | the pre-kickoff routine and per-surface fixes |
| [`EXCEL_IPAD.md`](EXCEL_IPAD.md) | tablet route |
| [`EXCEL_SETUP.md`](EXCEL_SETUP.md) | desktop route |
| [`LATENCY_AUDIT.md`](LATENCY_AUDIT.md) | where the 2–4 hours goes |
| [`DECISIONS.md`](DECISIONS.md) | the owner's standing decisions, and what implements each |
| [`IOS_SHORTCUTS.md`](IOS_SHORTCUTS.md) | one-tap run and status, from the home screen |
| [`PHASE13_MOBILE.md`](PHASE13_MOBILE.md) | proposal: operating from an iPad |
| [`PHASE13_STAGE2_CLOUDFLARE.md`](PHASE13_STAGE2_CLOUDFLARE.md) | design: the private dashboard (not built) |
| [`HYBRID_MIGRATION_PLAN.md`](HYBRID_MIGRATION_PLAN.md) | architecture, gaps, risks |
| [`DESIGN.md`](DESIGN.md) · [`WAGERING.md`](WAGERING.md) · [`INTEL.md`](INTEL.md) | the engine |
