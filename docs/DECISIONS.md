# Owner decisions

Standing decisions made by the repository owner, with what was implemented
for each. These are **settled** — not to be relitigated by a future change
without the owner reopening them. Where a decision says "revisit after data",
the instrument that gathers that data is named.

Recorded 2026-09-20.

---

## D1 — Props are a core market

**Decision.** The owner actively bets props and relies on the prop model.
Add collection capacity, keep manual dispatch, optimise so props are
available whenever realistically possible, **do not relax freshness
requirements to make props appear**, and prefer fresh prop data over API
conservation.

**Implemented.** `collect-football-props.yml` now fires on the **same
windows as `live-slate.yml`, 34 minutes ahead of each** (`:19` against the
slate's `:53`), instead of a fixed twice-daily pair.

The reasoning is worth keeping. Both workflows queue on the same platform
under the same conditions, so a prop cron 34 minutes before a slate cron
tends to land 34 minutes before that slate *runs* — inside the 75-minute
bar — rather than at a wall-clock time the slate has long since drifted away
from. Riding the same delay is the only lever available when the delay
itself cannot be controlled.

Measured cause it fixes: on 2026-09-20 the `15:19` cron started at **18:12**,
so by the 22:51 slate the newest board was **279 minutes old** against a
75-minute bar, and the entire props surface was missing.

Cost: 17 runs a week against 14 — about **28k** Odds API credits a month
where the pair spent 23k, against a 100k allowance.

**Explicitly refused:** raising the 75-minute freshness bar. A four-hour-old
prop line is not a price.

---

## D2 — Team totals stay

**Decision.** Keep team totals. Do **not** enable `--no-team-totals`. The
owner bets team totals, game totals and player props. Continue collecting,
exporting, simulating and pricing them, and preserve the ability to promote
them into A+/A/B tiers later.

**Implemented.**

* `--no-team-totals` is **not** set anywhere. The ~195 credits a run that
  team-total pulls cost are accepted as the price of a market that is
  actually bet.
* `distributions_*.parquet` now persists `home_score` and `away_score` pmfs
  alongside `total` and `margin`, so a team total can be priced off the
  simulation's own samples rather than a normal standing in for it.
* **`team_totals.csv`** and a **Team Totals** workbook tab: one row per
  side, with the market's number, the model's (the *median* — the 50/50 over
  point the slate prices against, not the mean), the edge and the over
  probability. Previously team totals reached only `plays.csv`, and only
  when a play was staked — the market appeared exactly when it had already
  been bet and nowhere when it had not.
* Promotion into A+/A/B is unaffected: `velocity/wagering/plays.py` tiers
  whatever the slate stakes, team totals included, and
  `velocity/wagering/tiers.py` will accept a team-total rule the moment the
  wager lab promotes one.

**Revisit only if** the lab measures team totals unprofitable.

---

## D3 — Email delivery

**Decision.** Assume email delivery is wanted. Run finishes → workbook
generated → workbook arrives on the iPad, with no additional manual work.

**Implemented.** `live-slate.yml` attaches `artifacts/exports/*.xlsx` to the
slate email, which sends when `MAIL_USERNAME`, `MAIL_PASSWORD` and `MAIL_TO`
are set (setup in [`EXCEL_IPAD.md`](EXCEL_IPAD.md) §3).

**One bug this decision surfaced and fixed.** The readiness gate was placed
*before* the email steps. A failing step stops the ones after it, so a NOT
READY board would have failed silently *into the inbox* by never arriving —
the exact opposite of what a gate is for. The gate is now the **last** step
in the job: artifact, email and site all deliver first, and the gate only
reports. A test pins its position.

---

## D4 — Stay on GitHub-hosted runners, and measure

**Decision.** No migration to Azure, a VPS, a self-hosted runner or
dedicated infrastructure yet. Gather data, measure actual scheduling
latency, document missed and delayed runs. After several NFL and NCAAF
weekends, produce a report covering delay frequency, average delay, impacted
slates and estimated missed opportunities. Current preference: **manual
dispatch + GitHub** until data proves otherwise.

**Implemented.** `scripts/report_schedule_latency.py` is the instrument.
It reads the Actions API, pairs each scheduled run with the cron slot it was
meant to fill, and reports:

* delay median, mean, min/max and p90, per workflow and overall
* **slots dropped** — how many crons never fired at all, which is the number
  that actually costs a slate and is invisible in any single run's log
* the worst run, linked, and any non-success conclusions

```bash
python scripts/report_schedule_latency.py --days 28
python scripts/report_schedule_latency.py --days 28 --csv latency.csv
```

Read-only, needs `actions:read`. Nothing is scheduled to run it — it is
pulled when the report is wanted, so there is no new writer and no data file
to maintain.

---

## D5 — Readiness and confidence thresholds are frozen

**Decision.** Leave unchanged until several weeks of real operation and
outcome data. Specifically preserved:

| | |
|---|---|
| Readiness stale threshold | **240 minutes** |
| Readiness kickoff threshold | **90 minutes** |
| Confidence — edge weight | **0.40** |
| Confidence — rule record weight | **0.35** |
| Confidence — intel weight | **0.25** |

**Implemented.** Unchanged, and each lives in one frozen place
(`readiness.DEFAULT_MAX_AGE_MIN` / `DEFAULT_KICKOFF_WARN_MIN`,
`plays.PlaysConfig`) with tests pinning the behaviour they produce. A test
asserts the values themselves, so a casual edit fails the build rather than
quietly changing what gets published as A+.

---

## D6 — No synthetic DFS ownership

**Decision.** Leave ownership and leverage blank. Do **not** generate
synthetic ownership. Do **not** estimate it without a credible, testable
source. Blank is preferred over fabricated data.

**Implemented.** `dfs.csv` and `dfs_optimizer.csv` export `ownership` and
`leverage_score` empty unless a projection is supplied to
`export_dfs(..., ownership=...)`. No estimator exists anywhere in the export
layer, and a test asserts that the default path produces nothing rather than
a number.

Future ownership modelling may be added behind that same parameter.
