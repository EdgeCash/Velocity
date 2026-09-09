# The Exchange Build — Kalshi & Polymarket

Prediction-exchange lines as a projection target and (eventually) a venue.
EDGE_RESEARCH §1 already names the CFTC exchange class (Kalshi, Novig,
ProphetX) as the durable home for origination edge; this plan wires the two
exchanges with real, free APIs — Kalshi and Polymarket — into the existing
ingest → devig → edge → stake → CLV loop. Data first, paper slate second,
execution never in this build (that belongs to WAGERING W1/W5).

Both venues' market data was **verified free and key-less by live pulls on
2026-09-09** from a plain unauthenticated client: markets, order books,
trades, and price history all returned 200 with no account. Trading needs
auth; reading does not.

## 0. The one-paragraph summary

The Monte Carlo sim already prices everything these exchanges list for a
game: `GameSim` keeps all 50k `(home_score, away_score)` draws, and every
exchange game contract is a binary functional of that joint distribution —
Kalshi's "BAL wins by >41.5" ladder is `market=spread, point=41.5` in the
existing `model_probability` dispatch (`velocity/wagering/slate.py`),
which already accepts any point. So the build is not modeling work; it is
two ingest adapters (the `theoddsapi.py` two-layer pattern), two
collectors on the existing hourly cron, one fee-aware EV extension
(exchanges have no vig — they have a bid/ask spread plus a taker fee), and
a calibration gate before we trust tight ladders at the key numbers the
rounded-normal sim smooths over.

## 1. The venues (all facts verified live 2026-09-09 unless marked)

| | Kalshi | Polymarket (global) |
|---|---|---|
| Read auth | none (key optional, free) | none |
| Base URL | `api.elections.kalshi.com/trade-api/v2` (alt: `external-api.kalshi.com`) | `gamma-api.polymarket.com` + `clob.polymarket.com` + `data-api.polymarket.com` |
| Football | NFL + NCAAF: winners, spread/total ladders, team totals, halves/quarters, player props | NFL + CFB: moneyline, ~22 alt spreads + ~22 alt totals per big game, quarters, team totals, props (136 markets on one week-1 NFL game) |
| Price | binary contract, $0.01 tick, price = probability; API returns fixed-point dollar **strings** (`yes_ask_dollars: "0.1900"`) | outcome tokens in [0,1] = probability, decimal strings, tick 0.01 (0.001 on some) |
| History | candlesticks (1min/1hr/1day; separate trade/bid/ask OHLC + volume + OI), ~3-month live window, archive endpoint beyond (backfill **unproven** — see §5) | `prices-history` per token, minute fidelity, survives resolution; **no historical order books** |
| Taker fee | `ceil(0.07·C·P·(1−P))` — 1.75¢/contract max at 50¢; per-series overrides via `/exchange/series_fee_changes` | sports `0.05·p·(1−p)`/share — 1.25¢ max at 50¢; makers $0 |
| Rate limits | keyless works but undocumented; free key ≈ 20 GET/s (Basic tier); batch candles = 100 tickers/call | Gamma 4,000 req/10s, CLOB 9,000/10s; 500-token batch endpoints |
| Legal (trading, not data) | CFTC DCM, nationwide with a live circuit split (3rd Cir. for, 9th Cir. against, SCOTUS cert pending) | global exchange is US **close-only**; "Polymarket US" (QCX) is a separate KYC venue with separate liquidity |

### 1.1 Kalshi cheat sheet

- **Discovery**: `GET /series?category=Sports` (3,661 series). Football
  series: `KXNFLGAME`, `KXNCAAFGAME` (winners), `KXNFLSPREAD`,
  `KXNFLTOTAL` + NCAAF twins (ladders keyed by `floor_strike`), team
  totals and half/quarter series, props (`KXNFLPASSYDS`, `KXNFLRECYDS`,
  `KXNFLREC`, `KXNFLTD`, …).
- **Ticker grammar**: event `KXNFLGAME-26SEP21NYGLAR` =
  `{SERIES}-{YY}{MON}{DD}{AWAY}{HOME}`; market appends the outcome:
  `-NYG` (winner), `-BAL42` (`floor_strike: 41.5`), `-76` (Over 75.5).
  MLB inserts a start time; NFL/NCAAF tickers carry date only — kickoff
  must come from our own `games` data by (date, teams) join.
- **Board**: `GET /markets?series_ticker=KXNFLGAME&status=open&limit=1000`
  returns top-of-book (`yes_bid/yes_ask/no_bid/last` as dollar strings),
  volume/OI in `*_fp` string fields (a plain integer `volume` key is NOT
  reliably present). Unfiltered `/markets` surfaces parlay combo markets
  (`KXMVECROSSCATEGORY-*`) first — always filter by series.
- **Depth**: `GET /markets/{ticker}/orderbook?depth=N` →
  `orderbook_fp.{yes_dollars,no_dollars}` as `[price, size]` levels (both
  sides quoted as bids).
- **History**: `GET /series/{s}/markets/{t}/candlesticks?start_ts=&end_ts=&period_interval=1|60|1440`;
  batch `GET /markets/candlesticks` (≤100 tickers, ≤10k candles). Quiet
  buckets omit trade OHLC and carry only bid/ask OHLC + `previous` —
  closing lines are recoverable from quotes even at zero volume.
  Live retention ~3 months; `GET /historical/cutoff` marks the boundary.
- **WebSocket** exists but requires a (free) API key + request signing —
  not needed for this build; REST polling suffices.

### 1.2 Polymarket cheat sheet

- **Discovery**: Gamma `GET /events?tag_id=450&closed=false` (NFL;
  CFB = `100351`; `tag_slug=nfl` also works), or by slug:
  `GET /events/slug/nfl-ne-sea-2026-09-10` (grammar
  `nfl-{away}-{home}-{YYYY-MM-DD}`, `cfb-…` for college). One event per
  game, `gameId` + all markets nested; each market carries
  `sportsMarketType`, `line`, `outcomes`, `outcomePrices`, and
  `clobTokenIds` — the two token ids every CLOB endpoint is keyed by.
- **Prices**: CLOB `GET /book?token_id=` (bids/asks + sizes + `tick_size`
  + `neg_risk`), `/price?token_id=&side=BUY` (the executable ask),
  `/midpoint`, batch POST variants at 500 tokens.
- **History**: `GET /prices-history?market={token_id}` with
  `interval=1h|6h|1d|1w|max` or `startTs/endTs` + `fidelity` (minutes).
  Three verified quirks: `startTs/endTs` windows much over a week are
  rejected (`400 "interval is too long"`) — chunk ~week-long windows or
  use `interval=max`; responses append one extra point at the *current*
  timestamp — trim it; history is mid/last samples, not OHLC, and there
  are **no historical order books** — spread history must be captured
  live from day one (E4).
- **Trades**: `data-api GET /trades?market={conditionId}` — public tape.
- **Clients**: official `py-clob-client` is deprecated/archived
  (2026-05); successor is `polymarket-client` (PyPI). We need neither —
  plain `requests` covers key-less reads, matching the house pattern.

## 2. Design decisions (ratified before code)

- **D1 — Price lane: convert ask → integer American at the normalizer
  boundary.** `Lines.price` is `Series[int]` American and
  `american_to_decimal` rejects (−100, 100), so cent prices cannot land
  raw. Rounding `prob_to_american(ask)` to int costs ≤ ~0.1–0.2%
  probability at worst (near even money) — several times finer than
  either venue's $0.01 tick — and keeps `Lines`, `pit.closing_line`,
  `devig`, the backtest, and every validator untouched. Escalation path
  if sub-cent ticks ever matter: a nullable probability-native column on
  `Lines`, which is a schema change and a separate decision.
- **D2 — The executable price is the ask, per side.** Buying YES at the
  yes-ask and NO at the no-ask; their implied probabilities sum > 1, so
  the bid/ask spread arrives as overround and the existing `devig`
  strips it exactly as it strips vig. Never price EV off the midpoint.
- **D3 — Fees enter EV as an adjusted net payout.** Exchanges charge a
  taker fee at match; today `expected_value`/`kelly_fraction`
  (`velocity/wagering/edge.py`) have no fee term. Buying at ask `a` with
  fee `f(a)`: cost per contract `a + f(a)`, payout $1, so
  `b′ = (1 − a − f(a)) / (a + f(a))` replaces `net_payout(price)`. A
  venue-keyed fee table (Kalshi `0.07·P·(1−P)` rounded up, checked
  against the per-series override endpoint; Polymarket sports
  `0.05·p·(1−p)`; sportsbooks `f = 0`) makes this uniform. Makers pay
  zero on both venues — a later execution refinement, not an EV input.
- **D4 — Market mapping rides the existing enums.** Winners →
  `moneyline`; spread ladders → `spread` with `point = ±floor_strike`
  (Kalshi "BAL wins by >41.5" ≡ BAL −41.5); total ladders → `total`;
  team-total series → `team_total_home/away`; props → matching
  `PROP_MARKETS` keys (`pass_yards`, `receiving_yards`, `receptions`,
  `anytime_td`, …). `model_probability` and `Bet.grade` already accept
  arbitrary points for these markets, so ladders need **no** new market
  names. Halves/quarters/exact-margin/first-TD have no sim support —
  dropped by the normalizer (the pandera `isin` gate enforces this) and
  parked in §5.
- **D5 — Push semantics: half-point strikes only, asserted.** Exchange
  contracts resolve binary; every observed strike is a half-integer
  (41.5, 75.5), where the sim's strict-`>` cover logic is exact. The
  normalizers assert half-integer points on ladder markets and drop
  anything else rather than risk mis-grading; if integer-strike
  contracts ever appear, that becomes an explicit `>=`-semantics branch,
  not a silent one.
- **D6 — Venue identity is just a book.** Rows land with
  `book="kalshi"` / `book="polymarket"`, flowing through devig pairing,
  `shop_best_prices`, closing-line preference, and `Bet.book` unchanged.
  Per-venue exposure caps stay with WAGERING W5 where they belong.
- **D7 — Storage follows the paid-provider discipline.** The data is
  free but the repo is public: snapshots go to private Actions artifacts
  like every other odds feed (raw JSON verbatim + normalized parquet),
  and nothing is committed until each venue's API terms are read and say
  redistribution is fine (an E1/E3 exit item). Secrets (the optional
  `KALSHI_API_KEY` for rate headroom) live only in Actions secrets.

## 3. The phased build

Each phase is one merge, run through the BUILD.md §1 safe loop: tests
first against frozen fixtures, offline suite green, live client verified
via `workflow_dispatch` only.

### Phase E1 — Kalshi ingest adapter

`velocity/ingest/kalshi.py`, two layers per the house pattern:

- Pure `normalize_kalshi_markets(payload, *, is_closing=False)` → a
  `Lines.validate`-clean frame. Series→market map table (the D4 dict);
  ticker parser (`KXNFLGAME-26SEP21NYGLAR-NYG` → league, date, away,
  home, outcome team); dollar-string fields parsed as `Decimal`; yes-ask
  prices the named side, no-ask prices the `_OPPOSITE` side; points from
  `floor_strike` with the D5 half-integer assertion; deterministic
  `line_id` per the `theoddsapi.py` recipe. Unknown series and combo
  markets (`KXMVE*`) are dropped, not errors.
- `extract_kalshi_events(payload, games)` → the events frame
  (`game_id, home_team, away_team, kickoff`) that `canonicalize_sides`
  and the live runner require; kickoff joined from our own schedule by
  (date, teams) since NFL tickers carry no time (the `sbro.py` join
  precedent, including team-alias tables for Kalshi's short codes).
- `KalshiClient` dataclass: key-less by default (the `prizepicks.py`
  polite-client posture — UA, timeout, backoff), optional
  `from_env()` key for rate headroom; network methods
  `# pragma: no cover - network`.
- A props normalizer for the `KXNFLPASSYDS`-family series →
  `PropLines`, same discipline.

Fixtures: frozen `tests/fixtures/kalshi_nfl.json` (+ a props payload);
tests cloning `test_ingest_theoddsapi.py` (validate, market filtering,
ladder point mapping, dollar-string parsing, line_id stability,
half-integer assertion, empty-in → valid-empty-out) and the
`OddsAdapter` protocol checks. Exit: suite green offline; ToS read (D7).

### Phase E2 — Kalshi collectors

- `scripts/collect_kalshi.py` on the existing hourly cron
  (`collect-odds.yml` pattern, own workflow file): snapshot the open
  board for the mapped series (`limit=1000` + cursor), bank raw JSON +
  normalized parquet to `artifacts/kalshi/…` (private, 30-day
  retention), tag `snapshot`/`collected_at` like
  `collect_historical_odds.py` so `backtest/archive.py` splits
  entry/close unchanged.
- `scripts/collect_kalshi_candles.py` (daily): for markets settled since
  the last run, pull 1-minute candlesticks via the batch endpoint (≤100
  tickers/call) and bank them — this is the CLV archive. The ~3-month
  live window means **bank-forward from day one**; a one-off spot job
  probes `/historical/*` backfill quality (empty on a Jan-2026 spot
  check) and records the verdict here.

Exit: two workflow_dispatch runs verified; candle → `Lines` close rows
feeding `pit.closing_line` proven on one settled game.

### Phase E3 — Polymarket ingest adapter

`velocity/ingest/polymarket.py`, same two layers:

- Pure `normalize_polymarket_event(event_json, books)` → `Lines`:
  Gamma's nested markets filtered by `sportsMarketType` to the D4 map,
  `line` → point (D5 assertion), slug parser
  (`nfl-ne-sea-2026-09-10` → teams/date), executable asks taken from
  CLOB `/book` (or `/price?side=BUY`) per outcome token — the Gamma
  `outcomePrices` mid is metadata, not the price we can buy (D2).
- `PolymarketClient`: key-less; Gamma events by `tag_id` (450 NFL,
  100351 CFB) + batch CLOB books (≤500 tokens/POST); token-id ↔
  (market, side) plumbing kept in the normalized frame so E4's history
  pulls don't re-resolve it.
- Props normalizer where `sportsMarketType` matches `PROP_MARKETS`.

Fixtures: one frozen Gamma event (the 136-market NFL game) + a books
payload; tests as E1. Exit: suite green; ToS read.

### Phase E4 — Polymarket collectors

- Board snapshots on the hourly cron (events + batch books →
  raw + parquet, private artifacts) — this **is** the spread/liquidity
  history, since the CLOB keeps no historical books.
- Closing lines: at grade time, `prices-history` with `endTs = kickoff`
  (≤1-week windows, trim the appended current-time point) as the
  fallback close where the hourly board missed the last pre-kickoff
  snapshot.

Exit: workflow verified; one game's close recovered both ways and
agreeing within tolerance.

### Phase E5 — Fee-aware EV

- `velocity/wagering/fees.py`: venue-keyed taker-fee functions
  (D3 table) + `fee_adjusted_net_payout(prob_cost, venue)`; Kalshi
  per-series overrides fetched by the collector, not hardcoded.
- `expected_value`/`kelly_fraction` grow an optional fee-aware path
  (default `fee=0` keeps every existing caller and test byte-identical);
  `SlateConfig` maps `book → venue fee schedule`; `evaluate` gates on
  fee-adjusted EV for exchange books.
- Tests: hand-computed Kalshi (yes-ask 0.19, fee ceil(0.07·0.19·0.81))
  and Polymarket examples; a devig test proving yes-ask + no-ask
  overround strips to sane fairs; property test that `fee=0` reproduces
  current outputs exactly.

Exit: suite green; a worked example in the doc showing edge → EV → stake
for one real Kalshi market.

### Phase E6 — Slate & live wiring

- Both clients handed to `LiveOddsAdapter` as fetch callables; events
  frames from E1/E3 into `canonicalize_sides`; team-alias tables
  extended for exchange codes.
- `run_live_slate` includes exchange books alongside sportsbooks;
  `shop_best_prices` now shops books *and* exchanges on the same
  American scale (D1's payoff); slate output labels venue so exchange
  picks are visibly fee-gated.
- Paper only: bets land in the `BetLog` with `book` set; no order
  placement anywhere.

Exit: one live slate run (workflow_dispatch) producing a mixed
sportsbook + exchange board with sane cross-venue prices.

### Phase E7 — CLV & backtest

- Kalshi candle closes and Polymarket history closes flow into
  `pit.closing_line` (last pre-kickoff observation — already
  provider-agnostic); `grade_yesterday` extended to grade exchange rows.
- Backtest: `backtest/archive.py` entry/close split over the banked
  snapshots, with E5 fees applied to simulated fills.
- The report this build exists for: **model vs exchange close vs book
  close** — is the exchange close sharper or softer than the sportsbook
  close, per market type? That answer decides how much origination edge
  the venue class actually offers and feeds EDGE_RESEARCH.

Exit: one full graded week of NFL/NCAAF with cross-venue CLV in the
eval output.

### Phase E8 — The ladder calibration gate (research, blocks ladder betting only)

The sim is a rounded bivariate normal; its own docstring
(`velocity/models/simulate.py`) calls the key-number treatment
first-order. Ladder contracts at 3/7 concentrate value exactly where a
smooth normal misplaces mass. Before any spread-ladder bet qualifies:
calibrate sim tail probabilities against empirical NFL/NCAAF margin
distributions at each half-point strike; whitelist only strikes where
the sim is honest (expectation: far strikes fine, ±2.5–7.5 suspect);
wire the whitelist as a `SlateConfig` exclusion. Moneylines, totals, and
team totals are not gated — they were priced credibly before this build.

## 4. Explicitly out of scope

- **Execution/trading** on either venue (WAGERING W1 bankroll ledger and
  W5 venue-aware execution are prerequisites; also the legal flux below).
- **Polymarket US (QCX)** — separate venue, separate liquidity, no
  documented public data API; the global exchange's data is what we read.
- **WebSockets** — both venues stream, Kalshi's needs a key; hourly REST
  plus candles covers projections and CLV. Revisit only for in-game.
- **Derivative game markets** (halves, quarters, exact margin, first
  TD): need period-level or drive-level simulation that does not exist;
  a separate research line, not an ingest problem.

## 5. Risks & open questions

- **Kalshi deep history is unproven.** Archived pre-cutoff markets
  returned empty candle arrays on a spot check. Posture: bank-forward
  from E2 day one; treat multi-season Kalshi backtests as unavailable
  until the archive probe says otherwise.
- **Fee schedule authority.** The official fee PDF is bot-walled; the
  formula is corroborated by three secondary sources and the per-series
  override endpoint is authoritative programmatically — E5 reads
  overrides at collect time rather than trusting the base formula.
- **Sim shape at key numbers** — the E8 gate. Until it lands, no spread
  ladders qualify, however fat the apparent edge (an apparent 4% edge at
  a 3.5 strike is more likely sim mass misplacement than market error).
- **Legal flux affects trading, not data.** Circuit split live, SCOTUS
  cert pending; nothing here places orders, so the build is unaffected,
  but the venue-strategy payoff (E7's question) could be repriced by a
  ruling.
- **ToS/redistribution.** Free ≠ redistributable. D7 keeps everything in
  private artifacts; each adapter phase exits only after the venue's
  terms are read.
- **Key-less rate limits on Kalshi are undocumented.** Fine at our
  volumes in testing; the collector should attach the free key via
  Actions secrets before scaling candle backfills.
- **Polymarket board history starts when we start.** No historical order
  books exist; every week E4 isn't running is spread history lost.

## 6. Immediate next step

E1 — the Kalshi ingest adapter, fixtures first. It is the smallest slice
that proves the whole path (ticker → teams → `Lines` → devig → EV) on
real exchange data, and everything after it reuses its decisions.
