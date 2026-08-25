# DK contest formats — what DK actually offers, and what Velocity prices

DraftKings runs far more than "the main slate". This is the authoritative map
of the formats DK serves for MLB, NFL and CFB, read from DK's own rules API
rather than from the lobby's marketing copy, plus what Velocity builds for
each one.

## Where the rules come from

Two unauthenticated endpoints carry everything:

* `https://www.draftkings.com/lobby/getcontests?sport={MLB|NFL|CFB}` — the
  day's **draft groups** (`DraftGroups[]`: id, `ContestTypeId`, `GameCount`,
  `StartDate`, `ContestStartTimeSuffix`) and the **contests** that reference
  them (`Contests[].dg` → `Contests[].gameType`, DK's plain-English format
  name).
* `https://api.draftkings.com/lineups/v1/gametypes/{id}/rules?format=json` —
  the roster rules for a game type: `lineupTemplate` (**singular** — the
  roster slots and their counts), `salaryCap`, `teamCount.minValue`,
  `uniquePlayers`, `draftType`, and per-slot multipliers.

Two traps worth stating, both of which cost real time to find:

1. **`ContestTypeId` is not `gameTypeId`.** They agree for the newer formats
   (showdown is 114/96/95 in both spaces) but not the oldest: MLB Classic
   ships as `ContestTypeId` 28 against game type 2, NFL Classic as 21 against
   1. So format detection keys on `gameType` — the **name** off the contest
   list — and falls back to the id only for snapshots taken before the
   collector captured it (`velocity.dfs.salaries.game_type_names`).
2. **DK runs simulated look-alikes.** "Madden Classic" and "Madden Showdown
   Captain Mode" (game types 158/159) have the identical roster shape and a
   completely different player pool — simulated games. A numeric-only or
   shape-only filter drafts them by accident.

## The map

Cap `—` means the format has no salary cap at all: the roster is built by
tier pick or by snake draft, so there is nothing for a knapsack to solve.

### MLB

| Game type | Format | Roster | Cap | Draft | Velocity |
|---|---|---|---|---|---|
| 2 | Classic | P,P,C,1B,2B,3B,SS,OF,OF,OF | $50,000 | SalaryCap | **built + backtested** (`MLB_CLASSIC`) |
| 114 | Showdown Captain Mode | CPT (1.5x), UTIL x5 | $50,000 | SalaryCap | **built + backtested** (`velocity.dfs.showdown`) |
| 45 | Tiers | T1…T6, one player per tier | — | Tiered | **built** (`velocity.dfs.tiered`) |
| 346 | Single Stat - Home Runs | UTIL x3 | — | Tiered | **built** (`velocity.dfs.tiered` + `docs/PROPS_HR.md`) |
| 178 | Snake | IF,IF,OF,OF,UTIL,UTIL,BN | — | SnakeDraft | planned (draft advisor) |
| 179 | Snake Showdown | UTIL x3, BN | — | SnakeDraft | planned (draft advisor) |

### NFL

| Game type | Format | Roster | Cap | Draft | Velocity |
|---|---|---|---|---|---|
| 1 | Classic | QB,RB,RB,WR,WR,WR,TE,FLEX,DST | $50,000 | SalaryCap | **built**, unbacktested — needs a DST projection |
| 96 | Showdown Captain Mode | CPT (1.5x), FLEX x5 | $50,000 | SalaryCap | **built + backtested** — legal and competitive, no measurable edge over DK's pricing |
| 145 | Best Ball | QB,RB,RB,WR,WR,WR,TE,FLEX + 12 BN | — | SnakeDraft | planned; position limits QB 1-3, RB 2-6, WR 3-8, TE 1-3 |
| 189 | Snake | QB,RB,WR/TE,WR/TE,FLEX,FLEX,BENCH | — | SnakeDraft | planned (draft advisor) |
| 192 | Snake Showdown | S-FLEX x3, BENCH | — | SnakeDraft | planned (draft advisor) |
| 158 / 159 | Madden Classic / Madden Showdown | same shapes as 1 / 96 | $50,000 | SalaryCap | **excluded** — simulated games, not real players |

### CFB

| Game type | Format | Roster | Cap | Draft | Velocity |
|---|---|---|---|---|---|
| 94 | Classic | QB,RB,RB,WR,WR,WR,FLEX,S-FLEX | $50,000 | SalaryCap | **built** (`CFB_CLASSIC`) |
| 95 | Showdown Captain Mode | CPT (1.5x), UTIL x5 | $50,000 | SalaryCap | **built** (`velocity.dfs.showdown`) |
| 364 | Single Stat - Touchdowns | FLEX x3 | — | Tiered | **built** (`velocity.dfs.tiered`) |
| 377 | Snake | QB,RB,WR/TE,WR/TE,FLEX,S-FLEX,BENCH | — | SnakeDraft | planned (draft advisor) |
| 378 | Snake Showdown | S-FLEX x3, BENCH | — | SnakeDraft | planned (draft advisor) |

## Showdown Captain Mode

The one format that applies identically across all three sports, and the
second one Velocity solves exactly.

**The doubled board.** DK lists every player on a showdown board **twice**:
once at the captain roster slot (MLB 573 / CFB 510 / NFL's equivalent) at
1.5x salary, once at flex (574 / 509) at 1x. Verified live on MLB group
152707: 190 draftables, 95 players, every pair exactly 1.5x (Tarik Skubal
$19,500 CPT / $13,000 UTIL). The salary normalizer therefore dedupes on
**price**, not roster slot — on a classic board an RB's RB and FLEX entries
share one salary and collapse to one row, while a showdown board's two
prices are genuinely two rows — and `showdown_board()` pairs them back into
one row per player carrying `salary` and `captain_salary`.

**The rules that bind beyond the cap**, both read off the rules payload:

* `uniquePlayers: true` — the captain may not also fill a flex slot;
* `teamCount.minValue: 2` — a roster must span at least two teams. This is
  not decoration: a lopsided projection set will happily build six players
  from one side, and DK rejects it.

**The solve** (`velocity/dfs/showdown.py`) is exact, not heuristic:

1. Dominance-prune the pool. A player beaten on both prices and on points by
   six *same-team* players can never appear in a six-man roster — any roster
   using him can swap in a dominator it is not already using, at no more
   salary, no fewer points, and identical team counts. Same-team only,
   because the two-team rule makes a cross-team swap unsafe.
2. Build the choose-5 flex frontier once, keyed by `(home_count, budget)` —
   the team count rides in the state so the two-team rule is answered
   exactly rather than repaired afterwards.
3. Enumerate captains against that frontier at `cap − captain_salary`,
   requiring at least one flex from the other side when the captain would
   otherwise make it a one-team roster. A captain who turns up inside his own
   best five triggers one rebuild without him.

Pinned against brute force on random pools, plus targeted tests for the two
rules (`tests/test_dfs_showdown.py`). A live 95-player MLB board solves in
about 0.1 seconds.

**Projections** are the flex-slot numbers; the 1.5x is applied by the
optimizer, never baked into the input. MLB uses the contextual model
(`docs/DFS_MODEL.md`); football uses the FantasyPros consensus.

### The showdown backtest

DK is its own historical database: retired draft-group ids still serve their
full board, so `scripts/harvest_dk_history.py --format showdown` walked ids
149000–152700 and recovered **243 real MLB showdown boards** (45,068 priced
rows) covering 6 June – 25 August 2026. Paired with the box-score banks —
which carry the DK scoring line for every batter and starting pitcher — that
is a complete backtest with no purchased data.

`scripts/validate_dfs_lineups.py` fits the projection model walk-forward
(each 14-day window sees only games that finished before it), builds the
board, and scores the roster the way DK scored it. A rostered player with no
box-score row scores 0.0 — exactly what DK pays a player who never appears.

**When you build matters more than any projection term.** `--lineups` picks
the scenario. *Early* is a build before statsapi posts the batting orders:
every banked bat is choosable and his slot is his most recent one.
*Confirmed* is the build most DFS players actually make, after the cards
drop: the pool is the announced nine a side and the order is a fact.

| | Early build | Confirmed card |
|---|---:|---:|
| Our projections through the optimizer | 48.02 | **62.92** |
| The same optimizer run on DK's salaries | 40.30 | 51.40 |
| Random legal rosters (the field proxy) | 24.68 | 47.36 |
| Retrospective best possible roster | 111.87 | 112.40 |
| Beat the salary build | 65.8% | **67.5%** |
| Edge over the salary build | +7.72 (t = 4.68) | **+11.51 (t = 5.83)** |
| Our field percentile | 81.5% | 72.7% |
| The salary build's field percentile | 73.2% | 56.1% |
| Rostered a player who never appeared | **2.13 of 6** | 0.00 |

Two things fall out, and both shipped:

1. **Waiting for the card is worth about fifteen DK points a roster** (48.02
   → 62.92), which dwarfs every context multiplier in the model. Two of our
   six players used to be men who never left the bench. So the live MLB
   surfaces now read statsapi's posted lineups: a team with a card
   contributes exactly its announced nine, at their announced slots, and a
   team without one falls back to the banked pool
   (`apply_confirmed_cards`, used by the lineup builder, the Tiers/Single
   Stat builder, and the home-run board).
2. **The edge over DK's own pricing is real either way.** DK's salary *is*
   the market's projection, so +11.5 points a board at t = 5.8 is the claim
   worth making — and it survives the early build, where it is +7.7 at
   t = 4.7.

**The methodological trap, stated because it nearly published a wrong
result.** The first pass matched each board to its box score by name overlap
on the same date. Two clubs play a three-game series with the same
twenty-six names every night, so overlap cannot tell the games apart — half
the boards matched the wrong day, silently scoring every roster against the
wrong box score and dropping both starting pitchers (the one thing that
changes daily). That version reported +1.98 points at t = 1.24: a null
result, entirely manufactured by the bug. Matching on **start time**, with
name overlap only as an eligibility bar, fixed it — 480 of 480 announced
starters now match — and the real effect is several times larger.

**One bias found, two fixes rejected.** Against realized DK points, pitchers
project 9.5% high (14.79 vs 13.38) while hitters, once the card is known,
are within 3% and slightly *low* (6.75 vs 6.92). The ordering inside each
class is fine; the level between them is not, and the optimizer chooses
across classes. A rolling recalibration — each window scaled by the
class-level actual/projected ratio of the windows before it, strictly past
data — halves the bias and costs a quarter of a point per board. It does not
ship: a uniform per-class multiplier moves everyone in a class together, so
it barely reorders anything the optimizer actually compares. A bias worth
fixing needs a fix that changes rankings, not levels.

The obvious such fix — weighting a starter's own history by recency — was
then built and measured over 12,936 starts, and it fails monotonically:
within-slate correlation runs +0.2682 flat, +0.2566 at a 45-day half-life,
+0.2297 at 14 days, with each slate's top-2 arms falling the same way. It
shrinks the level bias (+0.62 to +0.22) exactly as the rescale did. Two
independent attempts, one conclusion: **a starting pitcher's recent form is
mostly noise, and his season-long rate is the best estimate of him
available.** Both knobs survive, off, so the negative results stay
executable (`docs/DFS_MODEL.md` §4, which also carries the pitcher context
term the classic backtest retired).

## The classic backtest

The same machinery, pointed at the format that carries the field: 263
harvested MLB classic boards (122,078 priced rows), 6 June – 25 August 2026,
walk-forward, scored the way DK scored them.

| | Early build | Confirmed card |
|---|---:|---:|
| Our projections through the optimizer | 73.10 | **96.13** |
| The same optimizer run on DK's salaries | 69.87 | 83.07 |
| Random legal rosters (the field proxy) | 53.89 | 80.96 |
| Retrospective best possible roster | 213.19 | 215.81 |
| Beat the salary build | 51.5% | **63.3%** |
| Edge over the salary build | +3.23 (t = 1.55) | **+13.06 (t = 5.77)** |
| Our field percentile | 72.5% | 66.5% |
| The salary build's field percentile | 69.1% | 53.1% |
| Rostered a player who never appeared | **3.03 of 10** | 0.01 |

This is the sharpest statement of the same result. Built early, the classic
lineup's edge over DK's own pricing is **+3.2 points at t = 1.55 —
indistinguishable from zero**, because three of its ten players never left
the bench. Built from the confirmed card it is +13.1 at t = 5.77. The
projections did not change between those two columns. Only the hour did.

The random field tells the same story from the other side: 53.89 points when
rosters are drawn from every banked bat, 80.96 when they are drawn from the
announced starters. Most of what looks like "picking better players" in an
early build is really just picking players who play.

The hitter level bias makes it concrete: projected 6.48 against 3.34 actual
in the early build, and 6.87 against 6.92 — essentially perfect — once the
card is known. The model was never wrong about what a hitter does in a game.
It was wrong about whether he was in one.

## The football vertical

The MLB verticals could be backtested because the box-score banks carry what
every player actually scored. Football had no equivalent until now.

**The bank.** nflverse publishes weekly player stats as a free public
release asset per season, so `datasets/nfl/player_weeks.parquet` commits
like every other dataset: 112,450 player-weeks over 2020–2025, 4,062
players, each scored by `velocity.models.dfs_nfl.nfl_dk_points`.

Two details separate a real DK score from an approximation:

* **Milestone bonuses** (+3 at 300 passing / 100 rushing / 100 receiving).
  Projections leave them out — a bonus is a tail event and adding it at the
  mean overstates everyone — but actuals must include them, so the scorer
  takes `bonuses` as an argument rather than an assumption.
* **Kickers**, who DK's classic roster has no slot for but its Showdown
  roster does, routinely as a captain. DK pays by distance (3 / 4 / 5 at
  0–39, 40–49, 50+, plus 1 a conversion) and charges nothing for a miss.
  nflverse buckets more finely than DK, so the normalizer collapses its
  bands into DK's three.

Hand-checked rather than trusted, against the best weeks in the bank: Tyreek
Hill's 2020 week 12 (13-269-3) scores 60.9, Alvin Kamara's six-touchdown
week 59.2, Chris Boswell's 2024 opener (1 short, 2 mid, 3 from 50+) exactly
26.0.

**The projection** is empirical-Bayes DK points per game, shrunk toward the
player's *own position* mean — a quarterback averages 15.2 a game and a
tight end 5.7, so one pooled prior would drag every quarterback down and
every tight end up. Walk-forward over 27,342 player-weeks in 114 weeks:

| Mean within-week correlation with actual DK points | |
|---|---:|
| player rate | **+0.5718** |
| + opponent defense | +0.5695 (−0.0023) |

**That number reframes where the DFS edge is.** MLB hitters rank at r ≈ 0.12
within a slate and MLB starters at ≈ 0.27; a football slate ranks at 0.57.
Usage in football is role-driven and stable — per-game DK points correlate
r = 0.79–0.83 season over season — while a baseball hitter's night is four
plate appearances of near-binary events. The opponent term earns nothing and
stays off, the same verdict the MLB pitcher context term got.

### The NFL showdown backtest, and the assumption it kills

Harvested the same way MLB's was: ids 132,000–140,200 recovered **1,189 NFL
boards** covering the whole 2025 season, preseason through the Super Bowl —
869 showdown boards across 525 distinct games, plus 320 classic. 219 boards
cleared the training-history bar and were scored, confirmed-card build.

| | Realized DK points |
|---|---:|
| Our projections through the optimizer | 78.72 |
| The same optimizer run on DK's salaries | 77.67 |
| Random legal rosters (the field proxy) | 56.29 |
| Retrospective best possible roster | 117.08 |
| Beat the salary build | 53.0% |
| Edge over the salary build | **+1.04 (t = 0.82)** |
| Our field percentile | 83.8% |
| The salary build's field percentile | 81.7% |

**Our NFL projections are no better than DK's own pricing.** +1.04 points at
t = 0.82 over 219 boards is indistinguishable from zero, and our field
percentile (83.8%) barely separates from the salary build's (81.7%).

Put beside the baseball numbers, that kills the obvious assumption:

| | within-slate rank r | edge over DK's pricing |
|---|---:|---:|
| MLB classic | 0.12 (hitters) | **+13.06 (t = 5.77)** |
| MLB showdown | 0.12 / 0.27 | **+11.51 (t = 5.83)** |
| NFL showdown | **0.57** | +1.04 (t = 0.82) |

The better projection produced the *smaller* edge. Ranking players well and
beating a market are different problems, and what differs across these rows
is not the model — it is the opposition. DK's NFL salaries already encode
what our 0.57 projection knows, because the NFL DFS market is enormous and
heavily modelled. Baseball's does not, because the thing that mattered there
turned out to be the confirmed lineup card rather than the projection.

So NFL showdown ships as **legal and competitive, not as an edge**: it beats
a random field by 22 points and DK's pricing by nothing we can measure. Any
NFL DFS edge has to come from somewhere the market is weaker — ownership
leverage, correlation, or the inactive report — not from a better points
projection.

**And what is still missing, stated plainly.** `validate_dfs_lineups.py
--league nfl` supports **showdown only**, and refuses classic rather than
running it: DK's NFL classic roster requires a team DST and Velocity has no
team-defense projection. A classic backtest would quietly fill that slot
with a zero, produce a number that looks fine, and mean nothing. Until a DST
model exists, the 242,050-entry NFL classic pool ships on plumbing
confidence — the solver is verified on live boards, the projections are
validated per player-week, and the roster as a whole is not.

## Where the money actually is

Format coverage should follow entries, not novelty. Counted off DK's own
lobby on 2026-08-25 (every contest listed for the sport, summing its current
entrant count and entry fees):

| Sport | Format | Contests | Entries | Entry fees |
|---|---|---:|---:|---:|
| NFL | Classic | 1,778 | 242,050 | $1,563,024 |
| NFL | Showdown Captain Mode | 1,156 | 28,603 | $251,687 |
| MLB | Classic | 913 | 21,068 | $1,374,970 |
| CFB | Classic | 452 | 10,271 | $39,442 |
| CFB | Showdown Captain Mode | 588 | 1,639 | $3,616 |
| MLB | Showdown Captain Mode | 261 | 1,574 | $51,927 |
| NFL | Madden Classic | 134 | 767 | $3,547 |
| MLB | Tiers | 119 | 577 | $7,895 |
| MLB | Single Stat - Home Runs | 90 | 447 | $444 |
| NFL | Madden Showdown | 369 | 353 | $2,617 |
| NFL | Best Ball | 74 | 64 | $12,787 |
| CFB | Single Stat - Touchdowns | 6 | 19 | $55 |
| MLB / NFL / CFB | **Snake** | 87 | **3** | **$7** |
| MLB / NFL / CFB | **Snake Showdown** | 82 | **0** | **$0** |

Two conclusions, both acted on:

* Classic and Showdown are where the field is, which is why both are exact
  solvers with backtests behind them.
* **Snake and Snake Showdown are not worth building.** DK posts 169
  contests across the three sports and they hold three entrants between
  them; every one is a 3-player contest and almost all sit empty. A draft
  advisor for them would be a well-tested tool for a format with no
  opponents. The formats stay documented here, and the collector banks
  their boards (they are salary-free, so they land in the tiered artifact
  with their roster-slot eligibility intact) — if DK's liquidity ever
  arrives, the data is already there and only the advisor is missing.

NFL Best Ball is the one to watch: 64 entries but $12,787 in fees, so the
buy-ins are large. It is a 20-man snake draft with position limits (QB 1-3,
RB 2-6, WR 3-8, TE 1-3) — a different problem from a daily lineup, and a
real one if the fees hold.

## Eligibility, and one live bug worth remembering

`velocity.dfs.pipeline.eligible_board` drops players who cannot take the
field, on every MLB format:

* a DK status of IL/IR/O/OUT/NA/SUSP removes the player — **unless** DK also
  flags him probable for tonight. The probable marker (`playerGameAttributes`
  id 1) is a statement about *this game*; the status is a roster designation
  DK has not cleared. Live proof: on the 8/25 LAD @ ATL board DK carried
  Tyler Glasnow as `IL` **and** flagged him probable, and statsapi had him
  announced as the starter. The old ordering dropped the game's actual
  starting pitcher from both the classic and the showdown pool.
* the P pool keeps only DK's flagged probables — DK lists every rostered
  pitcher, and a non-probable never starts.

## Building it

```bash
python scripts/build_dfs_lineup.py --league mlb \
    --salaries artifacts/dk_salaries/dk_salaries_mlb_<stamp>.parquet \
    --fp artifacts/mlbstats/mlb_player_stats_<stamp>.parquet \
    --out artifacts/slate
```

Solves every classic slate grouping (main / Early / Night / Turbo) **and**
every showdown board on the snapshot. **Run it after statsapi posts the
batting orders** — roughly two hours before first pitch. The backtests above
say that single choice is worth more than every context term in the model
put together; run before the cards drop, the classic lineup's edge over DK's
own pricing is not statistically distinguishable from zero.

Showdown output is a separate `dfs_showdown_{league}_{stamp}.parquet`
carrying all solved boards, with a card and captions rendered for the
strongest one. `--no-showdown` skips it.

### The tournament unit is a portfolio

A cash lineup maximizes the mean. Tournaments pay the right tail of a huge
field, and the documented winning shape is many diversified, *stacked*
entries with overlap and exposure caps (docs/EDGE_RESEARCH.md §5). The GPP
builder already did that for football, anchored on a quarterback. Baseball
has no such anchor: runs come in innings, so the correlated block is a run
of the **batting order**. `velocity/dfs/gpp.py` now enforces that shape —
a primary block of four or five hitters from one club (DK caps a classic
roster at five) plus a two-hitter mini-stack from a second — and solves
candidates through the MLB-legal solver so its best stacks are not illegal.

Measured on 87 sampled classic boards, confirmed-card build, portfolios of
up to five lineups:

| | Realized DK points | Field percentile |
|---|---:|---:|
| Best entry of the portfolio | **106.03** | **77.4%** |
| Mean entry of the portfolio | 92.13 | — |
| The single cash lineup | 91.40 | 61.2% |

Read it carefully: best-of-N is mechanically increasing in N, so the top row
is not evidence that stacking beats the cash build. The evidence is the
middle row. **The stack constraint costs nothing on the mean** — the average
stacked entry scores as well as the cash-optimal one — while the spread of
the portfolio reaches a much better tail. That is the whole argument for
multi-entry, and it is now measured on real boards rather than assumed.

The backtest's portfolios averaged 2.6 lineups a board (the 4+2 requirement
and the overlap cap are restrictive at a small candidate budget); the live
build asks for twenty at a larger budget and returns eighteen, so the real
portfolio's tail is wider than the table above.

### The clock

`.github/workflows/dfs-slate.yml` runs the DFS surfaces on their own
schedule rather than riding the betting slate's 16:00/22:00 UTC cadence,
for exactly the reason the backtests give:

Every window sits **after** the relevant availability news and **before**
the slate it serves locks. MLB posts batting orders two to three hours out;
the NFL posts inactives ninety minutes out.

| Cron (UTC) | What it catches |
|---|---|
| `0 16 * * *` | MLB day games; NFL early-slate inactives (11:30 ET) |
| `30 19 * * *` | NFL late-slate inactives (14:35 ET) |
| `30 21 * * *` | MLB main slate, cards mostly in |
| `30 22 * * *` | MLB main slate, second pass nearer lock |
| `30 23 * * *` | Sunday-night inactives (18:50 ET); MLB west-coast cards |
| `0 1 * * *` | MLB night slate |

Each run takes a fresh DK snapshot (salaries move, and the probable-pitcher
and lineup flags are game-day state), builds every format, and uploads a
private artifact. The betting-slate workflow pulls the newest one in before
it publishes the site, so the DFS page shows the entries built closest to
lock. Every input is free and unauthenticated, so the extra runs cost
nothing but minutes.

To re-run either backtest:

```bash
python scripts/harvest_dk_history.py --from-id 149000 --to-id 152700 \
    --league mlb --format classic --workers 8 --out artifacts/dk_history
python scripts/validate_dfs_lineups.py --format classic --lineups confirmed \
    --boards 'artifacts/dk_history/dk_history_mlb_classic_*.parquet'
# add --gpp 5 --sample-every 4 for the portfolio pass (it re-solves the
# knapsack many times a board, so it samples rather than sweeping)
```
