# Launch runbook — going live at kickoff

Everything needed to run Velocity as a functional workflow when the season
starts. The system is built and tested; this is the operator's checklist for
turning it on, verifying it, and running it week to week.

## The pieces, and where they run

| Workflow | Schedule | Secret(s) | Output |
|---|---|---|---|
| `ci.yml` | push / PR | — | the test gate |
| `collect-bettingpros.yml` | every 3h | `BP_API_KEY`, `BP_USER_ID`, `BP_USER_KEY` | line snapshots → Actions artifact |
| `collect-odds.yml` | hourly | `THE_ODDS_API` | line snapshots + CLV archive → Actions artifact |
| `collect-fantasypros.yml` | weekly | `FP_API_KEY` | projections → Actions artifact |
| `live-slate.yml` | game days | `THE_ODDS_API` | **staked slate of recommended bets** → Actions artifact |
| `refresh-datasets.yml` | daily 09:29 UTC | `CFBD_API_KEY` (NCAAF) | current-season rows committed into `datasets/` — **self-verifying, see below** |
| `collect-football-props.yml` | daily 15:19/22:19 UTC | `THE_ODDS_API` | NFL/NCAAF prop snapshots → Actions artifact |
| `collect-dk-salaries.yml` | daily 15:31 UTC | — | DK salary snapshots → Actions artifact |

### The one job that writes to main (2026-09)

`refresh-datasets.yml` is the only workflow that commits and pushes. That makes
it the only one that can break `main`, and — because **a push made with
`GITHUB_TOKEN` deliberately does not trigger workflows** — the only one whose
breakage no CI run would catch.

It happened on 2026-09-16. `fd1c4f6` ("Data: refresh current-season dataset
rows") moved `datasets/` and left `velocity/eval/ladders.py` behind.
`OFFSET_BIAS` in that file is a **cache of those datasets**, and the commit
staged `git add datasets/` only. Main went red with no CI run at all and stayed
red until an unrelated PR — the card upgrade — was tested against the merged
result and failed on two NFL ladder assertions that had nothing to do with it.

The job now does three things before it writes anything:

1. **Asks whether the data actually moved.** Everything downstream is gated on
   it, so an unchanged refresh costs nothing and cannot make an empty commit.
2. **Regenerates what it invalidates** — `scripts/calibrate_ladders.py --write`,
   a no-op when the table is already current.
3. **Runs `ruff`, `mypy` and `pytest` and pushes nothing if they fail.** A
   breaking refresh now costs a failed job, which is visible, instead of a
   broken `main`, which was not.

The commit stages `datasets/` **and** `velocity/eval/ladders.py`, because
committing the data without the table it feeds is the exact shape of the bug.

`tests/test_refresh_workflow.py` pins all of it, including the *order* —
deleting the verify step would otherwise look like tidying.

**If you add another derived-from-`datasets/` artifact**, regenerate it in step
2. And keep generated blocks free of hand-written notes: the ones explaining
why the NFL sim uses 13.0 and the NCAAF sim 18.2 now live *above* `OFFSET_BIAS`
precisely because a regeneration deleted them once.

### The minute map

Every scheduled workflow gets its **own odd minute**, and none of them sit on
`:00` or `:30`. Two reasons, and both are real:

* **Queue time.** GitHub runs all of Actions' cron off one queue, and the
  default anyone writes first is `0 * * * *` — so the top of the hour is where
  the whole platform piles up, with `:30` second. This repo's 16:00 slate was
  routinely starting closer to 18:40, two and a half hours late, with nothing
  wrong on our side. Moving off the crowded minutes is the only lever we have
  over that, and it is free.
* **Ordering.** `live-slate.yml` consumes what the other workflows produce, so
  it runs **last** in its hour. It used to share `16:00` with `dfs-slate.yml`
  exactly, and its "Fetch the latest DFS entries" step downloads that
  workflow's newest *successful* run — so the site's DFS lineups were always
  one window stale, because the run it wanted was still in progress.

| Minute | Workflow | Hours (UTC) |
|---|---|---|
| `:07` | `collect-exchanges.yml` | hourly |
| `:11` | `dfs-slate.yml` | 16 |
| `:13` | `collect-bettingpros.yml` | every 3h |
| `:17` | `dfs-slate.yml` | 1 |
| `:19` | `collect-football-props.yml` | 15, 22 |
| `:23` | `collect-odds.yml` | hourly |
| `:29` | `refresh-datasets.yml` · `collect-kalshi-candles.yml` · `dfs-slate.yml` | 9 · 12 · 19, 23 |
| `:31` | `model-drift.yml` · `collect-dk-salaries.yml` · `dfs-slate.yml` | 6 (1st/15th) · 15 · 21 |
| `:33` | `collect-injuries.yml` | 16 (Sun) |
| `:37` | `collect-fantasypros.yml` · `collect-injuries.yml` | 12 (Tue/Thu/Sat/Sun) · 15 |
| `:39` | `dfs-slate.yml` | 22 |
| `:47` | `consolidate-exchanges.yml` | 9 (Mon) |
| `:53` | `live-slate.yml` | 11, 14, 17, 20, 23 |

`tests/test_workflow_schedules.py` pins all of it: no crowded minutes, no two
workflows on the same slot, and nothing `live-slate.yml` reads starting at or
after it. Adding a schedule means picking a free minute from this table.

> Cron is *best effort* on GitHub's side no matter which minute you pick — a
> run can still start late, and a busy hour can drop one entirely. The odd
> minutes shorten the queue; they do not make the schedule a guarantee.

### What the schedule really does

Measured, because the minute map's premise — move off `:00` and the queue
clears — turned out to be only half true. Read off the Actions run list on
2026-09-19:

* **`live-slate.yml` starts 1h48–3h00 after its cron.** Every scheduled run
  from Sep 16–19 (seven of them, both windows): +2:52, +2:01, +3:00, +1:56,
  +2:21, +1:48, +2:06. The job itself is twenty to thirty minutes, so a
  window written as `H:53` publishes between `H+2:10` and `H+3:30`. The old
  `16:53`/`22:53` pair therefore published at roughly 19:00–20:15 and
  01:00–02:15 UTC — after the noon-ET college slate, level with the 3:30
  window, and after every prime-time kick. On Sep 19 the 16:53 run started
  at 18:59, was reclaimed by GitHub mid-fit at 19:19 (exit 143, "the runner
  has received a shutdown signal"), and the site sat on Friday evening's
  board through the Saturday afternoon games.
* **`collect-odds.yml` is hourly on paper and roughly four-hourly in
  practice.** Forty runs in the seven days to Sep 19 — one every 4.2 hours
  on average, with gaps of 1.7 to 7.5 hours — so most hourly slots are
  dropped outright, not delayed. Two consequences: `live-slate.yml`'s
  banked-board reuse (`board_max_age_min`, 75 minutes) rarely qualifies and
  the run pays for a live pull, and a closing line is whichever snapshot
  happened to land last, not the one nearest kickoff.
* **`workflow_dispatch` runs start within a minute.** The delay is specific
  to the `schedule` event. When the site has to be rebuilt *now*, run the
  workflow by hand from the Actions tab.

GitHub's status page reported nothing during any of this; it is the
platform's normal best-effort behaviour, and no minute choice changes it.
What the schedule can do is compensate: `live-slate.yml` fires five times a
day, three hours apart from 11:53 to 23:53 UTC, so that

| Window (UTC) | Publishes at (measured delay) | Lands before |
|---|---|---|
| 11:53 | 14:03–15:23 | Sat noon-ET college kicks (16:00); Sun 1 PM ET (17:00) |
| 14:53 | 17:03–18:23 | Sat 3:30 ET (19:30); Sun 4:05/4:25 ET (20:05–20:25) |
| 17:53 | 20:03–21:23 | 7/7:30 ET prime time (23:00–23:30); MLB's 7 PM ET slate; SNF/MNF/TNF (00:15–00:20) |
| 20:53 | 23:03–00:23 | Sat 10:30 ET (02:30); a fresh board for the late prime-time swaps |
| 23:53 | 02:03–03:23 | the west-coast MLB board; the overnight grade |

and a run GitHub drops is covered by its neighbour three hours away.

The other half of the fix is in the runner: a league with no game inside
its window no longer fits its ratings first (NCAAB's fit alone was eleven
of the run's thirty minutes, for a board that was empty from April to
November), so each window publishes sooner and spends less time exposed to
a reclaimed runner.

### What the schedule costs

Measured off the same runs (2026-09-19). Three budgets are in play, and only
one of them moves.

* **The Odds API (100k credits a month).** A live-slate run that finds no
  banked board younger than `board_max_age_min` pays about **210 credits**:
  nfl 108, ncaaf 77, mlb 19, wnba 3, nhl 3 (run #126). 195 of those are the
  per-event team-total pulls for the two football boards — a market the
  slate stakes at zero (`--no-team-totals` drops a live run to ~15). A run
  that reuses a banked board pays nothing, team totals included. So the
  three extra windows add at most ~630 credits a day, ~19k a month. For
  scale, the props collector spends ~400 a run twice a day and the odds
  collector 15 a run; the whole schedule projects to roughly 40–45k a
  month against 100k, with 84.6k left on the 19th. Tight on a 20k plan,
  fine on this one.
* **BettingPros (5,000 calls a day).** Unchanged. `live-slate.yml` never
  calls BettingPros: it reads the parquet the 3-hourly collector banked,
  and the slate step is not given the key (only `collect-bettingpros.yml`
  has it). The collector's ~45 calls a run, eight runs a day, is under 8%
  of the cap.
* **GitHub Actions.** This repository is **private**, so minutes and
  artifact storage are metered: GitHub Free and Free for organizations
  include 2,000 minutes and 500 MB a month, Pro and Team 3,000 minutes and
  1–2 GB, and "if your account does not have a valid payment method on
  file, usage is blocked once you use up your quota." The schedule as
  written books ~280 minutes a day (five live-slate runs at ~20 minutes,
  six DFS runs, the hourly collectors), ~190 at the rate GitHub actually
  fires it — 5,700–8,400 a month either way, so past every plan's included
  minutes by mid-month, exactly as the old two-window schedule already was
  (~4,500–7,200). Retained artifacts are tens of gigabytes: the slate
  artifact is ~51 MB a run kept 60 days (15 GB at five a day), the
  exchange snapshot ~26 MB kept 90 days. Overage is $0.006 a minute and
  $0.25 a GB-month, so the extra windows cost on the order of $10 a month.
  It runs only on an account with a payment method and a spending limit
  above zero; check Settings → Billing → Usage. Cut retention before
  minutes: the grader reads the newest twelve slate runs (about two and a
  half days) and the season chain and ledger live in R2, so 60 days of
  slate artifacts is the easiest gigabytes to give back.

> **Note (2026-09):** the MLB-specific workflows referenced below were folded
> into `live-slate.yml` per [`docs/FOOTBALL_CUTOVER.md`](FOOTBALL_CUTOVER.md)
> Phase 4 — that consolidation happened. The *sport* was not retired with
> them: MLB runs in the shared slate on every cron, and the same grading,
> email and card features run for it there.

Everything paid is written **only to Actions artifacts**, never to this
git (provider ToS + it would leak the edge). `artifacts/` is gitignored.

## Pre-season checklist (do once)

1. **Secrets are set** as Actions repository secrets (Settings → Secrets and
   variables → Actions): `BP_API_KEY`, `BP_USER_ID`, `BP_USER_KEY`, `FP_API_KEY`,
   `THE_ODDS_API`. ✅ (already configured)
2. **Rotate any exposed keys.** If a key was ever pasted into a chat or logged,
   rotate it with the provider and update the secret. (The CFBD and BettingPros
   keys used during development should be rotated.)
3. **Verify each live client** by running its workflow manually — the dev sandbox
   can't see the secrets, so *this run is the first real proof each key works*:
   - **Actions → Collect The Odds API lines → Run workflow.** Check the log for a
     row count and `credits remaining`.
   - **Actions → Collect FantasyPros projections → Run workflow.** The `--inspect`
     log prints the raw response shape; if it differs from the tolerant
     normalizer's assumptions, capture the `first player raw` block and tighten
     `velocity/ingest/fantasypros.py`.
   - **Actions → Collect BettingPros lines → Run workflow.** (Already verified
     live; re-run to confirm the secrets in CI.)
4. **Smoke-test the slate.** Actions → **Live slate → Run workflow**. Off-season
   it reports "no games on the board" and writes an empty slate — that's success.
   The first week games are posted, it will produce real recommendations.

## What to expect off-season vs in-season

- **Off-season (now):** boards are empty or thin (only early futures). Collectors
  and the slate run clean and write empty/small artifacts. Nothing breaks.
- **In-season:** the board fills. `live-slate.yml` fires on game days and writes
  `slate_<league>_<timestamp>.parquet` with the staked bets.

## The weekly operate loop (in-season)

1. **Let it run.** `live-slate.yml` fires on the game-day cron; or trigger it
   manually a few hours before kickoff for the freshest board.
2. **Read the slate** from the run's artifact (or the job log). Each row is a
   recommended bet: `market, side, point, book, price, p_model, stake` (stake is
   an absolute amount and a `stake_pct` of bankroll).
3. **Check the skipped list.** Games whose teams didn't resolve to the model are
   reported, not silently dropped. For NCAAF especially, add any recurring
   misspellings to the resolver so coverage rises (see below).
4. **Place the bets you choose** at the referenced number or better (the edge is
   computed at that price; a worse number erodes it).
5. **Book them on the ledger** (docs/WAGERING.md §7) so the bankroll compounds
   off what you actually did and the kill-switch has real inputs:

   ```
   python scripts/ledger.py pull                 # the R2 copy, merged in
   python scripts/ledger.py todo --league nfl    # the newest card with bet ids
   python scripts/ledger.py place --bet-id "nfl|<game>|total|under|" --stake 2 --price -108 --book fanduel
   python scripts/ledger.py skip --bet-id "nfl|<game>|spread|home|"
   python scripts/ledger.py push                 # back to R2; the morning run settles it
   ```

   The workflow runs the ledger in `auto` mode by default — every staked row
   is booked at its recommended terms, a model bankroll — so nothing is
   waiting on you. The day you start placing by hand, dispatch the workflow
   with `ledger_mode: manual` (the two modes double-count each other).
   Deposits, withdrawals and corrections are `adjust --amount`; a bet the
   grader could not settle is `settle --bet-id ... --result win|loss|push`.
6. **Later, measure CLV** — the collectors keep snapshotting toward close, so the
   closing line is captured for comparison against your entry; the grader
   attaches it to the record and to the ledger's settled rows.

## Tuning knobs

- **Edge threshold / bankroll:** `min_edge` and `bankroll` inputs on the slate
  workflow dispatch (defaults 0.02 and 100). The design's real edge is **selective
  NCAAF totals** (see `docs/BACKTEST_NCAAF.md`) — raise `min_edge` to bet only the
  bigger disagreements.
- **Cron windows:** `live-slate.yml` fires every three hours from 11:53 to
  23:53 UTC. The spacing is set by what GitHub actually does with a schedule,
  not by the kickoff times alone — see **What the schedule really does**
  above before moving one. Keep the minute odd and keep it last in its hour —
  see **The minute map** for why.
- **Staking discipline:** fractional Kelly with per-bet and per-game group caps is
  already enforced (`velocity/wagering/staking.py`); the group cap keeps one
  game's correlated bets bounded.

## Email delivery (optional)

`live-slate-mlb.yml` emails the day's plays after each run — game markets
(including F5 and NRFI/YRFI), props, and parlays as matchup-labeled tables, with
the formatted workbook attached, and a "no plays today" heartbeat on empty days.
The email opens with a **model status** section: the previous day's plays graded
against StatsAPI finals (linescores settle the F5/NRFI segments, box scores the
props and parlay prop legs) as a per-section record with units won/lost.
Previous slates are fetched from recent runs' Actions artifacts; if none exist
yet (first runs) or grading fails, the section is simply omitted — it never
blocks the email.
It activates when these Actions secrets exist (until then the email steps skip
and the workflow behaves exactly as before):

- `MAIL_USERNAME` — the sending account (e.g. a Gmail address).
- `MAIL_PASSWORD` — its SMTP password. For Gmail this must be an **App
  Password** (Google account → Security → 2-Step Verification → App passwords);
  a normal account password will not authenticate.
- `MAIL_TO` — the recipient inbox. Kept in a secret, never in the workflow file:
  the mail carries paid-odds-derived data, so it must go
  to a private inbox.
- `MAIL_SERVER` / `MAIL_PORT` *(optional)* — default `smtp.gmail.com` / `465`
  (SSL); set both to use another provider.

To verify: add the secrets, then **Actions → Live slate (MLB) → Run workflow**
and check the inbox. The email is rendered by `scripts/render_slate_email.py`
from the run's persisted parquets, so what lands in the inbox is exactly what
the artifact says.

## Sim Check + model record (in every slate artifact)

Each run also grades the previous day *visually*:

- **Sim Check cards** (`simcheck_mlb_<stamp>_<AWY>_at_<HOM>.png`) — the actual
  final pinned onto the pregame simulated distribution, percentile as the hero
  number, the actual total as the amber bar in the model's teal histogram.
  Pregame distributions persist per run (`distributions_mlb_*.parquet`), so the
  card grades against exactly what the model published, not a re-simulation.
- **Model record card** (`recordcard_mlb_<stamp>.png`) — yesterday's W-L and
  units by section plus the season line.
- **The season chain** (`cumulative_record_mlb_<stamp>.parquet`) — every graded
  play accumulated run over run (each run downloads the previous artifact's
  chain and extends it), which also puts the `SEASON 41-38 · +6.2U` receipt
  line on every daily model card. The chain survives as long as consecutive
  runs stay within artifact retention.

## Social model cards (in every slate artifact)

Each MLB run also renders one shareable **model card** per game
(`social_mlb_<stamp>_<AWY>_at_<HOM>.png`, 1200×675 — the X/Twitter frame) plus
a `social_mlb_<stamp>_captions.md` file of ready-to-paste post copy. Cards
carry **model facts only** — win split, projected score, fair/F5 totals,
first-inning-run probability, the simulated total-runs distribution, and a
players-to-watch strip — no odds, no picks, no hype. Players to watch are
chosen where the model most disagrees with the market's prop line (when a
board is available), but the card states only the model's probability at that
line; a sharp reader needs no hand-holding, a casual reader sees a stat
graphic. The footer marks every card "model output, informational only".

## The plays app (optional, free)

`app/streamlit_app.py` is a dark, phone-friendly board over the same persisted
slate the email uses. The **Cards** tab is the posting workflow: every graphic
from the newest run — market-vs-model matchup cards, Sim Checks, the record
card, and the DFS lineup card — viewable per league (NFL / NCAAF), each with a
download button and the pre-written caption copy in an expander, so posting
from a phone is view → download → paste caption. **Plays** is the two-column
board (matchup | play), **Matchups** the per-game numbers, **Record**
yesterday's graded plays.

It reads the newest `slate-*` Actions artifact through the GitHub API — the
paid-odds data itself stays out of the repo. Deploy free on
[Streamlit Community Cloud](https://share.streamlit.io):

1. New app → this repo → main file `app/streamlit_app.py` (deps come from the
   root `requirements.txt`).
2. In the app's **Secrets**, add:

   ```toml
   GITHUB_TOKEN = "<fine-grained PAT for this repo with Actions: read>"
   # optional, defaults to EdgeCash/Velocity:
   GITHUB_REPO = "EdgeCash/Velocity"
   ```
3. Open the app — it fetches the latest successful run's artifact (cached ten
   minutes) and renders the board.

**Privacy:** the app displays paid-odds-derived data, so don't share the URL
publicly — deploy it as a private app (Community Cloud allows one free) or keep
the URL to yourself, the same discipline as the Actions artifacts.

Local, no token needed:

```bash
pip install -r requirements.txt
VELOCITY_SLATE_DIR=artifacts/slate streamlit run app/streamlit_app.py
```

## Team-name resolution (the one real-world seam)

The slate maps a provider's team name to the model's rating key. NFL is covered
exactly by an alias table (`NFL_TEAM_ALIASES` in `velocity/wagering/live.py`);
NCAAF's 250+ teams lean on a normalized fallback. Any unresolved game is
**skipped and printed**, never mis-projected — so the first live NCAAF slates will
show which names need aliases. Add them to a league alias map and coverage climbs
week over week.

## Honest status

- **Proven:** every normalizer (offline tests), the BettingPros live client
  (in-session), the backtest edge on real data, and the live-slate orchestration
  (real dataset + snapshot, end-to-end).
- **Proven on first Actions run:** the Odds API and FantasyPros live clients
  (keys are Actions-only). Run their dispatch jobs from the checklist above.
- **Model:** the slate runs the schedule-only **scores** ratings (tuned, robust).
  Swapping in EPA ratings is a one-line factory change once play-by-play is wired.
