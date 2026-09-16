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
| nflverse rosters / injuries / schedules | — | not yet audited |
| CFBD (NCAAF) | — | not yet audited |
| ESPN | — | not yet audited |
| MLB statsapi / Savant | — | not yet audited |
| Kalshi / Polymarket | — | not yet audited |
| NHL / NCAAB / WNBA | — | not yet audited |
| Banked datasets vs. consumers | — | not yet audited |
