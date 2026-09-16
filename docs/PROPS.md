# Prop Mastery — one headline prop per sport

The owner's X strategy: be known for ONE prop per sport, with the graded
record as the receipt. The picks (owner-researched, model-confirmed):

| Sport | Headline prop | Why it models | Status |
|---|---|---|---|
| MLB | **Pitcher strikeouts** | BF × K-rate × opponent-K decomposition; our own 2026 MLB backtest measured it the best MLB prop (ROI ≈ +3–4% at shrink 0.5; docs/WAGERING.md §74) | **Live** (this PR) |
| NFL | **Receptions** | Stable target shares, discrete counts, NegBin-friendly | Next (season start) |
| NHL | **Shots on goal** | TOI × shot-rate is the most stable NHL skater stat; boxscore `sog` banked per game | After puck drop (Oct) |
| NBA | **Rebounds** | Minutes × pace × position matchup; softer than points markets | Needs the NBA vertical first — the lab will arbitrate rebounds vs assists |

**Line banking started for all four** — the props collector
(`scripts/collect_football_props.py`, workflow "Collect player-prop
lines") snapshots each league's headline market twice daily
(`LEAGUE_PROP_MARKETS`: MLB `pitcher_strikeouts`, NHL
`player_shots_on_goal`, NBA `player_rebounds`, football's six-market
board unchanged), so the honest line-based backtests accumulate their
own archive from today.

## MLB pitcher Ks (live)

`velocity/models/props_mlb.py` — expected Ks = shrunken expected batters
faced × shrunken K/BF × the opposing lineup's K tendency, all from the
banked starters history (13.8k starts, 2024–). Distribution: negative
binomial with dispersion fit from the same history. Priced into
`slate_mlb_props_*.parquet` by `run_live_slate` (`_mlb_k_slate`) against
The Odds API's `pitcher_strikeouts` board, using statsapi probables;
graded next morning against the day's boxscores
(`grade_yesterday._grade_mlb_props`), joining the record chain and the
site like every other play.

Walk-forward validation vs actuals (2,932 out-of-sample 2026 starts,
refit every 15 days): MAE 1.72 Ks, bias −0.15, and **monotone,
conservatively-priced calibration** —

| model says over 4.5 | realized |
|---|---|
| <35% | 14% |
| 35–50% | 39% |
| 50–65% | 58% |
| >65% | **81%** |

The tails understate (the model claims less edge than it has), which is
the safe direction for staking. Launch posture: raw probabilities
(shrink 1.0); the per-market shrink sweep replays the banked slates once
graded days accumulate, exactly as football props are tuned.

## Football dispersion — fitted, not assumed (2026-09)

`FootballPropConfig` shipped with priors ("honest, not fitted"). The banked
`datasets/nfl/player_weeks.parquet` (112,450 player-weeks, 2020–2025) fits
them (`scripts/fit_prop_dispersion.py`, docs/SYSTEM_REVIEW.md §5.2). Every
number is a *within player-season* measurement — each player's own season
as the projection, the game-to-game spread around it as the outcome noise
the sim owes — decomposed the way the sim generates:

| quantity | was | fitted | how |
|---|---|---|---|
| pass-volume σ (team multiplier) | 0.18 | **0.118** | team receptions CV² − 1/μ (the Poisson part out) |
| rush-volume σ | 0.25 | **0.175** | team carries, the same way |
| receptions overdispersion φ (per player, net of the multiplier) | 0 | **WR 0.027 · TE 0.000 · RB 0.083** | (var − mean)/mean² − (e^{σ²} − 1) |
| per-catch yards sd (× √receptions) | 6.0 | **WR 10.57 · TE 7.92 · RB 7.56** | sd of (yards − receptions × season ypr)/√receptions |
| rushing CV (player noise, net of the multiplier) | 0.45 | **RB 0.72 · QB 0.87** | relative deviation sd, net of the rush σ |
| rushing shape | normal | **banked pool** (`datasets/nfl/prop_residuals.parquet`, 7,701 game residuals) | relative-deviation skew RB 1.55 / QB 1.42 |

What the fit changes on the board: receiving-yard distributions are ~15%
wider (the √receptions scaling was right; the per-catch constant was not —
with 10.6 the sim's receiving-yards CV of 0.68 for a 4-catch receiver
matches the measured 0.69), rushing distributions are 60% wider and
right-skewed (above 1.5× the mean the real frequency is 20.0%; a normal at
the fitted CV says 25.2%, a gamma 20.5%, and below 0.25× the mean the
gamma is too light — the pool matches both tails by construction), and the
receptions count carries a back's extra script variance. Wider is the
conservative direction for every alt-line and most main-line overs: the
old sim over-claimed edges on yardage props.

**A prior the data refused.** The shared pass-volume multiplier was meant to
move teammates together. On 525 top-receiver pairs the week-to-week
correlation of teammates' receiving yards is **−0.02** (receptions +0.02):
the passing pie is not a common multiplier, target share trades off. The
fitted σ implies ~0.035 between two receivers (the old prior implied
0.075); the QB-to-receiver correlation is structural (his yards are the sum
of theirs) and unchanged.

Not a backtest: the props line archive began this season, so the shrink
sweep that judges *confidence* has nothing to replay yet. These constants
set the *shape* the sweep will then tune, exactly as MLB's did.

**Prop closes are attached.** The grader reads the props collector's
snapshots (`artifacts/props`, fetched beside the odds archive), takes each
book's last pre-kickoff quote per (player, market, side), and reduces
across books to a median close; the graded prop rows carry `price_clv` /
`line_clv` like the game ledger's. `clv_trusted` stays False for props —
this is what the shrink sweep grades against, not the yardstick.

## The college board, and the dispersion that is not the NFL's (2026-09-16)

Audit finding 6: the collector bought the full NCAAF event-market prop board
every live run — per-market, per-event Odds API credits — and the slate could
price none of it. It read FantasyPros, and **the FantasyPros public API has no
college endpoint at all**, so the league filter came back empty, the slate
skipped, and the lines were banked and never read.

The DFS board hit the identical wall and solved it with
`datasets/ncaaf/player_games.parquet`. The prop slate now does the same. Two
pieces, neither a new model:

**The projection.** `velocity/models/props_ncaaf.py:player_prop_means` emits
the long `(player, stat, value)` frame `team_player_means` already eats —
per-game means over the same six-game recency window the college DFS board
swept walk-forward. Nothing downstream can tell it did not come from a
provider. Ten of the eleven football prop markets are available; the missing
one is `pass_completions`, because cfbfastR records an incompletion's passer
but never a completion count, and a league-average completion rate is a guess
rather than a projection.

Two filters, both there to stop a line being priced on the wrong man:

- **Only players active in the current season.** The window may still reach
  back into last season for a player's *games* — a roster turns over every
  August and six games beats one — but a player who has not taken a snap this
  season is not projected off a two-year-old mean. 11,697 banked players
  become 4,177 active ones.
- **No ambiguous names.** The prop line carries a name and no id, so a name
  held by two banked players cannot be resolved, only guessed, and
  `name_index_from_fp` would silently keep whichever came first. 180 names
  across the four banked seasons are held by more than one player, 20 of them
  live in 2026. Both are dropped. Skipped and reported, never guessed.

**The dispersion, which is the part that would have gone wrong quietly.**
Re-fitting `scripts/fit_prop_dispersion.py --league ncaaf` on the college bank:

| quantity | NFL | college |
|---|---|---|
| pass-volume σ (team multiplier) | 0.118 | **0.242** |
| rush-volume σ | 0.175 | **0.228** |
| receptions φ (WR / RB) | 0.027 / 0.083 | **0.000 / 0.035** |
| per-catch yards sd (WR / RB) | 10.57 / 7.56 | **11.66 / 9.80** |
| rushing CV (RB / QB) | 0.72 / 0.87 | **0.72 / 0.80** |
| rush-attempt φ (RB / QB) | 0.091 / 0.025 | **0.067 / 0.060** |
| rushing shape | banked pool, skew 1.55 / 1.42 | **banked pool** (`datasets/ncaaf/prop_residuals.parquet`, 18,487 residuals), skew 1.25 / 1.22 |

College team volume swings about **twice** as hard as the NFL's, while the
per-player numbers come in at or below it. That is a coherent story rather
than noise: blowouts, tempo and talent gaps move the whole team's pie far
harder, but *conditional* on the pie a player's share is no noisier than a
professional's. The one place college is clearly wilder per player is the
quarterback run (φ 0.060 against 0.025) — designed QB runs, which the NFL has
far fewer of.

Running `FootballPropConfig()` on a college board would have simulated
distributions roughly half as wide as they are. That failure does not announce
itself: a too-narrow distribution **manufactures edge**, on every market at
once, and the board would have looked like it was finding value everywhere.

**And it prices the board we already bought, rather than buying it twice.**
The finding is that NCAAF prop lines were bought every run and never read, so
pulling them a second time in order to read them would be a poor trade. NCAAF
never pulls prop lines live: `--prop-lines-dir` points at the props
collector's banked boards (already downloaded beside the odds archive for
grading), the freshest one inside `--board-max-age-min` is priced, and
anything staler is refused with its age named — pricing against a line that
moved six hours ago is worse than not pricing. Freshness comes off the
filename stamp, never the mtime, for the same reason the game board learned
it: these arrive by unzipping Actions artifacts, so every mtime is extraction
time. NFL still pulls live and is unchanged; pointing it at the same banked
boards would save credits too and is a separate call.

**What it prices.** Dry-run on Alabama/Georgia off the real bank, with a
synthetic book board built at the model's own medians: **0 bets, 0 unresolved
names** — no phantom edge against a fair line, and every player resolved.
Shade that same board 15% high and it returns 53 bets, **all unders**; at 30%
the edges pile up against the 0.12 `max_edge` ceiling and the group cap starts
zeroing stakes. De-vig, edge, Kelly and the ceilings all behave. A 60-game
Saturday slate simulates in about two seconds.

Not a backtest — the NCAAF prop line archive has nothing to replay yet, same
as the NFL constants when they landed. These set the shape; the shrink sweep
tunes the confidence once graded weeks accumulate.

## Two markets added from the coverage report (2026-09-16)

The BettingPros slug-coverage report exists to make abstention visible: an
unmapped slug contributes nothing, silently and correctly, so a board where
half the markets are inert reads like a healthy one. Its first honest NFL run
(after #207 unbroke the board) showed five slugs abstaining on every row. Two
of them could be priced with the machinery already here, and each was measured
against `datasets/nfl/player_weeks.parquet` before it was added — not reasoned
about, which is what #207 charged for.

| Market | BP slug / Odds API key | What it cost to add | The measurement |
|---|---|---|---|
| `rush_rec_yards` | `rushing-receiving-yards` / `player_rush_reception_yds` | Nothing but the sum — the sim already draws both legs per player per simulation | Within-player-season residual correlation of the legs is **0.0255** (4,388 RB games, 368 player-seasons). Pooled is 0.080, but that is mostly player quality, which the projection already carries. |
| `interceptions` | `interceptions` / `player_pass_interceptions` | A Poisson on the passer's FantasyPros projection — the shape `pass_tds` already uses | Observed variance/mean **1.020** on 3,219 QB games (≥15 attempts) against the structure's **1.012**. A closer fit than `pass_tds` itself, which is mildly *under*dispersed at 0.886. |

**The 1% this knowingly gives up.** A player's rushing and receiving legs ride
*separate* team multipliers (`rush_mult`, `pass_mult`, drawn independently), so
the sim puts his own two legs at r≈0.00 against a measured 0.0255. That makes
the summed market's sd about 1% narrow — the model runs slightly over-confident
on it. No correction is applied: inducing a 0.026 correlation is more machinery
than a 1% effect earns. It is pinned by a test so that the day someone does
induce it, the note fails rather than going quietly stale.

## The census answered it, and two more markets landed (2026-09-16)

Run [35108513721](https://github.com/EdgeCash/Velocity/actions/runs/35108513721)
printed the first stat-key census. FantasyPros serves **37 keys; we read 6** —
and all three volume keys are there:

| Key | Non-zero | Mean | Max |
|---|---|---|---|
| `rush_att` | 309 of 422 | 2.12 | 19.19 |
| `pass_att` | 70 of 82 | 12.84 | 35.22 |
| `pass_cmp` | 70 of 82 | 8.30 | 23.09 |

`pass_cmp` arrives as a plain number, not the `"21/33"` compound string the
melt would have dropped — so the guard was not needed, but it was not wrong to
have.

**`rush_attempts` and `pass_attempts` are now priced.** Both are gamma-mixed
Poissons on a team multiplier — the negative binomial `receptions` already
uses — with dispersion fitted from the bank rather than assumed
(`scripts/fit_prop_dispersion.py`, within player-season, net of each market's
own multiplier):

| Market | φ | Player-seasons |
|---|---|---|
| carries, RB | 0.0914 | 365 |
| carries, QB | 0.0247 | 169 |
| attempts, QB | 0.0260 | 212 |

A back's workload swings with the script about as hard as his targets do
(`receptions` RB φ is 0.083), which is the shape you would expect and a useful
sign the measurement is real. At the banked median volumes the structure
returns var/mean **2.331** for QB attempts against a measured **2.332**, and
2.63 for RB carries against 2.28 — the carries side runs a little wide, which
is the safe direction for a price. A bare Poisson would say 1.0 and price every
tail far too tight.

Each rides the multiplier for its **own** phase: carries with `rush_mult`,
dropbacks with `pass_mult`. Crossing them would make a back's workload rise
with his quarterback's, when the script that lifts one suppresses the other.
A test pins that ordering.

**`pass_completions` closed the board.** The projection was always served; the
blocker was a column. nflverse publishes `completions` in the same weekly file
we already read for `attempts` and `carries` — we simply never kept it. Banking
it (113,581 player-weeks, 3,251 QB games with ≥15 attempts, 64.9% completion
rate) gave the market both a dispersion to fit and something to settle against.

It is **not** modelled as a count of its own. Completions are a fraction of
attempts: they correlate **0.844** with attempts within player-season on 2,682
banked QB games, and can never exceed them. A separate Poisson would happily
print 30 completions on 25 attempts, and would sell an "over attempts + over
completions" parlay as two bets when it is nearly one. So the sim draws a
**binomial on the attempts it already simulated**, at the rate the player's own
projection implies — a checkdown passer and a deep thrower do not share a
league rate.

Measured: the structure returns var/mean **1.854** against a banked **1.753**
(~6% wide, the safe direction) and correlation 0.90 against a measured 0.844.
Real completion% wobbles game to game by more than binomial noise allows; that
gap is recorded rather than papered over. No attempts projection means no
completions market — the sim abstains rather than inventing a denominator.

With it, **every slug the NFL board serves is now priced.** The coverage report
that started this had 5 of 10 mapped and 388 of 551 rows usable.

**The census's own "UNREAD" label was too narrow, and I believed it.** It first
shipped reporting whether the *props model* read a key, then printed that as
whether anything did — so the team-defense block came back UNREAD when
`velocity/dfs/dst.py` has been reading it since the DST projection landed. (DK
classic needs a defense, and the pool used to join it at 0.0 points.) That is
the confusion this report exists to prevent, pointed the wrong way: it invites
someone to "discover" a key and wire up a second consumer for data already in
use. The census now names the consumer, and separates three states:

- **read** — `def_sack`, `def_int`, `def_fr`, `def_safety`, `def_td`,
  `def_retd` (DFS: DST), `def_pa` (DST's fallback when there is no game sim),
  `fumbles` (DK scoring), `rush_tds` / `rec_tds` (folded into `anytime_td`),
  plus every prop market.
- **declined** — `def_ff` and `def_tyda` (DK scores neither), and the three
  DraftKings scoring variants `points` / `points_half` / `points_ppr` (we
  compute DK points from the components rather than trust a served total).
  "Declined" and "nobody looked" are different findings and should not read
  the same.
- **UNREAD** — nothing. `fg` / `fga` / `xpt` were the last block, and
  `velocity/dfs/kicker.py` closed it (below). A test now fails on any key
  nobody has looked at, so a new one the feed starts serving cannot go
  unnoticed.

Eight milestone keys (`pass_yds_300`, `rush_yds_100`, `scrimage_yards_100` …)
are **all zero on every row** — placeholders in a weekly projection, not
projections, and the census flags exactly that case.

**Three slugs stay unmapped, each for its own reason.** `rushing-attempts` (57
rows, the largest) and `passing-attempts` (28) are the two worth having next:
both are ordinary count props and both have banked actuals to calibrate against
(`player_weeks` carries `carries` and `attempts`). What is missing is the
FantasyPros projection key — nothing reads a rush- or pass-attempt projection
today, and `FP_API_KEY` is an Actions secret the sandbox cannot see.
The answer now prints in
the "Collect FantasyPros projections" log: `--inspect` prints the stat-key
census (every key served, how many players carry a non-zero value, which keys
the model reads), that workflow always passes `--inspect`, and it costs no
extra request — so the census rides the existing 4x/week schedule as well as a
manual dispatch. Read the **volume-like keys (the open question)** block.
`scripts/inspect_fp_stat_keys.py` prints the same census from a downloaded
`fp_projections_*.parquet` when you want the table as a CSV.

One trap the census covers: `normalize_projections` silently drops any value
it cannot coerce to a number, so a completions projection served as `"21/33"`
would vanish from the long frame and the census would answer "not served" to a
feed that serves it. `unmelted_stat_keys` runs against the raw payload, where
that is still visible, and the collector reports it as a note.
`passing-completions` (28) is the one to leave alone even then: `player_weeks`
has no completions column at all, so there is nothing to fit the dispersion on
and nothing to walk it forward against, and a market we cannot backtest is a
market we cannot size.

**Neither is in the default Odds API pull.** Both are mapped in
`PROP_MARKET_BY_KEY`, so the rows normalize the moment a pull includes them,
but widening the football board from six markets to eight is roughly a third
more credits on every prop call. `scripts/report_odds_credits.py` still
withholds its projection until it has a full week, and that spend should be
decided against the number rather than ahead of it.

## The kicker projection (2026-09-16)

The Showdown board's missing position, and the same bug as the DST one slot
over: `DK_POINTS_PER_STAT` carries no kicking weight and the correlated prop
sim has no kicker markets, so a kicker collapsed to **0.00 expected points**
and the optimizer took whichever was cheapest. Verified before building —
a kicker priced at 0.00 beside a receiver at 11.2 on the same frame.

Two things had to be right, and neither is the projection itself.

**DK pays by distance; FantasyPros projects a total.** The feed serves `fg`
(made) with no split, while DK pays 3 / 4 / 5 for 0-39, 40-49 and 50+. The
banked kicks give the mix — 56.0% / 26.9% / 17.2% over 2020-2025, which is
**3.612 points per made field goal**. Scoring every make at 3.0 would
under-price a kicker by ~17%, and under-price the long-range ones most —
exactly the captain plays.

**Kicking counts are *under*dispersed, so a Poisson is the wrong shape.**
Within player-season the banked attempts run variance/mean **0.80** (field
goals) and **0.74** (extra points): a kicker's workload is bounded and regular
in a way a receiver's targets are not, and a Poisson would say 1.0 and price
both tails far too wide. Attempts are drawn as a binomial whose trial
probability is `1 - var/mean`, read straight off the measurement, and makes are
a binomial on those attempts at the kicker's own rate (`fg`/`fga`, league rate
as fallback) — so a make can never exceed an attempt.

That meant banking `fg_att` and `pat_att`, which nflverse publishes in the file
we already read. Same shape as `pass_completions`: without attempt actuals
there is no dispersion to calibrate, which is what kept completions waiting.

Checked: made variance/mean **0.832** (FG) and **0.755** (PAT) against banked
0.827 and 0.768, and the expectation reproduces the banked **8.366** DK points
per active kicker game exactly. The band lottery is drawn per simulation rather
than averaged, because three makes all landing 50+ scores 15 against the 10.8
the mean implies, and that tail is the whole reason a kicker is ever a captain.

## Open items

- NFL receptions distributional model (targets × catch rate NegBin — the
  generic engine in `velocity/models/props.py` is built for this; needs
  `targets` added to the nflverse weekly normalizer).
- NHL SOG after the season opens (skater `sog` is in every banked
  boxscore path already). Until then it is audit finding 6b: `player_shots_on_goal`
  is bought on the default schedule with no slate to price it and no skater
  bank to price it from (`datasets/nhl/starters.parquet` is goalies). Same for
  NBA `player_rebounds`, where the vertical is openly unbuilt. Both want
  deciding — build the bank, or cut the market from `LEAGUE_PROP_MARKETS` and
  stop paying for it.
- NBA vertical (nba_api pipeline) → rebounds vs assists lab arbitration.
- ~~NCAAF prop lines bought every run and never priced~~ — closed
  (above): the slate projects from the college player bank with college-fitted
  dispersion. Blocked until findings 9 and 10 put the touchdown columns back.
- ~~Confirm the FantasyPros rush-attempt / pass-attempt projection keys~~ —
  answered by the census (above); both markets are priced.
- ~~Bank a completions column into `player_weeks`~~ — banked; the market is
  priced and the NFL board is fully mapped.
- ~~MLB projections return 0 rows every run~~ — dropped from `LEAGUES`
  (2026-09-16). It cost ten requests a run to bank rows nothing read: MLB DFS
  prices from `collect_mlb_player_stats.py` and MLB props from the banked
  starters frame. `UNCONSUMED_LEAGUES` carried a correct note about this the
  whole time, which is the lesson — saying a thing in a log is not the same as
  not doing it, so a test now asserts nothing marked unconsumed is fetched.
- Decide whether the four new `player_*` keys join the default Odds API pull.
  The **per-market price no longer needs a week of data** — The Odds API bills
  one credit per market per region per call, which `cost_per_market`
  (`scripts/report_odds_credits.py`) now reads straight off the ledger. Going
  from the football six to ten is **+67% on every prop call**. What still needs
  a week is the denominator: what that percentage is of the monthly plan.
- The live slate was spending credits from four call sites and banking **none**
  of them (fixed 2026-09-16) — so ledgers banked before that date understate
  the real total, by however much the live slate costs. Treat the first full
  week after the fix as the first honest window.
- ~~Prop CLV: closes for props from the banked line archive~~ — attached
  (above); the sweep needs graded weeks to accumulate.
