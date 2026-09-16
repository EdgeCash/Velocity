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

## 1. BettingPros `/events` — today's lineup (OPEN, correctness)

`scripts/collect_bettingpros.py` keeps five fields of the twenty-three the MLB
payload carries:

```python
_EVENT_COLUMNS = ("id", "home", "visitor", "scheduled", "participants")
```

Discarded: `lineups` (`home_lineup`, `visitor_lineup`, `home_lineup_type`,
`visitor_lineup_type`), `pitchers`, `park_factors`, `notes`, `weather`,
`venue`, and ten more.

**This is a correctness bug, not an enhancement.** `scripts/build_hr_board.py`
derives a batter's lineup slot from his most recent *prior* game:

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

## 4. Statcast is collected every live run and never handed to the model (OPEN, one line)

**The highest-value finding so far, and the smallest fix.** The live workflow
fetches the Savant snapshot and then calls the board without it:

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
| MLB statsapi (`HITTING_KEYS`/`PITCHING_KEYS`) | — | not yet audited |
| CFBD / `cfb_players` (`STAT_COLUMNS`) | — | not yet audited |
| Kalshi / Polymarket | — | not yet audited |
| nflverse rosters / schedules | — | not yet audited |
| NHL / NCAAB / WNBA | — | not yet audited |

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
