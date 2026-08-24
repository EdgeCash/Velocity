# The publish gate, the drift check, and the engagement experiment

Three pieces of one decision: keep the wagering model running for the data
it generates, but stop treating every bettable play as a postable one.

## 1. Why a play worth BETTING is often not worth POSTING

The staked slate and the public post are different products. A bankroll
compounds on volume; a post is a promise that invites a tally. And a
genuinely profitable model still looks bad most days — bootstrapping 10,000
seasons from our own 195 graded bets (55.9%, +3.13% ROI):

| Volume | P(red day) | P(red week) |
| --- | --- | --- |
| 3 plays/day | 46.8% | 44.1% |
| 5 plays/day | 47.1% | 42.9% |
| 25 plays/day | 44.6% | 35.1% |

Median longest red streak in a 100-day season: **5 days**; 8 at the 90th
percentile; 15 at worst. That is not a model-quality problem, it is what a
3% edge looks like from the outside — and it is the churn engine.

## 2. The adverse-selection finding that shaped the gate

The obvious response — "only post the biggest edges" — is backwards.
Splitting our graded record into stake quartiles (stake is Kelly-sized, so
it is monotone in edge):

| Bucket | n | Win% | ROI | Mean CLV |
| --- | --- | --- | --- | --- |
| Q1 lowest edge | 49 | 65.3% | +27.9% | **+0.037** |
| Q2 | 49 | 51.0% | +6.0% | +0.023 |
| Q3 | 49 | 49.0% | −5.9% | +0.018 |
| Q4 **highest** edge | 48 | 58.3% | +3.9% | **−0.048** |

`corr(stake, CLV) = −0.35`. Our biggest claimed edges carry the *worst*
closing-line value — the signature of adverse selection. When the model
screams, it is usually the market knowing something we do not: a late
scratch, a lineup change, a stale line we mispriced.

**Caveat, stated plainly:** only 42 bets have a matched close, 8-13 per
bucket. This is a flag, not a verdict. The gate is built so that if the
finding softens with more data, the ceiling is one constant to move.

## 3. The gate (`velocity/intel/publish.py`)

Three rules, in the order they bite:

1. **An edge BAND, not a floor.** Below `DEFAULT_MIN_EDGE` (0.030) there is
   no edge; above `DEFAULT_MAX_EDGE` (0.120) the number is a data-quality
   alarm, per §2.
2. **Conviction, not arithmetic.** Tier A, plus a composite floor
   (`DEFAULT_MIN_CONVICTION` 0.72) and a *positive context* requirement
   (`DEFAULT_MIN_CONTEXT` 0.05). Tier A alone is not enough: it needs only a
   0.65 composite, and the blend (`0.4·edge + 0.6·context`) lets a bet reach
   that on **edge alone with neutral context** — precisely the
   adverse-selection profile of §2. A big edge that no signal corroborates
   is the shape of a line we have mispriced. A vetoed bet never posts
   regardless of edge.
3. **The market must not have moved against us.** `adverse_drift` compares
   the price we shopped to the newest board price, in probability terms.
   Positive drift means the market moved away from our side. Note the sign
   carefully: moving *toward* us is positive CLV and a good sign — an
   inverted comparison here would withdraw exactly the best plays, which is
   what the test suite pins.

A fourth rule is a guardrail rather than a filter: `DEFAULT_MAX_PLAYS` (5)
caps the night at the highest-conviction plays. "No picks is a pick" sets
the floor at zero; this stops one freak board from dumping thirty plays into
a feed that is supposed to read as high-conviction.

Every candidate lands in an audit frame with the reason it failed, so a
quiet night is explainable rather than mysterious.

### Calibration status — these numbers are provisional

The first live board cleared **30 of 121** candidates on tier A alone, which
is not a high-conviction product; the conviction and context floors above
were added in response. Their exact values are reasoned, not yet fitted: the
audit frame banks `conviction` and `context` for every candidate precisely
so the thresholds can be set from a few weeks of real boards. Expect these
constants to move once there is data to move them with.

**"No picks is a pick."** The gate returns nothing on most nights by
design. An empty wager post is the honest output of a quiet board.

## 4. The drift check (`scripts/model_drift_check.py`)

Runs on the 1st and 15th. Re-runs the walk-forward gates, compares to
`datasets/baselines/model_drift.json`, and exits non-zero on drift beyond
tolerance.

It does **not** refit, retune, or promote anything. Two weeks of live
results is 60-80 graded bets — noise. Auto-recalibrating on that would make
the model chase variance. The models already refit daily on committed
history; what needs a schedule is the question "did something break?".
Updating the baseline is a deliberate `--update-baseline` run by a human
after investigating.

## 5. The engagement experiment (`velocity/report/engagement.py`)

One account, two post styles: `dfs` daily, `wager` only when the gate opens.
Measuring this naively is how you fool yourself, because the styles differ
in cadence, timing, and the outcome mood they land in. So:

* every post records the follower count **at post time** (the normalizer),
  its style, and the prior day's result (`green` / `red` / `quiet`);
* comparisons are **per-post medians**, not sums — engagement is
  heavy-tailed and one viral post would otherwise decide the experiment;
* `compare_by_context` splits style × prior-day-result. That is the cell the
  whole question turns on: if wager posts collapse after a red night while
  DFS holds, the forgiveness gap is showing up in our own numbers;
* `cadence` states the posting-frequency confound out loud rather than
  hiding it;
* `minimum_posts_for_signal` estimates how many posts each style needs
  before a difference means anything — a guard against calling it in week one.

Entry point: `scripts/record_post.py` (log a post, fill metrics in later,
`--report` for the tables). Engagement numbers arrive by hand or by export;
nothing calls a social API.
