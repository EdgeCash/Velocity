# Game day — getting a good board before kickoff

The pipeline can finish successfully and still hand you half a board. On
2026-09-20 it did: a green job, a complete betting card, and **no props, no
DFS pool and no weather** — with nothing anywhere saying so. This page is
what to do about that.

---

## 1. Why a green run is not the same as a good board

Three failures are possible, and only the first one used to be visible.

| Failure | What it looks like | Visible before? |
|---|---|---|
| The run didn't happen | no new artifact | yes |
| The run happened and **a surface is missing** | green job, blank column | **no** |
| The run happened but the numbers are **hours old** | green job, plausible numbers | **no** |

The second and third are now stated in three places:

* **`readiness.csv`** — one row per surface: present, row count, why not.
* **The workbook's first tab** — `Run status` leads the Dashboard, and the
  verdict is on Read Me, colour-coded.
* **The job itself** — a `Board readiness gate` step that **fails the run**
  when a surface the card actually needs is missing.

### The three verdicts

| | Meaning | Job | What to do |
|---|---|---|---|
| **READY** | every surface present, numbers fresh | green | use it |
| **DEGRADED** | usable card, something optional missing (no props tonight, no DFS pool) | green | use the parts that are there; §3 if you want the rest |
| **NOT READY** | a required surface missing, **or** numbers past their age bar with too little time to redo them | **red** | §2, now |

A failing job is the point: GitHub emails the repository owner on a failed
Actions run by default, which is an alert that reaches a phone without any
new infrastructure.

---

## 2. The pre-kickoff routine

**About three hours before the first kickoff you care about**, on the iPad:

1. **Actions → Live slate → Run workflow.** A dispatched run starts within a
   minute; a *scheduled* one starts **1h48–3h00** late
   ([`docs/LATENCY_AUDIT.md`](LATENCY_AUDIT.md)). This is the single most
   effective thing you can do, and it is two taps.
2. Wait ~10–15 minutes (run #132 took 11).
3. Open the run. If the **Board readiness gate** step is green, the board is
   at least usable. If it is red, the step output names the missing surface.
4. Download the artifact, open `velocity.xlsx`, read **Run status** at the top
   of the Dashboard.

Three hours is not superstition: it clears the run itself (~15 min), leaves
room for one retry, and still lands inside the window where prices are worth
acting on.

### If you only do one thing

Dispatch **Live slate** by hand on game day. Every measured failure so far
traces back to a scheduled run starting late or being dropped.

---

## 3. Fixing a DEGRADED board

Work out *which* surface, then:

### Props missing — "board N min old (bar 75)"

The prop collector's snapshot was too stale to price. This is the scheduling
delay: on 2026-09-20 the `15:19` cron started at **18:12** (+2h53m), so by
22:51 the newest board was 279 minutes old against a 75-minute bar. The
runner was right to refuse it — a four-hour-old prop line is not a price.

**Do:** Actions → **Collect player-prop lines** → Run workflow (starts in a
minute), wait for it, then dispatch **Live slate** again.

> The 75-minute bar is not the problem and should not be raised. What is
> stale is stale.

### DFS pool missing

Usually one of two things, and the log says which:

* `no solvable lineup on any slate grouping (N priced pool(s) still banked)` —
  the optimizer could not fill a roster from the groups on the board, often
  because the main slate has locked and what remains is a Showdown or a
  multi-week format. **The pool still exports**, so the DFS Pool tab fills
  even though no lineup solved.
* `empty salaries or projections; no lineup to build` — the DK salary
  snapshot or the FantasyPros frame is missing for that league.
  **Do:** dispatch **Collect DraftKings salaries**, then Live slate.

### Weather missing

`weather forecast: no stadium-days returned` means all 21 outdoor-stadium
requests to Open-Meteo failed. This only affects the wind/precipitation
adjustment on NFL totals and the workbook's Weather column; nothing else
depends on it. NCAAF has no weather model at all, so blank there is correct.

### Numbers stale, kickoff close

`NOT READY · numbers N min old`. There is no time for a scheduled rerun
(1h48–3h00 late plus a 20–30 minute run). Dispatch **Live slate** by hand and
accept that it lands when it lands, or bet the previous board knowing its
age.

---

## 4. What would make this better, and what it costs

Not done, because each is a spending or policy decision rather than a bug:

| Change | Effect | Cost |
|---|---|---|
| A third daily prop collection, timed ~3h before the main slate window | props would usually be inside the 75-minute bar | ~380 Odds API credits a run, ~11k/month on top of the current ~38k of 100k |
| `--no-team-totals` on the live slate | 210 → ~15 credits a run, freeing budget for the above | removes a priced market the slate currently stakes at zero ([`docs/LATENCY_AUDIT.md`](LATENCY_AUDIT.md) §4) |
| Move the slate windows earlier | more margin before kickoff | more runs, more Actions minutes |
| A self-hosted runner | no scheduled-queue delay at all | a machine that stays up |

The first two pair naturally: the team-total saving more than pays for the
extra prop collection. Both are yours to decide.

---

## 5. Checking without re-running anything

To ask "is the board I already have any good?" without spending a run:

**Actions → Refresh Excel exports → Run workflow.** It rebuilds the CSVs and
the workbook from the newest banked artifacts in about two minutes, runs the
same readiness gate, and re-reports. It does not re-simulate, so it cannot
make a stale board fresh — it tells you whether the one you have is worth
acting on.
