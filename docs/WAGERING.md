# Velocity — Wagering System: Current State & Build Plan

**Status:** Plan (v0.1), grounded in the repo as of 2026-07-26; **W1 (the
ledger) and W3 (the monitor) landed 2026-09-09** — see §7 and §8. W2's
sizing half shipped earlier (§6); its kill-switch half is wired by W1.
**Companion to:** [`docs/DESIGN.md`](DESIGN.md) §6 (the de-vig → edge → stake →
log philosophy), [`docs/BUILD.md`](BUILD.md) (the branch → tests → verify → PR
loop and gate discipline every phase below inherits),
[`docs/BUILD_MLB.md`](BUILD_MLB.md) (the MLB vertical this plan builds on), and
[`docs/BUILD_EXCHANGES.md`](BUILD_EXCHANGES.md) (the Kalshi/Polymarket venue
build — its E5 fee-aware EV and per-venue caps touch this plan's seams).
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
| Drawdown kill-switch | `wagering/portfolio.py` | ~~`should_halt` exists and is tested, but nothing supplies `current_bankroll` / `peak_bankroll`~~ **Wired 2026-09-09:** the ledger (§7) supplies both; the runner halts the card explicitly past 30% from the peak. |

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

1. **No bankroll.** ~~`starting_bankroll` is a CLI constant (default 100).~~
   *Closed 2026-09-09 (§7):* the ledger holds the seed, every recommendation,
   every placed bet and every settlement; the runner stakes off its bankroll
   and `--bankroll` only seeds an empty one.
2. **No portfolio view.** Per-game caps only. Fifteen MLB games × game markets
   × props can stack far past any sane aggregate exposure, and correlated
   same-game exposure (a team total, the game total, the opposing pitcher's Ks)
   is capped only within each pass, not across them.
3. **No kill-switch in practice.** ~~The circuit breaker exists but is
   unreachable without bankroll state (gap 1).~~ *Closed with gap 1.*
4. **Game-market CLV is automated; props are not.** The daily grader now
   attaches consensus closes from the hourly odds archive to every game
   bet (`closing_for_slate` in scripts/grade_yesterday.py — all five
   leagues snapshot hourly), and the record chain carries
   `price_clv`/`line_clv` onto the site's Record view. Still open:
   closes for prop bets, and automated per-market decay alerts.
5. **One global `min_edge`.** DESIGN §6.2 calls for thresholds sized to
   estimation error (higher for props/NCAAF); one number (0.02) used to serve
   every market. *(Now wired: `SlateConfig.min_edge_by_market` overrides the
   global per market in both slates; the runner defaults the prop bar to 2×
   the game bar (`--prop-min-edge`), takes `--min-edge-market MARKET=EDGE`
   overrides, and sits NCAAF spreads out per the backtest's no-edge finding.
   The remaining gap is W4's re-tune: replacing the reasoned 2× prop
   multiplier with archive-fitted per-market values.)*
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
- **Ceilings on selection, paper below them (2026-09).** The staked slate
  refuses to stake an edge above 0.12 absolute or 50% of the fair
  probability — the adverse-selection guard the publish gate already
  applied (`PUBLISH_GATE.md` §2), moved to where the money is. A row past a
  ceiling, or on a market without a promoted edge (team totals; every
  market in NCAAB/NHL/WNBA), is *paper*: priced, logged and graded for CLV at
  stake zero with the reason on the ticket (`Bet.note`). Paper rows never
  become parlay legs and never post. The reasoning and the numbers are in
  `STRATEGY_REVIEW.md` §1–§4.
- **The ledger is append-only.** Corrections are new records, so every
  bankroll number remains reproducible from history.

---

## 4. Immediate next step

~~Land **Phase W1** (the ledger).~~ Landed 2026-09-09 (§7), and **W3** the
same day (§8). Next is **W4**'s re-tune loop, now that the monitor names the
markets to re-tune — and the DoD clocks: two consecutive slates off the ledger
bankroll, seven consecutive automated grades on the health page.

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

## 6. Addendum (2026-08): research-driven upgrades landed early

Three pieces of the W2/W3 plan shipped ahead of the ledger, driven by the
edge research (docs/EDGE_RESEARCH.md):

- **Portfolio sizing (W2's sizing half).** The live runner now routes the
  combined game + prop card through `portfolio.size_portfolio` — one
  correlation group per game, correlation de-scaling at ρ=0.5, the per-game
  cap, and the 25% aggregate slate cap — printing an exposure summary and
  persisting the sized card as `portfolio_{league}_{stamp}.parquet`. The
  per-slate parquets keep solo-Kelly stakes for backtest comparability; the
  portfolio card is the number to bet. The kill-switch remains unreachable
  until W1 supplies bankroll state, exactly as §1.2 said.
- **Per-market CLV trust (a W3 monitor building block).**
  `eval.metrics.clv_by_market` reports per-market CLV with a `clv_trusted`
  flag: CLV is the yardstick only where the close is efficient
  (spread/total/moneyline); props and team totals close on numbers few
  sharps price, so those rows say "judge on P/L instead". Printed by
  `run_backtest_local.py` whenever a ledger exists.
- **Sweep-family FDR (the §3 multiple-comparisons rule, made executable).**
  `eval.metrics.benjamini_hochberg` bounds the false-discovery rate across a
  sweep family; the standing budget is ~45 variants per 5 years of data
  before overfit is near-certain (Bailey/López de Prado).

## 7. Addendum (2026-09): W1 landed — the ledger

`velocity/wagering/ledger.py`, `scripts/ledger.py`, and the wiring in the
runner, the grader, the workflow and the site (docs/STRATEGY_REVIEW.md S5).

**The record.** One private parquet, append-only, five record types:
`seed` (the opening bankroll, written once), `adjust` (a deposit, withdrawal
or correction — corrections are new records), `recommended` (every row of a
run's sized card at the stake the portfolio rules gave it; paper rows at
zero with their reason), `placed` (a bet actually taken — price, stake,
book, number — or a `skip` at stake zero), and `settled` (a placed bet
graded: `+stake·b` / `−stake` / 0 / the exchange's 50c tie rule, summed over
the bet's placements; one row per bet, so re-grading settles nothing twice).
A bet's identity is `league|game|market|side|player` — no stamp, so a bet
recommended Wednesday, placed Thursday and settled Monday is one bet.
Records merge by identity, so every copy of the ledger unions into the same
ledger; nothing can overwrite anything.

**Identity carries the number; implied probability decides whether to place.**
Two jobs, two mechanisms, and conflating them is what made the first live
exchange run (E8c) place one bet all night while nineteen exchange rows across
NFL and NCAAF were held.

- The **id** is `league|game|market|side|player|point`. The number belongs
  there because *settlement* needs it: a total landing on 30 wins an under 44.5
  and loses an under 25.5, so two rungs that can disagree must settle apart.
  The venue does not, because settlement does not care — the same number at two
  books is one outcome, and pooling them is what gives the settled row its
  stake-weighted price.
- Whether a new number on a view you already hold becomes a *second bet* is
  decided on the **price's implied probability**, not the number. A book moving
  a total 44.5 → 45.5 re-prices to hold roughly −110: the number moved, the
  implied probability did not, and placing again would double a directional
  view for nothing. A ladder rung at +900 is 10% against 52% and is plainly a
  different bet. `SAME_POSITION_TOLERANCE` is 20% *relative* — a six-point gap
  is nothing at even money and is the whole bet at a tenth, the same lesson the
  E8b gate learned. `Ledger.holding()` is the one place that answers it, so the
  card and the operator's to-do cannot drift apart.

An id written before the number joined it is a bare view, and still resolves:
`place()` and `settle()` match it to the one bet on that view, so a position
opened under the old scheme is neither re-placed nor left unsettled. The short
form is also what an operator might type off an older to-do; when it names more
than one contract, the error says which.

Two more things follow, both found on that same first live run:

- **One view can be bought as several contracts.** `place()` must not fill
  missing terms
  from whichever record is newest — doing that booked a −110 bet at 44.5 as a
  +900 longshot at 25.5, which is a wrong ledger, a wrong bankroll and a wrong
  CLV at once. It now refuses and names the contracts on record; pass `book=`
  and `point=` to say which was taken. The runner's auto path passes the row's
  own terms rather than relying on inheritance at all.
- **Being held has to be visible.** A row held because an equivalently priced
  bet on the same view is already open used to show only as a count. The card
  carries `held_by`, and the run names what is holding each one —
  `lowvig 45.5 held by lowvig 44.5` — so a venue that never places says why
  instead of leaving it to be inferred from an arithmetic that does not add up.

**The views.** Current bankroll (seed + adjustments + settlements), peak,
drawdown, open exposure (placed money with no settlement), P&L by league and
market, and the operator's to-do (the newest card with each row's status:
open / placed / skipped / settled / paper).

**The runner.** `--ledger PATH` makes the bankroll the ledger's; `--bankroll`
seeds an empty one and is otherwise ignored. The sized card goes through
`size_portfolio` with the ledger's current and peak bankroll, so
`should_halt` finally trips — and trips *explicitly*: the log says
`KILL-SWITCH — halted: drawdown 35% ≥ 30%`, every stake on the card is zero,
the card carries `halted` and the reason as its note, and the site shows a
red banner. Open bets on games off today's card count against the slate
cap; a bet already on the books from an earlier card this week is *held*,
not doubled. Two modes: `manual` (the spec's — only what the operator
records is placed) and `auto` (every staked row is booked at its
recommended terms, so the bankroll compounds off the card while nobody is
placing by hand; the workflow's default, labeled as such on the site). The
modes are exclusive by design — switch to manual the day you start placing.

**The grader.** `--ledger PATH` settles open bets from the day's grade by
bet identity, carrying the close and CLV it was graded against; any open
game bet the graded slate no longer lists settles straight from the finals
(`Bet.grade`). Props wait for box scores. Re-runs are no-ops.

**Durability.** The durable copy sits beside the season chain in R2
(`velocity-wasm/ledger/ledger.parquet`), fetched before the grade and parked
after the slates; every slate artifact carries a copy, and the run merges
every copy it can see. `scripts/ledger.py pull / push` give the operator the
same round trip (`push` merges the remote in first). Skipped without the
Cloudflare token: the runner then stakes against `--bankroll` as before.

**The operator's loop** (docs/LAUNCH.md): `pull` → `todo` → `place` /
`skip` → `push`. `settle --result` and `adjust --amount` are the manual
corrections.

**Tests.** `tests/test_ledger.py` (the W1 list: exact-value round trip,
idempotent re-settlement, pending leaves the bankroll alone, the peak
through win–loss–win, a clean seed; plus finals settlement, ties, merges),
`tests/test_ledger_cli.py`, the runner's held/halt/exposure-room tests in
`tests/test_portfolio_slate.py`, the grader's settlement in
`tests/test_grade_chain.py`, and the site tables in
`tests/test_build_site_data.py`.

**Still W2.** The sizing half of W2 shipped in §6; with the kill-switch
and open exposure wired here, W2's remaining ask is the exposure-summary
block's drawdown state, which the runner's ledger line now prints.

## 8. Addendum (2026-09): W3 landed — the monitor

`velocity/report/monitor.py`, appended to the daily grade
(`scripts/grade_yesterday.py`), on the site under **Record** as *Market
health*, with the flag count pushed to the rail and each flag marked on the
market it is about in the game sheet (docs/SITE.md, *Market health*;
docs/STRATEGY_REVIEW.md S6).

**What it reads.** The season chain the grader carries (S1's durable copy),
which now also records what the model *claimed* on every play (`p_model`,
`p_fair` joined `RECORD_COLUMNS`). Settled rows with a stake move a
market's money; paper rows still count toward CLV.

**What it computes.** Per market, over trailing 7- and 30-day windows: bets,
staked, profit, ROI, mean line and price CLV, the share of bets beating the
close, the claimed probability against the realized win rate on decided
bets, and the flags:

- *negative CLV* — trusted markets (spread / total / moneyline) losing to
  the close; a one-sided test on the mean CLV, confirmed only where it
  survives Benjamini–Hochberg across the window's markets (§3's
  multiple-comparisons rule made executable); a raw rejection that does
  not survive reads *unconfirmed*.
- *negative ROI* — the same test on per-bet return; the read for markets
  whose close is not a yardstick.
- *overclaims by x* — realized win rate more than 0.05 below the claimed
  probability on ≥ 20 decided bets: the shrink or anchoring weight has
  drifted from what the record earns.
- *exclusion candidate* — an untrusted market with a confirmed negative
  30-day ROI: the `total_bases` pattern. Exclusion still needs the flag
  across two review windows (§3).
- *thin* — under 20 bets; nothing else is said.

**Where it lands.** `monitor_{league}_{stamp}.parquet` in every grading
run's artifact (the site's `market_health` table) and the table in the
grade's log, flagged markets first. `tests/test_monitor.py` runs the whole
thing on a synthetic chain with a spread market that beats the close for a
month and loses to it for a week (the 7-day window flags, the 30-day does
not), a prop that loses while claiming 0.62 (exclusion candidate,
overclaims), a thin market, paper and pending rows, and empty chains.

**Not yet.** The DoD's retroactive `total_bases` verification needs the MLB
archive graded back through the chain; the DoD's seven consecutive runs
start with the next grade.


## 9. Addendum (2026-09): same-game correlation, measured

`size_portfolio` de-scales exposure inside a correlation group by
`1/(1+(n−1)ρ)`, and the live runner groups by game with a single
`group_correlation = 0.5` for every pair on it. That constant was set when
one bet per view was the rule. An exchange ladder broke that: a card can
now hold several contracts on one game's total, so what ρ *is* for a given
pair decides real stake.

`velocity/eval/correlation.py` measures it; `scripts/measure_correlation.py`
runs it. **Staking is unchanged** — the flat 0.5 still applies. The
measurement is deliberately not wired into the sizing path, because the
dominant correction *raises* exposure and that decision wants the realized
table next to the historical one.

The quantity is the correlation between the two bets' **win indicators**,
which is what inflates the variance of a combined stake. Bets are defined
against each game's closing number (the sharpest per-game expectation
available, so this isolates the shape of joint outcomes rather than the
model's aim — the same reasoning as the E8 ladder gate), pushes dropped on
both legs, 95% intervals bootstrapped over games.

### 9.1 What it says

**The flat 0.5 is wrong in both directions, and the biggest error is on the
pair a card carries most often.** Spread and total on the same game are
close to independent: NFL ρ **−0.006**, CI [−0.034, +0.027] on 3,952
non-push games, and every one of fifteen seasons admits zero. De-scaling
that pair as if it were half-correlated under-bets both legs by ~34%.

**That pair must be read from the favourite's side.** A first pass measured
*home* covers × over and got +0.023 — the home-field artifact. Restated from
the favourite it is −0.006. The two framings disagree because a home
favourite and a home dog contribute mirror images that cancel, which is
exactly how a real effect hides inside a near-zero pooled number.
`tests/test_correlation.py` pins this with a fixture built so the framings
disagree by construction (ρ = +1 favourite-relative, 0 home-relative).

**The exception is blowout pricing, and only in college.** NCAAF
favourite-covers × over is +0.067 pooled, which represents no actual game:

| NCAAF favourite | n | ρ | 95% CI |
|---|---|---|---|
| 0–3 | 1,373 | +0.018 | [−0.035, +0.070] |
| 3–7 | 2,796 | +0.001 | [−0.036, +0.036] |
| 7–10 | 1,519 | +0.014 | [−0.038, +0.065] |
| 10–14 | 1,514 | +0.006 | [−0.045, +0.058] |
| **14–21** | 1,928 | **+0.102** | [+0.056, +0.145] |
| **21–28** | 1,121 | **+0.185** | [+0.127, +0.243] |
| **28+** | 1,132 | **+0.260** | [+0.205, +0.316] |

Flat under 14 points, monotone above it. Physically plain: a four-touchdown
college favourite covering *is* a game with points in it. Stable season by
season inside the big-favourite slice (sd 0.065 across eleven seasons), so
the apparent recent upward "trend" in the pooled number is a mix effect —
the dataset carries more mismatches lately, not a changing game. NFL prices
a favourite that big in 2–5% of games and shows nothing at any size.

**The ladder curve is the opposite story and far better determined.** Two
rungs on the same side of one total, pooled ρ with the per-season spread:

| apart | NFL ρ | NFL sd | NCAAF ρ | NCAAF sd | stake err at 0.5 (NFL / NCAAF) |
|---|---|---|---|---|---|
| 2p | +0.878 | 0.027 | +0.912 | 0.016 | +25% / +28% |
| 4p | +0.766 | 0.027 | +0.815 | 0.012 | +18% / +21% |
| 8p | +0.588 | 0.039 | +0.655 | 0.022 | +6% / +10% |
| 12p | +0.443 | 0.036 | +0.530 | 0.015 | −4% / +2% |
| 16p | +0.315 | 0.032 | +0.417 | 0.021 | −12% / −6% |
| 20p | +0.220 | 0.036 | +0.320 | 0.020 | −19% / −12% |

Positive `stake err` means the flat 0.5 stakes too much. The practical
consequence runs against the worry that prompted the measurement: **our own
rungs are not the correlated ones.** A rung priced at +900 is priced there
because it sits sixteen to twenty points off the book number, and that is
where ρ has already fallen to 0.22–0.42. The flat 0.5 over-de-scales the
exchange legs too. The pairs genuinely near ρ 0.9 are two rungs within a
couple of points of each other, which the E8b gate and the ledger's 20%
`SAME_POSITION_TOLERANCE` largely prevent us from holding at all.

**Opposite sides are negatively correlated and the formula cannot say so.**
An exchange `under 25.5` against a book `over 44.5` cannot both lose: NFL
−0.588 at eight points apart, NCAAF −0.655. A *middle* (`over 36.5` against
`under 44.5`) both-wins 19–23% of the time at −0.61 / −0.67. Applying a
positive ρ charges a hedge for adding risk it removes. Hedge and middle are
separate pair classes (`pair_class`): a side-only label collapses them, and
they are different bets — one has a both-win region of zero, the other a
both-lose region of zero.

A realistic three-leg game group — spread, total, one rung sixteen points
off — has mean pairwise ρ of −0.11 (NFL) / −0.14 (NCAAF) against the 0.5
applied. Floored at zero that group's scale is 1.00 against the 0.50 the
flat assumption gives it: a 50% under-bet.

### 9.2 Why a measured ρ should not be used naked

These legs share a **model**, not only a game. If the sim is wrong about a
game's pace, the over and the team total are both wrong even though their
outcomes are near-independent under the true distribution. Outcome
correlation is not correlation of our *edge estimates*, and the second is
what loses money together. Any staking use of this table wants a floor
under it — `ρ_eff = max(floor, measured)` — for that reason alone. At a
floor of 0.25 the two-leg spread+total pair moves 0.67 → 0.80 (+20% stake)
rather than → 1.00 (+50%), and the tightening of near rungs is unaffected
either way.

### 9.3 What is still missing

**Zero graded rungs.** Run 106's own log: `NFL market health: nothing
settled in the window`, the same for NCAAF, `season record: 0 settled
row(s)` in both football leagues, and all 46 settled ledger bets are
MLB/WNBA. Football exchange positions were opened after the games that have
graded, so the realized half of the measurement is empty and is *correctly*
empty — `realized_correlation` returns `[]` rather than reporting a ρ from
nothing.

The ledger already carries what the live measurement needs on settled rows
(`game_id`, `market`, `side`, `point`, `book`, `result`), so no schema
change is pending — `measure_correlation.py --ledger` produces the realized
table as soon as football settles. The first rungs to grade are on the
current card.

Until then the historical table stands on its own for the ladder curve (sd
0.012–0.039 per season; nothing further to confirm) and for NFL spread×total
(fifteen seasons, all admitting zero). What wants live evidence is which
pair classes our cards actually end up holding, and at what venues — the
question the datasets cannot answer because they do not know what we bet.
