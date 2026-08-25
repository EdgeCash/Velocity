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
