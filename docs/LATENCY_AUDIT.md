# Latency audit — where the 2–4 hours actually goes

The reported symptom: data is collected, and it reaches the operator two to
four hours later. This is the measurement behind that, the part of it this
repository can change, and the part it cannot.

The short version: **the delay is GitHub's scheduled-run queue, not our
pipeline.** The engine run is twenty to thirty minutes. Everything else is
waiting for a runner. So the fix is not a faster pipeline — it is a pipeline
whose slow part is not on the critical path for a refresh, plus a fast path
that does not use the `schedule` event at all.

---

## 1. Measured

From the Actions run list, 2026-09-16..19 (recorded in
[`docs/LAUNCH.md`](LAUNCH.md) "What the schedule really does"):

| Observation | Measurement |
|---|---|
| `live-slate.yml` start delay after its cron | **+1:48 to +3:00**, seven consecutive scheduled runs (+2:52, +2:01, +3:00, +1:56, +2:21, +1:48, +2:06) |
| `live-slate.yml` job duration | ~20–30 minutes |
| `collect-odds.yml` — hourly on paper | **~4.2 hours in practice**: 40 runs in 7 days, gaps of 1.7–7.5 hours. Most hourly slots are *dropped*, not delayed |
| `workflow_dispatch` start delay | **under a minute** |
| One run reclaimed mid-fit | 2026-09-19, exit 143, "the runner has received a shutdown signal" |

A cron written as `H:53` therefore publishes between `H+2:10` and `H+3:30`.
That is the reported 2–4 hours, almost exactly, and GitHub's status page
reported nothing during any of it — this is ordinary best-effort behaviour
for the `schedule` event on a shared queue.

**No cron minute, no caching change and no code optimisation in this
repository removes it.** A workflow advertising a 15-minute market refresh on
the `schedule` event would be advertising something the platform does not
deliver.

---

## 2. Where the run's own time goes

The part that *is* ours is the 20–30 minutes, and two changes have already
taken time out of it:

* **Leagues with no game in the window no longer fit their ratings.** NCAAB's
  fit alone was eleven of one run's thirty minutes, for a board that is empty
  from April to November.
* **Banked-board reuse.** `board_max_age_min` (75 minutes) lets a run price
  off the odds collector's snapshot instead of paying for a live pull. It
  rarely qualifies *because* the collector is effectively 4.2-hourly, which
  is the same platform problem wearing a different hat.

---

## 3. What changed for the Excel front end

### 3.1 The export runs inside the run that made the numbers

`live-slate.yml` now ends with:

```
python -m velocity.run_weekly --steps export --slate-dir artifacts/slate
```

The export step reads only frames the earlier steps banked. It re-simulates
nothing, so it costs seconds, and it ships in the same artifact as the slate.
Marginal latency added: none. Marginal latency removed: a whole scheduled
workflow's queue wait, which is the 1h48–3h00 above.

This is the single most useful change available, and it is available only
because the export layer is a read-only projection. An export that re-ran the
engine would have had to be its own workflow, and would have inherited the
delay it was built to avoid.

### 3.2 The fast path is a button, not a cron

`refresh-exports.yml` rebuilds the six CSVs from the newest banked slate
artifact. It has **no `schedule`**, deliberately:

* `workflow_dispatch` starts in under a minute — measured, above.
* A cron would start 1h48–3h00 late and hand back a board *older* than the
  one already sitting in the last slate run's artifact. A refresh that
  returns staler data than doing nothing is worse than no refresh.
* `workflow_run` on `DFS slate` covers the one case a machine can see coming:
  a DFS build finishing after the slate window it belongs to.

It installs runtime dependencies only (no `[ingest]` extras) and runs the
export alone, so it is minutes end to end.

### 3.3 The refresh targets, stated honestly

The brief asked for market data every 15 minutes in active betting windows,
weather hourly, DFS hourly, team metrics daily. Measured against the
platform and the API caps:

| Target | Deliverable on `schedule`? | What is actually in place |
|---|---|---|
| Market data, 15 min | **No.** A 15-minute cron fires with a 1h48–3h00 delay and most slots dropped; the effective rate would be worse than hourly, not better | `collect-odds.yml` hourly (≈4.2h effective). The operator's sub-minute path is `workflow_dispatch` on `collect-odds.yml`, then `refresh-exports.yml` |
| Weather, hourly | Not separately scheduled | Fetched inside the slate run, priced into the total, and persisted per game (`weather_*.parquet` → `games.csv`'s `weather` column). A separate hourly job would pay a queue wait to refresh a forecast the slate re-fetches anyway |
| DFS, hourly when contests are active | Partially | `dfs-slate.yml` runs its existing game-day windows; `refresh-exports.yml` now fires on its completion, so a DFS build reaches the CSVs without waiting for the next slate window |
| Team metrics, daily | **Yes** | `refresh-datasets.yml`, `29 9 * * *` |

### 3.4 API budget, unchanged

Nothing here increases provider spend. The export step makes no network
calls at all.

* **BettingPros — 5,000 calls/day.** Current use is the collector's ~45
  calls a run, eight runs a day: under 8% of the cap. `live-slate.yml` never
  calls BettingPros; it reads the banked parquet, and is not given the key.
* **The Odds API — 100k credits/month.** The schedule projects to ~38k.

---

## 4. Measured recommendation, not taken

A live-slate run that finds no fresh banked board pays **~210 credits**, and
**195 of those are the per-event team-total pulls for the two football
boards** — a market the slate currently stakes at **zero**. `--no-team-totals`
drops a live run to ~15 credits: a 93% reduction on the single largest line
item in the budget.

This is not applied here. Team totals being staked at zero today is a
consequence of the current promoted rules, not a decision to stop pricing
them, and switching the flag on by default would quietly remove a priced
market from the board — the one thing the migration brief says not to do
without proving a replacement. It belongs to whoever owns the wagering
policy, with the numbers above in front of them.

---

## 5. Summary

| | Before | After |
|---|---|---|
| Engine run → CSVs available | not available (parquet + styled workbook only) | same run, seconds later |
| Re-export without re-simulating | not possible | `--steps export`, seconds |
| On-demand refresh | none | `refresh-exports.yml`, starts in under a minute |
| Scheduled-run queue delay | 1h48–3h00 | unchanged, and now off the critical path for a refresh |

The queue delay is still there. It is a property of GitHub's `schedule`
event, and the honest response is to keep it off the path between "the
numbers exist" and "the operator can read them" — which is what these
changes do — rather than to write a cron that claims otherwise.
