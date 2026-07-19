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
  cost more per call than live).
- **FantasyPros** supplies consensus player projections that feed the props model
  (`velocity/models/props.py`) as a prior/blend against our own numbers.

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

## How this plugs into the stack

`BettingProsClient.game_lines` returns the same canonical `Lines` frame the
backtest already consumes, so it drops straight into
[`LiveOddsAdapter`](../velocity/ingest/odds.py) as the production `fetch`
callable — swapping the historical archive for the live feed is a config change,
not a rewrite. CLV is then the live snapshot vs the closing snapshot from the
archive.

## Security

- Keys are read from the environment only — never a literal, never committed.
- The repo is public: **no paid odds/props data in git, ever** (ToS + edge leak).
  Snapshots live only in private Actions artifacts.
- If a key is ever exposed (e.g. pasted into a chat), rotate it with the provider.
