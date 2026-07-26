# Launch runbook — going live at kickoff

Everything needed to run Velocity as a functional workflow when the season
starts. The system is built and tested; this is the operator's checklist for
turning it on, verifying it, and running it week to week.

## The pieces, and where they run

| Workflow | Schedule | Secret(s) | Output |
|---|---|---|---|
| `ci.yml` | push / PR | — | the test gate |
| `collect-bettingpros.yml` | every 3h | `BP_API_KEY`, `BP_USER_ID`, `BP_USER_KEY` | line snapshots → private artifact |
| `collect-odds.yml` | hourly | `THE_ODDS_API` | line snapshots + CLV archive → private artifact |
| `collect-fantasypros.yml` | weekly | `FP_API_KEY` | projections → private artifact |
| `live-slate.yml` | game days | `THE_ODDS_API` | **staked slate of recommended bets** → private artifact |
| `live-slate-mlb.yml` | daily 9am & 4pm CT | `THE_ODDS_API` (+ mail secrets, optional) | staked MLB game/prop/parlay slate → private artifact + **email** |

Everything paid is written **only to private Actions artifacts**, never to this
public repo (provider ToS + it would leak the edge). `artifacts/` is gitignored.

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
5. **Later, measure CLV** — the collectors keep snapshotting toward close, so the
   closing line is captured for comparison against your entry.

## Tuning knobs

- **Edge threshold / bankroll:** `min_edge` and `bankroll` inputs on the slate
  workflow dispatch (defaults 0.02 and 100). The design's real edge is **selective
  NCAAF totals** (see `docs/BACKTEST_NCAAF.md`) — raise `min_edge` to bet only the
  bigger disagreements.
- **Cron windows:** `live-slate.yml`'s cron is a sensible default
  (Thu/Sat/Sun/Mon, 16:00 & 22:00 UTC); tighten it to the real kickoff windows.
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
Previous slates are fetched from recent runs' private artifacts; if none exist
yet (first runs) or grading fails, the section is simply omitted — it never
blocks the email.
It activates when these Actions secrets exist (until then the email steps skip
and the workflow behaves exactly as before):

- `MAIL_USERNAME` — the sending account (e.g. a Gmail address).
- `MAIL_PASSWORD` — its SMTP password. For Gmail this must be an **App
  Password** (Google account → Security → 2-Step Verification → App passwords);
  a normal account password will not authenticate.
- `MAIL_TO` — the recipient inbox. Kept in a secret, never in the workflow file:
  the repo is public, and the mail carries paid-odds-derived data, so it must go
  to a private inbox.
- `MAIL_SERVER` / `MAIL_PORT` *(optional)* — default `smtp.gmail.com` / `465`
  (SSL); set both to use another provider.

To verify: add the secrets, then **Actions → Live slate (MLB) → Run workflow**
and check the inbox. The email is rendered by `scripts/render_slate_email.py`
from the run's persisted parquets, so what lands in the inbox is exactly what
the artifact says.

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
