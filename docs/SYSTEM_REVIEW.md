# Velocity — System Review: every model, market and DFS format

**Status:** Mechanics review + build outline (v0.1), written 2026-09-09, the
day before NFL Week 1. Companion to [`STRATEGY_REVIEW.md`](STRATEGY_REVIEW.md),
which covered wagering *policy* and the site; this one goes underneath it —
the simulation engine, each game model, the prop engine, every DFS format,
the pricing seams, and the data pipeline they all sit on.
**Principle:** as before — every finding carries the number that produced it,
from the committed datasets, the live boards of the day, or the CI logs.
Nothing here changes code; §8 is the ordered change list.

---

## 0. The verdict — what is wrong *today*, ordered by money × certainty

1. **Eleven of the 32 NFL teams are priced for Week 1 with the wrong
   quarterback.** The QB-adjusted fit detects each team's starter as "the
   primary passer in its latest training game" — which for a team that
   clinched and rested in Week 18 is the backup. Kansas City is priced with
   Chris Oladokun (QB effect −0.073) instead of Patrick Mahomes: **5.6 points
   a game off KC's expected score.** Denver (Stidham for Nix, −3.4), the Jets
   (Cook for Fields, −2.7), Miami (Ewers for Tagovailoa, −2.3), Dallas
   (Milton for Prescott, −2.2). No rule that reads only last season's plays
   can see an offseason; the fix is an explicit starter source (§3.1).
2. **The whole weekly-projection surface is dormant for Week 1.** The
   FantasyPros week resolver reads `datasets/nfl/games.parquet`, which holds
   **zero 2026 rows** — `refresh_datasets.py` commits played games only — so
   on Sep 8 it saw no kickoff ahead and fetched week 0 (season-long). The
   prop slate, the NFL DFS classic and showdown builds, and the pick'em
   board all refuse season totals (correctly) and skip. It self-heals for
   Week 2 only after Week 1 is played and refreshed (§1.1).
3. **NCAAF 2025 play-by-play is missing entirely.** `plays.parquet` carries
   2024 (120k plays, 916 games), nothing for 2025, and 13k plays of 2026. The
   promoted college blend's EPA half is fit across a full-season hole
   immediately before the season it prices (§1.2).
4. **Neutral sites are never passed live.** Every wrapper model accepts
   `neutral_site`; `project_board` never supplies it. 71 of 1,601 NCAAF games
   in 2025 (4.4%) and 7–8 NFL games a season (the international slate) are
   priced with home-field advantage nobody has (§3.2).
5. **The simulation is one Gaussian per league.** Residual sd on the
   committed closes rises **15.0 → 17.9** across NCAAF closing-total quintiles
   while the sim uses one constant; and the Gaussian shape is what the E8
   ladder gate exists to work around. Both are fixable at the source (§2).
6. **The NFL DFS surface is running on its weakest configuration:** the
   classic roster fills DST at 0.0 points, the GPP builder is called without
   sim samples so its tail scoring never runs, milestone bonuses are excluded
   from every projection when the correlated sim can price them, and the
   live projection is FantasyPros consensus rather than the rate model the
   backtest validated (§6).

Everything in §0 is small to fix. The larger, lab-gated work follows in §8.

---

## 1. The data pipeline — the foundation everything sits on

| | Finding | Number | Consequence |
|---|---|---|---|
| 1.1 | `datasets/nfl/games.parquet` has no current-season schedule | 0 rows for 2026; `refresh_datasets.py` keeps played games only, by design | `current_week()` → 0 → season-long FP snapshot → props, NFL DFS, pick'em all skip for Week 1. Fix: resolve the week from the free nflverse schedules CSV (unplayed rows included — `NFLVERSE_SCHEDULE_URL` is already imported there), or commit a `schedule_{season}` frame beside `games`. |
| 1.2 | NCAAF plays skip 2025 | seasons present: …2023, 2024, 2026 | The EPA half of the promoted blend (`compress_plays` cells, λ=50) has no 2025 signal. `refresh-datasets.yml` already has a `ncaaf_pbp_seasons` dispatch input — run it for 2025. |
| 1.3 | No garbage-time information in the plays frame | columns: play_id … epa, success, passer_player_id — no `wp`, `qtr`, `score_differential` | Standard practice (nfelo, PFF) down-weights or drops plays at extreme win probability; the fit cannot. Needs a dataset rebuild keeping `wp`/`qtr`, then a lab variant (§3.5). |
| 1.4 | `neutral_site` is in both games frames and never joined at slate time | NCAAF 2025: 71 neutral games; NFL: 7–8/season | §3.2. |
| 1.5 | Injuries: NFL only (FantasyPros) | 211 rows, 208 outs on Sep 8 | College QB injuries — the largest single swing in that sport — are unpriced and un-vetoed. No free NCAAF injury source exists; accepted, recorded. |
| 1.6 | The odds shop is 11 US books, no market-maker | draftkings, fanduel, betmgm, williamhill_us, betrivers, fanatics, bovada, betonlineag, lowvig, betus, mybookieag | The de-vig anchor is a soft two-way, and the CLV close is a soft-book median — `EDGE_RESEARCH.md` §1.1 puts the overstatement at ~4.5%. Pinnacle is the `eu` region; the credit cost of adding one region is 2×, to be measured against the collector's remaining budget (§4.3). |

---

## 2. The simulation engine (`models/simulate.py`)

The one engine prices every derivative in every league, so its shape is the
shape of every edge.

**2.1 Homoscedastic.** One `sd_total`/`sd_margin` per league regardless of
the matchup. Measured on the committed closes (2015+), residual sd by
closing-total quintile:

| | closing total | NFL sd (total / margin) | NCAAF sd (total / margin) |
|---|---|---|---|
| lowest | ~40 / ~44 | 12.9 / 12.9 | **15.0** / 14.9 |
| middle | ~45 / ~54 | 13.1 / 13.0 | 15.9 / 15.2 |
| highest | ~52 / ~66 | 13.6 / 12.5 | **17.9** / 16.2 |

NFL is nearly flat (the constants are fine). NCAAF totals are 19% wider in
shootouts than in slugfests, and the sim prices a 44-point game and a
66-point game with the same 16.7. That is exactly the tail-rung and
team-total pricing the exchange board buys. Fix: `sd_total = a + b·μ_total`
fit from the residuals (a lab variant with a calibration-error gate, the same
gate that promoted 18.2/16.7).

**2.2 Gaussian.** E8 measured the shape error a normal makes at every offset:
leptokurtic in the shoulders (3pp at NFL ±4.5), fatter than normal past ~15.
The ladder gate refuses rungs where that error exceeds a tolerance — a
bandaid over a known-wrong distribution. The fix at the source is a
**nonparametric residual draw**: bank the walk-forward standardized residuals
per league (they already exist — the same run that produced the sd constants),
and draw `(margin, total)` residual pairs from that pool scaled by the
(heteroscedastic) sd, instead of from a bivariate normal. Real football
margins land on real football numbers (3, 7, 10) by construction, the tails
are the tails the league actually produces, and the ladder gate becomes a
check that passes rather than a filter that shapes the card. Deterministic
under the seed like everything else.

**2.3 Team scores by symmetric split.** `home = (total + margin)/2`, clipped
at zero, rounded. The clip is what carries the "censoring correction" the
team-totals thesis relies on; it is a one-sided fix (the other team's score
is not re-balanced, so the total is no longer the total). Second-order; note
it, revisit with 2.2.

---

## 3. The game models

**3.1 The starter (NFL).** `fit_qb_ratings` prices the team's *latest-game*
primary passer. On the 2025 regular-season leading passer as a proxy for the
true starter, 11 of 32 teams mismatch for Week 1 — the proxy has its own
misses (SF's leading passer was Mac Jones because Purdy was hurt, so Purdy is
right), but the eight Week-18-rest cases (KC, DEN, NYJ, MIA, DAL, IND, TEN,
WAS) are unambiguous, and the same mechanism mis-prices every in-season
injury and every offseason move until the new starter has played. The fix is
an explicit starter map at projection time: the FantasyPros projections
snapshot (even the season-long one) names each team's QB1 by projected pass
attempts; map that name onto the nflverse id through `player_weeks`, pass it
as `qb_id` (the hook already exists in `matchup_delta`), and let the injuries
snapshot demote an Out to the next name. The intel layer's QB veto measured
47.9% on exactly these bets (`BACKTEST_INTEL.md`) — the projection should stop
producing them, not just flag them. **A QB swap also moves every receiver's
prop and every DFS projection on that team**, so this is the highest-leverage
single change in the repo.

**3.2 Neutral sites.** All five wrapper `project()` signatures take
`neutral_site`; the live closures pass `(home, away, kickoff)`. The current
season's games frame carries the flag (NCAAF: CFBD; NFL: nflverse) — join by
team pair and date at slate time, exactly as `align_game_ids` does for the
exchanges. Effect per game: NFL 2.0 pts, NCAAF 2.5 (EPA half) blended with
the scores fit's learned edge.

**3.3 College pace is a constant.** The live EPA half is `NFLModelConfig(…,
plays_per_game=65.0)`. Real 2024 pace: mean 63.8, **sd 6.9, p10 54.5, p90
71.0** across 230 teams. `team_pace` / `matchup_pace` and the pace-aware
`NCAAFGameModel` are built, tested, and used only by the backtest scripts —
never by the live runner. A tempo team's total is under-projected by
~10% of its scoring before any rating is consulted. Lab variant: wire pace
into the EPA half, gate on Brier + O/U at the totals filter.

**3.4 Home field, two answers.** NFL: constant 2.0 vs learned 2.26 — fine.
NCAAF: the EPA half adds 2.5; the scores fit learns **4.85 (2022+) / 5.77
(2024+)**; the blend averages them. One of those is wrong, and neither was
chosen by a sweep. Lab: fit the home dummy inside the EPA ridge (one column)
and share a single HFA across the blend; per-venue HFA (altitude, the
documented NCAAB pattern) is the follow-up.

**3.5 Garbage time.** After 1.3, a lab variant that down-weights plays at
`wp` < 0.05 or > 0.95 (or fourth-quarter plays at ≥ 3 scores). Literature
says it sharpens EPA ratings; the lab decides.

**3.6 The other verticals.** NHL prices goalie-neutral (no free confirmed
starter feed; DailyFaceoff is the known follow-up). NCAAB and WNBA are
lab-gated and in the content posture — nothing to change until their
in-season CLV says otherwise.

---

## 4. The pricing chain — seams between model and stake

**4.1 A flat probability edge is loose on longshots.** `evaluate()` requires
`edge ≥ 0.02` in probability regardless of price. At +1000 (fair ≈ 0.087)
that is a 23% relative edge and an EV of ~+22%; at −300 it is 2.7% relative.
The same threshold is a different bet at different prices — which is why the
NCAAF card fills with dogs (`STRATEGY_REVIEW.md` §1.2). Parlays and pick'em
already gate on EV; singles should too: a `min_ev` floor **and** a relative
edge floor (`edge / p_fair ≥ r`) alongside the absolute one.

**4.2 De-vig.** The game slate uses `multiplicative` (the `SlateConfig`
default) against the *same book's* opposite side. The Methods page says
"worst case across multiplicative, additive, Shin, and power" — that is what
pick'em does, not the slate. Two corrections: (a) fix the page; (b) note the
direction — for a pick'em leg the conservative fair probability is the
*minimum* across methods (it is the hit rate being bet), but for an EV gate
the conservative fair probability is the *maximum* (it is subtracted from the
model's), so a worst-case devig for singles is the opposite helper. And on a
+2400 / −5000 pair one soft book's two sides are a noisy anchor; de-vig
against the cross-book consensus — or the market-maker, once one is in the
shop (1.6).

**4.3 The CLV yardstick.** `closing_for_slate` takes the median close across
all 11 soft books. Grade against Pinnacle where available and fall back to
the median; carry a `close_source` column so the site can say which.

**4.4 The publish gate's drift rule is inert.** `current_prices()` reads the
same single snapshot the slate priced from, so "the newest price" is whichever
book's row happens to be last — and since the slate shopped the *best* price,
drift is always ≤ 0. The rule never withdraws a play. The previous hourly
snapshot is already downloaded into `artifacts/odds` for grading; compare
against *its* consensus and the rule means what it says.

**4.5 Portfolio: model risk has no group.** Correlation groups are game ids.
Sixty-six moneyline dogs from one sim's tail are sixty-six "independent"
bets to the sizer. Add a market-class group (or a per-class cap in
`PortfolioConfig`) so one model's one assumption cannot take 60% of a card.

**4.6 Parlays** inherit every leg's error and multiply it; `STRATEGY_REVIEW`
S2 restricts legs to promoted markets. After 2.2 the same-game EV also stops
compounding a wrong tail.

---

## 5. Props

**5.1 Dormant for Week 1** — 1.1.

**5.2 Dispersion is a prior, not a fit.** `FootballPropConfig` states it
plainly: lognormal volume σ 0.18/0.25, per-catch yards sd 6.0, rushing sd
`max(0.45·μ, 8)` as a *normal* — "honest priors, not fitted constants". The
bank to fit them from is committed: `player_weeks.parquet`, **112,450
player-weeks 2020–2025** with receptions, targets, receiving yards, carries,
rush yards, attempts, pass yards. Fit per position: NegBin `r` for receptions
against targets, the per-catch yard sd (a deep threat is not a slot
receiver), and the *skew* of rushing yards (right-skewed by long runs — a
normal under-prices every alt-line over). One script, one table, and the
prop sim prices real spread instead of a guess.

**5.3 The QB is the volume.** 3.1 applies here twice over: a receiver's
targets ride the passer, and a backup at QB moves every receiving prop on
the team.

**5.4 Prop closes are banked and unattached.** The props collector snapshots
twice daily; grading attaches game-market closes only (`WAGERING.md` gap 4).
The `clv_trusted` flag says prop CLV is not the yardstick anyway — but the
attach is what lets the shrink sweep grade against something.

---

## 6. DFS — every format, what it runs on, what it is missing

| Format | Runs on | Backtest | Gap |
|---|---|---|---|
| **NFL classic** (242k entries/day) | FP consensus × linear DK scoring; **DST at 0.0** | none — "refuses classic … no team-defense projection" | 6.1, 6.2, 6.3, 6.4 |
| **NFL showdown** | same | +1.04 pts over DK's pricing, t = 0.82 — no edge | 6.4, 6.5 |
| **CFB classic / showdown** | FP — which has **no CFB projections** | none | never builds; 6.6 |
| **MLB classic / showdown** | contextual rate model + confirmed cards | **+13.06 (t = 5.77) / +11.51 (t = 5.83)** | the strongest measured edge in the repo; season runs to Oct 4 |
| **Tiers / Single Stat** | projection ranking | HR: top-3 homer 24.6% vs 11.5% field | 6.7 |
| **GPP portfolio** | jittered knapsack + stack rules | best entry 77th pct on one slate | 6.2 |

**6.1 A DST projection.** DK's classic roster needs one; the pool joins DST
at 0.0 so the optimizer takes the cheapest defense and the lineup total
ignores 5–15 points of variance. The game sim already produces the
opponent's score distribution, which prices DK's points-allowed brackets
*exactly*; sacks, takeaways and defensive scores are team rates from the
committed plays and player-weeks. One model, one week of work, and the
largest DFS pool DK runs stops being "plumbing confidence".

**6.2 GPP tail scoring is dormant.** `build_gpp_portfolio(pool, spec=…,
rng=…)` — no `samples`. Without them selection is by projected points, and
the entire correlated-tail machinery (`tail_score`, the reason the builder
exists) never runs. The correlated prop sim (`game_props`) already produces
per-player sample arrays; scoring them with `nfl_dk_points` per simulation
gives exactly the `samples` mapping the builder wants.

**6.3 Milestone bonuses.** Excluded from projections because "a bonus is a
tail event and adding it at the mean overstates every player" — true of a
linear pass, false of the sim: E[bonus] = 3·P(yards ≥ 100) is read straight
off the sample array. 6.2's samples give bonus-inclusive means for free.

**6.4 Which projection?** The showdown backtest validated the empirical-Bayes
rate model (`DfsNflModel`, within-week r = 0.57); the live lineup uses
FantasyPros consensus through `dk_expected_points`. Neither beat DK's pricing.
Run the two head-to-head on the 1,189 harvested 2025 boards — the harness
(`validate_dfs_lineups.py`) exists — and ship the winner, or the blend.

**6.5 Where an NFL DFS edge could come from.** The backtest's own verdict:
not projection accuracy — "ownership leverage, correlation, or the inactive
report". Correlation is 6.2. Inactives are the Sunday pre-kick injuries
snapshot already collected. Ownership has no model and no free feed wired;
lowest priority.

**6.6 CFB DFS never builds.** FantasyPros has no college projections
endpoint (the collector skips it by design). Either drop the CFB specs from
the default league list, or build a CFB player projection from CFBD player
stats — a real model, not a plumbing fix.

**6.7 A new single-stat format on the board.** Today's DK snapshot carried
"Single Stat - Total Yards" (NFL), which `build_dfs_tiered` saw and skipped
as unknown. The prop sim produces yards distributions; ranking by expected
total yards is the same shape as the HR contest and a small addition to the
tiered builder.

---

## 7. Grading and evaluation

- NCAAF grading fails in CI (`STRATEGY_REVIEW` S1).
- Prop closes banked, unattached (5.4).
- A slate-path walk-forward with real multi-book lines is not yet possible —
  the committed football lines are single closes — but the hourly archive
  has banked since August; by mid-season a genuine backtest of the live
  policy (prices, vig, shopping, caps) becomes possible for the first time.
  Worth planning as the arbiter for §4.

---

## 8. What to do — ordered, as PRs

Numbered M0–M6 so they do not collide with `STRATEGY_REVIEW.md`'s S1–S6.
M0 is the set that should land before Week 1 kicks off.

### M0 — Before kickoff (four small PRs, all plumbing) — **landed 2026-09-09**

Shipped as one PR the afternoon before Week 1. What the live run showed once
the pieces were in: the starter map re-priced **12 of 32 teams** — the eight
Week-18 rest cases plus offseason moves the leading-passer proxy could not
know (Tagovailoa → ATL, Murray → MIN, Geno Smith → NYJ, Watson at CLE); the
nflverse schedule resolved Week 1 and flagged the Melbourne SF–LA game as
neutral; NCAAF 2025 plays are on file (125,315 plays, 934 games). One lesson
banked on the way: the one-off pbp backfill script *overwrote* the plays
file with the seasons it fetched, taking 2015–2024 with it; the file was
restored from a pre-backfill checkout and the script now merges by season.

- **Starter map** (3.1): FP QB1 → nflverse id → `qb_id`; injuries Out →
  next name. Test: KC projects with Mahomes' effect; a fixture Out demotes.
- **Current-season schedule** (1.1): `current_week` reads the nflverse
  schedules CSV when the committed frame has no upcoming kickoff; the
  collector re-runs for Week 1. Test: Sep 8 resolves to week 1.
- **NCAAF 2025 plays** (1.2): dispatch `refresh-datasets.yml` with
  `ncaaf_pbp_seasons=2025`; assert per-season play counts in a test.
- **Neutral flag** (3.2): join at slate time; pass through every closure.
  Test: a neutral fixture game projects with zero HFA.

### M1 — The sim (lab-gated, one PR)
Heteroscedastic sd (2.1) and the nonparametric residual draw (2.2), each a
lab variant gated on calibration error and Brier exactly as 18.2/16.7 was.
DoD: E8's offset-error table re-measured on the new draw shows the
shoulders inside tolerance without the gate.

### M2 — Pricing seams (one PR)
`min_ev` + relative edge for singles (4.1); consensus de-vig and the Methods
correction (4.2); drift vs the previous snapshot (4.4); a market-class group
in `PortfolioConfig` (4.5). Tests pin each default; the NCAAF card re-run
concentrates in totals.

### M3 — Props (one PR)
Dispersion fit from `player_weeks` (5.2) → `FootballPropConfig` values with
the table in `PROPS.md`; prop-close attach in the grader (5.4).

### M4 — DFS (two PRs)
(a) DST model (6.1) + sim-scored GPP and bonus-inclusive means (6.2, 6.3)
+ Total Yards single stat (6.7). (b) The projection head-to-head on the
harvested boards (6.4) and a decision on CFB DFS (6.6).

### M5 — College model lab (one PR)
Pace into the EPA half (3.3); one HFA across the blend (3.4); garbage-time
rebuild and variant (1.3, 3.5). Promote what wins.

### M6 — The shop (one PR, after a credit-cost check)
Pinnacle via the `eu` region (1.6); sharp-close CLV with `close_source`
(4.3).

---

## 9. What this review does not change

The caps, the intel contract, the promoted totals filter, the publish
floors — as in `STRATEGY_REVIEW.md` §8. And nothing in M1–M6 moves without
its lab table: the whole point of §2–§3 is that the constants in the live
path were chosen, and the repo already has the harness to choose them better.
