# Velocity — MLB Decommission & Football Cutover Plan

**Status:** Cutover plan (v0.1)
**Companion to:** [`docs/BUILD.md`](BUILD.md) (the safe build loop every phase
below follows), [`docs/BUILD_MLB.md`](BUILD_MLB.md) (the plan this one retires),
and [`docs/BACKTEST_NFL.md`](BACKTEST_NFL.md) /
[`docs/BACKTEST_NCAAF.md`](BACKTEST_NCAAF.md) (the honest starting point for
the football models).
**Principle:** MLB did its job — it proved the ingest → model → de-vig → edge →
Kelly → grade → report pipeline end-to-end on a live daily board. Now we retire
it cleanly and point the whole machine at the sport this repo was designed for,
before the 2026 season starts.

---

## 0. The clock

Today is **2026-08-09**. NCAAF Week 0 kicks off ~**Aug 22**, Week 1 over Labor
Day weekend (~Sep 3–7), and the NFL opener is ~**Sep 10**. That gives us:

- **~2 weeks** until the first live NCAAF board,
- **~4 weeks** until the NFL board,
- and books are already posting season-win totals, Week 1 lines, and early
  props — so every day the football CLV archive isn't collecting is data lost.

The ordering below is driven by that clock: stop MLB spend immediately, get the
football *collection* loops running this week (archives compound), and use the
remaining preseason for the model work.

---

## 1. Audit — what the repo is today

Three buckets, verified file-by-file.

### 1a. Sport-agnostic core — untouched by this cutover

| Layer | Files | Note |
|---|---|---|
| Store | `store/schema.py`, `store/io.py`, `store/pit.py` | canonical schema + point-in-time access |
| Wagering math | `wagering/devig.py`, `edge.py`, `staking.py`, `portfolio.py`, `bet_log.py`, `odds.py` | pure odds/Kelly math |
| Backtest | `backtest/engine.py`, `backtest/archive.py` | walk-forward engine |
| Eval | `eval/metrics.py` | Brier/calibration/CLV metrics |
| Util | `util/seed.py` | determinism |
| Odds ingest | `ingest/odds.py`, `ingest/bettingpros.py`, `scripts/collect_theoddsapi.py`, `scripts/collect_bettingpros.py` | league-parameterized |

This is the machinery MLB existed to prove. It carries over as-is.

### 1b. Football assets — already built and waiting

| Asset | State |
|---|---|
| `ingest/nfl.py` | nflverse adapter (schedules, pbp w/ EPA, weekly rosters) — live |
| `ingest/ncaaf.py`, `scripts/pull_cfbd_lines.py`, `scripts/build_ncaaf_boxscores.py` | CFBD adapter + historical build |
| `models/game_nfl.py`, `models/game_ncaaf.py` | EPA-ratings game models over the shared `models/simulate.py` Monte Carlo |
| `models/props.py`, `features/player.py` | football prop decomposition (volume × share × efficiency, NegBin, correlated sim) — **not yet wired to live data** |
| `features/team.py`, `scores.py`, `priors.py` | opponent-adjusted ratings |
| `datasets/nfl/` | committed: 232k plays + 1,424 games, 2021–2025 |
| `datasets/ncaaf/` | committed: games + boxscores 2002–2025 |
| `.github/workflows/live-slate.yml` | football live slate, cron already on Thu/Sat/Sun/Mon game days |
| `.github/workflows/collect-odds.yml` | hourly `nfl ncaaf` snapshot — already football-only |
| `.github/workflows/collect-bettingpros.yml`, `collect-fantasypros.yml` | NFL/NCAAF collectors — live |
| `docs/BACKTEST_NFL.md`, `docs/BACKTEST_NCAAF.md` | real walk-forward results (see §4) |

### 1c. MLB surface to decommission

**Delete outright** (own the sport, nothing else imports them):

- Workflows: `live-slate-mlb.yml`, `collect-mlb-props.yml`, `backtest-mlb.yml`,
  `backtest-props-mlb.yml`, `collect-historical-props.yml`
- Scripts: `collect_mlb_props.py`, `backtest_mlb.py`, `backtest_props_mlb.py`
- Ingest: `mlb_stats.py`, `mlb_people.py`, `mlb_context.py`, `mlb_weather.py`,
  `mlb_advanced.py`
- Models: `game_mlb.py`, `props_mlb.py`, `mlb_build.py`, `simulate_baseball.py`,
  `run_environment.py`
- Features: `baseball.py`
- Backtest: `backtest/mlb.py`, `backtest/props_mlb.py`
- Report: `park_factors.py`
- Tests + fixtures: every `test_mlb_*` / `test_*_mlb*` /
  `test_simulate_baseball` / `test_park_factors` /
  `test_collect_historical_props` file and the `mlb_*.json` fixtures

**Untangle** (shared code with MLB branches):

- `scripts/run_live_slate.py` — drop the `mlb` league choice, `--mlb-shrink`,
  `--mlb-prop-shrink`, `--mlb-exclude-props`, the weather/run-environment hook,
  the MLB prop-slate and parlay blocks
- `scripts/grade_yesterday.py`, `grade_archive.py`, `render_slate_email.py` —
  remove MLB grading paths
- `wagering/slate.py`, `props_slate.py`, `parlay.py`, `live.py` — strip the
  F5/NRFI derivative markets and MLB correlation groups; **keep** the generic
  props-slate and correlated-parlay engines (they're the template for SGP/
  team-total work in football)
- `velocity/report/*` (`cards`, `card_html`, `social`, `social_png`,
  `email_html`, `sim_check`, `assets`) — remove pitcher/park/F5 content;
  the rendering machinery stays
- `app/streamlit_app.py`, `app/format_plays.py` — reboard for football
- `.github/workflows/collect-historical-odds.yml` — keep, repoint default
  league from `mlb` to `nfl ncaaf`

**Keep as record:** `docs/BUILD_MLB.md` and the MLB backtest write-ups stay in
`docs/` with a "decommissioned — code lives at tag `mlb-final`" banner. Docs
don't run; history is cheap.

---

## 2. Delete, don't mothball (the one real decision)

Recommendation: **remove the MLB code, behind a tag** — not keep it dormant.

- Dormant code still costs: it's in every `pytest`/`mypy`/`ruff` run, every
  refactor of the shared schema has to keep it compiling, and its live-data
  adapters rot silently in the offseason anyway.
- Git history is the archive. Tag the last green MLB commit as **`mlb-final`**;
  reviving MLB next April is `git checkout mlb-final -- <paths>` plus a
  re-integration pass — cheaper than eight months of dragging dead weight.
- The genuinely valuable MLB artifacts are not the code: they're the **lessons**
  (per-market confidence shrink, prop-market exclusions, CLV archive
  discipline, the grade→record→email loop), and those are carried forward in
  §6 as football work items.

---

## 3. Phases

Each phase is a branch → tests → real-data verify → PR, per `BUILD.md §1`.

### Phase 0 — Freeze MLB (immediate, ~1 day) — ✅ DONE 2026-08-09

1. Remove the `schedule:` blocks from `live-slate-mlb.yml`,
   `collect-mlb-props.yml`, `collect-historical-props.yml` (keep
   `workflow_dispatch` so a final manual run is possible).
2. Run one final grading pass so the MLB record closes cleanly; download the
   last CLV-archive artifacts if we want them off-platform.
3. Tag the commit **`mlb-final`**.

**DoD:** zero scheduled MLB workflow runs; final MLB record graded and archived.

*Execution notes:* the freeze commit deschedules both cron'd MLB workflows
(`collect-historical-props.yml` was already dispatch-only). The `mlb-final`
tag exists locally on the last full-MLB commit (`6cbb566`) but the session's
push credentials are branch-scoped, so pushing it is a one-command manual step:
`git push origin mlb-final`. The final grading pass happens automatically with
the last scheduled `live-slate-mlb` run before this branch merges (dispatch
remains available on `main` until then).

### Phase 1 — Excise (~2–3 days) — ✅ DONE 2026-08-09

1. Delete the "delete outright" list from §1c.
2. Untangle the shared files: every `league == "mlb"` branch, flag, and import
   goes; the generic engines stay.
3. Repoint `collect-historical-odds.yml` to `nfl ncaaf`.
4. Banner the MLB docs.

**DoD:** `pytest`, `ruff`, `mypy` green; `grep -ri "mlb\|baseball"` hits only
`docs/` and this file; `run_live_slate.py --league nfl --data datasets/nfl`
produces a slate from a snapshot fixture.

*Execution notes:* all four DoD items verified (the grep also allows two
one-line "MLB-era backtest" history comments in `wagering/`). Beyond the plan:
the correlated-parlay engine was retargeted to the football sim (game legs live
now, prop legs return with Phase 3), the prop-slate pricer was generalized
behind a `PropDistributions` protocol with `PROP_MARKETS`/The Odds API keys
switched to football stats, and the report layer's MLB-content card/social/
sim-check renderers were deleted rather than untangled — they were built around
pitchers/parks/F5 and Phase 4 rebuilds them for football from `mlb-final`. The
grade→record→email chain survives sport-agnostic and is wired to nflverse/CFBD
finals.

### Phase 2 — Football data loops on, archives banking (week 1) — ✅ BUILT 2026-08-09 (needs live verification)

The MLB lesson: the CLV archive is only as good as the days it was collecting.
Start these **now**, weeks before kickoff.

1. **Season-refresh job:** weekly workflow that pulls current-season nflverse
   schedules/pbp/rosters and CFBD games/lines, rebuilding `datasets/` (2026
   rows will start appearing in preseason).
2. **Football props collection:** successor to `collect-mlb-props.yml` —
   The Odds API + BettingPros NFL/NCAAF player-prop markets (pass/rush/rec
   yards, receptions, anytime TD), snapshotted into the same private-artifact
   CLV archive pattern.
3. Verify the existing `collect-odds` / `collect-bettingpros` /
   `collect-fantasypros` crons are green and capturing 2026 markets.
4. **DK salary snapshots** (see §5a) — start banking alongside the props
   archive; the collector is small and the history compounds.

**DoD:** every collector has a green scheduled run with 2026-season rows in the
artifact; props archive banking daily.

*Execution notes:* built and offline-tested on the branch; the DoD's green
**scheduled** runs can only happen on `main` with secrets, so after merge the
operator should manually dispatch each new workflow once as verification:

- `refresh-datasets.yml` (Tue 09:00 UTC) — `scripts/refresh_datasets.py` tops
  up `datasets/` with the current season's played games (nflverse for NFL,
  CFBD REST for NCAAF — needs the `CFBD_API_KEY` secret) and commits the
  changed parquets. Idempotent per-season replace; if branch protection blocks
  the github-actions bot's push, allow bypass or convert the push to a PR.
- `collect-football-props.yml` (15:00/22:00 UTC daily) —
  `scripts/collect_football_props.py` banks NFL+NCAAF prop boards (raw
  per-event JSON + normalized `PropLines` parquet) to private artifacts.
- `collect-dk-salaries.yml` (15:00 UTC daily) — `velocity/dfs/salaries.py` +
  `scripts/collect_dk_salaries.py` bank every DK draft group's salaries (raw
  lobby/draftables JSON + normalized `Salaries` parquet) to private
  artifacts. Unauthenticated; no secret.

The pre-existing `collect-odds` / `collect-bettingpros` / `collect-fantasypros`
crons were already football-configured; verifying they're green with 2026
markets is an operator check on `main`, not a code change.

### Phase 3 — Model work (weeks 1–4, the real work — see §4)

1. Wire `models/props.py` to live inputs: FantasyPros projections +
   nflverse rosters/depth for volume shares; injuries reprice the share room
   via `features/player.py`.
2. Port the **per-market confidence-shrink calibration** proven on MLB props
   (shrink 0.5, market exclusions) to football props, as a sweep + backtest,
   not a guess.
3. Derivatives: team totals, 1H/1Q markets — the football analog of the
   F5/NRFI finding that edge lives in soft low-limit markets, not the
   full-game close.
4. Re-run walk-forward game backtests on refreshed data; the NCAAF
   points-of-disagreement totals filter is already wired and stays the
   flagship game-market angle.
5. Stand up the football prop backtest (successor to `backtest_props_mlb`)
   once the archive from Phase 2 has depth.

**DoD:** props slate generates from a real snapshot with calibrated
confidence; backtest scorecards published in `docs/`.

### Phase 4 — Live surfaces cut over (weeks 2–4, parallel with Phase 3)

1. `live-slate.yml` becomes the only live workflow: add the grade-yesterday →
   slate → email → artifact chain that `live-slate-mlb.yml` had (the football
   workflow currently projects but doesn't carry the full daily loop).
2. Rework report surfaces for football: matchup cards with spread/total/QB
   context replacing pitcher/park; sim-check percentile cards keyed to weekly
   (not daily) cadence; the public record chain **resets at 0–0 for football**.
3. Reboard the Streamlit app for the football slate.
4. Tune crons to the real 2026 schedule windows (Thu/Fri/Sat/Sun/Mon).

**DoD:** a full dress-rehearsal run of the daily loop off a live snapshot —
slate, email, cards, grading — with no MLB residue in any surface.

### Phase 5 — Season readiness gate

- **NCAAF Week 0 (~Aug 22) and Week 1:** paper slates only — full pipeline
  live, stakes logged but not bet, CLV vs close measured.
- **NFL Week 1 (~Sep 10):** same paper discipline.
- Real staking turns on only when the paper CLV is positive over a
  meaningful sample, per the CLV-first principle in `README.md`.

---

## 4. Honest model status (why Phase 3 is the long pole)

From the real backtests already in `docs/`:

- **NFL:** Brier 0.237 vs 0.248 baseline (informative), calibration error
  0.037 (good) — but **48.6% ATS / 48.9% O/U** against the close: below the
  52.4% break-even. The EPA game model is a sound backbone and is not, by
  itself, a bettable edge on full-game sides/totals.
- **NCAAF:** same shape; the wired totals-disagreement filter is the current
  best game-market angle.
- **The MLB proof transfers:** the edge lived in props and soft derivatives,
  found via a disciplined CLV archive and per-market calibration. Phase 2–3
  rebuild exactly that stack for football, where prop boards are wider and
  softer than the NFL close.

Plan accordingly: the cutover (Phases 0–2) is a week of plumbing; the wagering
edge is Phase 3, and it's honest work measured in CLV, not a switch to flip.

---

## 5. DFS track — salary-cap lineups (DraftKings / FanDuel)

DFS is a second consumer of the same player simulation the prop slate uses,
not a second model. A DFS score is a **fixed linear function of the stats we
already simulate** (pass/rush/rec yards, TDs, receptions, turnovers), and
`models/props.py` returns `PropSim` — per-player, per-stat sample arrays drawn
*jointly* per simulated game. Summing a lineup's scoring function inside each
simulation gives a full **lineup score distribution with real correlations**
(QB↔WR1 stacks, game-total leverage) — which is precisely the thing most
public DFS optimizers fake with heuristic stacking rules. That joint
distribution is our structural edge in tournaments.

### 5a. Data to gather

| Data | Source | How |
|---|---|---|
| Salaries + slates + roster rules | **DraftKings** | Unauthenticated JSON endpoints (`draftgroups` → `draftables`: player, salary, position, game). Collector workflow in the `collect-odds` mold. |
| Salaries | **FanDuel** | Requires an authenticated session (lobby player-list CSV export). Phase-2 optional; DK-first. |
| Salaries (fallback/cross-check) | FantasyPros | We already have the client; their pages carry DK/FD salaries. |
| Scoring rules | Public, static | Encoded as constants: DK full-PPR + 100/300-yd bonuses; FD half-PPR. Versioned in code, tested. |
| Ownership projections (GPP leverage) | Paid (RotoGrinders etc.) | **Deferred.** Start without; approximate from salary + FantasyPros consensus rank if needed. |
| Contest results / payout curves | Own entered-contest CSVs | Bank as we go; no good free historical source. |

Same archive discipline as odds: salary snapshots land in private Actions
artifacts, never in git. Snapshots start **now** — salary vs. projection
history is the DFS equivalent of the CLV archive.

### 5b. Build (rides Phase 3's prop wiring; ~1 week on top)

1. `velocity/dfs/scoring.py` — DK/FD scoring functions over `PropSim`
   samples → per-player fantasy-point distributions. Pure, fixture-tested.
2. `velocity/dfs/salaries.py` + `scripts/collect_dk_salaries.py` +
   workflow — ingest DK draftables to a canonical `(slate, player, salary,
   positions)` frame; name-match against our player IDs (reuse the alias
   machinery the odds ingest already has).
3. `velocity/dfs/optimizer.py` — salary-cap lineup MILP (PuLP/CBC): roster
   slots (QB/2RB/3WR/TE/FLEX/DST for DK classic), cap, team limits.
   - **Cash:** maximize mean projected points.
   - **GPP:** optimize over the *simulated distribution* — maximize
     P(lineup > target score), with stack/exposure constraints and
     multi-lineup generation (max-overlap constraints between entries).
4. Grading: score generated lineups against actual box scores (nflverse
   weekly stats, already ingested) — the DFS analog of `grade_yesterday`.
5. Surface: lineups tab in the slate email / Streamlit app.

**Value calibration, honestly:** salaries are set by the same sharp
information as prop lines, so mean-projection edge is thin — like the
full-game close. The realistic edge is (a) correlation-aware GPP construction
from the joint sim, and (b) reacting to late injury news via
`redistribute_shares` repricing every teammate's projection at once. Cash-game
lineups are the calibration proof; GPPs are where the sim earns its keep.

**DoD:** dress-rehearsal on a real DK NFL slate — salaries ingested, 20
lineups generated (1 cash, 19 GPP with exposure caps), scored post-slate
against actuals.

---

## 6. Carried-forward MLB lessons (so they don't get lost with the code)

1. **Per-market confidence shrink** — raw model probabilities were
   overconfident on props; a swept, per-market shrink fixed calibration.
   Rebuild the sweep for football props before betting them.
2. **Market exclusions** — some markets (MLB: `total_bases`) never calibrated
   and were excluded. Expect football analogs; let the backtest choose.
3. **CLV archive discipline** — paid-odds snapshots live only in private
   artifacts, never in git; grading and record-keeping run on the archive.
4. **Daily loop shape** — grade yesterday → build slate → email → artifact →
   social cards. The football cadence is weekly-with-bursts, but the loop is
   the same.
5. **Baseline fallback** — the MLB slate fell back to a league-average model
   when live lineup builds failed. Football equivalent: fall back to
   prior-season ratings when current-season data is thin (crucial for
   Weeks 1–4).
