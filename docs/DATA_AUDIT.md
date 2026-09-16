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

## 3. BettingPros `/props` — form splits and opposition rank (OPEN, additive)

`_PROP_COLUMNS` keeps 22 fields — the lines and BP's projection block — and
discards `performance` (`last_1`, `last_5`, `last_10`, `last_15`, `last_20`,
`season`, `prior_season`, `h2h`) and `extra.opposition_rank`.

We have a `FormSignal` and a `PropMatchupSignal`. Those are precisely their
inputs, arriving pre-computed on the row we already bank.

Lower priority than 1 and 2: the signals abstain safely without it, so this is
additive rather than a correction.

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

## 6. NCAAF prop lines are bought every run and never priced (OPEN, costs credits)

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
The prop slate never got the same treatment. Two honest options, and they want
deciding rather than drifting: point the NCAAF prop slate at the banked player
games the way DFS does, or stop buying NCAAF prop markets. Doing neither is the
only choice that costs money for nothing.

## 6b. The same shape, one league over: NHL (OPEN, costs credits)

Finding 6 is not a one-off. Mapping every league we BUY prop lines for against
every league a slate can PRICE:

| League | Lines bought | Priced by a slate? | Bank to price from |
|---|---|---|---|
| nfl | six-market board | `_prop_slate` (FantasyPros) | player_weeks + FP |
| ncaaf | six-market board | **NO** (FP has no college) | `player_games` — banked, unused for props |
| mlb | `pitcher_strikeouts`, `batter_home_runs` | `_mlb_k_slate` (banked starters) | starters + batters |
| nhl | `player_shots_on_goal` | **NO** | **goalie starters only — no skater data at all** |
| nba | `player_rebounds` | **NO** | none (vertical not built) |

`run_live_slate.py` contains **zero** references to `shots_on_goal`. NHL prop
lines are bought on the default schedule (`--leagues 'nfl ncaaf mlb nhl'`) and
there is no slate to price them and no bank to price them from:
`datasets/nhl/starters.parquet` is goalies (saves, shots_against), not skaters.
`shots_on_goal` sits in `PROP_MARKETS` and nothing produces it.

NBA is honest by comparison — the vertical is openly unbuilt — but it is in
`LEAGUE_PROP_MARKETS` all the same.

**NCAAF and NHL differ in what they need.** NCAAF needs only wiring plus a
projection: `datasets/ncaaf/player_games.parquet` is 101,121 rows over
2023-2026 and carries `attempts`, `carries`, `interceptions`, `pass_tds`,
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

## 8. `load_rosters()` is dead code (OPEN, trivial)

`velocity/ingest/nfl.py:248` defines a network fetch for nflverse rosters.
Nothing in the repo calls it. Not a data gap — nothing needs it — but it is a
maintained network path with no consumer, and it reads like a capability the
system has. Delete it, or wire it to the thing it was written for.

## 9. NCAAF 2026 has no passing or receiving touchdowns (OPEN, live defect)

Found while checking whether finding 6's substitute is usable. It is not, yet.

`datasets/ncaaf/player_games.parquet`, touchdowns by season:

| Season | Rows | Weeks | `pass_tds` > 0 | `receiving_tds` > 0 | `rush_tds` > 0 |
|---|---|---|---|---|---|
| 2023 | 29,806 | 15 | 2,761 | 3,862 | 3,439 |
| 2024 | 31,925 | 16 | 2,974 | 3,949 | 3,755 |
| 2025 | 34,575 | 16 | 2,148 | 2,687 | 2,519 |
| **2026** | **4,815** | **1** | **0** | **0** | 507 |

Passing and receiving touchdowns are empty for the current season while rushing
touchdowns populate — so it is not a missing `touchdown_player_id` column,
which would zero all three. Attempts (620) and receptions (2,802) populate too,
so the passer and receiver roles are being matched; only their scoring plays
are not.

This is live: `velocity/models/dfs_ncaaf.py` prices the college DFS board from
this bank, and a passing touchdown is 4 DK points with a receiving touchdown at
6. Every college quarterback and receiver is currently projected without them.

Root cause needs the 2026 cfbfastR play frame, which is a network fetch — the
banked `plays.parquet` carries eleven columns and no player fields, so it
cannot answer this offline.

## 10. The NCAAF player bank is stuck at week 1 (OPEN)

Same table: 2026 holds **one week** on 2026-09-16, when the college season is
several weeks old. Prior seasons hold 15-16.

Not yet established whether cfbfastR has not published the later weeks or our
refresh is not reading them — worth one check before it is called a bug. But
either way the practical effect today is that any model reading this bank for
the current season is reading one week of it.

## Consequence for finding 6

Finding 6 says the NCAAF prop substitute "already exists and is already
proven", pointing at this bank and at `dfs_ncaaf.py`. That is true of the
*mechanism* and overstated about the *data*: a 6-game recency window has
nothing to work with when the bank holds one week, and two of the eleven prop
markets cannot be priced at all while the touchdown columns are empty.

So finding 6 is **blocked on 9 and 10**, not merely unstarted. Stopping buying
NCAAF prop lines remains available and needs nothing.

## 7. `bank_starters` cannot recreate a deleted batter bank (OPEN, minor)

`refresh_datasets.py` tops the batter bank up with
`batters_out=batters if batters.exists() else None`. The bank is current today,
and the comment beside it records that leaving it out of this call once froze
it "at the last manual backfill while the lineups those models priced moved on
without it" — so the guard is there for a good reason.

But the `exists()` condition means a deleted or never-created bank is never
rebuilt: the refresh would run clean, write nothing, and the home-run and DFS
models would fit on whatever was left. Low priority — it is a recovery path,
not a live defect — but the failure mode is silent, which is this list's theme.

---

## Deliberately not used

- **BP `park_factors`** — we fit our own from banked games, shrunk by sample
  (`props_hr.py`). A served, unshrunk number is not an improvement.
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
| BettingPros `/props` | 2026-09-16 | **1 finding above** |
| nflverse play-by-play | 2026-09-16 | Clean — the 12-column narrowing is deliberate and documented |
| NFL weather / roof handling | 2026-09-16 | Clean — dome-aware live forecast, well built |
| Baseball Savant (Statcast) | 2026-09-16 | **Finding 4 — collected, not passed** |
| ESPN injuries + depth | 2026-09-16 | Clean — collected and passed for every league it covers |
| Banked datasets vs. consumers | 2026-09-16 | **Clean — every banked column is read.** See the note below |
| Optional data flags vs. workflows | 2026-09-16 | **Finding 4** (Statcast); finding 7 (minor) |
| NCAAF prop path | 2026-09-16 | **Finding 6 — bought, never priced** |
| MLB statsapi (`HITTING_KEYS`/`PITCHING_KEYS`) | 2026-09-16 | Clean — `HITTING_KEYS` maps DK's hitter scoring exactly. One micro-gap: pitcher `hitByPitch` (DK −0.6) is not collected, worth ~0.2 DK points a start. Not worth acting on. |
| `cfb_players` / NCAAF player bank | 2026-09-16 | Clean as a bank — and it is the substitute finding 6 needs |
| Prop lines bought vs. priceable | 2026-09-16 | **Finding 6b — NHL and NBA join NCAAF** |
| Kalshi / Polymarket / exchanges | 2026-09-16 | Clean — collected hourly, `--exchanges` passed by the live slate (default true), graded via `--exchanges-dir` |
| nflverse rosters / schedules | 2026-09-16 | Schedules clean. `load_rosters()` is **dead code** — defined, called by nothing (finding 8) |
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
| **6 / 6b** | NCAAF, NHL and NBA prop lines bought every run, no slate can price them | decision first, then either wiring or a `LEAGUE_PROP_MARKETS` cut — but see 9 and 10: the NCAAF half is blocked |
| **9** | NCAAF 2026 has no passing or receiving touchdowns, and the college DFS board prices from that bank | root-cause first (needs a network fetch) |

~~Finding 4 is the one to do first~~ — **done**. The remaining two are the
live-output ones: a benched hitter still prices as a starter, and three
leagues' prop lines are still bought with nothing to price them.

## Unblocking something

| # | What | Note |
|---|---|---|
| **2 / 5** | Bank the BettingPros weather forecast | The HR model says it cannot model weather for lack of banked data. Banking starts the clock; the coefficient comes when there are enough games to fit rather than assume. NFL already has the careful version to copy. |
| **6** | Point the NCAAF prop slate at `player_games` | 10 of 11 markets available; `dfs_ncaaf.py` is the template and its verdict applies — *"The missing half was data, not modelling."* |

## Small or cosmetic

| # | What |
|---|---|
| **3** | BP per-prop form splits and `opposition_rank` — `FormSignal` and `PropMatchupSignal` want exactly these |
| **7** | Batter bank cannot be recreated if deleted (`exists()` guard) |
| **8** | `load_rosters()` dead code |
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
