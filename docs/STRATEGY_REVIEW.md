# Velocity — Wager/Pick Strategy & Dashboard Review

**Status:** Review + build outline (v0.1), written 2026-09-09 — NFL Week 1
kicks off tomorrow; the NCAAF Week-2 board is live.
**Companion to:** [`WAGERING.md`](WAGERING.md) (the primitives and the W1–W6
plan), [`PUBLISH_GATE.md`](PUBLISH_GATE.md), the `BACKTEST_*.md` records,
[`EDGE_RESEARCH.md`](EDGE_RESEARCH.md), [`BUILD_EXCHANGES.md`](BUILD_EXCHANGES.md),
[`SITE.md`](SITE.md) and [`DASHBOARD_RESEARCH.md`](DASHBOARD_RESEARCH.md).
**Principle:** every finding below carries the number that produced it — from
the committed datasets, the repo's own backtest records, or the live boards of
the day this was written. Nothing changes until this outline is agreed; the
phases in §7 are the change list.

---

## 0. The one-page verdict

1. **The primitives are sound; the policy layer is uneven.** De-vig, EV,
   fractional Kelly, caps, CLV grading, walk-forward backtests — all built,
   tested, in daily use. But *which* markets those primitives are allowed to
   stake is decided market by market, and the evidence behind each decision
   ranges from a decade of closes to nothing at all.
2. **Where the money actually goes today is the market with no evidence.**
   The NCAAF slate that ran this morning (run 34367469317, 95 bets, 363u of
   solo-Kelly stake): **66 moneylines took 218u — 60% of the card**. 44 of
   them at +300 or longer, 28 at +1000 or longer. The model's median win
   probability on those dogs is **0.288 against the market's 0.124** — 2.3×.
   NCAAF moneylines have never been backtested in this repo
   (`BACKTEST_NCAAF.md` tests spreads and totals; the committed closes carry
   no moneyline column). And the one calibration study the repo *does* have
   on its own graded record says the biggest claimed edges carry the worst
   CLV (§4). This is the single finding the rest of the review orbits.
3. **The receipt is broken.** The record chain has 0 settled rows, the
   NCAAF grader fails in CI on a missing dependency (every NCAAF slate grades
   zero rows, forever, until fixed), and the chain itself lives only inside
   60-day artifacts. The Performance page — the credibility centerpiece — is
   empty on launch day and cannot fill for college.
4. **The site shows the wrong stake.** The portfolio-sized card
   (`WAGERING.md` §6: "the number to bet") is built every run and displayed on
   no page. Today shows no stake at all; Matchup and Plays show the solo-Kelly
   number the portfolio pass then scales down (NFL today: 10.29u → 6.85u;
   NCAAF: 532u → 25u).
5. **The fixes are small and ordered.** Three of the six phases in §7 are
   one-PR policy or plumbing changes; one is the research the money needs;
   the last two are the ledger and the monitor `WAGERING.md` already planned.

---

## 1. Where the money goes — the audit table

Per league and market: what the evidence says, what the live policy does, what
today's board staked, and the verdict. Exposure figures are solo-Kelly units
before the 25% aggregate cap, from this morning's runs; "paper" means priced,
logged and graded for CLV, but staked at zero.

### 1.1 NFL (18 bets, 10.29u solo → 6.85u sized; Week-1 board)

| Market | Evidence | Live policy | Today | Verdict |
|---|---|---|---|---|
| Spread | 48.6% ATS on 1,280 games; Round-2/3 disagreement cuts (55.2% on 301 at ≥6) **not promoted**; close Brier 0.2109 beats best model 0.2205 | w=0.2 market anchoring + min_edge 0.02 ⇒ a bet needs 10pp of *raw* disagreement | 6 bets, 3.3u | CLV practice, as `WAGERING.md` §1.3 already says. Keep small; say so on the site. |
| Total | 48.9% O/U; windy-game control 46.3% | same anchoring | 1 sportsbook + 7 exchange rungs | Same posture. Model runs **+2.4 pts mean above the market** across all 16 Week-1 games (10/16 high, max +10.5 SF@LA) — cold-start or signal, unknown. |
| Moneyline | as spread | same | 2 bets, 1.6u | Same posture. |
| Team totals | censoring study: mean bias real (+0.97 at ≤14) but over-rate 45–52% — no cut clears 52.4% (`BACKTEST_NCAAF.md` addendum) | disagreement gate **off**; EV gate only | 2 exchange rungs | Unproven; EV-gate only on an unfiltered derivative. Paper until posted closes calibrate the gate. |
| Props | FP snapshot is season-long ⇒ prop slate refuses (correct) | shrink 1.0, prop bar 2× | 0 | Dormant until weekly FP. |
| Exchange rungs | E8 gate admits 9.5–11.5-pt offsets where shape error (0.0087–0.0165) is **40–70% of the claimed edge** (0.022–0.031) | `--exchanges` off in cron; ladder tolerance 0.02 | 9 rungs, 5.4u (local run) | Paper. The tolerance is a policy question; §7 S1 asks it. |

### 1.2 NCAAF (95 bets in CI, 363u solo → 25u sized; 182 / 532u with exchanges on)

| Market | Evidence | Live policy | Today | Verdict |
|---|---|---|---|---|
| Spread | 50.1% ATS on 9,518; no cut at any threshold | **excluded** | 0 | Correct. |
| Total | ≥6 pts: **53.0%** on 4,398 all-history — but **50.6% in 2025 alone** (496); ≥4 no longer clears break-even (52.3%) | `--ncaaf-total-edge 6`, raw model (w=1.0) | 24–29 sportsbook, ~99u | **The one evidence-backed edge.** Keep. 2025's sub-water season is a standing warning; live CLV decides. |
| **Moneyline** | **None.** Never backtested; closes lack the column | raw model, min_edge 0.02, no anchoring, no shrink | **66 bets, 218u (60% of the card); 28 at ≥+1000** | **Stop staking until backtested** (§7 S1, S3). Every structural factor points the same way: the NCAAF sim's margin sd was just widened 17.0→18.2 (fattening exactly this upset tail), the raw model is un-anchored and un-shrunk, and the adverse-selection study (§4) says huge edges are the bad ones. |
| Team totals | no >52.4% cut on derived numbers; posted-close study pending | gate off; EV only | 59 exchange rungs (local); CI fetches sportsbook TTs too | Paper until the posted-close study runs. |
| Exchange rungs | as NFL; offsets 10.5–27.5, median 12.5 | as NFL | 92 rungs, 246u (local) | Paper. Exchange rows' median edge (0.100) is *lower* than the sportsbook rows' (0.154) — the exchanges did not cause the over-confidence, they inherit it. |

### 1.3 The other verticals

| League | Evidence | Posture today | Verdict |
|---|---|---|---|
| NCAAB | N3: **null after FDR**, 0 of 90 segment cells; raw model ties the close | content + CLV on posted prices; no filter | Correct posture. Bets that clear the EV gate are still staked — confirm that is intended, or paper it like the exchanges. |
| NHL | lab-gated Brier; **no closes-joined backtest yet** (sbro archive exists) | content + CLV; goalie-neutral pricing | Same question as NCAAB. Closes backtest is the open item. |
| WNBA | 55.4% flat ATS on 504 bets (~2.4σ) — "worth tracking live before anyone stakes on it" | staked through the EV gate like every league | Tracking ≠ staking. Off-season now; decide before May. |
| MLB | decommissioned (tag `mlb-final`) | still in the cron league list; empty board | Remove from the default list, or leave as a free no-op — but the Methods page still describes it as live. |

### 1.4 The derivative products

| Product | How it selects | What it inherits | Verdict |
|---|---|---|---|
| Parlays | 3 legs max, EV ≥ 0.05, 5 per slate, sim-exact joint pricing, legs only from qualifying singles | Every upstream selection error, **multiplied**. This morning's NFL parlay board stacked three +1000-class exchange rungs into a +264556 ticket at "EV 19.96". | Sound engine, wrong inputs. Restrict legs to markets with a promoted edge (S1). |
| Pick'em | Poisson-binomial / correlated slip EV over the prop sim; worst-case devig | Board transport not landed; prop sim dormant (season-long FP) | Paper by construction today. Fine. |
| Publish gate | tier A + conviction ≥0.72 + context ≥0.05 + edge band 0.03–0.12 + no adverse drift, max 5 | NFL today: **0 of 18 published** (14 tier B, 3 conviction, 1 edge floor) | Working exactly as designed. Its edge *ceiling* is the one place the repo already refuses the huge-edge profile — the staked slate should learn from it (§7 S1). |
| DFS | separate track; not Kelly-staked | own backtests (`DFS_MODEL.md`) | Out of scope here; the page is fine. |

---

## 2. The staking chain — what binds, and what it cannot fix

The constitutional caps (`WAGERING.md` §3): ¼-Kelly, 5% per bet, 10% per game,
25% per slate, 30% drawdown halt.

- **The 25% aggregate cap binds on every NCAAF run** (363u → 25u this morning;
  532u → 25u with exchanges). It prevents ruin. It does **not** fix selection:
  the cap scales every stake by the same factor, so the 66 moneylines keep
  their 60% share of whatever is staked. A cap that binds every day is a
  symptom that the layer above it is over-producing.
- **Correlation de-scaling at ρ=0.5 works within a game** but the slate's
  moneylines are ~66 different games — nothing above the game level says "66
  long-shot dogs from one model are one bet on that model's tail
  calibration."
- **No ledger (W1, never built).** `--bankroll 100` is a fresh notional every
  run; nothing records what was placed; the kill-switch has no inputs. The
  site's "units" is the graded record at *recommended solo* stakes — a paper
  bankroll that resets to 100 daily.
- **Per-market edge thresholds exist (`min_edge_by_market`) and default to
  nothing** for game markets. Props get 2×. NCAAF moneylines get the same
  0.02 as an NFL spread with market anchoring.

---

## 3. Calibration and shrink, per league

| League | Shrink | Anchoring w | Sweep on the *wagering* path? |
|---|---|---|---|
| NFL | 1.0 | 0.2 (Round 3 select-chosen) | Brier/close tests, yes; a staking sweep, no — "tuning w from live paper CLV" is still open |
| NCAAF | 1.0 | 1.0 (raw) | **No.** The totals filter was swept; nothing else was. The league with the most exposure has the least calibration. |
| NCAAB / NHL / WNBA | 1.0 | 1.0 | No |
| MLB (retired) | 0.35 game / 0.5 props | — | Yes — the only league that ever had one, and it found the raw model over-confident. |

The MLB lesson (`WAGERING.md` §1.3) was that a raw sim over-stakes and shrink
toward 0.5 pulled ROI positive. Football kept shrink 1.0 "until its own sweep
says otherwise" — and the sweep never ran. §7 S3 is that sweep.

---

## 4. The finding that should govern the whole slate

`PUBLISH_GATE.md` §2, on the repo's own 195 graded bets (42 with a matched
close): stake quartiles are monotone in edge, and

| Quartile | Win% | ROI | Mean CLV |
|---|---|---|---|
| Q1 lowest edge | 65.3% | +27.9% | **+0.037** |
| Q4 highest edge | 58.3% | +3.9% | **−0.048** |

`corr(stake, CLV) = −0.35`. The biggest claimed edges carry the worst
closing-line value — adverse selection. The doc calls it "a flag, not a
verdict" on 42 bets, and built the publish gate's edge *ceiling* (0.12) on it.

The staked slate has no such ceiling. This morning's NCAAF card: median
moneyline edge 0.117, max 0.367; **32 of the 66 moneylines — and 60 of all
95 bets — sit above the 0.12 ceiling** the publish gate would refuse. The staking layer and the posting layer
disagree about whether a 0.30 edge is an opportunity or an alarm, and the
posting layer has the evidence.

Also in this section because it is the counter-example: the **injury veto is
evidence-positive** (`BACKTEST_INTEL.md` addendum: vetoed picks 47.9% vs the
pool's 51.7%, 11/15 seasons). It covers NFL only; the stat signals measured
null. The intel layer's contract — annotate and veto, never promote, never
touch stakes — is right and should not change.

---

## 5. The record chain — broken in two places

The Performance page is the trust surface, and it cannot fill:

1. **NCAAF grading fails in CI.** `live-slate.yml` installs `pip install -e .`;
   the CFBD client lives in the `[ingest]` extra. This morning's log:
   `schedule fetch failed (cfbd is required for live NCAAF ingest…)` → 0
   graded rows. Every NCAAF slate since the football cutover has graded
   nothing, and will until the extra is installed (or the grader reads CFBD's
   REST endpoint directly, no client). One-line fix, and the highest-value
   line in this document.
2. **The chain is not durable.** `cumulative_record` is rebuilt each run from
   the last 6 successful runs' artifacts (60-day retention). A workflow-failure
   streak, or any gap longer than retention, loses the season. Flagged in
   `DASHBOARD_RESEARCH.md` §1; the migration plan's step (2) was to persist it
   and did not happen. The site already has a durable, private, token-in-CI
   store: the R2 bucket `deploy.sh` parks the WASM in. One `wrangler r2 object
   put` per run, one `get` at grading time.
3. **0 settled rows today** — expected on Sep 9 (football hasn't played; MLB
   was retired and its chain with it), but it means launch day shows an empty
   receipt. Worth a "record since <date>" line so empty reads as new, not broken.
4. Props, parlays and exchange ties all grade (E7b). Fine.

---

## 6. The site — page by page

What each page shows against what the run produces. `build_site_data.py`
assembles 20 tables from the run's artifact families; **the pages query 18
of them**.

| Page | Shows | Missing / wrong |
|---|---|---|
| **Today** | board rows sorted by edge desc; league/matchup/market/side/line/price/model%/edge/tier; 3 tiles | **No stake, no book/venue.** Sorted by edge — per §4, that is worst-CLV-first. The "Plays (edge ≥ 2%)" tile is tautological (every slate row already cleared `min_edge`). No exposure figure (sized total vs the 25% cap — the one number the operator needs). No per-league split. |
| **Plays** | the gate's calls + the held-back audit with reasons | Good design. Stake column is solo-Kelly. |
| **Performance** | season units/win rate, CLV block, cumulative units, by-league, latest graded | Empty today (§5). No per-market split; the `clv_by_market` helper with its `clv_trusted` flag (`WAGERING.md` §6) exists and is unused — the page averages prop and moneyline CLV into one number the repo's own doctrine says not to trust. No pending count. Units at solo stakes, not sized stakes. |
| **Matchup** | dossier: projection tiles, weather, line movement, markets, sims, injuries, cards | Markets table shows solo stake, no venue chip for exchange rows, no ladder-gate refusals. The intel layer's `rationale` text — the "argued card" — is in `intel_*.parquet` and never rendered. |
| **Ratings** | per-league table with movement | Good. |
| **DFS** | cash/showdown/tiered/GPP | Good. |
| **Methods** | `MODEL_CONFIG` imported from the Streamlit app that is supposed to retire | **Stale:** NCAAF row says "≥4 pts (52.8% over 5,477)" — live is ≥6 at 52.3/53.0%; MLB and WNBA rows describe leagues that are retired or dark; no NCAAB or NHL rows at all. The runner already prints each league's real fit description (`kind`) — generate the block from that. |
| **Graphics** | sheets, grids, sim checks, record card | Good. |

**Built every run, shown nowhere:** the portfolio-sized card (`portfolio`
table, 0 pages); the props slate (`props` table, 0 pages); parlays (not even
collected by `build_site_data.py`); pick'em; the exposure summary the runner
prints; the intel rationale lines; exchange venue counts and gate refusals.

---

## 7. What to build — six phases, in order

Each phase is one PR into `main` through the `BUILD.md` §1 loop with a
Definition of Done. Ordering: fix the receipt first (it is a bug), then stop
the unsupported exposure (policy), then the research the money needs, then
the site, then the ledger and the monitor `WAGERING.md` already scheduled.

### S1 — Fix the receipt (plumbing; smallest, first) — **landed 2026-09-09**

Shipped: `.[ingest]` installs in the live workflow (the NCAAF grader has its
client); the season chain round-trips through R2 (`records/` prefix, fetched
before grading and parked after, the worker never serves it) and the grader
carries forward whichever copy *reaches furthest* by settled date rather
than whichever was written last; the graded record now carries the
portfolio-sized stake and the profit at it beside the solo-Kelly pair.
`record_since` is the chain's earliest settled date, computed where it is
read (the site) rather than stored.

- **Build:** install `.[ingest]` in `live-slate.yml` (or drop the client and
  read CFBD `/games` over `urllib` like the NCAAB grader reads hoopR — no
  extra either way). Park `cumulative_record_{league}` in R2 after grading
  and read it back before, so the chain survives any artifact gap; the
  artifact copy stays as today. Add a `record_since` date to the chain.
- **Tests:** the grader's NCAAF path under a fake `cfbd`-less environment
  produces rows from a fixture schedule; chain round-trip through a stub
  store; `record_since` survives a merge of two chains.
- **DoD:** the next NCAAF game day grades non-zero rows in CI; a simulated
  7-day artifact gap leaves the season line intact.

### S2 — Stop staking what has no evidence (policy; flags and defaults only) — **landed 2026-09-09**

Shipped as designed, with one correction to the design: the review asked
for a *floor* on relative edge or EV, but floors bind favorites (a 0.02 edge
at −300 is 2.7% EV; at +1000 it is 22%) — the longshot problem needs a
relative **ceiling**. So the staked slate now carries the publish gate's
absolute ceiling (0.12) and a relative one (edge ≤ 50% of the fair
probability); a row past either is logged as paper with the reason, so its
CLV grades the ceiling itself. NCAAF moneylines sit out by default; team
totals are paper on every league; NCAAB, NHL and WNBA run in an explicit
paper posture; parlay legs come only from staked sportsbook rows; the
publish gate refuses paper rows first.


- **Build:** in `run_live_slate.py` defaults — NCAAF `moneyline` joins
  `spread` in `exclude_markets` (re-enable flag, like `--ncaaf-spreads`);
  team totals paper-only (stake 0, still priced/logged/graded) until the
  posted-close study sets `min_team_total_disagreement`; parlay legs
  restricted to markets with a promoted edge (NCAAF totals ≥6, NFL anchored
  game markets); an **edge ceiling on the staked slate**, defaulting to the
  publish gate's 0.12 — above it a row is logged with reason
  `edge above ceiling` and staked 0 (the adverse-selection guard applied where
  the money is). NCAAB/NHL/WNBA: an explicit `--paper` posture flag per league
  so "content + CLV" is a setting, not a convention.
- **Tests:** policy-default tests pin each default (the `build_parser` pattern);
  a slate fixture with a 0.30-edge row lands in the log at stake 0 with the
  reason; NCAAF moneyline rows never reach `stake_amount`.
- **DoD:** the NCAAF slate re-run on this morning's banked board concentrates
  its exposure in totals ≥6; the aggregate cap no longer binds; the site's
  held-back logic (S4) can show the ceiling refusals.

### S3 — The evidence the money needs (research; one lab PR per item)

1. **NCAAF moneyline backtest.** *Done 2026-09, and it failed:* the columns
   are kept, 2021–2025 banked (CFBD has no earlier moneylines), and the
   walk-forward test loses at every bucket — raw −4.8% over 2,807 bets,
   −36% at ≥ +1000; the model's Brier 0.217 against the market's 0.183, and
   a fifth of the model already scores worse than the market alone. **The
   S2 exclusion is permanent** (`BACKTEST_NCAAF.md`, the S3 round).
2. **NCAAF staking sweep** — *done 2026-09:* at the ≥6 filter the claim
   matches the realization at w ≈ 0.13, not 0.2 (the live weight claimed
   1.7× what it earned); the edge is +0.3% ROI at ≥6 and grows to +2.6% at
   ≥10. **w = 0.13 promoted**; the 0.02 gate then admits eight-point
   disagreements and refuses six-point ones.
3. **Ladder tolerance** — measure claimed-edge-vs-shape-error on the banked
   Kalshi candle closes as they accumulate (E7's graded week); set
   `--ladder-tolerance` from that, not from the round number it is now.
4. **NFL totals cold-start** — track the +2.4 model−market gap week over week;
   if it persists past Week 4 it is a level bias, not noise. *Resolved
   2026-09 without waiting:* the residual bank showed it was a level bias
   over fifteen seasons (the QB decomposition, +2.3 a game against actuals,
   +2.8 against the close), and the level is now fitted through the model
   (`docs/MODEL_LAB.md`, the sim-shape round).

- **DoD:** each item ends in a promote/exclude decision recorded in its
  `BACKTEST_*.md`, with the sweep table; defaults move only through those PRs.

### S4 — Show the money correctly (site) — **landed 2026-09-09**

Shipped: Today ranks by tier → conviction → edge with the sized stake, the
venue and a status column (staked / watch / paper with its reason), an
exposure tile (sized total against the slate cap, per league) and a league
filter; Performance leads with units at sized stakes beside solo, a pending
count, the record-since line, the paper record kept outside the headline,
and a per-market CLV table whose untrusted rows say "judge on P/L"; the
matchup page carries the sized stake, venue, status and the intel rationale
("the argument"); Plays shows the sized stake and a parlays section with
the same-game caveat; Methods is written by the run itself
(`config_{league}_{stamp}.parquet` from `live_config_rows`) with the hand
table as fallback for older artifacts. The builder back-fills any column a
family predates, so an old chain or card still renders.

- **Today:** portfolio stake and book/venue columns; sort by conviction (tier
  first, then conviction, then edge) instead of raw edge; replace the
  tautological plays tile with **exposure** (sized total / cap, per league);
  league tabs; a freshness badge from the board stamp (already in `HeroBand`)
  plus the odds-archive age.
- **Performance:** per-market table from `clv_by_market` with the
  `clv_trusted` flag (trusted rows get the CLV number, untrusted rows say
  "judge on P/L"); pending count; units at sized *and* solo stakes; "record
  since" line; per-league CLV chart already exists — keep.
- **Matchup:** the intel `rationale` lines under each staked market (the
  argued card); venue chips on exchange rows; ladder-gate and ceiling
  refusals as a one-line count.
- **Plays:** stake column = sized stake.
- **New sections:** parlays (collect the family; show legs, EV, the same-game
  upper-bound caveat); props slate when the weekly FP snapshot lands.
- **Methods:** generate `MODEL_CONFIG` from the runner's fit descriptions and
  the live policy defaults (`build_parser`), drop the `app/` import; NCAAB and
  NHL rows; retire the MLB row.
- **Tests:** `build_site_data` golden test on the fixture slate for the new
  tables; the Evidence build passes on the sentinel (empty) state for every
  new query.
- **DoD:** a run's exposure summary, sized stakes and venue are visible on the
  site without opening the log; Methods matches `build_parser` defaults.

### S5 — The ledger (WAGERING W1, unchanged)

The single largest structural gap remains exactly what `WAGERING.md` §1.4
says: no bankroll state, so no real units, no open-exposure awareness, no
kill-switch. Build as specified there (`recommended` / `placed` / `settled`,
append-only, private). S1's R2 store is the natural home. Nothing in S1–S4
depends on it; everything after it does.

### S6 — The monitor (WAGERING W3, unchanged)

Per-market trailing CLV/ROI over 7/30 days with flags — a "market health"
page on the site. Needs S1 (a durable chain) and S5 (real stakes). The
`clv_by_market` and `benjamini_hochberg` helpers already exist.

---

## 8. What this review does NOT change

- **The caps.** ¼-Kelly, 5/10/25%, 30% halt stay constitutional; S2 adds a
  ceiling on *selection*, it does not loosen any cap.
- **NFL policy.** w=0.2 anchoring is already the conservative reading of
  Round 3; the site should *say* it is CLV practice rather than change it.
- **The NCAAF totals filter.** ≥6 is the repo's one promoted edge; 2025's
  50.6% is a warning to watch, not a reason to move the threshold without a
  sweep.
- **The intel contract.** Annotate, veto, never promote, never touch stakes —
  measured and right.
- **The publish gate's floors.** Provisional and settable per run;
  `calibrate_publish_gate.py` fits them once boards accumulate. Left alone.
- **Any tolerance without a sweep** — ladder, shrink, anchoring, disagreement
  thresholds all move only through S3-style PRs with the table attached.

---

## 9. Immediate next step

S1 and S2 are each one small PR and together remove the two live problems —
an ungraded league and 60% of the card riding on an untested market — before
the first NFL kickoff. S3's moneyline item is the research PR that decides
whether S2's exclusion is temporary. S4 follows once the numbers it needs to
show are the right numbers.
