# The Exchange Build — Kalshi & Polymarket

**Status: E1-E3 + E5-E7 done (E7's graded week waits on banked
snapshots); E2/E4 landed, awaiting in-CI `workflow_dispatch`
verification; E8 next. Companions: [BUILD.md](BUILD.md) §1 (the safe loop),
[WAGERING.md](WAGERING.md) (W1 ledger prerequisite), [DATA_PROVIDERS.md](DATA_PROVIDERS.md)
(secrets & artifact discipline), [EDGE_RESEARCH.md](EDGE_RESEARCH.md) §1.3 + §7.13
(venue strategy).**

Prediction-exchange lines as a projection target and (eventually) a venue.
EDGE_RESEARCH (§1.3, §7 item 13) already names the 2026 CFTC exchange class
(Kalshi, Novig, ProphetX) as the durable home for origination edge; this
plan wires the two exchanges with real, free APIs — Kalshi and Polymarket —
into the ingest → devig → edge → stake → CLV loop. Data first, paper slate
second, execution never in this build (that belongs to the WAGERING plan).

Both venues' market data was **verified free and key-less by live pulls on
2026-09-09** from a plain unauthenticated client: markets, order books,
trades, and price history all returned 200 with no account. Trading needs
auth; reading does not.

## 0. The one-paragraph summary

The Monte Carlo sim already prices everything these exchanges list for a
game: `GameSim` keeps all 50k `(home_score, away_score)` draws, and every
exchange game contract is a binary functional of that joint distribution —
Kalshi's "BAL wins by >41.5" ladder is `market=spread, point=−41.5` (the
BAL side; D4's sign convention) in the existing `model_probability`
dispatch (`velocity/wagering/slate.py`), which already accepts any point.
So the build is not modeling work; it is two ingest adapters (the
`theoddsapi.py` two-layer pattern), two collectors on the existing hourly
cron, one fee-aware EV extension (exchanges have no vig — they have a
bid/ask spread plus a taker fee), one real slate-machinery fix (fair-price
pairing must become point-aware before ladders flow through it — E5), and
a calibration gate before we trust tight ladders at the key numbers the
rounded-normal sim smooths over.

## 1. The venues (all facts verified live 2026-09-09 unless marked)

| | Kalshi | Polymarket (global) |
|---|---|---|
| Read auth | none (key optional, free) | none |
| Base URL | `api.elections.kalshi.com/trade-api/v2` (alt: `external-api.kalshi.com`) | `gamma-api.polymarket.com` + `clob.polymarket.com` + `data-api.polymarket.com` |
| Football | NFL + NCAAF: winners, spread/total ladders, team totals, halves/quarters, player props | NFL + CFB: moneyline, ~22 alt spreads + ~22 alt totals per big game, quarters, team totals, props (136 markets on one week-1 NFL game) |
| Price | binary contract, $0.01 tick, price = probability; API returns fixed-point dollar **strings** (`yes_ask_dollars: "0.1900"`) | outcome tokens in [0,1] = probability, decimal strings, tick 0.01 (0.001 on some) |
| History | candlesticks (1min/1hr/1day; separate trade/bid/ask OHLC + volume + OI), ~3-month live window; `/historical` archive beyond, **verified dense** back through Sep 2025 at hourly resolution (probe 2026-09-09 — see §5) | `prices-history` per token, minute fidelity, survives resolution; **no historical order books** |
| Trading fee | `fee_multiplier · 0.07·P·(1−P)`/contract, rounded up to $0.000001 (≈1.75¢ max at 50¢); football series run `fee_type: quadratic_with_maker_fees` — **makers pay too**. Authority: `fee_type`/`fee_multiplier` on `GET /series/{ticker}`; scheduled changes at `GET /series/fee_changes` | sports taker `0.05·p·(1−p)`/share — 1.25¢ max at 50¢; makers $0 (`takerOnly` confirmed) |
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
  MLB inserts a start time; NFL/NCAAF tickers carry date only — and the
  date is the **US/ET game date** (verified: SNF/MNF games differ from
  their UTC date) — kickoff must come from our own `games` data by
  (teams, ET-local date) join.
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
  Live retention ~3 months; `GET /historical/cutoff` marks the boundary,
  and the `/historical` archive beyond it is real (probe 2026-09-09):
  hourly candles + trades verified dense for NFL/NCAAF from Sep 2025
  through the Jan 2026 playoffs. Archive quirks: the listing's
  `status`/`min_close_ts` filters are **silently ignored** (paginate
  and filter client-side), `start_ts` is mandatory on candlesticks,
  field names drop the `_dollars`/`_fp` suffixes, and zero-volume
  voided duplicate markets (rescheduled games) return empty candles —
  filter `volume_fp == 0` up front.
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
  + `neg_risk`), `/midpoint`, batch POST variants at 500 tokens.
  **Careful with `/price`**: `side=BUY` returns the best *bid* and
  `side=SELL` the best *ask* (verified live) — the executable buy price
  is the book's best ask, never `side=BUY` and never Gamma's
  `outcomePrices` (a midpoint).
- **Slug dates are UTC**: `nfl-ind-kc-2026-09-21` is the Sunday-night
  Sep 20 ET game (kickoff 00:20Z) — the opposite convention from
  Kalshi's ET ticker dates. But no date join is needed: every market
  carries `gameStartTime`, the exact UTC kickoff, so the slug date is
  only part of the identifier (E3 takes kickoff from the market).
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
  raw. Rounding `prob_to_american(ask)` to int costs at most 0.083%
  probability on the cent grid (computed exhaustively; the theoretical
  bound near ±100 is 0.125%) — ~12× finer than the $0.01 tick — and
  keeps `Lines`, `devig`, the backtest, and every validator untouched.
  The precise escalation trigger: the American-int lane loses
  information only if a $0.001-tick market trades near even money;
  live census (2,652 mapped-market tokens across 300 NFL events) shows
  every mapped market at $0.01 ticks — the only $0.001-tick markets
  are exact-margin tails the normalizer drops anyway. If that changes:
  a nullable probability-native column on `Lines` (a schema change and
  a separate decision).
- **D2 — The executable price is the ask, per side.** Buying YES at the
  yes-ask and NO at the no-ask; their implied probabilities sum > 1, so
  the bid/ask spread arrives as overround and the `devig` *math* strips
  it exactly as it strips vig. Never price EV off the midpoint. The
  devig *pairing*, however, does not transfer as-is: `build_slate`
  buckets sides by `(market, book, timestamp)` **without the point**
  (`velocity/wagering/slate.py:218-222`), so ~44 alt-spread/alt-total
  rungs per exchange game would overwrite each other and a rung's
  `p_fair` could be devigged from a different rung's prices (the props
  path shares the flaw). Pairing must become per-contract — and
  **`abs(point)` is not enough**: both teams' ladders exist at the same
  |strike| (live: `KC8` and `DEN8` are distinct contracts, both 7.5).
  The key that works is the **home-perspective point**
  (`point if side == home else −point`): yes/no of one contract
  normalize to the same value, the two teams' equal-|strike| contracts
  stay distinct. Scheduled, load-bearing work in E5, not a free ride.
- **D3 — Fees enter EV as an adjusted net payout.** Exchanges charge a
  fee at match; today `expected_value`/`kelly_fraction`
  (`velocity/wagering/edge.py`) have no fee term. Buying at ask `a` with
  fee `f(a)`: cost per contract `a + f(a)`, payout $1, so
  `b′ = (1 − a − f(a)) / (a + f(a))` replaces `net_payout(price)`. The
  venue-keyed fee table: Kalshi
  `f = fee_multiplier · 0.07 · a·(1−a)` (rounded up to $0.000001 — the
  micro-ceil, **not** to a cent; e.g. at `a = 0.19` the fee is
  ≈$0.0108, not 2¢), with `fee_type`/`fee_multiplier` read from
  `GET /series/{ticker}` at collect time (scheduled changes:
  `GET /series/fee_changes`); Polymarket sports taker `0.05·a·(1−a)`;
  sportsbooks `f = 0`. Maker rebates are Polymarket-only — Kalshi
  football series run `quadratic_with_maker_fees`, so no maker-zero
  assumption anywhere on Kalshi. Fees thread through **both** `evaluate`
  and staking (`stake_amount` calls `kelly_fraction(p, price)` un-fee'd
  today — `velocity/wagering/staking.py:54-70`).
- **D4 — Market mapping rides the existing enums.** Winners →
  `moneyline`; spread ladders → `spread` with `point = ±floor_strike`
  (Kalshi "BAL wins by >41.5" ≡ BAL −41.5); total ladders → `total`;
  team-total series → `team_total_home/away`; props → matching
  `PROP_MARKETS` keys (`pass_yards`, `receiving_yards`, `receptions`,
  `anytime_td`, …). No new market names anywhere — but the two paths
  differ: *game* ladders price via `model_probability` and grade via
  `Bet.grade`, both of which already accept arbitrary points; *prop*
  ladders ride `PropLines` → `props_slate`/`PropModel`
  (`prob_over`/`prob_under` at any point) and grade via
  `grade_prop_ledger`, never through `Bet.grade` (which raises on prop
  markets). Halves/quarters/exact-margin/first-TD have no sim support —
  dropped by the normalizer (the pandera `isin` gate enforces this) and
  parked in §5.
- **D5 — Push semantics: half-point strikes only, asserted.** Ladder
  contracts resolve binary at half-integer strikes (live census: all
  2,708 open Kalshi football ladder strikes are half-integers,
  `strike_type: greater`), where the sim's strict-`>` cover logic is
  exact. The normalizers assert half-integer points on ladder markets
  and drop anything else rather than risk mis-grading; if integer-strike
  contracts ever appear, that becomes an explicit `>=`-semantics branch,
  not a silent one. One exception to "binary": Kalshi **winner** markets
  resolve at $0.50/contract on a tie (per their rules), while
  `Bet.grade` grades a moneyline tie as a push — pricing is unaffected
  (`p_home_win` splits ties 0.5, exactly the contract's expected
  payout), but grading needs an exchange tie branch, scheduled in E7.
- **D6 — Venue identity is just a book.** Rows land with
  `book="kalshi"` / `book="polymarket"`, flowing through
  `shop_best_prices`, closing-line preference, and `Bet.book` unchanged
  (devig pairing needs the D2/E5 point-aware fix first). Per-venue
  exposure caps are **new scope this doc proposes for the WAGERING
  W2/W5 family** — neither phase contains them today (W2 is per-game/
  aggregate portfolio caps, W5 is execution polish) — and are a
  prerequisite for trading, not for this build.
- **D7 — Storage follows the paid-provider discipline, and the terms
  make it mandatory.** Terms read 2026-09-09 (primary documents; not
  legal advice). **Kalshi**: the Developer Agreement (§3.1) permits
  collecting/storing API data only "for purposes of facilitating your
  own trading on Kalshi" and bars sharing it with third parties "in any
  manner" without written authorization; the Data Terms bar archived
  datasets to others and ML/AI-training use. Operating posture: private
  Actions artifacts only, **never** committed or redistributed; the
  archive's justification is this system's own trading of these
  markets (de-vig/CLV analytics of quotes, not model training on
  Kalshi data); a written research/data license is the route to
  anything more. **Polymarket**: storage isn't specifically restricted
  and redistribution is restricted only toward "Capital Market
  Clients"/market-data distributors — but the same private-artifact
  posture applies anyway (public repo discipline). Secrets (the
  optional `KALSHI_API_KEY` for rate headroom) live only in Actions
  secrets.

## 3. The phased build

Each phase is one merge, run through the BUILD.md §1 safe loop: tests
first against frozen fixtures, offline suite green, live client verified
via `workflow_dispatch` only. Each phase's **Exit** is its definition of
done and mints a tag (`v*-e1` … `v*-e8`, the WAGERING.md convention).

### Phase E1 — Kalshi ingest adapter (done)

`velocity/ingest/kalshi.py`, two layers per the house pattern:

- Pure `normalize_kalshi_markets(payload, *, is_closing=False)` → a
  `Lines.validate`-clean frame. Series→market map table (the D4 dict);
  ticker parser (`KXNFLGAME-26SEP21NYGLAR-NYG` → league, date, away,
  home, outcome team); dollar-string fields parsed as `Decimal`; points
  from `floor_strike` with the D5 half-integer assertion; deterministic
  `line_id` per the `theoddsapi.py` recipe. Unknown series and combo
  markets (`KXMVE*`) are dropped, not errors. Row emission per market
  shape (verified against live payloads 2026-09-09):
  - **Winner events carry TWO markets, one per team** ("New York G
    wins" + "Los Angeles R wins"). Emit one `moneyline` row per team
    market from **its own yes-ask only**; the no-asks are near-duplicate
    quotes of the other team (and differ under a tie), so emitting them
    would double-count sides in the devig bucket.
  - **Ladders are ONE market with two economic sides**: a spread rung
    (`KXNFLSPREAD-…-KC7`, `floor_strike: 6.5`) emits side=KC,
    `point=−6.5`, price=yes-ask **and** side=opponent, `point=+6.5`,
    price=no-ask; a total rung emits over/under at the same point from
    yes-ask/no-ask. Team totals likewise.
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
- **Raw snapshots start day one** (this phase, not E2/E4): a dumb
  hourly workflow banking raw JSON verbatim for BOTH venues (Kalshi
  `/markets` pages + Polymarket events + batch `/book`) to private
  artifacts under the E2 durability rules — no normalizer needed, and
  the later phases backfill parquet from banked raw. Polymarket order
  books have zero history; every week this isn't running is spread
  history lost while E1–E3 development proceeds.
- Fixture sets for both venues include a prime-time (SNF/MNF) game, the
  case where Kalshi's ET ticker date and Polymarket's UTC slug date
  disagree.

Fixtures: frozen `tests/fixtures/kalshi_nfl.json` (+ a props payload);
tests cloning `test_ingest_theoddsapi.py` (validate, market filtering,
ladder point mapping, dollar-string parsing, line_id stability,
half-integer assertion, empty-in → valid-empty-out) and the
`OddsAdapter` protocol checks. Exit: suite green offline; ToS read (D7).
**Done:** `velocity/ingest/kalshi.py` (16 offline tests, live-verified
board pull: 11,695 game lines + 3,957 prop lines in one snapshot); raw
day-one collector live in `collect-exchanges.yml`; terms read and
ratified into D7. Series-map correction from the live run: rush yards
is `KXNFLRSHYDS` (no U), and `KXNCAAFTEAMTOTAL` is real (1,148 open
markets).

### Phase E2 — Kalshi collectors (landed; in-CI verification pending)

- `scripts/collect_kalshi.py` on the existing hourly cron
  (`collect-odds.yml` pattern, own workflow file): snapshot the open
  board for the mapped series (`limit=1000` + cursor), bank raw JSON +
  normalized parquet to `artifacts/kalshi/…`, tagged `snapshot` per
  `collect_historical_odds.py` (the column `archive.select_boards`
  splits on) plus `collected_at`/`league` per `collect_theoddsapi.py`.
- `scripts/collect_kalshi_candles.py` (daily): for markets settled since
  the last run, pull 1-minute candles for the pre-close day plus hourly
  candles for the market's life, and bank them — this is the CLV
  archive. **Probe verdict (2026-09-09): the `/historical` archive is
  dense** — hourly candles and trades verified for Sep 2025 → Jan 2026
  playoffs, both leagues; the earlier "empty" result was a zero-volume
  voided duplicate market, not an archive gap. So multi-season hourly
  backtests are backfillable later; bank-forward remains primary
  because pre-cutoff **minute** resolution is unverified and the honest
  close wants minute granularity.
- **Archive durability is an exit criterion, not a default.** Unlike
  The Odds API, expired exchange data cannot be re-pulled, and Actions
  artifacts expire (30-day retention on `collect-odds.yml`, 90 on
  `collect-historical-odds.yml` — and nothing in the repo consolidates
  them). Exchange collectors use ≥90-day retention **plus** a scheduled
  consolidation job that merges aging artifacts into a rolling
  long-lived archive artifact (or, if the ToS read under D7 allows, a
  private dataset). A 30-day artifact stream would silently starve E7.

Exit: two workflow_dispatch runs verified; candle → `Lines` close rows
feeding `pit.closing_line` proven on one settled game; the consolidation
job demonstrated on real artifacts; the new collectors documented in
DATA_PROVIDERS.md.
**Landed:** `collect_kalshi.py` + `collect_polymarket_raw.py` (hourly,
`collect-exchanges.yml`), `collect_kalshi_candles.py` (daily,
`collect-kalshi-candles.yml`), `consolidate_exchanges.py` (weekly,
`consolidate-exchanges.yml`, rolls the newest previous archive
forward). Board + raw collectors verified end-to-end from the sandbox
(keyless, so no dispatch needed for the client — the dispatch runs
still verify the Actions plumbing); the close path is proven offline:
`normalize_kalshi_candles` on a real settled game's frozen candles
feeds `pit.closing_line` and picks the last pre-kickoff minute.
Remaining for the exit: the dispatch runs and the first consolidation
over real artifacts.

### Phase E3 — Polymarket ingest adapter (done)

`velocity/ingest/polymarket.py`, same two layers:

- Pure `normalize_polymarket_event(event_json, books)` → `Lines`:
  Gamma's nested markets filtered by `sportsMarketType` to the D4 map,
  `line` → point (D5 assertion), slug parser
  (`nfl-ne-sea-2026-09-10` → teams/**UTC** date), executable asks taken
  from CLOB `/book`'s best **ask** per outcome token — never
  `/price?side=BUY` (verified: that returns the best *bid*) and never
  the Gamma `outcomePrices` mid (D2). A fixture test pins the
  normalized price to the book ask, not the bid or mid.
- `PolymarketClient`: key-less; Gamma events by `tag_id` (450 NFL,
  100351 CFB) + batch CLOB books (≤500 tokens/POST); token-id ↔
  (market, side) plumbing kept in the normalized frame so E4's history
  pulls don't re-resolve it.
- Props normalizer where `sportsMarketType` matches `PROP_MARKETS`.

Fixtures: frozen Gamma events + a books payload; tests as E1. Exit:
suite green; ToS read.
**Done:** `velocity/ingest/polymarket.py` (15 offline tests; live board
pull normalized 17,425 game lines + 4,936 prop lines across 353 games).
Findings from the live data that shaped the code:
- Spread `line` is always signed from `outcomes[0]`'s perspective
  (2,735/2,735 markets agree with the question text), and the slug's
  `-spread-home-`/`-spread-away-` token says which team that is.
- **Every** line is a half-integer (8,422/8,422) — D5 holds here too.
- Sides are the slug's **team codes**, not display nicknames:
  Polymarket's own labels are not internally consistent (one live event
  quotes `Texans` on the moneyline and `HOU` on the spread), and
  `canonicalize_sides` matches exactly, so nicknames would silently drop
  rows. Frozen as a regression fixture.
- Kickoff comes from each market's `gameStartTime`, so no ET/UTC date
  join is needed after all — the slug date is only an identifier.
- Book levels arrive **worst-first**: the best ask is `min(asks)`, not
  `asks[0]`. Gamma's `bestAsk` field covers only `outcomes[0]`.
- CFB carries no yardage/reception player props (only anytime- and
  team-touchdown markets, both unmapped for want of a line).

### Phase E4 — Polymarket collectors (board half landed; closes pending)

- Board snapshots on the hourly cron (events + batch books →
  raw + parquet, private artifacts) — this **is** the spread/liquidity
  history, since the CLOB keeps no historical books. The E2
  durability rule applies verbatim: ≥90-day retention + the
  consolidation job, because this data is unrecoverable by definition.
- Closing lines: at grade time, `prices-history` with `endTs = kickoff`
  (≤1-week windows, trim the appended current-time point) as the
  fallback close where the hourly board missed the last pre-kickoff
  snapshot.

Exit: workflow verified; one game's close recovered both ways and
agreeing within tolerance; collectors documented in DATA_PROVIDERS.md.
**Landed (board half):** `scripts/collect_polymarket.py` now banks raw
Gamma events + CLOB books *and* normalized `Lines`/`PropLines` parquet
on the hourly `collect-exchanges.yml` cron, tagged like the Kalshi
board. Still pending: the `prices-history` fallback close and the
two-ways agreement check.

### Phase E5 — Fee-aware EV & point-aware fair pairing (done)

- **The pairing fix (D2's debt, blocking for ladders):** the devig
  snapshot key in `build_slate` (`velocity/wagering/slate.py:218-222`)
  and `props_slate` gains the contract identity — the
  **home-perspective point** (`point if side == home else −point`;
  totals/team totals share the raw point; moneyline keys on `None`)
  for game markets, `(market, player, book, timestamp, point)` for
  props — so each ladder rung devigs against its own opposite side,
  never a neighbor rung and never the other team's equal-|strike|
  ladder (D2's `KC8`/`DEN8` case; plain `abs(point)` would collide
  them). Sportsbook feeds carry one main line per bucket today, so the
  change is behavior-preserving there; a regression test pins that, and
  a ladder test pins per-rung pairing including the equal-|strike|
  collision.
- `velocity/wagering/fees.py`: venue-keyed taker-fee functions
  (D3 table) + `fee_adjusted_net_payout(prob_cost, venue)`; Kalshi
  per-series overrides fetched by the collector, not hardcoded.
- `expected_value`/`kelly_fraction` grow an optional fee-aware path
  (default `fee=0` keeps every existing caller and test byte-identical);
  `SlateConfig` maps `book → venue fee schedule`; `evaluate` gates on
  fee-adjusted EV for exchange books.
- Tests: hand-computed Kalshi (yes-ask 0.19 → fee ≈ $0.010773 at
  `fee_multiplier` 1 — the micro-ceil, not 2¢) and Polymarket examples;
  a devig test proving yes-ask + no-ask overround strips to sane fairs;
  property test that `fee=0` reproduces current outputs exactly.

Exit: suite green; a worked example in the doc showing edge → EV → stake
for one real Kalshi market.
**Done.** `contract_key` (`velocity/wagering/slate.py`) joins the devig
bucket key in both the game and prop paths, and
`velocity/wagering/fees.py` carries the venue fee table with the
fee-adjusted payout threaded through `expected_value`, `kelly_fraction`,
`evaluate`, `stake_fraction` and `stake_amount` (all defaulting to no
venue, so sportsbook math is bit-identical — pinned by a test).
`SlateConfig.charge_exchange_fees` turns the fee off for modelling a
maker fill. Two notes on what the work actually taught:
- `abs(point)` really is insufficient, as the review warned: both teams
  ladder at the same absolute strike, so the key is the **home-perspective
  point**. The ladder tests were checked against the pre-fix code and do
  fail there — a symmetric board hides the bug, so they use asymmetric
  rungs.
- The fee's micro-ceil has to round the increment count before ceiling:
  an exact $0.0175 lands a few float ulps above 17,500 increments and
  would otherwise bill $0.017501.

Worked example (a real Kalshi market): a $0.19 yes-ask is +426 American;
the fee is `0.07 · 0.19 · 0.81 = $0.010773`, so the cost is $0.200773 and
the payout per unit staked falls from 4.26 to 3.98. A model probability
of 0.24 still clears the gate, but stakes materially less than the same
edge at a sportsbook.

### Phase E6 — Slate & live wiring (done)

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
**Done.** `velocity/ingest/exchanges.py` assembles one shoppable board;
`run_live_slate --exchanges` prices it alongside the sportsbooks (paper
only, best-effort per venue). The wiring turned out to be mostly an
identity problem, and the live run found a real bug the fixtures could
not:

- **Venue ids and team codes had to be reconciled before boards merge.**
  Every venue invents its own game id, so the same game arrived three
  times under three names and no price was ever shopped across venues.
  `align_game_ids` re-keys a venue's rows onto the sportsbook board by
  team pair and kickoff, with a generous window because Kalshi dates
  are ET and Polymarket's UTC.
- **Team codes are venue-specific and collide**: `sdst` is South Dakota
  State on Kalshi and San Diego State on Polymarket. So each venue's
  alias table is built from *its own* display names and applied only to
  its own rows (`exchange_aliases` + `apply_team_aliases`), never
  merged. Deriving codes from the payload rather than hand-keying them
  lifts NCAAF resolution from 8-12% to 97-98%; NFL codes mostly are our
  rating keys already, needing only `LAR→LA` and `JAC→JAX`.
- **Kalshi's `game_id` was series-scoped, which silently stranded every
  ladder.** Kalshi files each market type under its own event ticker
  (`KXNFLGAME-…` vs `KXNFLSPREAD-…`), so ladder rows never matched the
  winner event that names their teams: a live board yielded **64 lines
  instead of 1,585**. `game_id` is now the series-independent
  date-and-teams key, with a regression test. Fixtures caught none of
  this — only running the real board did.

Live proof: 32 NFL games quoted by both venues, Kalshi winning 1,239 of
the best prices and Polymarket 2,000; at the −14.5 rung of one game
Kalshi's +178 beats Polymarket's +163 — the cross-venue shopping this
phase exists for.

### Phase E7 — CLV & backtest (machinery done; graded week pending)

- **Point-aware closes (blocking for ladders, like E5's pairing):**
  every closing-line key in the loop is point-blind today —
  `pit.closing_line` groups by `(game_id, market, side, book)` and
  keeps `tail(1)` (`velocity/store/pit.py:51`), `_closing_for` matches
  the same way (`slate.py:342-356`), and `grade_yesterday`'s consensus
  medians point and price across all rungs. With 25 rungs per
  game/market/book that discards 24 closes, can assign a −20.5 bet the
  −1.5 rung's close (~19 points of fictitious CLV), and lets
  un-excluded rungs "bet the close" in backtests. The point (and for
  spreads the home-perspective point, per E5) joins all three key
  paths, with tests, before any exchange CLV number is read.
- `grade_yesterday` extended to grade exchange game-market rows via
  `Bet.grade`, **plus the winner tie branch** (Kalshi ties resolve at
  $0.50/contract, not a push — D5); exchange prop rows grade through
  the existing props path (`grade_prop_ledger`), not `Bet.grade`.
- **Close provenance is recorded per row** (board-ask vs
  Polymarket-history-mid vs Kalshi-candle bid/ask): ask-entry vs
  mid-close CLV is biased by ~half the spread, venue-asymmetrically —
  exactly the bias that would corrupt the venue comparison below. CLV
  is computed like-for-like; mixed-basis rows are excluded from the
  cross-venue report.
- Backtest: `backtest/archive.py` entry/close split over the banked
  snapshots, with E5 fees applied to simulated fills.
- The report this build exists for: **model vs exchange close vs book
  close** — is the exchange close sharper or softer than the sportsbook
  close, per market type? That answer decides how much origination edge
  the venue class actually offers and feeds EDGE_RESEARCH.

Exit: one full graded week of NFL/NCAAF with cross-venue CLV in the
eval output.
**Done, except the graded week itself**, which needs the collectors to
have banked a week of snapshots against settled games — that waits on
the merge, not on code.

- **Closes are point-aware, with the sportsbook path deliberately
  unchanged.** `LADDER_BOOKS` (schema) names the venues whose every
  number is its own contract. For them `pit.closing_line` keys on the
  point and `_closing_for` matches the rung exactly; for a sportsbook
  both stay loose, because its close *is* the same market at whatever
  number it moved to — which is precisely what `line_clv` measures.
  Tests were run against the pre-fix code and do fail there.
- **Exchange rows stay out of the sportsbook consensus.** An
  executable ask on one rung is a different price basis from a two-way
  main-line quote, so folding them into `grade_yesterday`'s cross-book
  median would have silently shifted the existing CLV benchmark and
  medianned a point across a whole ladder.
- **Dead heats settle, they don't push.** A tied game pays $0.50 a
  contract on an exchange, so a ticket bought at 19¢ returns +163% and
  one bought at 82¢ loses 39%. `Bet.grade` returns a `tie` result for
  ladder books, and the backtest counts it toward ROI while `hit_rate`
  correctly ignores it (it was neither a win nor a loss).
- **Every row records its `price_basis`.** Comparing an ask entry to a
  mid close is biased by roughly half the spread, venue-asymmetrically
  — the exact bias that would masquerade as a sharpness gap in the
  report below. `velocity/eval/venues.py` de-vigs each venue's closing
  pair, refuses to mix bases by default, and scores each venue's Brier
  against outcomes.
- `contract_key` moved to `store/schema.py`, since de-vig pairing (E5)
  and venue comparison (E7) both need the same contract identity.

Live cross-validation (no settled outcomes yet, so this is agreement,
not sharpness): across **620 contracts quoted by both exchanges**, the
de-vigged fair probabilities differ by a mean of 0.0000 and a median of
under one point — two independently built pipelines, from different
APIs, landing on the same number. Building the report also caught a bug
in it: the basis was read destructively, mislabelling exactly half the
rows `mixed` and silently halving the comparison set. Only running it
on the real board showed that.

### Phase E8 — The ladder calibration gate (research, blocks ladder betting only)

The sim is a rounded bivariate normal; its own docstring
(`velocity/models/simulate.py`) calls the key-number treatment
first-order. Ladder contracts at 3/7 concentrate value exactly where a
smooth normal misplaces mass — and the same tail-honesty question
applies to every market type's far rungs, not just spreads: the
exchanges list total ladders from ~33.5 to ~63.5, far beyond the
main-number band `min_total_disagreement` was calibrated on. Before any
**ladder rung outside the historically backtested band of its market
type** qualifies: calibrate sim tail probabilities against empirical
NFL/NCAAF margin and total distributions at each half-point strike;
whitelist only strikes where the sim is honest (expectation: main
numbers and near-band rungs per existing evidence; spread rungs
±2.5–7.5 suspect for key-number mass, deep tails suspect everywhere);
wire the whitelist as a **new** `SlateConfig` concept — point-level
filtering per market/venue, which doesn't exist today
(`exclude_markets` is market-level, `min_total_disagreement` gates
disagreement, not strikes — those two are the design precedents to
extend, not reuse). Moneylines and main-number lines are not gated —
they were priced credibly before this build.

## 4. Explicitly out of scope

- **Execution/trading** on either venue (the WAGERING W1 bankroll ledger
  plus the per-venue caps D6 proposes for the W2/W5 family are
  prerequisites; also the legal flux below).
- **Novig and ProphetX** — the other two venues EDGE_RESEARCH's exchange
  class names. Neither publishes a documented free public market-data
  API comparable to the two wired here (unverified beyond absence of
  docs; revisit if that changes).
- **Polymarket US (QCX)** — separate venue, separate liquidity, no
  documented public data API; the global exchange's data is what we read.
- **WebSockets** — both venues stream, Kalshi's needs a key; hourly REST
  plus candles covers projections and CLV. Revisit only for in-game.
- **Derivative game markets** (halves, quarters, exact margin, first
  TD): need period-level or drive-level simulation that does not exist;
  a separate research line, not an ingest problem.

## 5. Risks & open questions

- **Kalshi deep history: available at hourly resolution.** The
  2026-09-09 probe verified dense `/historical` candles and trades back
  through Sep 2025 (the earlier empty spot-check was a zero-volume
  voided duplicate market). Minute resolution pre-cutoff is unverified,
  so bank-forward stays primary for honest closes; a backfill script
  against `/historical` is unblocked for hourly-resolution backtests.
- **Kalshi's terms bind the archive to our own trading.** Storage is
  permitted only as trading facilitation (D7); no dataset built on
  Kalshi data can ever be published from this repo, and Kalshi data
  must not be used to train models — quotes are inputs to de-vig, EV,
  and CLV accounting only. A written license is the path to anything
  broader.
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
- **Artifacts are not an archive.** Exchange data, once expired, cannot
  be re-pulled (unlike The Odds API) — hence the E2/E4 consolidation
  job and ≥90-day retention as exit criteria, and the standing rule
  that the consolidated archive's continuity is checked whenever the
  collectors change.

## 6. Immediate next step

E1 — the Kalshi ingest adapter, fixtures first. It is the smallest slice
that proves the whole path (ticker → teams → `Lines` → devig → EV) on
real exchange data, and everything after it reuses its decisions.
