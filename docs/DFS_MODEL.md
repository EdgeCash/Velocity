# The MLB DFS projection model

## 1. What the first live slate exposed

Our lineups scored 62-86 DK points and cashed nothing. Grading the
projections against actual DK points that night gave a within-slate
correlation of **+0.03 for hitters** — indistinguishable from noise.

One slate proves nothing (with n≈109 the standard error on that correlation
is ~0.10), but the post-mortem pointed at something structural: the
projection was a **flat season rate with no context at all**. A hitter facing
a Cy Young contender in Oracle Park was priced identically to one facing a
call-up in Sacramento, and a nine-hole bat identically to a leadoff man —
despite the home-run model having already validated all three of those
effects.

## 2. The model (`velocity/models/dfs_mlb.py`)

    hitter DK  = dk_per_pa(batter) × pitcher_factor × park_factor × E[PA|slot]
    pitcher DK = dk_per_start(pitcher)          ← flat, see §4

Every term is fit from the banked box scores with empirical-Bayes shrinkage.
Unknown players, pitchers, parks and slots fall back to the league mean
rather than guessing.

This required extending the box-score banks to carry the **full DK scoring
line** — hits/doubles/triples/HR/RBI/runs/BB/HBP/SB for batters, and
earned runs/hits allowed/wins for starters. `datasets/mlb/batters.parquet`
is now 139,483 batter-games with everything DK scores, which is also what
makes the backtest below possible with no external data.

## 3. Validation — walk-forward, 34,889 hitter-games over 148 slates

Measured **within slate**, because a DFS projection ranks one night's pool;
a season-pooled correlation flatters any model that merely knows a star from
a utility infielder.

| Hitters | mean within-slate r |
| --- | --- |
| flat season rate (previous) | +0.1129 |
| **contextual (shipped)** | **+0.1249** |

Top-10 by projection: **9.14** actual DK points vs **8.97** flat (field
average 6.88). Positive at every cut (top 10 / 20 / 50).

**Stated honestly: this is not yet a statistically proven win.** Paired
across 148 slates the improvement is +0.0120 with SE 0.0069 — *t* = 1.74,
95% CI [−0.0015, +0.0255], better on 56% of slates. It ships because the
point estimate is positive on every metric, the mechanism was independently
validated in the home-run model, and the downside is one-sided — not because
the evidence is conclusive. Re-check it once another season of slates lands.

Note also that last night's alarming +0.03 sits comfortably inside the noise
band around this +0.12 average. One slate genuinely could not have told us
anything.

## 4. What did NOT ship, and why

Pitcher context was built, tested, and **rejected**:

| Pitchers | mean within-slate r |
| --- | --- |
| **flat season rate (shipped)** | **+0.2730** |
| contextual (rejected) | +0.2656 |

Better on only 45% of slates. The opposing-offense term (season-long team DK
per PA) and the park term (an *inverted* hitter park factor rather than a
fitted pitcher-park effect) are crude, and a starter's own rate already
absorbs much of what they attempt to add. The capability remains behind
`use_context=False` so a properly fitted replacement can be switched on when
it earns it.

Pitcher **recency** was built, tested, and **rejected** too. The lineup
backtests (docs/DFS_FORMATS.md) found pitcher projections running ~9% hot
against realized points while hitters, once the confirmed card is known,
come in within half a point. Weighting a starter's own history by recency
is the obvious candidate that changes *rankings* rather than levels — so it
was measured across 12,936 starts over 532 slates:

| Pitchers | mean within-slate r | top-2 arms | level bias |
| --- | --- | --- | --- |
| **flat season rate (shipped)** | **+0.2682** | **19.05** | +0.62 |
| recency half-life 45d | +0.2566 | 18.83 | +0.38 |
| recency half-life 30d | +0.2494 | 18.94 | +0.33 |
| recency half-life 21d | +0.2420 | 18.78 | +0.28 |
| recency half-life 14d | +0.2297 | 18.50 | +0.22 |

Monotone in the wrong direction: the shorter the half-life, the worse the
ranking and the better the level. That is the *same shape* as the per-class
rescale the showdown backtest rejected — both fix the bias by discarding
sample, and both cost ranking. Two independent attempts now say the same
thing: **a starting pitcher's recent form is mostly noise, and his
season-long rate is the best estimate of him available.** The knob survives
as `pitcher_half_life=None` so the negative result is executable.

Worth noting the levels: pitchers project at **r ≈ 0.27**, hitters at
**≈ 0.12**. Pitcher projections carry more than twice the signal, which is
where roster and research effort is best spent.

## 5. The ceiling, and what it implies

Both hitter models sit near r ≈ 0.12 within a slate. That is not a defect —
a hitter's night is four or five plate appearances of near-binary events, and
most of that variance is genuinely unforecastable. Context buys about 10%;
the rest is noise.

The strategic read: in MLB DFS the edge lives less in hitter projection
accuracy than in pitcher selection, contest selection, and (for tournaments)
ownership leverage. Chasing hitter correlation past ~0.15 is likely chasing
noise.

## 6. Historical DK data for backtesting

**DraftKings is its own archive.** Retired draft groups still resolve:
`api.draftkings.com/draftgroups/v1/draftgroups/{id}/draftables` returns the
full historical board — players, salaries, positions — plus a competition
`startTime` that dates it. Verified against ids from 100000 (2024-era) to
today's boards.

There is no index, so ids must be walked. `scripts/harvest_dk_history.py`
does this: `--probe` samples the id space to locate a date range, then
`--from-id/--to-id` scans and keeps the boards matching a league. Leagues are
identified from the board itself using **distinctive** position markers (DK
reuses letters across sports — "C" is a catcher in MLB and a centre in NHL —
and ships multi-eligibility combos like `2B/SS` that must be split before
matching).

Paired with the box-score banks (which now carry actual DK points), that
gives both halves of a full lineup backtest: what a lineup would have cost,
and what it would have scored. Third-party archives (RotoGuru and friends)
are unnecessary.

---

## 7. The WNBA model (`velocity/models/dfs_wnba.py`) — 2026-09

The league had a DraftKings roster spec and nothing to put in it: the repo
banked six team-level columns for the WNBA and no player rows at all
(docs/DFS_FORMATS.md). `scripts/build_wnba_player_box.py` fixes the input —
the sibling wehoop release to the team box already in use, on the same CI-safe
transport — banking **16,896 player-games across 878 games and 299 players**
for 2024–2026 with the full DK scoring line, minutes, and the starter flag.

### The model is two factors on purpose

    player DK  =  dk_per_minute(player)  x  E[minutes | her recent games]

A WNBA roster runs eight or nine deep, so the minute split is the largest
single term in any projection: a starter's 32 minutes against a reserve's 11
separates two players of identical per-minute value by a factor of three.
Both factors shrink toward a **positional** prior — guards, wings and posts
bank DK points differently when assists pay 1.5 against rebounds at 1.25 and
blocks at 2.0 — so a player with four games prices near her position rather
than off a four-game fluke.

### The sweep says: do not smooth the minutes

Walk-forward over **11,668 player-games**, sweeping the two prior strengths
and the minutes window:

| rate prior | minutes prior | window | RMSE ↓ | corr ↑ | within-slate rank ↑ |
|---|---|---|---|---|---|
| 60 | 0 | 5 | **9.050** | **0.7412** | **0.7161** |
| 60 | 1 | 5 | 9.131 | 0.7412 | 0.7157 |
| 60 | 2 | 10 | 9.203 | 0.7377 | 0.7054 |
| 180 | 4 | 10 | 9.508 | 0.7190 | 0.6839 |

**Monotone toward no minute prior at all and the shortest window tried**, and
the mechanism is obvious in hindsight: a rotation change is exactly what a
longer window and a league-mean prior are slowest to see. The promoted point
is the second row, not the first — the endpoint is a limit rather than a bar,
and the two are four ten-thousandths apart on the within-slate rank
correlation, which is the metric a lineup is actually built on. Keeping a
one-game prior means a single appearance can never *fully* determine what a
player is projected to play.

### Promoted, full daily walk-forward

| | value |
|---|---|
| correlation with realized DK points | **0.727** |
| within-slate rank correlation (600 slates) | **0.698** |
| RMSE | **9.19** against 13.30 for the league-mean constant (−31%) |
| mean projection | 18.36 against an actual 18.38 |

For contrast, the MLB hitter model's first live slate correlated **+0.03**.

### The one input that is not machine-verified

DK's basketball scoring constants (`DK_WNBA_POINTS`) were hand-entered from
DraftKings' published rules, and unlike the roster template there is no
machine-readable source: the rules endpoint carries the template and no
scoring, `/help/rules/4/37` renders client-side, and every scoring-shaped API
path 404s. `scoring_disagreement()` is the check that closes this — DK
publishes its own fantasy-points-per-game on any live board, so the first
WNBA slate DK posts will confirm or refute the constants against the same box
scores. Until then it is the one assumption in this vertical, and it is
written down rather than buried.

---

## 8. The college model (`velocity/models/dfs_ncaaf.py`) — 2026-09

NCAAF was the last empty cell, and the audit called it the largest build on
the list. It turned out to be a **data** problem rather than a modelling one:
the repo had a college roster spec (`CFB_CLASSIC`), collected college salaries
daily, and pointed the builder at the FantasyPros scorer — which serves no
college players at all, so the projection frame filtered to zero rows and the
builder exited cleanly every single run.

### The feed

CFBD serves college player statistics behind an API key. **cfbfastR publishes
the same substrate keyless**, on the identical raw-CDN transport the WNBA
(wehoop) and NCAAB (hoopR) verticals already use, so it needs no secret and
works from CI. It is play-level — one row per play with a column per role —
and `velocity/ingest/cfb_players.py` folds it to one row per player-game in
the **NFL DFS vocabulary**, which is also the correct scoring line: DK's
college scoring is its NFL scoring. Banked: **101,121 player-games across
11,885 players**, 2023–2026.

Three things about the source, each verified against it rather than assumed:

* **Touchdown attribution is inconsistent.** On a passing touchdown the
  `touchdown_player` column names the passer 57% of the time and the receiver
  43% — whichever ESPN's play text put first. So the fold never reads that
  column to decide *whose* touchdown it was. A completion on a scoring play is
  a passing touchdown for the passer and a receiving one for the receiver;
  a rush on a scoring play is a rushing touchdown. Verified safe: an
  interception play never carries a completion (so a pick-six cannot become a
  passing touchdown) and a fumble play never carries a touchdown.
* **The passer/receiver assignment itself is sound**, which is worth checking
  before trusting any of it: the top passer holds **91%** of a team-game's
  passing yards (median 100%, 1.66 distinct passers per team-game) while
  receptions spread across ~7 receivers with the top at 40%. Exactly the
  shape football has.
* **Coverage fills progressively, and that is the thing to watch.** Scoring a
  team-game as covered when 7×TD + 3×FG lands within a point of the final
  score the frame itself carries: **2023 at 82%, 2024 at 75%, 2025 at 46%
  (good only through week 8), 2026 at 24%.** An unfilled season looks exactly
  like a season in which nobody scored, so `season_coverage()` measures it and
  the build script refuses below half rather than banking a frame that would
  price every player at nothing.

Positions are read off usage rather than a second feed: a player who throws is
a quarterback, one who is handed the ball is a back, one who is thrown to is a
receiver, judged on his whole sample so a wildcat snap reclassifies nobody.
Interceptions come in light (~1.3 a game against a real ~2.4 — the text names
the interceptor reliably on a return touchdown and less so otherwise) and
fumbles are skipped entirely, because the frame names the fumbler but not the
recovering team. Both leave projections high by a fraction of a point, which
is why the walk-forward below projects 8.9 against an actual 9.2.

### The model, and the window

The model is deliberately the football one the repo already fitted and tested
(`DfsNflModel`): per-player DK points per game shrunk toward **his own
position's** mean. What is college-specific is the window. A professional's
career is one long sample; a college player's is a roster that turns over
every August and a depth chart that moves under him.

Swept walk-forward across the whole 2024 season — **29,217 player-games**:

| window | corr ↑ | RMSE ↓ | within-slate rank ↑ |
|---|---|---|---|
| 2 | 0.4969 | 8.324 | 0.4322 |
| 4 | 0.5201 | 8.012 | 0.4551 |
| **6** | **0.5216** | **7.937** | **0.4561** |
| 8 | 0.5194 | 7.921 | 0.4535 |
| 16 | 0.5082 | 7.961 | 0.4381 |
| 40 | 0.5058 | 7.973 | 0.4356 |

A real interior optimum rather than a sweep running to an endpoint — two
games is noise, forty is stale, six is the peak. Half a college season, which
is about what a roster that turns over annually should be worth.

Against baselines on the same 29,217 rows: a league-mean constant scores RMSE
9.224 and an *in-sample* positional mean 8.908, so the model's 7.937 is 14%
and 11% better respectively. Position alone carries a **negative** within-slate
rank correlation, so all of the model's 0.456 is player-level signal.

The within-slate number is lower than the WNBA's 0.698, and honestly so: a
college board's pool is far deeper in fringe players and the game is higher
variance. It is nonetheless the first college projection this repo has ever
had, against a surface that previously produced nothing at all.
