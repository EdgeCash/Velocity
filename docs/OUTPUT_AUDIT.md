# The output audit — the curated list of plays, measured against the close

**Status:** findings + ordered improvement list (v0.1), written 2026-09-17,
the day the projection audit's list closed (`docs/PROJECTION_AUDIT.md`).
The inputs are where the lab left them; this is the same treatment for what
comes out: the plays the runner stakes and the five it posts.

**Harness:** `scripts/wager_lab.py` over `velocity/backtest/wagers.py` —
every selection rule scored on the promoted chain's walk-forward projections
(`datasets/{league}/projections_promoted.parquet`, the model lab's
out-of-sample frame for `live-{league}-promoted`, 4,080 NFL and 8,360
FBS-vs-FBS college games with a closing line), at the price the games frame
carries (the NFL schedule's closing juice; −110 for college), with the
per-season table beside every aggregate. The rule of this document, as of
the projection audit: **nothing is promoted from an aggregate**; a rule that
pays in five seasons of twelve is a coin whatever its total says.

## 1. What the output is today

One pipeline (`scripts/run_live_slate.py` → `velocity/wagering/slate.py`):
project → shop and de-vig → the edge gate (0.02 on a belief anchored to the
market at `model_weight` 0.2 NFL / 0.13 college) → the college totals'
6-point disagreement filter → quarter-Kelly with a 5% bet cap, a 10% game
cap and a 25% slate cap → paper demotions (college spreads and moneylines,
team totals, FCS games, exchanges, the 0.12 / 0.50 edge ceilings) → the
intel layer's annotation → the publish gate (tier A, conviction ≥ 0.72,
five plays). Two things are worth stating plainly:

- **Nothing caps the count of the staked list.** Money is capped three
  ways; the number of plays is not. The only count ceiling in the system
  is the publish gate's five, which governs posting.
- **The five posted plays are chosen by a signal the record already calls
  a null.** `docs/BACKTEST_INTEL.md`: tier A 50.7% against tier C 52.8% in
  the NFL; confirming and contradicting context identical at 48.8% in
  college. The injury veto is the one intel channel with evidence (vetoed
  picks 47.9% against the pool's 51.7%).

## 2. What the lab measures

### 2.1 The model's probabilities are not probabilities against the close

A picked side's raw probability — the sim's normal at the projection's own
dispersion, evaluated at the closing number — against how often it won:

| picked side's raw probability | NFL spread | NFL total | NCAAF spread | NCAAF total |
|---|---|---|---|---|
| 0.50–0.53 | 47.3% (1,216) | 49.3% (1,129) | 50.1% (1,546) | 51.2% (1,687) |
| 0.53–0.56 | 48.3% (1,044) | 51.8% (1,062) | 49.1% (1,500) | 50.4% (1,590) |
| 0.56–0.60 | 50.4% (912) | 48.4% (943) | 49.1% (1,755) | 53.0% (1,780) |
| 0.60–0.65 | 50.4% (540) | 53.4% (601) | 47.0% (1,574) | 50.0% (1,641) |
| 0.65–0.75 | 52.4% (246) | 51.6% (277) | 46.8% (1,409) | 53.3% (1,332) |

The close already prices most of what the model knows. The weight the close
would put on the model's number — the least-squares coefficient of (actual
− close) on (model − close), the projection audit's information weight —
is **+0.03 on the NFL spread, +0.07 on the NFL total, −0.09 on the college
spread, +0.15 on the college total, −0.01 on the NFL moneyline.** The
runner anchors at 0.2 and 0.13, weights set from Brier sweeps on the chains
of a month ago, so the edges on the board still run about double what they
earn in the NFL and are outright wrong-signed on college spreads.

### 2.2 Where the edge is, and where it is not

Every rule is "the model's disagreement with the close, in points, in the
side's direction"; ROI is per unit at the price paid; the last column is
seasons with five or more bets that cleared 52.4%.

| rule | bets | win | ROI | seasons cleared | worst season |
|---|---|---|---|---|---|
| **NFL totals, 4+ points, either side** | 641 | 54.3% | +5.7% | 9 of 15 | 36% |
| NFL totals, unders 4+ | 340 | 55.6% | +8.4% | 9 of 15 | 41% |
| NFL totals, overs 4+ | 301 | 52.8% | +2.6% | 9 of 14 | 33% |
| NFL totals, 6+ either | 182 | 58.8% | +14.3% | 5 of 11 | 20% |
| NFL spreads, 6+ either | 121 | 55.4% | +7.6% | 8 of 12 | 0% |
| NFL spreads, away 4+ | 214 | 55.1% | +7.5% | 9 of 15 | 0% |
| NFL spreads, home 4+ | 285 | 49.5% | −4.3% | 6 of 15 | 22% |
| NFL totals, anchored edge ≥ 0.01 at w=0.07 | 420 | 53.3% | +3.7% | 10 of 15 | 0% |
| NCAAF totals, 6+ either (**the shipped rule**) | 1,658 | 52.1% | −0.5% | 5 of 12 | 47% |
| **NCAAF totals, unders 4+** | 1,263 | 53.6% | +2.3% | 7 of 12 | 49% |
| NCAAF totals, unders 8+ | 297 | 57.2% | +9.3% | 8 of 11 | 47% |
| NCAAF totals, overs 4+ | 1,975 | 50.0% | −4.6% | 2 of 12 | 40% |
| NCAAF spreads, 6+ either | 1,897 | 47.3% | −9.6% | 1 of 12 | 43% |
| NCAAF spreads, 8+ home | 472 | 46.0% | −12.2% | 4 of 12 | 35% |
| NFL moneyline, any edge bucket | — | at the market's rate | ≈ 0 | — | — |

**Readings, honestly:**

1. **The NFL total is the one clean edge**, and it is a totals-under
   edge more than a totals edge: 55.6% on the unders at 4 points against
   52.8% on the overs, positive in nine seasons of fifteen either way. The
   6-point cut's 58.8% is 182 bets across eleven seasons with a 20% worst
   season — the aggregate a reader wants and the robustness column says
   not to trust yet.
2. **The college edge is entirely on the under side.** The shipped rule —
   6 points, either side — is 52.1% and pays in five seasons of twelve,
   because the overs it takes are 50.0% at every threshold. Unders alone
   at 4 points are 53.6% on 1,263, seven seasons of twelve; at 8 points
   57.2% on 297, eight of eleven. A market that prices college scoring
   high and a model that prices it low is a hypothesis with eleven
   seasons behind it, not a fit.
3. **College spreads are inverted at every size.** When the model
   disagrees with the college close by six points, the close wins 52.7%.
   That is consistent with the negative information weight and with the
   market knowing what the model does not — quarterback and roster news
   the fit has not seen — and it is why the spread stays papered. It is
   *not* a reason to fade the model: a rule with no hypothesis is a rule
   waiting to reverse.
4. **The NFL moneyline carries nothing.** The model's moneyline edges
   pick underdogs that win exactly as often as the market says, in every
   edge bucket. The market's Brier beats the model's (0.2102 against
   0.2176) and the anchoring weight is −0.01.
5. **The edge is thin.** Two to eight percent on the totals rules that
   hold up, a few dozen bets a season in the NFL, a hundred in college.
   The list will be short, which is the point of curating one.

### 2.3 What the record cannot yet say

The graded live record is 195 bets, 42 with a matched close
(`docs/PUBLISH_GATE.md` §2). The adverse-selection finding that shaped the
edge ceilings — the highest-edge quartile carrying the worst closing-line
value — came from those 42. Nothing about the published five, the
conviction floor or the 0.12 ceiling has been scored against outcomes at a
sample the lab would accept. The lab above is the first such scoring of
the selection rules; live closing-line value by rule is the second, and it
does not exist yet.

## 3. The improvement list, in order

1. **The wager lab** (this document's harness) — *done with this audit*.
   Every rule change below goes through it, with the per-season column,
   before it reaches the runner.
2. **Re-fit the anchoring weights per market from the promoted chains**
   and make them per market, not per league: the NFL total's 0.07 and the
   college total's 0.15 in place of 0.2 and 0.13; zero on the spreads and
   the moneylines, where the close knows more. Honest beliefs make honest
   Kelly stakes; the 0.12 ceiling, a patch on inflated edges, can then be
   re-measured rather than assumed. *Gate: the lab's calibration tables,
   anchored.*
3. **Directional totals rules.** College: unders only, at 4 points
   (53.6%, seven seasons of twelve), the overs dropped. NFL: the 4-point
   cut promoted on both sides, the under half flagged as the stronger.
   *Gate: the lab; per-season robustness stated on the card.*
4. **Exclude what has no edge instead of papering it.** College spreads
   and moneylines and the NFL moneyline out of the board's staked and
   published sets; NFL spreads paper until the 6-point cut has more than
   121 bets behind it.
5. **A count-capped, tiered list.** Rank by anchored edge within rules that
   have a record, cap the count per slate and per market, and print each
   tier's walk-forward win rate and season count on the card. Retire the
   conviction floor in the publish gate and keep the injury veto.
6. **Live closing-line value by rule tier as the gate on the list.** Every
   published play gets its close attached in the grader; CLV and the
   record by tier accrue in the ledger; a rule is promoted or demoted on
   that evidence, the way a model change is on the lab's.

Later, once the list has a record: the early-season NFL totals (58% on
199 bets in weeks 1–6 against 53% after — a phase-aware rule); the
8-point college under (57%, thin); the away-side NFL spread (55%, thin);
props and exchanges, which the lab does not score.

## 4. Status

**2026-09-17, the selection round (items 2–4).** Landed together, because
the weights and the rules only make sense as a pair:

- **Per-market anchoring weights** (`SlateConfig.model_weight_by_market`,
  the runner's `DEFAULT_MODEL_WEIGHT_BY_MARKET`, `--model-weight-market`),
  fitted as the weight that maps each promoted rule's claimed edge onto its
  walk-forward record (`velocity.backtest.wagers.rule_weight`; the lab
  prints it beside every `--rules` entry): the NFL total's 4-point cut earns
  0.29 of its raw disagreement over 637 bets, the college under-only cut
  0.21 over 1,255. The linear weight over every game (0.07 / 0.15) is the
  wrong number for the bets a rule makes — the model's information sits in
  the large disagreements, where a picked side at a raw 0.66 wins 54–56%.
  Spreads and moneylines anchor at 0 in both leagues: the close would put
  nothing on the model there, so those markets leave the board rather
  than sit on it as paper — the lab is where they earn their way back.
- **Directional totals rules** (`SlateConfig.total_sides`, the runner's
  `DEFAULT_TOTAL_EDGE_BY_LEAGUE` / `DEFAULT_TOTAL_SIDES_BY_LEAGUE`,
  `--nfl-total-edge`, `--ncaaf-total-sides`): the NFL total at 4 points
  either side, the college total at 4 points on the under alone (from 6
  either side). With the fitted weight a 4-point disagreement claims
  0.029 of edge in the NFL and 0.023 in college, so the 0.02 edge gate
  stays as the EV sanity check and the points rule does the selecting;
  quarter-Kelly at a 54% belief stakes about 1% of bankroll.
- **Exclusions** fall out of the zero weights: college spreads and
  moneylines and the NFL spread and moneyline produce no rows. The paper
  flags stay for the day a market has a rule.

**2026-09-17, the curated-list round (items 5–6).**

- **Rule tiers on every play** (`velocity/wagering/tiers.py`): the tier a
  play earns is the rule that admitted it — market, side, points of
  disagreement — and the tier carries the lab's record. NFL: A = unders at
  4+ (55.6% over 340 bets, 9 of 15 seasons), B = overs at 4+ (52.8%, 301,
  9 of 14). College: A = unders at 8+ (57.2%, 297, 8 of 11), B = unders at
  4–8 (53.6%, 1,263, 7 of 12). The table is pinned in code and a test
  recomputes it from the committed projections, so it cannot drift from
  the evidence silently. The slate row carries ``rule_tier`` and
  ``rule_record``; the card's POST line prints them.
- **The publish gate runs by rule** (`publish_slate(rule_tiers=)`,
  `--publish-by-rule`, on by default): a play posts only when a rule with
  a record admits it, the running order is tier then edge, and the
  conviction and context floors stand down — the intel backtest measured
  them as a null. The injury veto, the edge band, the drift check and the
  five-play cap keep their say.
- **Closing-line value by tier** (`velocity.eval.metrics.clv_by_tier`, the
  site's `clv_by_tier` table): the tier rides the ``Bet`` into the settled
  record, so the grader's CLV and the win rate and ROI accrue per tier,
  with the un-tiered plays as the control. It is the live gate on the
  list: a tier whose live record parts from its walk-forward record is
  demoted the way a model change is rejected in the lab. Empty until the
  first tiered slates are graded.

**2026-09-17, the report round.** The graphics read the same table the
gate does, so a card can no longer say something the list would not:

- **Leans keyed to the rule tiers** (`velocity.report.social.rule_call`,
  used by the social card's verdict row and the matchup card's panels):
  a lean fires only where a rule with a record admits the disagreement, and
  carries the rule and its record ("under by 4.3 · rule A · 55.6% on
  340"). The fixed bars it replaces (2.5 spread / 3.0 total / 7 points of
  win probability) were never measured. The blanks say why they are blank:
  "no rule with a record" on the spread and moneyline in both leagues,
  "no rule for overs" in college, "under by 2.1 · below the 4 bar" on a
  total short of the rule. The matchup card's "unusually wide" caution
  now applies only where no rule admits the number — the lab measured the
  widest total gaps directly and found them the strongest rule, not the
  worst bet.
- **The PLAY badge reads the rule tier** (`plays_from_bets` takes the
  runner's `rule_tiers_for` map): the letter on the chip is the one the
  publish gate ranked on, with the rule's record beside the price and
  stake; the intel conviction tier it printed before was the null the
  backtest measured.
- **The WHY band leads with the argument that admitted the play**
  (`model_why(plays=)`): the model's number against the market's, the
  disagreement in the side's direction, and the rule's walk-forward record
  — "UNDER 45.5: model 41.2, under by 4.3; unders 4+ ran 55.6% over 340
  bets, 9 of 15 seasons." The intel layer keeps one line on the card: its
  veto, when it fired.

Every item on the list is landed. What the list now needs is time: the
record by tier, at a sample the lab would accept, before any rule is
re-ranked on live evidence.
