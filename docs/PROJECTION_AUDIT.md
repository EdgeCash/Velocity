# Projection audit — is the game model using everything we have?

**Status:** findings + ordered improvement list (v0.1), written 2026-09-17,
ahead of NFL Week 2.
**Scope:** the game-result projection for NFL and NCAAF — the ratings fits
(`features/team.py`, `features/scores.py`), the scoring models
(`models/game_nfl.py`, `models/game_ncaaf.py`, `models/game_scores.py`, the
lab's `BlendedGameModel`), the level (`models/level.py`), the sim
(`models/simulate.py`), the situational wrappers (rest, wind), and the data
all of it reads. Props, DFS and the wagering chain are out of scope except
where the projection's shape leaks into them.
**Principle:** as in `DATA_AUDIT.md` and `SYSTEM_REVIEW.md`, every finding
carries the number that produced it, from the committed datasets. Nothing here
changes code; §3 is the ordered list.

---

## 0. The verdict

`DATA_AUDIT.md` established that every banked column is read by *something*.
This audit asks the narrower question: is the **projection** reading it? Mostly
not. The ratings fit consumes five columns of the NFL plays file
(`posteam`, `defteam`, `epa`, `passer_player_id`, `season/week`) and three of
the college one, and the scoring model turns them into points with three
constants. Everything else the system knows — the injury history, the weather
bank, the schedule's own starter and rest columns, the SP+ special-teams
rating, the per-player usage banks — either reaches the projection through
a wrapper (wind, rest) or never reaches it at all.

Three things were wrong outright, and each is cheap:

1. **A quarter of the plays in the NFL ratings fit are not offensive plays.**
   Kickoffs, punts, field goals, extra points, penalties-with-no-play, kneels
   and spikes — 24.5% of the committed frame — go into the offense/defense
   ridge as if they were scrimmage plays. Kneels carry a mean EPA of −0.58,
   so the teams that kneel most (the ones winning late) are docked for it.
   On 2024–25 the difference between the shipped fit and a scrimmage-only
   fit is **1.4 points of net team strength on average, 2.9 at the extremes**
   (§2.1). No lab variant has ever tested the filter.
2. **The model's totals are over-dispersed by about half.** Regressing actual
   totals on projected totals across the walk-forward residual banks gives a
   slope of **0.48 (NFL) and 0.66 (NCAAF)**: when the model's total sits six
   points above the league mean, the game scores about three above it. The
   one staked strategy in the repo selects on exactly this quantity (§2.2).
3. **The college lab is not the college live model.** The lab's NCAAF blend
   carries no SP+ prior (the live runner does), its EPA half has never seen a
   2025 play (the 2025 games rows carry boxscore-style ids that match 4 of
   934 play game-ids), and since 2022 the games frame is 45% FCS games the
   EPA half cannot see and the live board never prices. Every promoted
   college number — the ≥6 totals record, the anchoring weight, the residual
   bank — was measured on a model that differs from the one running (§2.3).

Below those, the list splits cleanly: data we already download and discard
(§2.4), and structure the design called for that was never built (§2.5).

---

## 1. What the projection reads today

| | NFL | NCAAF |
|---|---|---|
| Ratings input | plays: `posteam`, `defteam`, `epa`, `passer_player_id` (QB dummy), `play_type` (pass rate only) | plays: `posteam`, `defteam`, `epa` (cells); games: scores, `neutral_site` |
| Prior | none (ridge to league average, λ=200 team / 300 QB) | SP+ final ratings as 12 pseudo-games, **scores half only** |
| Recency | half-life 17 weeks, trailing 4 seasons | none (rejected in the games-level lab) |
| Points conversion | `22.5 + 63 × Δ`, level fitted on trailing 2 seasons | EPA half `base + 65 × Δ`; scores half in points; 50/50 blend; both levelled |
| Home field | constant 2.0 | EPA half constant 2.5; scores half learned (~4.9) |
| Situational | bye +1 / short week −1; wind ≥15 mph on totals | none |
| Starter | ESPN depth chart / FantasyPros → `qb_id`; injuries demote an Out | none |
| Sim | bivariate normal, sd 13.0 / 13.6, rounded | bivariate normal, sd 18.2 / 16.7, rounded |

Columns banked in `datasets/` that the projection never reads: `success`,
`down`, `yards_gained` (both plays files); `roof`, `surface` (NFL games);
`temp_mean`, `precip` (NFL weather); all of `injuries.parquet` except through
the starter map; all of `player_weeks` / `player_games`; `special_teams` in
`sp_ratings`; `kickoff` time-of-day.

---

## 2. Findings

### 2.1 The NFL ratings fit includes every play type

`fit_ratings` / `fit_qb_ratings` drop rows with a null team or EPA and keep
the rest. The committed frame (`build_nfl_pbp_datasets.py` keeps any play
with a `posteam` and an `epa`) is:

| play_type | share | mean EPA |
|---|---:|---:|
| pass | 44.8% | +0.022 |
| run | 31.7% | −0.036 |
| kickoff | 6.2% | +0.059 |
| no_play | 5.7% | −0.009 |
| punt | 5.3% | −0.078 |
| extra_point | 2.9% | +0.009 |
| field_goal | 2.4% | +0.057 |
| qb_kneel | 0.9% | **−0.577** |
| qb_spike | 0.2% | −0.093 |

Two of these are unambiguously wrong to price: a kneel is the *winning* team
running out the clock (2024–25 leaders: BUF 52, PHI 49, NE 47, LA 45, KC 41),
and a spike is a two-minute drill. Kickoffs credit the *receiving* team's
offense with the return (nflfastR's `posteam` on a kickoff is the receiver),
and punts, field goals and extra points are special-teams outcomes folded
into the offense and defense columns of the unit that happened to be on the
field.

Refitting the promoted configuration (QB-adjusted, recency-17, λq=300) on
2024–25 with and without the filter, net team strength in points at 63 plays
(offense + starter QB − defense):

| team | all plays | scrimmage only | shift |
|---|---:|---:|---:|
| PHI | 5.60 | 8.46 | −2.86 |
| BUF | 6.32 | 9.09 | −2.77 |
| DEN | 1.61 | 3.80 | −2.19 |
| GB | 4.30 | 6.39 | −2.09 |
| … | | | |
| DAL | −4.04 | −6.57 | +2.53 |
| NYJ | −11.06 | −13.65 | +2.59 |
| TEN | −8.66 | −11.47 | +2.81 |

Mean absolute shift 1.37 points; the good teams are systematically
under-rated and the bad ones over-rated, which is the kneel effect. Whether
special teams belong in a *separate* rating (they do — SP+ carries one) is a
modelling choice; whether kneels belong in the offense rating is not.

The college frame is clean by comparison: 0.9% non-scrimmage (2,191
`End Period` rows at mean EPA −0.40, field goals, return touchdowns).

### 2.2 Slope calibration — the level was fixed, the scale was not

`models/level.py` fits the *intercept* of the projection through the model.
Nothing fits the *slope*. Regressing actual on projected across the committed
walk-forward residual banks (2015+; the promoted models):

| | n | margin slope | total slope |
|---|---:|---:|---:|
| NFL | 3,028 | 0.870 ± 0.039 | **0.479 ± 0.041** |
| NCAAF | 12,212 | 1.145 ± 0.017 | **0.664 ± 0.025** |

A slope of 1 is a calibrated scale. The totals are over-dispersed in both
leagues: the model's total deviations from the league mean carry about twice
the signal they should. By week of season:

| | margin slope | total slope |
|---|---|---|
| NFL wk 1–3 / 4–6 / 7–10 / 11+ | 0.72 / 0.71 / 0.89 / 0.97 | 0.56 / 0.49 / 0.35 / 0.51 |
| NCAAF wk 1–3 / 4–6 / 7–10 / 11+ | 1.19 / 1.09 / 0.97 / 1.10 | 0.55 / 0.65 / 0.76 / 0.68 |

Three readings:

- **NFL early-season margins are over-confident by ~28%.** A trailing-four-
  season fit with a 17-week half-life still trusts last year's roster in
  September; the market does not. It converges by November.
- **NCAAF margins in the lab are under-dispersed early** (1.19 in weeks 1–3),
  which is what a fit with no prior looks like — and the lab has no prior
  (§2.3). Big favourites also win by more than projected at every point in
  the season (top-quartile |μ| ≈ 20: slope 1.13).
- **Totals are the real problem.** The NCAAF ≥6 totals filter selects on
  `|μ_total − close|`, and about a third to a half of `μ_total`'s deviation is
  noise. A calibrated total would move which games clear the filter and would
  let the claimed edge be a claim about a calibrated number rather than a
  weight (`w = 0.13`) fitted to absorb the over-dispersion after the fact.

Honest sizing: a walk-forward slope correction on the *margin* alone moves
moneyline Brier by nothing (NFL 0.22175 → 0.22163; NCAAF flat), because a
symmetric mis-scale barely moves win probabilities. The value is in totals
and in the early-season weeks, and it wants a lab run, not a constant.

### 2.3 The college lab measures a model that is not the live model

Three divergences, all on the NCAAF side:

1. **No SP+ prior in the lab.** `sp_pseudo_games` is applied in
   `run_live_slate.py` only; `lab.py` / `model_lab.py` carry no reference to
   it (only the NCAAB Torvik prior). The promoted K=12 was chosen in a
   one-off sweep (`BACKTEST_NCAAF.md` addendum) and then never carried into
   the variant harness — so `blend-level2`, the residual bank, the sd
   constants and the anchoring sweep all describe a prior-less model.
2. **The lab's EPA half has never seen a 2025 play.** The 2025 rows of
   `games.parquet` were backfilled from the boxscore file and keep its id
   format (`2025_20250823_IowaState_KansasState`); the plays use CFBD's
   numeric ids. The lab's blend cuts plays to `game_id ∈ train_games`, and
   **4 of 1,601** 2025 games match by id (887 of 934 play-games match by
   season/week/teams). The live runner uses every play, so live is fine;
   every backtest projection of 2025 and 2026 was made with an EPA half
   missing the season before it. Anything that joins plays to games by id
   for 2025 (`intel/context.py` does, when scoped) is affected the same way.
3. **The games frame changed composition in 2022.** Through 2021 it is
   FBS-only (plus ~110 FBS-vs-FCS games a season). From 2022 the lines pull
   added every game with a line:

   | season | FBS–FBS | one FCS | FCS–FCS |
   |---|---:|---:|---:|
   | 2019 | 774 | 107 | 0 |
   | 2021 | 770 | 117 | 0 |
   | 2022 | 776 | 116 | **567** |
   | 2024 | 798 | 106 | **653** |
   | 2025 | 808 | 130 | **663** |

   The scores half trains on all of it; the EPA half (CFBD `/plays`,
   `classification=fbs`) sees none of the FCS games — which is why plays
   cover only ~60% of games since 2022. The backtest's totals record since
   2022 is 39% FCS–FCS games that the live Odds API board never carries.
   Split by class (residual bank joined to closes, side picked by the
   model, pushes dropped):

   | ≥6 pts disagreement | all history | 2022+ |
   |---|---|---|
   | FBS–FBS | 53.2% (3,157) | **55.4% (1,222)** |
   | FCS–FCS | 53.9% (854) | 53.9% (854) |
   | FBS–FCS | **48.7% (310)** | **47.4% (152)** |

   Good news first: the edge is *stronger* on the games the live board
   actually prices. But the FBS-vs-FCS class — where one side's rating rests
   on a handful of games — is a loser at every threshold, and the model's
   totals in those games are the worst-scaled of all (slope 0.40). That is a
   filter candidate today and a prior problem underneath.

### 2.4 Data already downloaded, or free, that the projection discards

**nflverse schedule** (`nfl.py` `normalize_schedules` keeps the canonical
columns only). Coverage 2011–2025 of what it drops:

| column | coverage | what it would do |
|---|---|---|
| `home_qb_id` / `away_qb_id` (+ names) | 100% | the **actual starter per game**, back to 1999. Backtests currently detect the starter as "primary passer in the latest training game" — wrong in Week 1, wrong the week of every injury. The QB adjustment was promoted on that proxy, so its lab value is understated, and a walk-forward "starter changed this week" feature is impossible without this |
| `home_moneyline` / `away_moneyline` | 100% | a real NFL moneyline close. The market-blend sweep and the w=0.2 anchor use a spread-probit *approximation* of the market; MLB's sweep called the same approximation structurally invalid there |
| `home_spread_odds`, `over_odds`, `under_odds` | 100% | the actual juice on the close; every ATS/O/U record assumes −110 |
| `home_rest` / `away_rest` | 100% | precomputed; the rest wrapper re-derives it from kickoff dates (fine, but the NCAAF side has nothing) |
| `div_game` | 100% | divisional HFA discount, the literature-standard rest/familiarity interaction |
| `temp`, `wind` | 65–75% (38% in 2022) | stadium-reported at kickoff; a second weather source to reconcile against Open-Meteo's daily max |
| `referee`, `home_coach`/`away_coach`, `gametime`, `weekday` | 100% | coaching-change flags, primetime / body-clock (west-coast 1pm ET) features |

**nflverse play-by-play** (`_PBP_COLUMNS` kept 12 of ~370 until the plays
rebuild; it now carries `PBP_CONTEXT_COLUMNS` as well): `wp`,
`vegas_wp`, `qtr`, `game_seconds_remaining`, `score_differential` (garbage
time — `SYSTEM_REVIEW.md` 1.3; tested and rejected in the play-context
round); `home_team` (a home flag for the EPA ridge — joined from the games
frame instead; the fitted edge lost); `interception`, `fumble_lost`
(turnover-luck regression, the SP+ / Football Outsiders standard — the
×0.5 shrink is promoted); `qb_epa`, `cpoe`, `air_yards`, `xpass` (passer
skill separated from receiver and scheme; the first two are on the file);
`penalty`, `aborted_play`; `rusher_player_id` / `receiver_player_id`.
The raw 2025 season sits in the repo root as `pbp-2025.zip` (20 MB committed,
115 MB CSV, referenced by nothing).

**Open-Meteo bank** (`datasets/nfl/weather.parquet`): `temp_mean` and
`precip` are banked for every outdoor game and unread; only `wind_max` is.
Wind is applied symmetrically to the total and never to the margin, though a
pass-first offense loses more in it than a run-first one.

**cfbfastR play frame** (downloaded weekly for `player_games`, ~70 columns,
keyless): carries `passer_player_id`, `rusher_player_id`, period, clock,
score state and win probability — every input a college QB decomposition and
a garbage-time filter need, discarded after the DK fold.

**CFBD, free with the key already in CI:** `/venues` (dome, elevation,
lat/lon, timezone — `roof`/`surface` are 100% null in the college games
frame), `/games/weather`, `/player/returning` (returning production),
`/recruiting/teams` and `/talent` (the composite), `/player/portal`,
`/coaches`, `/games` classification and conference fields, `/ratings/fpi`
and `/ratings/elo`. `features/priors.py` was written for the first three and
has never been given real data; `sp_ratings.special_teams` (83% populated)
is banked and unread.

### 2.5 Structure the design called for and the model does not have

| gap | league | what exists | what is missing |
|---|---|---|---|
| Starter term | NCAAF | none | the NFL QB decomposition was the largest single Brier gain in the lab (−0.0027, calibration error ÷2.5); college QB injuries are "the largest single swing in that sport" (`SYSTEM_REVIEW` 1.5); `player_games` names each team's passer by attempts per game, cfbfastR names him per play |
| Preseason prior | NCAAF EPA half | SP+ pseudo-games in the scores half | the EPA half regresses every team to average in September; `priors.py` (recruiting + returning production + prior rating) is built and unwired |
| Pace | both | `team_pace`/`matchup_pace` built; used by nothing live | NFL constant 63, college EPA half constant 65 (real p10–p90 54.5–71.0). The college pace variant bought calibration (0.0169 → 0.0150) and was left unpromoted for a Brier hair |
| Special teams | both | none | folded into offense/defense (§2.1); SP+ ST rating banked |
| Turnover luck | both | none | EPA absorbs every interception and fumble at face value; standard practice regresses recovery luck |
| Garbage time | both | none | needs `wp`/score state (§2.4); standard in nfelo/PFF |
| Phase matchups | both | `fit_split_ratings` (two independent fits, rejected) | a single joint ridge with pass/rush defense columns and pass-rate weighting has not been tried; `success`, `down` banked for early-down / success-rate blends (lab backlog #5, never run) |
| Injuries beyond QB | NFL | 51,800 designations 2011–2026, `player_weeks` usage 2020–2026 | a starter-out burden in the projection (share of a team's production ruled out), backtestable for six seasons; today it only vetoes picks |
| Rest / schedule | NCAAF | none | bye weeks, short weeks, travel — all computable from `kickoff` now |
| Home field | both | one constant per league (EPA half) | team-specific HFA (altitude, the documented NCAAB pattern), divisional discount, body-clock |
| Weather | NCAAF | none | no venue table, no roof, no wind |
| Blend weight | NCAAF | fixed 0.5 | the EPA half has no prior and the scores half does; a week-dependent weight is the obvious first variant |

---

## 3. The improvement list, in order

**Status (2026-09-17, end of the first pass).** Landed through the lab and
merged in four PRs (#227–#230), every table in `docs/MODEL_LAB.md`:

- **Promoted.** The points gate (#5's measurement half); the 2025 re-key
  (#2); the SP+ prior in the lab (#3); the FBS evaluation population and
  the FCS paper rule (#4); the scale in both leagues, phase-specific in
  college (#5); the nflverse schedule columns and the announced-starter
  backtest (#6); rain on NFL totals (#10's precipitation half); the NFL
  injury burden (#9); the college residual bank and the NFL bank rebuilt
  on the new bases. Then, in the second pass: the plays rebuild with the
  win probability, clock, score state, turnover and QB context on every
  play (#7), and out of it the turnover-EPA shrink at ×0.5 (the NFL bank
  rebuilt on the shrunk core).
- **Rejected on the table.** The scrimmage-only fit and the narrower
  live-plays cut (#1 — the kicks carry field position the ratings want, and
  a kneel is the winning team's fingerprint); NFL pace (#11); a divisional
  home-field discount (#18); cold on totals (#10); the NFL phase scale
  (#19); the college K=24 prior, pace, bye bonus and early-season blend
  weight (#12, #17); from the play-context round, garbage-time
  down-weighting on either probability column, EPA winsorization, `qb_epa`
  and the home edge fitted in the ridge (#7). Each stays in the lab as a
  variant.
- **Still open.** The college QB term (#8); college weather and venues
  (#10, needs the CFBD key); the college preseason prior into the EPA half
  (#13); special teams (#14); the joint phase ridge (#16).

**Status (2026-09-17, the college round).** The college QB term (#8) is
built — passer ids on every play from cfbfastR, the passer-cell QB fit —
and measured: 0.26 off the margin RMSE on the flat fit, 0.05 beside the
recency that was tested next to it, with the total paying for it; **not
promoted**, flag and variants kept. The preseason prior into the EPA half
(#13) is **rejected** (a wash at K=6, worse at 12). What the round found
instead was never on the list: **recency on the college EPA half** (the
promoted fit weighed a four-season window flat) — a six-week half-life,
with the college bank rebuilt on the recency core, took the FBS
walk-forward from Brier 0.2002 to 0.1881 and the margin RMSE from 17.73 to
16.88 (the close 15.64), the largest single gain the lab has recorded.
Still open: college weather and venues (#10), special teams (#14), the
joint phase ridge (#16).

**Status (2026-09-17, the recency round).** The question the college
finding raised, asked of every recency key: an **offseason gap** in the
NFL key (8 weeks; margin RMSE 13.25 → 13.23, Brier 0.2181 → 0.2176, the
NFL bank rebuilt) and in the college EPA half's (6 weeks), **recency on
the college scores half** (34 weeks; it had been flat since Round 1) and
**SP+ special teams in the prior** (#14's SP+ half — the pseudo-game
margin is now the whole rating) — the college three promoted together,
the college bank rebuilt: Brier 0.1881 → 0.1866, margin RMSE 16.88 →
16.82, total RMSE 16.85 → 16.81. The college calibration error climbed
0.019 → 0.029 with it: the sim's dispersion was measured on the flat
model and is now the open item ahead of the rest. Still open: college
weather and venues (#10), a college special-teams *rating* (#14's other
half), the joint phase ridge (#16), the college sim dispersion.

Ordered by expected value × certainty ÷ effort. "Gate" is what promotes it:
every model change goes through `model_lab.py` on the standard walk-forward,
as the repo's rule requires.

### Tier 1 — fix what is measurably wrong (small PRs, this week)

1. **Scrimmage-only ratings fit (NFL).** Filter to `play_type ∈ {pass, run}`
   before `fit_qb_ratings` in the live runner and the lab (a `scrimmage`
   flag on `Plays`, or a filter in `compress_plays`/`fit_*`). Lab: the
   promoted config with and without the filter; expect Brier to move and
   calibration to improve. Follow-up: kicks, punts and field goals into a
   separate special-teams rating rather than the bin. *Effort S. Gate: lab.*
2. **Re-key the 2025 college games rows to CFBD ids** (or match plays to
   games by season/week/teams in the lab's blend cut). Then re-run the
   college lab and rebuild `ncaaf/sim_residuals.parquet`. *Effort S. Gate:
   per-season play coverage asserted in a test, as `SYSTEM_REVIEW` M0 asked.*
3. **Put the SP+ prior into the lab.** `ncaaf_variants` grows a
   `prior_k` parameter mirroring the NCAAB Torvik path; the promoted blend
   becomes `blend-level2-sp12` and every downstream number (residual bank,
   sd constants, anchoring weight, the ≥6 record) is re-measured on the model
   that actually runs. *Effort S. Gate: lab.*
4. **Decide the FCS question.** Either restrict the college backtest
   population to what the live board prices (FBS–FBS, plus FBS–FCS if the
   board carries them) and re-report the totals record on that population,
   or keep FCS–FCS games as training signal but never as evaluation rows.
   Separately, paper the FBS-vs-FCS class on the live card until a prior
   fixes its ratings (47% at ≥6 on 152 bets). *Effort S. Gate: policy +
   re-reported table in `BACKTEST_NCAAF.md`.*
5. **Slope calibration through the model.** Extend `models/level.py` with a
   scale term: fit `actual = a + b·μ` for margin and total on the training
   window (walk-forward), apply `b` to the deviation. Lab it on both leagues,
   and re-run the totals sweep on the calibrated total — the ≥6 threshold
   may not be the right number once the total is honest. *Effort S–M. Gate:
   lab, and the totals sweep.*

### Tier 2 — use data already banked or already downloaded (one PR each)

6. **nflverse schedule columns into `games.parquet`:** `home_qb_id`,
   `away_qb_id`, moneylines, spread/total odds, `div_game`, `home_rest`/
   `away_rest`, `temp`, `wind`, `gametime`, coaches. Then (a) the lab's
   starter is the real pregame starter, not the latest-game proxy — re-run
   Round 3; (b) a "starter changed this week" variant; (c) the NFL
   market-blend / anchoring sweep against a real moneyline close instead of
   the probit; (d) ATS/O/U records at the real juice. *Effort S for the
   ingest, M for the re-runs. Gate: lab.*
7. **Rebuild the NFL plays frame with `wp`, `qtr`, `score_differential`,
   `home_team`, `interception`, `fumble_lost`, `qb_epa`, `cpoe`** (the raw
   2025 CSV is already in the repo; the release parquets are one download a
   season). Lab variants: garbage-time down-weighting, turnover-luck
   regression, EPA winsorisation, a home flag inside the ridge. *Effort M.
   Gate: lab; `SYSTEM_REVIEW` 1.3 / 3.5.*
8. **College QB term from the cfbfastR frame.** Keep `passer_player_id`,
   period, clock and score state when the frame is downloaded; fit
   `fit_qb_ratings` on college cells; starter from `player_games` attempts
   (last game) with the ESPN depth chart where it covers college. This is
   the NFL's largest lab gain, unbuilt in the league where it matters most.
   *Effort M. Gate: lab.*
9. **Injury burden in the NFL projection.** From `injuries.parquet` ×
   `player_weeks`: the share of a team's trailing-season targets, carries and
   snaps ruled Out/Doubtful this week, priced as a point adjustment per unit
   (skill positions, OL when the data allows). Six backtestable seasons.
   Expect a calibration gain rather than an edge (the market prices visible
   outs), which still matters everywhere the model prices without a line.
   *Effort M. Gate: lab.*
10. **Weather, both directions and both leagues.** NFL: `temp_mean` and
    `precip` as variants beside wind; wind on the *margin* by each team's
    pass rate. NCAAF: `/venues` for roof/elevation/coordinates, then the
    same Open-Meteo path the NFL has. *Effort S (NFL), M (NCAAF). Gate: lab,
    windy-game conditional as in Round 5.*
11. **Pace in both EPA halves.** Wire `matchup_pace` into the NFL model and
    the college EPA half (the college variant already measured a
    calibration gain). *Effort S. Gate: lab, with the totals sweep.*
12. **College rest and schedule spots.** Port `RestAdjustedModel` to NCAAF
    (bye weeks, short weeks; travel from venue coordinates once #10 lands).
    *Effort S. Gate: lab.*

### Tier 3 — free data not yet ingested

13. **College preseason prior from CFBD returning production + recruiting
    composite + prior-year rating**, through `features/priors.py` as
    designed, into *both* halves of the blend (the EPA half has no prior at
    all). Transfer-portal production at half credit per Connelly. Gate on
    weeks 1–4 Brier and the early-season slope in §2.2. *Effort M.*
14. **SP+ special teams and a college ST rating**, once #1's separation
    exists. *Effort S.*
15. **Venue and conference fields on college games** (`/games` classification
    and conference, `/venues` elevation): per-venue HFA, conference-game flag,
    and an FBS/FCS classification the fit can shrink on. *Effort S–M.*

### Tier 4 — modelling experiments the harness can run now

16. **Joint phase ridge** — one design with pass-offense, rush-offense,
    pass-defense, rush-defense columns and pass-rate weighting, in place of
    the rejected two-fit split. Early-down EPA and success-rate blends from
    the banked `down`/`success` columns (lab backlog #5). *Effort M.*
17. **Week-dependent blend weight (NCAAF)** — `w` as a function of games
    played, so the prior-carrying half dominates in September. *Effort S.*
18. **Team-specific and divisional HFA (NFL)** from `div_game` and a per-team
    home column with heavy shrinkage. *Effort S.*
19. **Early-season shrinkage (NFL)** — a stronger ridge or a preseason prior
    in weeks 1–6 to remove the 0.72 slope; an offseason discount in
    `recency_weights` (the current key deliberately does not inflate the
    offseason gap; the slope says it should, a little). *Effort S.*

### Housekeeping

- Delete or move `pbp-2025.zip` (20 MB in the root, unreferenced).
- Add a per-season plays-coverage assertion for both leagues (games with
  plays ÷ games), so #2 cannot recur silently.

---

## 4. What was checked and found sound

The sim's dispersion constants are walk-forward measured against the model's
own projections (the `MODEL_LAB` trap is recorded and avoided); the empirical
residual draw and the dispersion slope were measured and honestly not
promoted; the level is fitted through the model in both leagues; the NFL
starter map reads the depth chart and demotes an Out; neutral sites reach
every wrapper; recency and the QB decomposition were promoted on real
walk-forward gains; the college postseason ordering leak was closed. The
harness for everything in §3 exists — `model_lab.py`, `compress_plays`, the
totals sweep, `build_sim_residuals.py` — which is why most of the list is
small.

---

## 5. Data we do not have coming in at all

§2.4 covered feeds we already fetch and discard. This section is the other
half of "are we using all the data": sources **not wired**, ordered by what
they would change on the matchup card and in the projection. Everything
marked free is Python-reachable today; the paid rows are few and cheap.
`EDGE_RESEARCH.md` §8 listed some of these as a shopping list in August; as of
this audit none of them has an ingest.

### 5.1 Cross-sport — the market and the environment

| # | Source | Cost | What it adds | Where it lands |
|---|---|---|---|---|
| 1 | **Pinnacle closes** — The Odds API `eu` region (the collector flag exists, `SYSTEM_REVIEW` M6) | ~2× odds credits, inside budget | a sharp close for CLV and a sharp anchor for de-vig; today both are a median of 11 soft books, ~4.5% overstated | grader, `SlateConfig` de-vig |
| 2 | **Opening lines and movement** — CFBD `/lines` already returns `spreadOpen`/`overUnderOpen` per provider (discarded by `pull_cfbd_lines.py`); SBR / Aussportsbetting archives for NFL open→close back to 2007 | free | open-to-close movement as a feature (steam, where the market moved *away* from us) and CLV on college measured historically, not just live | `games_lines.parquet`, lab |
| 3 | **Officials** — nflverse `officials` (crew per game), MLB `hydrate=officials` on the schedule (home-plate umpire), official.nba.com referee assignments (covers WNBA), ESPN box officials (NCAAB) | free | penalty rate and pace by crew move NFL totals 1–2 pts; umpire zone size moves K/BB (2026 ABS caveat); foul-heavy NBA/WNBA crews move totals by free throws | totals wrapper, MLB K props |
| 4 | **A venue table for every league** — CFBD `/venues` (dome, elevation, lat/lon, timezone), statsapi venues, static NFL/NHL/WNBA arena tables | free | travel distance, time-zone shift, altitude, body-clock (west-coast teams at 1pm ET) — `RestAdjustedModel` can only see days between games | rest/travel wrapper |
| 5 | **Hourly weather at kickoff** — Open-Meteo hourly archive + forecast (the current bank is a daily max) | free | wind *at kickoff* rather than the day's peak; temperature and precipitation on every outdoor game; forecast-vs-actual error as a feature of when to bet | weather wrapper, all outdoor sports |
| 6 | **Public betting splits** — tickets vs money % (Action Network, VSiN, Covers) | scrape / cheap | a weak but documented signal (money against tickets); mostly a card annotation | intel signal |
| 7 | **Injury news speed** — RotoWire API (via OpticOdds), curated beat-reporter lists | quote / cheap | the WNBA and NBA edges are news-speed edges; the NFL Sunday inactives too | starter map, prop availability |
| 8 | **Public rating ensembles** — Massey composite (college), nfelo, ESPN FPI/BPI (CFBD serves FPI and Elo), Inpredictable | free | ensembling public ratings is a documented accuracy gain; doctrine stays — they track the market, so this is calibration and no-line pricing, not edge | priors / lab benchmark |

### 5.2 NFL — the player layer the model is missing entirely

All from the nflverse release assets the pbp already comes from; none is ingested.

| Source | Since | What it adds |
|---|---|---|
| **Snap counts** (`snap_counts`) | 2012 | usage in snaps, not box stats: role changes, OL continuity, the injury burden (§3 #9) measured in the unit that matters |
| **FTN charting** (`ftn_charting`) | 2022 | per-play pressure, blitz, man/zone, play action, motion, RPO, screen, box count, throw depth, catchable, drop, contested — pressure-adjusted QB ratings and scheme matchups; the card's "what moves this game" gets content |
| **Next Gen Stats weekly** (`nextgen_stats`) | 2016 | CPOE, time to throw, aggressiveness, separation, cushion, rush yards over expected — skill separated from luck inside EPA |
| **PFR advanced stats** (`pfr_advstats`) | 2018 | pressures, hurries, drops, bad throws, YAC, blitzes — a second charting source |
| **Participation** (`pbp_participation`) | 2016–2023 | personnel packages, defenders in box, pass rushers — historical only, for fitting |
| **Depth charts** (`depth_charts`) | 2001 | historical starter changes at every position (the backtest for #9) |
| **ESPN QBR, contracts, draft picks** | various | a roster-value prior for Week 1 (the 0.72 early-season slope in §2.2) |
| **PFF grades** (PFF+) | paid, ~$40–60/yr, no API | OL/DL and coverage grades — the one paid input most public elite NFL models carry; college too |

### 5.3 NCAAF — CFBD endpoints with the key already in CI

| Endpoint | What it adds |
|---|---|
| `/player/returning`, `/recruiting/teams`, `/talent`, `/player/portal` | the preseason prior `features/priors.py` was written for (returning production alone is >60% of SP+'s preseason accuracy); portal production at half credit |
| `/ppa/players/season`, `/player/usage`, `/plays/stats` | per-player PPA — a college QB term (§3 #8) from CFBD's own numbers rather than reconstructed from cfbfastR |
| `/stats/game/advanced`, `/drives` | success rate, explosiveness, line yards, havoc, field position per game — the SP+ components as features; drive-level scoring for the sim |
| `/coaches`, `/rankings`, `/games/media` | coaching changes; AP-rank holdover bias in openers; TV network and kickoff window (the documented TV-game over bias) |
| `/venues`, `/games/weather` | roof, elevation, coordinates, per-game weather — college has no weather and no venue data today |
| `/lines` per provider + `spreadOpen`/`overUnderOpen` | line movement and multi-book closes, free, 2013+ |
| `/metrics/wp/pregame`, `/ratings/fpi`, `/ratings/elo` | public benchmarks and prior candidates |
| **Conference availability reports** (SEC and Big 12 since 2024, Big Ten since 2025), Ourlads depth charts | the only real college injury/starter source — ESPN returned 3 rows for the whole league; these are official and pre-kickoff |

### 5.4 MLB

| Source | Cost | What it adds |
|---|---|---|
| Umpire assignments (statsapi `hydrate=officials`), UmpScorecards | free | plate-umpire zone and K/BB tendencies for the K-prop and totals; re-estimate on 2026 data under ABS |
| Every pitcher appearance from the boxscores already walked (relievers, pitch counts, days since) | free, internal | bullpen fatigue and availability — the market's second-largest MLB factor after the starter; the starters bank holds only starters |
| Pitcher Statcast (Savant pitch-level, pybaseball) | free | velocity, spin, movement by start — decline and injury show up here before the ERA does; the batter side is already banked |
| FanGraphs projections (Steamer / ZiPS / ATC daily), Stuff+ / Pitching+ | free page exports | an ensemble prior for hitters and pitchers (the FanGraphs finding: ensembles win) |
| statsapi transactions / IL, platoon splits, Savant OAA | free | availability, lineup-vs-handedness, team defense |

### 5.5 WNBA, NHL, NCAAB (content posture, but the card is generated for them)

| League | Source | Cost | What it adds |
|---|---|---|---|
| WNBA | wehoop play-by-play | free | exact possessions, rotation minutes, garbage time — the pace×efficiency model runs on a box-score estimator |
| WNBA | official injury report (wnba.com), 5pm ET day-before | free | the documented WNBA edge; ESPN's report is thin |
| WNBA | referee assignments (official.nba.com) | free | foul rate → totals |
| NHL | DailyFaceoff confirmed starting goalies | scrape | the largest NHL factor; pricing is goalie-neutral today by design |
| NHL | MoneyPuck shot-level xG, team xGF/xGA, goalie GSAx; Natural Stat Trick 5v5 | free | expected-goals ratings in place of goals (goals are the noisiest outcome in the four sports) |
| NHL | NHL API rosters, injuries, line combinations | free | availability and top-line changes |
| NCAAB | Torvik timemachine (as-of daily ratings) | free | a leak-free daily prior for the walk-forward |
| NCAAB | KenPom API | ~$25/yr | the market's own input, for calibration |
| NCAAB | hoopR play-by-play, ESPN officials | free | possessions, garbage time, officials |

### 5.6 What to wire first

Ordered by value for the projection and the card, across leagues:

1. Pinnacle closes (a flag) and the CFBD opening lines (already in the
   payload) — the market's own history, for the yardstick every other item
   is judged by.
2. NFL snap counts and FTN charting — the player layer; snaps feed the injury
   burden and FTN feeds pressure-adjusted QB and scheme matchups.
3. CFBD priors (returning production, recruiting, portal) into both halves of
   the college blend — September is where the college model is weakest.
4. Venue tables and hourly weather for every league — travel, altitude,
   body-clock and kickoff-time wind, one wrapper shared by all four sports.
5. Conference availability reports for college and the WNBA official injury
   report — the only pregame availability sources those leagues have.
6. MLB umpires and bullpen usage from data already walked.
7. Officials for NFL and NBA/WNBA.
8. NHL confirmed goalies and MoneyPuck xG, if the NHL posture ever moves off
   content.

---

## 6. The scoreboard — distance to the closing line, in points

The goal is the most accurate score projection. The closing line *is* a score
projection, and the sharpest one available, so it is the yardstick. RMSE of
actual against projected, out-of-sample walk-forward of the promoted models
(`sim_residuals.parquet`) joined to the committed closes, 2015+:

| | n | margin: model / close | total: model / close | weight on (model − close) |
|---|---:|---|---|---|
| NFL | 3,028 | 13.16 / **12.72** | 13.94 / **13.23** | +0.01 ± 0.07 (margin), −0.07 ± 0.06 (total) |
| NCAAF | 11,572 | 18.46 / **15.53** | 17.38 / **16.24** | −0.01 ± 0.02 (margin), **+0.03 ± 0.03** (total) |

The last column is the least-squares weight the actual result puts on the
model's disagreement with the close: 0 means the close already contains
everything the model knows; 1 means the model is the better forecast. Today
the margin projection carries nothing beyond the close in either league, and
the college total carries a little — which is the thin totals edge measured
the other way round.

By phase of season (margin, model / close): NFL weeks 1–4 13.28 / 12.96,
weeks 5–9 12.91 / 12.56, weeks 10+ 13.22 / 12.70. NCAAF weeks 1–4
**19.38 / 15.58**, weeks 5–9 17.79 / 15.36, weeks 10+ 18.34 / 15.63. The
college gap is ~3 points in every season 2015–2025 with no trend, and 3.8 in
September. On FBS-vs-FBS games since 2022 it is 17.94 / 15.26.

What this says about the plan:

- **College is where the accuracy is.** A three-point RMSE gap on margin is
  roster knowledge the market has and the model lacks — starters, returning
  production, recruiting, injuries — plus the early-season prior. The NFL
  gap is under half a point on margin and 0.7 on totals.
- **NFL totals are the more fixable NFL market.** The model's total carries
  half the signal it claims (§2.2), and pace, kickoff-hour weather and crews
  are all unmodelled; the margin is already close to the ceiling.
- **Accuracy has to come from information, not fit.** Every promoted lab
  round to date moved Brier by fitting the same inputs better; the weight
  column says the result converges to the close rather than past it. The
  items in §3 and §5 that add *inputs the market prices* (starters,
  injuries, priors, pace, weather, officials) are the ones that move both
  RMSE and that weight.
- **Gate on it.** The lab scores Brier, log-loss, calibration and ATS/O/U.
  Add margin and total RMSE against actual and against the close, and the
  weight above, so a variant is promoted for score accuracy and independent
  information rather than for win-probability alone.


### 6.1 The scoreboard after the first pass (2026-09-17)

The same yardstick, on the promoted chains as they now run (walk-forward,
out of sample, the lab's 4,000-sim gate):

| | margin: model / close | total: model / close | information beyond the close (margin / total) |
|---|---|---|---|
| NFL, before | 13.33 / 12.72 | 13.90 / 13.23 | +0.08 / −0.00 |
| NFL, after the first pass | 13.26 / 12.97 | 13.55 / 13.23 | +0.11 / +0.08 |
| NFL, after the turnover shrink | 13.25 / 12.97 | 13.52 / 13.23 | +0.09 / +0.09 |
| **NFL, now** (the offseason gap) | **13.23** / 12.97 | **13.51** / 13.23 | +0.07 / +0.08 |
| NCAAF, before | 18.46 / 15.53 | 17.38 / 16.24 | −0.01 / +0.03 |
| **NCAAF, now** | **18.43** / 15.54 | **17.21** / 16.23 | −0.00 / **+0.05** |
| NCAAF, FBS vs FBS | 17.89 / 15.64 | 17.42 / 16.30 | −0.01 / +0.06 |
| NCAAF, FBS vs FBS, the college round's base (phase scale) | 17.73 / 15.64 | 17.25 / 16.30 | −0.05 / +0.05 |
| NCAAF, FBS vs FBS, after the college round (recency on the EPA half) | 16.88 / 15.64 | 16.85 / 16.30 | −0.09 / +0.09 |
| **NCAAF, FBS vs FBS, now** (the offseason gap, the scores half's recency, special teams) | **16.82** / 15.64 | **16.81** / 16.30 | −0.09 / **+0.10** |

(The NFL close's margin RMSE reads 12.97 here and 12.72 in §6 because the
two tables score different windows — the lab's 2011–2025 trailing-4 run
against the bank's 2015+ rows; compare each row with its own close.)

The NFL total closed a third of its gap to the close and now carries
information the close does not; the college total moved a sixth of the
way. The margins barely moved in either league in the first pass, which
is what the audit predicted: margin accuracy is roster knowledge. The
college round then moved the college margin 0.85 points and the total
0.40 — not through the roster items the audit named (the QB term is
measured and small, the EPA-half prior lost) but through recency on the
EPA half, which is roster knowledge by another route: in the portal era,
last season's snaps describe a different team.
