# Velocity — Wagering System: Current State & Build Plan

**Status:** Plan (v0.1), grounded in the repo as of 2026-07-26
**Companion to:** [`docs/DESIGN.md`](DESIGN.md) §6 (the de-vig → edge → stake →
log philosophy), [`docs/BUILD.md`](BUILD.md) (the branch → tests → verify → PR
loop and gate discipline every phase below inherits), and
[`docs/BUILD_MLB.md`](BUILD_MLB.md) (the MLB vertical this plan builds on).
**Principle:** the wagering layer is the most testable part of the system —
every function has a closed-form correct answer — so every phase here ships
with exact-value tests and lands only behind a green gate.

---

## 0. The one-paragraph summary

The wagering *primitives* are built, tested, and in daily live use: de-vig,
edge/EV, fractional-Kelly staking with per-bet and per-game caps, bet logging
with two CLV measures, walk-forward backtests, and calibrated per-market
confidence shrinks. What is **not** yet built is the layer that turns a
per-slate bet recommender into a *bankroll-running wagering system*: portfolio
sizing across the whole card (built but never wired), a persistent bankroll
ledger (stakes are still computed against a fresh notional bankroll every run),
the drawdown kill-switch (has no inputs without that ledger), automated daily
grading/CLV monitoring, and per-market edge thresholds. This plan closes those
gaps in six phases, sized so the full loop is battle-tested on the live MLB
slate before football arrives in September.

---

## 1. Current state — what exists, what's proven, what's wired live

### 1.1 The primitives (built, tested, in the live path)

| Piece | File | State |
|---|---|---|
| Odds conversion | `wagering/odds.py` | ✅ live — American ↔ decimal, net payout |
| De-vig | `wagering/devig.py` | ✅ live — multiplicative (default) et al., paired-side de-vig at same (book, timestamp) |
| Edge / EV / Kelly | `wagering/edge.py` | ✅ live — `evaluate()` gates on `edge ≥ min_edge` **and** `EV > 0`; line shopping keeps best-EV per market side |
| Fractional Kelly + caps | `wagering/staking.py` | ✅ live — ¼-Kelly default, 5% per-bet cap, `apply_group_cap` shared by game & prop slates |
| Bet log + CLV | `wagering/bet_log.py` | ✅ live — price CLV and line CLV (signed points), settlement → reproducible bankroll curve |
| Game slate | `wagering/slate.py` | ✅ live — point-in-time entry lines, shop books/numbers/times, de-vig, edge, stake, per-game group cap, closing attach |
| Prop slate | `wagering/props_slate.py` | ✅ live — same discipline for player props; unresolved players reported, never guessed |
| Live runner | `wagering/live.py`, `scripts/run_live_slate.py` | ✅ live — team/player resolution, `live-slate-mlb.yml` twice daily |
| MLB derivative segments | `models/simulate_baseball.py` (`f5`/`i1` sims), `wagering/slate.py` | ✅ live — F5 moneyline/run line/total and NRFI/YRFI (`total_i1` at 0.5) priced off the same simulation as the full game; segment bets grade against segment scores or stay pending |
| Parlay engine | `wagering/parlay.py` | ✅ live — sim-exact joint pricing (correlated within a game, independent across), push-reduction, conservative selection off qualifying single legs, tight stake cap; same-game combos flagged (books reprice SGPs below the product payout) |
| Confidence calibration | `SlateConfig.prob_shrink` / `prop_shrink_by_market` / `exclude_markets` | ✅ live — tuned by walk-forward sweeps (see §1.3) |
| Walk-forward backtests | `backtest/engine.py`, `backtest/mlb.py`, `backtest/props_mlb.py` | ✅ — game CLV+ROI and grade-free prop CLV over the banked archive |
| Grading / scorecard | `report/scorecard.py`, `report/results.py`, `scripts/grade_archive.py` | ✅ — finals join (Odds-API id ↔ StatsAPI gamePk by team+date), ROI + calibration tables |
| CLV archive | `collect-odds.yml`, `collect-mlb-props.yml`, `backtest/archive.py` | ✅ — hourly line snapshots + per-event prop banking → private artifacts |

### 1.2 Built and tested, **not** wired

| Piece | File | Gap |
|---|---|---|
| Portfolio sizing | `wagering/portfolio.py` | `size_portfolio` (correlation de-scaling `1/(1+(m−1)ρ)`, per-group cap, aggregate slate cap) is referenced **only by its own unit tests**. The live path uses per-game `apply_group_cap` only — there is no slate-wide cap, and a game's game-market bets and its prop bets are sized in two independent passes that never see each other. |
| Drawdown kill-switch | `wagering/portfolio.py` | `should_halt` exists and is tested, but nothing supplies `current_bankroll` / `peak_bankroll` — there is no bankroll state anywhere to feed it. |

### 1.3 What the evidence says (the edges we act on today)

- **NFL sides/totals: no edge.** The from-scratch model cannot beat the razor
  close (`docs/BACKTEST_NFL.md`). Football slates run the raw model
  (shrink 1.0) but the honest read is: NFL is CLV-capture practice, not profit.
- **NCAAF selective totals: the first real edge — and now the live filter.**
  51.6% flat → **52.6% at ≥4 points of disagreement, 53.4% at ≥6**, monotone,
  10 seasons (`docs/BACKTEST_NCAAF.md`). Implemented as
  `SlateConfig.min_total_disagreement` and wired for NCAAF at 4.0 points
  (`--ncaaf-total-edge`); re-measurable any time via
  `run_backtest_local.py --totals-sweep`. Re-verification reproduced the
  aggregate edge but **6 of 10 seasons positive, not 7** — real, thinner than
  first written up.
- **MLB game markets: shrink 0.35.** The walk-forward sweep found the raw model
  overconfident (over-staking); shrink toward 0.5 pulled ROI positive. Wired
  (`--mlb-shrink 0.35`, PR #47).
- **MLB props: shrink 0.5, total_bases excluded.** The per-market sweep
  (PR #58/#59): `pitcher_strikeouts` and `pitcher_outs` optimize near 0.5
  (ROI ≈ +3–4%), `hits` roughly break-even, `total_bases` loses at **every**
  shrink → excluded rather than tuned. Wired (`--mlb-prop-shrink 0.5`,
  `--mlb-exclude-props total_bases`). Caveat, per the commit itself:
  **in-sample over one ~15-day window; re-tune as more archive banks.**

### 1.4 The honest gap list

1. **No bankroll.** `starting_bankroll` is a CLI constant (default 100). Every
   slate stakes off that fresh notional amount; nothing records which
   recommendations were actually placed, at what price, or what they returned.
   Kelly's whole premise is compounding a *real* bankroll — today we emit
   stake *percentages* attached to a fiction.
2. **No portfolio view.** Per-game caps only. Fifteen MLB games × game markets
   × props can stack far past any sane aggregate exposure, and correlated
   same-game exposure (a team total, the game total, the opposing pitcher's Ks)
   is capped only within each pass, not across them.
3. **No kill-switch in practice.** The circuit breaker exists but is
   unreachable without bankroll state (gap 1).
4. **CLV loop is manual.** Backtests and grading run on dispatch; nothing
   automatically grades yesterday's live slate, attaches closes to live prop
   bets, or watches rolling CLV per market and flags decay.
5. **One global `min_edge`.** DESIGN §6.2 calls for thresholds sized to
   estimation error (higher for props/NCAAF); today one number (0.02) serves
   every market. *(The NCAAF points-disagreement filter is now live — the
   remaining gap is per-market probability thresholds.)*
6. **Execution seams unmodeled.** No stale-snapshot guard (a slate can price a
   board minutes before lineups shift), no limit-aware stakes (prop limits are
   $200–500), no repricing at MLB lineup release, and football team-name alias
   coverage for NCAAF will need its first-weeks loop (`docs/LAUNCH.md`).

---

## 2. The plan — six gated phases

Each phase is one PR into `main` via the `BUILD.md §1` loop: branch → offline
tests → real-data verification → merge, with a Definition of Done. Ordering is
deliberate: state first (W1), then sizing that uses it (W2), then the
monitoring that keeps it honest (W3), then selectivity (W4), then execution
polish (W5), then football readiness (W6). W1–W3 are the system's spine and
should land while the MLB season provides a daily live testbed.

### Phase W1 — The bankroll ledger (state, at last)

The single highest-leverage change: make bankroll a persisted, settled, real
number.

- **Build:** `wagering/ledger.py` — an append-only ledger (parquet, same
  private-artifact discipline as the archive) with three record types:
  `recommended` (every slate row, auto-appended by the runner), `placed`
  (operator-confirmed: actual price/stake/book — a tiny CLI,
  `scripts/ledger.py place/skip`, since the operator places bets manually per
  `LAUNCH.md`), and `settled` (auto: reuse `report/results.py` finals join +
  `Bet.grade`). Derived views: current bankroll, peak bankroll, open exposure,
  per-market and per-league P&L. The slate runner reads current bankroll from
  the ledger (CLI `--bankroll` becomes the *seed* for an empty ledger, not a
  per-run constant).
- **Tests:** exact-value settlement round-trip (recommend → place → settle →
  bankroll moves by `stake·b` / `−stake` / 0); idempotent re-settlement;
  pending games leave bankroll untouched; peak tracks correctly through a
  win–loss–win sequence; empty ledger seeds cleanly.
- **DoD:** two consecutive live MLB slates run off ledger bankroll; yesterday's
  placed bets auto-settle in the morning run; the slate artifact shows real
  bankroll and open exposure. → `v*-w1`.

### Phase W2 — Portfolio sizing + kill-switch, wired

- **Build:** merge game-market and prop candidates for the same game into one
  correlation group and route the whole card through
  `portfolio.size_portfolio` — correlation de-scaling within each game,
  per-game cap, aggregate slate cap (`max_portfolio_fraction`, default 25%) —
  replacing the two independent `apply_group_cap` passes. Feed
  `current_bankroll` / `peak_bankroll` from the W1 ledger so `should_halt` is
  finally reachable; a tripped kill-switch produces an *explicitly empty* slate
  ("halted: drawdown 32% ≥ 30%") rather than a silent one. Add open-exposure
  awareness: stakes already placed today count against the aggregate cap.
- **Tests:** a game with 2 game bets + 3 props forms one group and de-scales
  together; aggregate cap binds across many games with relative sizing
  preserved; kill-switch trips at the threshold exactly and reports why;
  golden-file the sized slate on the existing fixture snapshot.
- **DoD:** live MLB slate emits portfolio-sized stakes with a visible
  exposure-summary block (per game, aggregate, drawdown state); backtest
  re-run with portfolio sizing shows equal-or-better drawdown at comparable
  CLV capture. → `v*-w2`.

### Phase W3 — The automated CLV loop (grade, monitor, alarm)

- **Build:** a daily `grade-and-monitor.yml`: settle the ledger (W1), attach
  closing lines/props from the archive to yesterday's bets, and append to a
  running scorecard artifact. A rolling monitor (`report/monitor.py`) computes
  per-market trailing CLV and ROI over 7/30-day windows and **flags**: any
  market with materially negative trailing CLV, shrink drift (realized
  calibration vs. the wired shrink), and prop markets behaving like
  `total_bases` did (candidates for exclusion). Output is a flat "market
  health" table in the artifact/log — the operator's daily one-glance read.
- **Tests:** monitor math on a synthetic ledger with a known drifting market;
  close-attach joins by `(game_id, market, player, side)` exactly as the
  backtest does; a day with no bets appends cleanly.
- **DoD:** seven consecutive automated daily runs; the scorecard artifact shows
  cumulative CLV/ROI per market; at least one flag rule verified end-to-end on
  real banked data (total_bases, graded retroactively, must flag). → `v*-w3`.

### Phase W4 — Per-market selectivity (bet where the evidence is)

- **Build:** per-market edge thresholds in `SlateConfig`
  (`min_edge_by_market`, mirroring `prop_shrink_by_market`) — wider for props
  and NCAAF per DESIGN §6.2. Implement the **proven NCAAF totals filter** as
  backtested: bet totals only when |model − market| ≥ N points (default 4),
  alongside — not instead of — the probability-edge gate. Re-run the prop
  shrink sweep on the season-to-date archive (the wired 0.5 was one 15-day
  window) and adopt the re-tuned values; promote the exclusion decision from
  hand-set CLI default to a monitor-informed config reviewed on a cadence.
- **Tests:** per-market thresholds override the global exactly; the NCAAF
  points filter reproduces the backtest's bet counts on the historical dataset
  (6,448 bets at ≥3 pts, 5,477 at ≥4); sweep is deterministic under seed.
- **DoD:** NCAAF backtest through the live slate path with the points filter
  reproduces the 52.4–53.4% selective win rates; MLB props re-tuned on ≥2×
  the original window with per-market thresholds wired. → `v*-w4`.

### Phase W5 — Execution polish (the seams where real money leaks)

- **Build:** snapshot-freshness guard (refuse to stake a board older than a
  configurable age; print "stale board" instead); limit-aware stake caps per
  market class (props capped at realistic limits so Kelly output is placeable);
  MLB lineup-release repricing (re-run the slate when StatsAPI confirms
  lineups — the operational edge BUILD_MLB §5 calls out); surface *board
  movement* between the two daily runs (a line that moved toward us since the
  morning run is confirmation; away is a warning).
- **Tests:** stale snapshot refuses and reports; limit cap binds before the
  bankroll cap when smaller; repricing on a lineup-change fixture provably
  moves the affected pitcher-K and team-total prices.
- **DoD:** one live week where every recommendation was placeable as-is (stake
  ≤ limit, board fresh); at least one lineup-release reprice observed live
  changing a recommendation. → `v*-w5`.

### Phase W6 — Football season readiness (September 2026)

- **Build:** run the full W1–W5 loop for NFL/NCAAF: football ledger namespace,
  portfolio sizing across Saturday's 50-game NCAAF slates (the aggregate cap
  matters most here), the W4 NCAAF totals filter as the headline strategy, and
  the `LAUNCH.md` alias-coverage loop for the first weeks. Football keeps
  shrink 1.0 until its own sweep on live-archive data says otherwise —
  MLB's numbers are not transferable.
- **Tests:** existing football golden tests unchanged (the standing regression
  gate); a mixed-league day (MLB + NCAAF) shares one bankroll and one
  aggregate cap.
- **DoD:** week-1 NCAAF slate runs end-to-end off the ledger with portfolio
  sizing and the selective totals filter; CLV capture confirmed against the
  archived close. → `v*-w6` / `v2.x`.

---

## 3. Risk governance (standing rules, not a phase)

- **CLV over P&L, always.** Thin edges swing for months; the monitor (W3)
  judges markets on trailing CLV first. Realized ROI is reported, never used
  alone to kill or scale a strategy inside a season.
- **Sweep discipline.** Every re-tune (shrink, thresholds, exclusions) is
  walk-forward, run on data banked *after* the previous tune where possible,
  and recorded in the PR with the full table — the #59 pattern. One 15-day
  window is a starting value, not a truth.
- **Multiple-comparisons honesty.** The monitor will flag markets by chance;
  exclusion/inclusion changes require the flag to persist across two review
  windows (DESIGN §7.3).
- **Caps are constitutional.** ¼-Kelly, 5% per bet, 10% per game, 25% per
  slate, 30% drawdown halt. Loosening any of them requires a backtest PR
  showing the drawdown cost, never a config tweak in passing.
- **The ledger is append-only.** Corrections are new records, so every
  bankroll number remains reproducible from history.

---

## 4. Immediate next step

Land **Phase W1** (the ledger). It is small, purely additive, offline-testable,
and every other phase depends on its state. The very next MLB slate after it
merges becomes the first Velocity run staked against a real, compounding
bankroll — and starts accumulating exactly the placed-vs-recommended history
that W3's monitor and W4's re-tunes need.

## 5. Pick'em slips (`velocity/wagering/pickem.py`)

Fixed-payout pick'em boards (PrizePicks-style) are parlays against a posted
line, not markets — no vig, no price discovery, just a payout table per slip
shape. The engine prices them exactly:

- **`PAYOUTS`** — the official structures (help-center, fetched 2026-08),
  as total-return multiples: power 2/3/4/5/6 at 3/6/10/20/37.5x; flex
  2–6 with their partial-payout tiers. `reverted_table` implements the
  published DNP/void rule (revert one structure smaller; a 2-pick reverts
  to a refund). A frozen test is the tripwire for payout changes.
- **`slip_ev`** — exact Poisson-binomial hit distribution for independent
  legs; **`slip_ev_from_hits`** — the correlated path, a boolean hit matrix
  read off the correlated prop sim (`PropSim` samples every player
  conditioned on the same simulated game). Fixed payout tables implicitly
  assume independence, so positively correlated same-game stacks push
  power-play EV above what the multiplier prices — that joint-vs-marginal
  gap is the engine's structural edge, and the sampled path measures it
  directly.
- **`breakeven_leg_prob`** — the uniform per-leg probability where a shape
  returns 1.0 (power-2 = 1/√3 ≈ 57.7%; the big flexes sit in the mid-50s).
- **`fair_leg_prob`** — devigged P(over) from a book's two-sided prices at
  the board's line: the leg-probability source until our own prop model is
  lab-validated per stat.
- **`best_slips`** — enumerates candidate combinations across shapes and
  ranks by EV; correlation-aware when given a samples source, independent
  screening otherwise.

Not modeled on purpose: demon/goblin alternates (unpublished payout deltas —
they arrive flagged from the collector and are excluded from standard legs)
and promos. The board feed itself is phase-gated on a transport decision
(docs/DATA_PROVIDERS.md); until then leg probabilities come from our own
Odds API props snapshots, which is also exactly how the engine gets
lab-validated before anything is published.
