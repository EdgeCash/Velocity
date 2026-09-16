# Data audit — what we fetch and do not use

A standing list of data already arriving that nothing reads, and of places
where a model reaches for a proxy while the real thing sits in a payload we
discard. **Findings only — nothing here is a decision.** We work through it
after the audit is complete.

## Why this exists

The FantasyPros stat-key census (2026-09-16) found the feed served 37 keys and
we read 6 — including a kicker block that left a Showdown position scoring
0.00 on every board. That was not a one-off. A field nobody reads produces no
error, no warning and no failing test: **abstention looks exactly like
health**, which is why every finding here survived months in plain sight.

The pattern is consistent enough to state as a rule: where a normalizer names
the columns it keeps, assume the payload carries more, and check.

Status values: **OPEN** (found, not yet acted on), **DONE** (landed, with the
PR), **DECLINED** (deliberately not used, with the reason).

---

## 1. BettingPros `/events` — today's lineup (DONE)

`scripts/collect_bettingpros.py` keeps five fields of the twenty-three the MLB
payload carries:

```python
_EVENT_COLUMNS = ("id", "home", "visitor", "scheduled", "participants")
```

Discarded: `lineups` (`home_lineup`, `visitor_lineup`, `home_lineup_type`,
`visitor_lineup_type`), `pitchers`, `park_factors`, `notes`, `weather`,
`venue`, and ten more.

**CORRECTION (2026-09-16).** The first version of this finding said a benched
hitter is priced as a starter. That is wrong where statsapi has posted the
card: `apply_confirmed_cards` already folds statsapi's confirmed order into the
slot and team maps and returns the eligible set, so an announced bench does
leave the board — keyed on MLBAM ids, with no name matching, which is strictly
more reliable than any name join. I should have read that path before writing
the finding.

**What is actually true is a timing gap, and it is still worth closing.**
Measured on 2026-09-16:

| Source | Sides with a card | When |
|---|---|---|
| statsapi (`fetch_lineups`) | **8** of 60 | 18:55 UTC |
| BettingPros `/events` | **38 confirmed + 22 projected = 60** of 60 | 16:53 UTC |

statsapi publishes "a couple of hours before first pitch". The live slate runs
at **16:53 and 22:53 UTC**, and 26 of today's 30 games start at 22:00 UTC or
later — so at the 16:53 run the overwhelming majority of sides have no
confirmed card and fall back to the batter's most recent prior game. BP had a
card for every one of those sides two hours earlier.

So the gap is not "we ignore the lineup". It is that our lineup source posts
after the run that needs it, and a second source posts before.

**Fixed 2026-09-16.** `normalize_lineups` banks the order, and
`apply_projected_cards` folds it in **under** `apply_confirmed_cards` — filling
the early window and overridden the moment statsapi posts, because a confirmed
card is the manager's order on MLBAM ids while the book's is a read on names.
The board intersects the two eligible sets rather than replacing, since
statsapi calls every bat on an unposted team eligible and that would otherwise
undo the book's restriction.

On the 2026-09-16 slate the fallback corrected **208 slots** and excluded
**554 bats** who were on a carded team but not in its lineup, out of 899 the
board would otherwise have priced.

The original fallback, for the record:

```python
recent.sort_values("game_id").drop_duplicates("batter_id", keep="last")
```

Lineup slot sets expected plate appearances, which drives `P(>=1 HR)` directly.
So:

- a hitter who batted 2nd yesterday and 7th today is priced at the wrong PA;
- **a hitter not in today's lineup at all is priced as a starter** — the same
  class of error as a position joining the pool at zero, pointed the other way.

It also leaves a hole in the veto path. `PropAvailabilitySignal` vetoes on the
*injury report*, which is right for football. Baseball players are not "out",
they are simply not in the lineup — so a benched hitter's prop has no veto
today. `lineup_type` (confirmed vs projected) is exactly the availability
signal that gap needs.

## 2. BettingPros `/events` — weather (OPEN, blocked on a false premise)

`velocity/models/props_hr.py` states:

> Weather is deliberately absent: temperature and wind genuinely move home-run
> distance, but we have no banked historical weather to FIT a coefficient on.

True, and circular. BP serves `weather` on every event, every run, every three
hours: `forecast_temp`, `forecast_wind_speed`, `forecast_wind_direction`,
`forecast_wind_degree`, `forecast_rain_chance`, `forecast_humidity`,
`forecast_pressure`. Wind in a ballpark is one of the largest home-run effects
there is. **The bank needed to fit that coefficient is the thing the collector
has been dropping.**

Same shape as `pass_completions` (#211): the blocker was a column, not a feed.
Banking it starts the clock; the coefficient comes later, once there are
enough games to fit rather than assume.

## 3. BettingPros `/props` — form splits and opposition rank (DECLINED)

`_PROP_COLUMNS` keeps 22 fields — the lines and BP's projection block — and
discards `performance` (`last_1`, `last_5`, `last_10`, `last_15`, `last_20`,
`season`, `prior_season`, `h2h`) and `extra.opposition_rank`.

### This finding's premise was wrong, and it was mine

It said: *"We have a `FormSignal` and a `PropMatchupSignal`. Those are
precisely their inputs, arriving pre-computed on the row we already bank."*
Neither half survives reading the two signals.

**`FormSignal` is a team signal.** It evaluates only `spread`, `moneyline` and
`total`, and it reads `TeamContext.recent_net_ppg()` against `net_ppg()` — net
points per game. It never sees a player prop, so a per-player `last_5` is not
its input and could not be passed to it. There is **no** player-level form
signal in the set (`MatchupSignal`, `FormSignal`, `RestSignal`,
`InjurySignal`, `PropAvailabilitySignal`, `PropMatchupSignal`,
`ExternalRatingSignal`, `PropExternalSignal`, `PropLineOutlierSignal`).

**`PropMatchupSignal` already computes the matchup from our own fit.** It
takes the opponent's `pass_def`/`rush_def`/`def_epa` — EPA per play allowed,
fitted here — and z-scores it against the slate's own dispersion. BP's
`opposition_rank` is a served ordinal on an undisclosed basis; it does not fit
that machinery, and it is the same category this repo already declines by
name two sections below: *"BP `park_factors` — we fit our own from banked
games, shrunk by sample. A served, unshrunk number is not an improvement."*

### And the form block is a copy of something we already own

`datasets/nfl/player_weeks.parquet` holds **113,581 player-weeks, 2020–2026**,
carrying `pass_yards`, `pass_tds`, `rush_yards`, `receiving_yards` and
`receptions` — every market BP's `performance` block covers. So every one of
those windows is ours to compute, for any player, any market, retrospectively
and at any window length, with no provider dependency. Checked on the 2025
receiving leader: last-1 165.0, last-5 91.0, last-10 119.7, season 107.7.
`prior_season` is a season filter; `h2h` is an `opponent` filter, and the bank
carries that column too.

Banking BP's copy would add nine columns nothing reads to a bank whose audited
property today is that **every banked column is read** — which is the same
defect as finding 6, one table over. Unlike finding 2/5 (weather), there is no
clock to start: this data does not have to accumulate forward, because we
already have all of it.

**Declined.** Moved to "Deliberately not used" below.

### What is actually worth doing here, if anything

The gap the finding half-found is real and is *not* about BettingPros: there
is no player-level form signal at all, and the data to build one is already
banked. That is new modelling with a validation question attached (does recent
form predict a prop outcome beyond the projection already in the sim?), not a
wiring job, so it wants deciding rather than doing quietly.

## 4. Statcast is collected every live run and never handed to the model (DONE)

**Fixed 2026-09-16.** `live-slate.yml` now resolves the snapshot and passes
`--statcast`, mirroring `dfs-slate.yml`. Two things landed with it so this
cannot recur quietly: `HomeRunModel` carries `statcast_batters` (how many
batters actually got the batted-ball prior), the board prints it on the fit
line and emits a `::warning::` when it is zero, and
`tests/test_live_slate_workflow.py` asserts the flag **inside the step** —
scoped that way because the flag existing elsewhere in the directory is what
made the omission invisible. Verified by reverting the workflow: three tests
fail on the old version.

The original finding, kept for the record — the live workflow fetched the
Savant snapshot and then called the board without it:

```yaml
# .github/workflows/live-slate.yml:460
python scripts/collect_mlb_statcast.py --out artifacts/statcast || true
PYTHONPATH=scripts python scripts/build_hr_board.py \
  --out artifacts/slate || true          # <-- no --statcast
```

The sibling workflow gets it right:

```yaml
# .github/workflows/dfs-slate.yml:172
${STATCAST:+--statcast "$STATCAST"} --out artifacts/slate || true
```

Same script, two call sites, one flag. `--statcast` defaults to `None`, and
`_statcast_prior` then returns `{}` — documented as "the prior collapses to the
league rate and the model degrades to plain shrinkage."

What that costs, in the module's own words:

> last season's barrel rate predicts this season's HR/PA at r2 ~ 0.39, edging
> prior-season HR/PA itself (0.36). **That gap is the whole reason this model
> exists** — the market anchors on the counting stat, the batted-ball rate
> knows first.

So the live home-run board has been running on the thing the model was built to
beat. Nothing complains: the collector succeeds, the board builds, the fallback
is deliberate and silent, and `|| true` plus `continue-on-error` swallow the
rest. Abstention looking like health, again.

## 5. MLB has no weather path, while NFL has a good one (OPEN)

Worth stating beside finding 2, because it shows the gap is not difficulty.
NFL has `velocity/features/weather.py`: a live per-stadium forecast fetch,
dome and closed-roof exclusion (`OUTDOOR_ROOFS`), per-era relocation rows, and
`WeatherAdjustedModel` applying wind to totals symmetrically. It is careful
work.

MLB has none of it — and wind in a ballpark is one of the largest home-run
effects there is. The MLB path simply never got the NFL path's treatment, and
the forecast it would need is already arriving on every BettingPros event
(finding 2).

## 6. NCAAF prop lines are bought every run and never priced (DONE)

`FOOTBALL_PROP_LEAGUES = ("nfl", "ncaaf")`, and the collector buys the full
event-market board for both (`LEAGUE_PROP_MARKETS["ncaaf"] = DEFAULT_EVENT_MARKETS`,
`--leagues "nfl ncaaf mlb nhl"`). Those are per-market, per-event Odds API
credits, spent every run.

The prop slate then cannot price any of it. It reads FantasyPros:

```python
fp = fp[fp["league"].astype(str) == args.league]
if fp.empty:
    print(f"prop slate skipped: no {args.league} rows in {args.fp_projections}")
```

and the FantasyPros public API **has no NCAAF projections endpoint at all** —
the collector skips college by design and says so every run. So the filter is
always empty, the slate always skips, and the lines we paid for are banked and
never read.

**The substitute already exists and is already proven.** The DFS board hit the
same wall and solved it:

```yaml
# dfs-slate.yml:120 — "FantasyPros serves no college players at all, so the
# board never built (velocity/models/dfs_ncaaf.py)."
if [ "${league}" = "ncaaf" ]; then PROJ_FILE="datasets/ncaaf/player_games.parquet"; fi
```

`datasets/ncaaf/player_games.parquet` is banked, current, and 20 columns wide.
The prop slate never got the same treatment. Two honest options: point the
NCAAF prop slate at the banked player games the way DFS does, or stop buying
NCAAF prop markets. Doing neither is the only choice that costs money for
nothing.

### Done — the first option, once 9 and 10 unblocked it

`velocity/models/props_ncaaf.py` projects the bank into the long
`(player, stat, value)` frame the prop sim already eats, over the same
six-game window the college DFS board swept walk-forward. **Ten of the eleven
football prop markets** price; `pass_completions` is the exception, because
cfbfastR records an incompletion's passer but never a completion count.

Two decisions that were the NFL's by default are now the league's:

* **Where the projection comes from.** `_prop_projection_frame` resolves it
  per league and prints why when it has none. The old gate also required
  `--fp-projections` to be passed at all, which NCAAF will never have — that
  condition is gone, which is what the comment above it had already argued
  for on the MLB near-miss.
* **How a board name becomes a team.** The Odds API writes "Georgia
  Bulldogs"; the bank keys by school. `nickname_aliases` — already in the repo
  for the exchange boards — resolves it by longest prefix, so "Georgia
  Southern Eagles" cannot land on Georgia. No match resolves to nothing and
  the game is skipped, never guessed.

Two filters keep a line off the wrong man: only players active this season
(11,697 banked → 4,177 active), and no name held by two banked players (180
across four seasons, 20 live in 2026 — both dropped rather than coin-flipped).

**And the dispersion is college's own.** This is the part that would have gone
wrong quietly. Re-fitting on the college bank puts team volume σ at roughly
**twice** the NFL's (pass 0.242 against 0.118) while the per-player numbers
come in at or below it — blowouts and tempo move the whole pie, but a player's
share of it is no noisier than a professional's. Shipping `FootballPropConfig()`
would have simulated distributions about half as wide as they are, and a
too-narrow distribution does not fail loudly: it **manufactures edge**, on
every market at once. Full table in docs/PROPS.md.

**And it prices the board already bought, not a second copy of it.** This is
the part that would have quietly made the finding worse: the league gate used
to require `--fp-projections`, which NCAAF never has, so removing it also let
NCAAF reach the live prop-line pull — doubling the spend on the exact board
this finding says we waste. NCAAF now never pulls prop lines live.
`--prop-lines-dir` points at the props collector's banked boards, already
downloaded in the same job for grading; the freshest inside
`--board-max-age-min` is priced and anything staler is refused with its age
named, off the filename stamp rather than the mtime (the game board learned
that one the hard way). NFL is unchanged and still pulls live.

The projection needed **no** workflow flag, deliberately: the bank is
committed, so the runner's default resolves in a fresh checkout, and adding a
`--ncaaf-player-games` line would repeat the Statcast mistake in reverse — a
flag pointing at a path nobody checked. A test asserts the default exists
instead.

## 6b. The same shape, one league over: NHL (DONE — cut)

Finding 6 is not a one-off. Mapping every league we BUY prop lines for against
every league a slate can PRICE:

| League | Lines bought | Priced by a slate? | Bank to price from |
|---|---|---|---|
| nfl | six-market board | `_prop_slate` (FantasyPros) | player_weeks + FP |
| ncaaf | six-market board | **NO** (FP has no college) | `player_games` — banked, unused for props |
| mlb | `pitcher_strikeouts`, `batter_home_runs` | `_mlb_k_slate` (banked starters) | starters + batters |
| nhl | `player_shots_on_goal` | **NO** | **goalie starters only — no skater data at all** |
| nba | `player_rebounds` | **NO** | none (vertical not built) |

`run_live_slate.py` contained **zero** references to `shots_on_goal`. NHL prop
lines were bought on the default schedule (`--leagues 'nfl ncaaf mlb nhl'`)
with no slate to price them and no bank to price them from:
`datasets/nhl/starters.parquet` is goalies (saves, shots_against), not skaters.
NBA was honest by comparison — the vertical is openly unbuilt — but it sat in
`LEAGUE_PROP_MARKETS` all the same.

### Cut, 2026-09-16

NCAAF took the other option (finding 6, done). These two had no such option —
there is nothing to price them *from* — so they stopped being bought. Both
halves, because either alone leaves the spend in place: `nhl` is out of
`LEAGUE_PROP_MARKETS`, and out of the collector workflow's `--leagues`
default, which is the half that actually spends.

**And the fallback had to go with them.** `LEAGUE_PROP_MARKETS.get(league,
DEFAULT_EVENT_MARKETS)` meant a league dropped from the config but left in
`--leagues` would have pulled the **six-market football board** against its
events — strictly more expensive than the one market being cut, and invisible
in a log that only says the league was snapshotted. An unconfigured league now
buys nothing and says why.

**The way back in is kept.** Cutting a purchase is not deleting a capability:
the normalizer mapping (`theoddsapi.PROP_MARKET_BY_KEY`) and the display
labels (`report/social.py`) stay, because they cost nothing and are what a
future vertical re-enters through. SOG is in every banked NHL boxscore
(docs/BUILD_NHL.md), so the skater bank is a build away; restoring the league
means adding it back to `LEAGUE_PROP_MARKETS` **and** to the workflow's
`--leagues`, in that order.

**NCAAF and NHL differ in what they need.** NCAAF needs only wiring plus a
projection: `datasets/ncaaf/player_games.parquet` is 90,819 rows over
2023-2026 (after findings 9 and 10) and carries `attempts`, `carries`, `interceptions`, `pass_tds`,
`pass_yards`, `receiving_tds`, `receiving_yards`, `receptions`, `rush_tds`,
`rush_yards`, `targets` — which covers **10 of the 11 football prop markets**
(everything but `pass_completions`, the same column gap the NFL bank had until
2026-09-16). `velocity/models/dfs_ncaaf.py` already solved the projection half
of this and says why it worked: *"The missing half was data, not modelling"* —
it runs the NFL rate model on the college bank with a bounded recency window,
because a college roster turns over every August. The prop sim is
league-agnostic; it takes a long `(player, stat, value)` frame.

NHL needs a skater bank first. `docs/BUILD_NHL.md` notes shots on goal are "in
every boxscore" — they are simply not collected.

## 8. `load_rosters()` is dead code (DONE)

`velocity/ingest/nfl.py` defined a network fetch for nflverse weekly rosters
that nothing in the repo called. Not a data gap — nothing needs it — but a
maintained network path with no consumer reads like a capability the system
has.

Deleted, and it was a whole chain rather than one function: `load_rosters`,
`normalize_rosters`, `NFLVERSE_ROSTER_URL`, the `raw_nfl_rosters.csv` fixture,
three tests, **and the `Players` schema itself** — every real reference to
`Players` outside `schema.py` was in that chain, no `players` table is written
anywhere, and a schema nothing validates against is the same kind of thing as
a fetch nobody calls. Deleting only the function would have left a tested
normalizer nobody could reach, which reads *more* maintained, not less.

Positions and availability come from FantasyPros, the DK salary file and
ESPN's depth chart, which is why this was never wired up.

## 9. NCAAF 2026 has no passing or receiving touchdowns (DONE)

Found while checking whether finding 6's substitute is usable. It was not.

`datasets/ncaaf/player_games.parquet` as banked, touchdowns by season:

| Season | Rows | Weeks | `pass_tds` | `receiving_tds` | `rush_tds` |
|---|---|---|---|---|---|
| 2023 | 29,806 | 15 | 4,509 | 4,497 | 4,422 |
| 2024 | 31,925 | 16 | 4,690 | 4,698 | 4,838 |
| 2025 | 34,575 | 16 | 3,225 | 3,221 | 3,158 |
| **2026** | **4,815** | **1** | **0** | **0** | 507 |

Passing and receiving touchdowns were empty for the current season while
rushing touchdowns populated — so not a missing `touchdown_player_id` column,
which would have zeroed all three.

### Root cause: upstream stopped naming one kind of scorer

Fetched the 2026 cfbfastR play frame (41,013 rows, weeks 1-2). The schema is
identical to 2025's, 70 columns, nothing renamed. The *contents* changed:

| Role | Plays | Carrying `touchdown_player_id` — 2026 | — 2025 |
|---|---|---|---|
| `completion_player_id` | 11,067 | **0** | 3,225 |
| `reception_player_id` | 10,952 | **0** | 3,221 |
| `rush_player_id` | 20,406 | 1,043 | 3,158 |

Of 1,074 plays the column marks in 2026, 1,043 are rushes and none are
completions or receptions. `touchdown_stat` carries the same 1,074 and no
more. So `velocity/ingest/cfb_players.py` was reading the column correctly and
the column had stopped answering — an upstream data-shape change, not a
normalizer bug.

### Fix: read the touchdown off the field, not off the play text

`yards_to_goal` is the distance to the goal line at the snap, so a gain that
covers it ended in the end zone. That is a fact about football rather than
about ESPN's play text, and both columns are fully populated in every season
(zero nulls). The fold now takes a scoring play as *either* signal, per role.

Validated three ways:

* **Against the column, on 2025, where it still works.** The geometry agrees
  with 99.4% of its passing touchdowns and finds 15% more it missed. Those
  extras are spread evenly over down, distance and field position — they look
  like touchdowns the play text did not name, not like false positives.
* **Against the seasons the column covers well.** On 2023 and 2024 taking
  both together moves the touchdown count by under 2%.
* **Against the rate.** On 2026 it takes the count from 1,074 to 2,149 — 6.4
  a game, which is what 2023 scores at (6.4) and 2024 (6.3).

Two-point conversions are the one false positive the rule would admit, and the
release does not carry them: no spike at the three-yard line, and no extra
points anywhere in the frame.

### The alarm that should have caught this

`season_coverage` exists for exactly this failure — it asks what share of
team-games the release's own scoring explains. It counted touchdowns off
`touchdown_player_id` alone, so it measured the release rather than the bank,
and it read 0.21 on 2026: **it did fire.** `bank_player_games` printed
`TOO THIN to price a lineup` and banked the season anyway, because the check
only ever printed. It now counts off the same mask the fold banks, and it
gates.

## 10. The NCAAF player bank is stuck at week 1 (DONE)

Two causes, one of them worse than a stale bank.

### The refresh never ran

The top-up lived in `refresh_datasets.refresh_inseason` under
`if league == "ncaaf":`. That function is only ever called with `"mlb"` and
`"wnba"` — NCAAF goes through `refresh_ncaaf` — so the branch had never
executed once. The bank sat at whatever the last hand-run left in it while the
board went on pricing from it. Moved into `refresh_ncaaf` and placed *ahead*
of the `CFBD_API_KEY` check, because cfbfastR is keyless and has no business
being held hostage by a secret it does not use.

(Week 1 itself was complete — 203 games banked against 203 upstream. Only
week 2 was missing, and only because nothing re-read the release.)

### The gate was a season when the break is a week

Checking coverage week by week rather than season by season showed 2025 does
not degrade — it stops:

| 2025 week | 1-8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16 |
|---|---|---|---|---|---|---|---|---|---|
| coverage | 0.62-0.77 | 0.48 | 0.16 | 0.14 | 0.15 | 0.14 | 0.15 | 0.17 | 0.00 |

As one number that season reads 0.49, and **either verdict on it is wrong**:
bank it whole and 679 games price as though nobody scored in them, refuse it
whole and eight good weeks go in the bin. So the gate is per week now, and
only the cliff is cut.

### What the bank holds after both fixes

| Season | Games | `pass_tds` | `rush_tds` | `receiving_tds` | Weeks dropped |
|---|---|---|---|---|---|
| 2023 | 1,473 | 4,563 | 4,545 | 4,550 | — |
| 2024 | 1,596 | 4,742 | 4,947 | 4,753 | — |
| 2025 | 974 | 2,806 | 3,015 | 2,803 | 9-16 |
| 2026 | 334 | 1,037 | 1,081 | 1,018 | — |

90,819 player-games, down from 101,121 — the 10,302 it lost are the 2025
games the release never attributed, which were pricing college players at
nothing. Per game the four seasons now agree: 2.9-3.1 passing touchdowns,
3.1-3.2 rushing, 2.9-3.1 receiving. Before, 2025 read 1.95/1.91/1.95 and 2026
read 0.00/3.15/0.00.

## Consequence for finding 6

Finding 6 said the NCAAF prop substitute "already exists and is already
proven", pointing at this bank and at `dfs_ncaaf.py`. That was true of the
*mechanism* and overstated about the *data*.

With 9 and 10 closed it is true of both: the bank holds two attributed weeks
of 2026 plus eight of 2025 behind them, and all three touchdown columns
populate. That unblocked finding 6, which is now **done** — see its section
above.

One correction the work earned, though. "Already proven" was still not quite
right even about the mechanism: the *dispersion* is modelling, not data, and
the NFL's would have been wrong here by about a factor of two on team volume.
The projection was a wiring job; the sim's width was not.

## 7. `bank_starters` cannot recreate a deleted batter bank (DONE)

`refresh_datasets.py` topped the batter bank up with
`batters_out=batters if batters.exists() else None`. The bank is current today,
and the comment beside it records that leaving it out of this call once froze
it "at the last manual backfill while the lineups those models priced moved on
without it" — so the guard was there for a good reason.

But the `exists()` condition meant a deleted or never-created bank was never
rebuilt: the refresh ran clean, wrote nothing, and the home-run and DFS models
went on fitting whatever was left.

**`bank_starters` had handled this correctly all along** — a game counts as
banked only when *both* banks hold it, so an absent batter bank re-walks
history once and then stays incremental, which is exactly how the bank was
first added. The only thing preventing recovery was the caller.

Now `refresh_mlb_player_banks` passes it unconditionally. The outer
`starters.exists()` guard stays, because never having been backfilled is a
different thing from a bank going missing and only the second is a recovery
worth attempting. And the rebuild announces itself: that first run re-walks
every banked game and takes a while, so it says so rather than looking hung.

---

## Deliberately not used

- **BP `park_factors`** — we fit our own from banked games, shrunk by sample
  (`props_hr.py`). A served, unshrunk number is not an improvement.
- **BP prop `performance` splits** (`last_1`/`last_5`/`last_10`/`last_15`/
  `last_20`/`season`/`prior_season`/`h2h`) — a provider's copy of windows we
  can compute ourselves from `datasets/nfl/player_weeks.parquet` (113,581
  player-weeks, every market they cover). See finding 3.
- **BP `extra.opposition_rank`** — a served ordinal where `PropMatchupSignal`
  already z-scores the opponent's fitted defensive EPA. Same reasoning as
  `park_factors`.
- **FantasyPros `def_ff` / `def_tyda`** — DK scores neither.
- **FantasyPros `points` / `points_half` / `points_ppr`** — we compute DK
  points from the components rather than trust a served total.
- **FantasyPros milestone keys** (`pass_yds_300`, `rush_yds_100`, …) — served
  as structural zeros on every row. Placeholders, not projections.

## Audit coverage

| Source | Audited | Result |
|---|---|---|
| FantasyPros projections | 2026-09-16 | Complete — nothing unread (#209, #212) |
| The Odds API | 2026-09-16 | Complete — every mapped key priced |
| nflverse weekly player stats | 2026-09-16 | Complete — attempts, completions, kicking banked |
| BettingPros `/events` | 2026-09-16 | **3 findings above** |
| BettingPros `/props` | 2026-09-16 | Finding 3 — re-examined and **declined**; the discarded fields are a copy of what we own plus a served ordinal |
| nflverse play-by-play | 2026-09-16 | Clean — the 12-column narrowing is deliberate and documented |
| NFL weather / roof handling | 2026-09-16 | Clean — dome-aware live forecast, well built |
| Baseball Savant (Statcast) | 2026-09-16 | **Finding 4 — collected, not passed** |
| ESPN injuries + depth | 2026-09-16 | Clean — collected and passed for every league it covers |
| Banked datasets vs. consumers | 2026-09-16 | **Clean — every banked column is read.** See the note below |
| Optional data flags vs. workflows | 2026-09-16 | **Finding 4** (Statcast); finding 7 (minor) |
| NCAAF prop path | 2026-09-16 | **Finding 6 — bought, never priced** |
| MLB statsapi (`HITTING_KEYS`/`PITCHING_KEYS`) | 2026-09-16 | Clean — `HITTING_KEYS` maps DK's hitter scoring exactly. One micro-gap: pitcher `hitByPitch` (DK −0.6) is not collected, worth ~0.2 DK points a start. Not worth acting on. |
| `cfb_players` / NCAAF player bank | 2026-09-16 | **Findings 9 and 10 — both fixed.** Upstream stopped attributing passing and receiving touchdowns in 2026; the weekly top-up had never once run; the coverage alarm fired and was ignored |
| Prop lines bought vs. priceable | 2026-09-16 | **Clean.** NCAAF was wired (finding 6); NHL and NBA were cut (6b). Every league bought is now a league something prices |
| Kalshi / Polymarket / exchanges | 2026-09-16 | Clean — collected hourly, `--exchanges` passed by the live slate (default true), graded via `--exchanges-dir` |
| nflverse rosters / schedules | 2026-09-16 | Schedules clean. The roster chain was **dead code** — fetch, normalizer, fixture, tests and the `Players` schema, none of it reachable. Deleted (finding 8) |
| NCAAB / WNBA / NHL game markets | 2026-09-16 | Clean — priced by `ScoresGameModel` + `fit_scores_ratings`; NCAAB adds Torvik, NHL adds starting goalies. No prop lines bought for NCAAB/WNBA, which is consistent. |

**The audit is complete.** Every ingest module, every banked dataset, every
optional data flag and every prop-line purchase has been checked.

### The structural finding

Every column in every banked parquet is referenced by something — 26 datasets,
no exceptions. **The banks are tight; the losses happen at the ingest boundary**,
where a normalizer names the fields it keeps and the rest of the payload is
dropped unrecorded. That is where the remaining audit should look, and it is
why the allow-lists (`_PROP_COLUMNS`, `_EVENT_COLUMNS`, `_PBP_COLUMNS`,
`STAT_COLUMNS`, …) are the map.

A corollary worth keeping: a repo-wide grep is NOT enough to check a flag is
passed. `--statcast` appears in the workflows directory, so a naive search says
"passed" — it is passed by `dfs-slate.yml` and omitted by `live-slate.yml`.
Finding 4 hid behind exactly that.


---

# Working order

The findings split into three kinds, and the numbering is not the order to do
them in.

## Costing something right now

| # | What | Fix size |
|---|---|---|
| ~~**6b**~~ | ~~NHL and NBA prop lines bought every run~~ — **cut** 2026-09-16. Nothing could price them and nothing existed to price them from |

~~Finding 4 is the one to do first~~ — **done**. The remaining two are the
live-output ones: a benched hitter still prices as a starter, and three
leagues' prop lines are still bought with nothing to price them.

## Unblocking something

| # | What | Note |
|---|---|---|
| **2 / 5** | Bank the BettingPros weather forecast | The HR model says it cannot model weather for lack of banked data. Banking starts the clock; the coefficient comes when there are enough games to fit rather than assume. NFL already has the careful version to copy. |
| ~~**6**~~ | ~~Point the NCAAF prop slate at `player_games`~~ | **Done.** 10 of 11 markets priced, dispersion re-fitted on college. `dfs_ncaaf.py` was the template and its verdict held — *"The missing half was data, not modelling"* — with one correction: the *dispersion* was modelling, and the NFL's would have been wrong. |

## Small or cosmetic

| # | What |
|---|---|
| ~~**3**~~ | ~~BP per-prop form splits and `opposition_rank`~~ — **declined.** The premise was wrong: `FormSignal` is a team signal that never sees a prop, and we can compute every form window ourselves |
| ~~**7**~~ | ~~Batter bank cannot be recreated if deleted (`exists()` guard)~~ — **done** |
| ~~**8**~~ | ~~`load_rosters()` dead code~~ — **done**, and the whole unreachable chain with it |
| — | Pitcher `hitByPitch` uncollected (~0.2 DK points a start) — recorded, not worth acting on |

## What the audit says about the system

Two results are worth more than any single finding.

**The banks are tight.** Every column of every banked parquet is read by
something — 26 datasets, no exceptions. Nothing is rotting in storage.

**Every loss is at the ingest boundary or the call site.** A normalizer names
the fields it keeps and the rest of the payload goes unrecorded; or a script
takes an optional data argument and a workflow forgets to pass it. Both
failures are silent by construction — no error, no warning, no failing test.
That is the same reason the FantasyPros census was needed at all, and it is why
a *standing* report beats a one-off sweep: **abstention looks exactly like
health.**
