# Live data providers — BettingPros, The Odds API, FantasyPros

Three paid feeds sit behind the wagering stack. They serve different jobs, and —
critically — they must never write into this **public** repo: provider terms
forbid redistributing their odds, and committing them would leak our edge. All
paid data lives only in **private GitHub Actions artifacts**, never in git.

| Provider | Job | History? | Secret(s) | Limit |
|---|---|---|---|---|
| **BettingPros** | Live multi-book **game lines** (spread/total/moneyline) + player props | ❌ live only | `BP_API_KEY`, `BP_USER_ID`, `BP_USER_KEY` | 5k calls/day |
| **The Odds API** | Historical + live odds, **line archive for CLV/backtest** | ✅ | `THE_ODDS_API` | 100k credits/month |
| **FantasyPros** | Consensus **player projections** (prop inputs) | partial | `FP_API_KEY` | not published |

The secrets are configured as **GitHub Actions repository secrets** (see the repo
Settings → Secrets and variables → Actions). They are injected only into workflow
runs — the local dev sandbox never sees them, which is why the collector runs as
an Action, not from a checkout.

## Why each provider

- **BettingPros** is the production line feed. It has **no archive** — a line
  exists only while it is live — so line *history* has to be built by snapshotting
  the current board on a schedule (see the collector below). It covers both NFL
  and NCAAF, and (premium tier) carries its own projections.
- **The Odds API** is the one with real **history**, so it is the source for the
  closing-line archive that powers CLV measurement and the market-facing backtest.
  Its 100k monthly credits are the budget to spend deliberately (historical pulls
  cost more per call than live). Built in `velocity/ingest/theoddsapi.py`:
  `normalize_odds_events` flattens the `events → bookmakers → markets → outcomes`
  JSON onto the canonical `Lines` schema (markets `h2h`/`spreads`/`totals` →
  `moneyline`/`spread`/`total`; prices requested American so they land as
  integers; `unwrap` accepts both the live array and the historical `{data: …}`
  wrapper). `TheOddsAPIClient.from_env()` reads `THE_ODDS_API`; `odds()` is a live
  snapshot, `historical_odds(date)` is the closing/CLV pull. Because the key is
  Actions-only, the live client is **verified in CI** by running the collector
  workflow manually (`workflow_dispatch`), not from the dev sandbox.
- **FantasyPros** supplies consensus player projections that feed the props model
  (`velocity/models/props.py`) as a prior/blend against our own numbers. Built in
  `velocity/ingest/fantasypros.py`: `normalize_projections` is deliberately
  **tolerant** (we have no pinned FP schema) — it discovers the player list and
  stat keys at runtime and melts them into a long `(player, stat, value)` frame,
  tagged with season/week/source, rather than hard-coding field names. The live
  shape is confirmed by the CI dry-run (`workflow_dispatch --inspect` dumps the
  raw first-player JSON to the run log).

## BettingPros ingest (`velocity/ingest/bettingpros.py`)

Two layers, same discipline as every other adapter:

- `normalize_offers(offers, markets)` — **pure**: flattens the nested
  `offers → selections → books → lines` JSON into one long row per live line
  (stale/pulled lines dropped), carrying game markets *and* player props.
- `to_lines(long)` — projects the three game markets onto the canonical
  [`Lines`](../velocity/store/schema.py) schema (spread/total/moneyline, by
  **slug** — the numeric ids differ by sport: NFL moneyline is 1, NCAAF's 198).
  `price` is American odds, `point` is null for moneyline.
- `BettingProsClient` — the network layer. Built with `from_env()`; sends the
  `x-api-key` partner header and, when `BP_USER_ID`/`BP_USER_KEY` are present,
  the `auth=user` premium triple. `client.game_lines(sport)` returns a validated
  `Lines` frame.

The pure functions are unit-tested offline (`tests/test_ingest_bettingpros.py`);
the client is verified against the live API but is out of the per-commit gate.

Endpoint note: `/offers` **requires** an `event_id` for game markets, so
`game_lines` first pulls the current `/events`, then batches all event ids into a
single `/offers` call across all three markets (≈3 API calls per sport per
snapshot).

## The collector (`scripts/collect_bettingpros.py` + workflow)

`.github/workflows/collect-bettingpros.yml` runs every 3 hours (and on manual
dispatch). It:

1. installs the package (runtime deps only),
2. runs `collect_bettingpros.py`, which snapshots NFL + NCAAF game lines into a
   single timestamped parquet under `artifacts/bp/`, tagged with `league` and
   `collected_at`,
3. uploads that parquet as a **private Actions artifact** (`retention-days: 30`).

It never commits. `artifacts/` is gitignored so a local run can't leak paid data
into the public repo either. Off-season / empty boards are a success, not a
failure — the job still writes an (empty) artifact so the schedule keeps running.

At 3-hour cadence the collector uses ≈48 BP calls/day, far under the 5k/day cap;
tighten the cron toward kickoff windows in-season if closer-to-close snapshots
are wanted for CLV.

## The Odds API collector (`scripts/collect_theoddsapi.py` + workflow)

`.github/workflows/collect-odds.yml` runs hourly (and on manual dispatch). It
snapshots the live board for both leagues into `artifacts/odds/*.parquet` (tagged
`league` + `collected_at`), uploads it as a **private Actions artifact**, and
prints the remaining monthly credits each run. Live `/odds` for 2 leagues × 3
markets is ≈6 credits/run → ~4.3k/month, well under 100k; true historical backfill
uses the pricier `/historical` endpoint on demand. Same rules as the BP collector:
never commits, `artifacts/` gitignored, empty boards succeed.

## The FantasyPros collector (`scripts/collect_fantasypros.py` + workflow)

`.github/workflows/collect-fantasypros.yml` runs weekly (and on manual dispatch).
It snapshots consensus projections for both leagues into `artifacts/fp/*.parquet`
(long `(player, stat, value)` rows tagged `league` + `collected_at`) and uploads a
**private Actions artifact**. The manual dispatch runs with `--inspect`, which
prints the raw top-level keys and first-player JSON to the log — that's how we
verify the `FP_API_KEY` secret and tighten the tolerant normalizer against the
real response. Same rules: never commits, `artifacts/` gitignored.

## How this plugs into the stack

`BettingProsClient.game_lines` returns the same canonical `Lines` frame the
backtest already consumes, so it drops straight into
[`LiveOddsAdapter`](../velocity/ingest/odds.py) as the production `fetch`
callable — swapping the historical archive for the live feed is a config change,
not a rewrite. CLV is then the live snapshot vs the closing snapshot from the
archive.

## The live slate runner (`scripts/run_live_slate.py`)

The end of the pipeline: today's board → staked recommendations, running the
*same* wagering engine the backtest proved. It fits the projection model on
committed history, pulls one live snapshot (The Odds API `/odds`, or a saved JSON
via `--snapshot-file` for offline runs), and calls
[`build_live_slate`](../velocity/wagering/live.py):

1. **Side canonicalization** — a provider names spread/moneyline sides by team and
   totals by Over/Under; the engine speaks home/away/over/under. Because each
   event names its own home/away teams in the same snapshot, this is an exact
   per-game lookup (`canonicalize_sides`).
2. **Team resolution** — the provider spells a team ("Kansas City Chiefs")
   differently from the ratings key ("KC"). `resolve_team` bridges that with an
   NFL alias table plus a normalized fallback, and returns `None` rather than
   guess — an unmatched game is **skipped and reported**, never silently
   mis-projected.
3. **Live-mode slate** — `build_slate(..., exclude_closing=False)` keeps every
   observation as a candidate (a live snapshot is the only board), then de-vigs,
   measures edge, and stakes with the existing fractional-Kelly + group-cap logic.

The result prints as a table of recommended bets (market, side, point, book,
price, model probability, stake) plus the list of unresolved games. It does **not**
place bets — a human acts on the slate. CLV is measured later, against the closing
snapshot from the archive.

    # offline, from a saved Odds API payload:
    python scripts/run_live_slate.py --league ncaaf --data datasets/ncaaf \
        --snapshot-file snap.json
    # live:
    THE_ODDS_API=... python scripts/run_live_slate.py --league nfl --data datasets/nfl

## Security

- Keys are read from the environment only — never a literal, never committed.
- The repo is public: **no paid odds/props data in git, ever** (ToS + edge leak).
  Snapshots live only in private Actions artifacts.
- If a key is ever exposed (e.g. pasted into a chat), rotate it with the provider.
