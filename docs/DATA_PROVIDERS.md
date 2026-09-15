# Live data providers — BettingPros, The Odds API, FantasyPros, ESPN

Three paid feeds sit behind the wagering stack. They serve different jobs, and —
critically — they must never write into **git**: provider terms forbid
redistributing their odds, committing them would leak our edge, and git history
is permanent and clonable in a way an artifact is not. All
paid data lives only in **GitHub Actions artifacts**, never in git.

> ## ⚠️ An Actions artifact is not a private place
>
> This document called them "private Actions artifacts" everywhere, and so did
> most of the collectors. **That word was load-bearing and it was wrong** — it
> is the reason banking a raw provider payload verbatim looked safe.
>
> An artifact's audience is exactly whoever can read the repository, plus a
> 30-day clock. While this repo was **public**, that audience was the public
> internet. There was never a private tier to fall back on; the levers are
> repository visibility, artifact retention, and not putting the secret in the
> artifact to begin with.
>
> **The repository went private on 2026-09-15**, which closed that audience.
> The guidance below is what still applies, because a private repo is a
> narrower audience and not a safe one — anyone with repo access still reads
> every artifact, unmasked.
>
> ### What is established, and what is not
>
> Precision matters here because the first write-up of this overstated its
> evidence, and a doc about not fooling yourself is a bad place to do it.
>
> **Established.** The BettingPros partner key and user id were written into
> **89 unexpired artifacts**, from 2026-08-28 until found. Read directly out of
> the banked zips; this does not depend on who could fetch them. BettingPros
> echoes the request URL — credentials included — back inside its `/props`
> response, and the collector banked that payload verbatim. `scrub_secrets()`
> (#197) stops new ones and cannot un-bank the old.
>
> **Not established.** Whether an *unauthenticated* stranger could have
> downloaded them. #198 claimed that, citing a `curl` with no token that
> returned the real zip. **That test was invalid**: it ran inside an agent
> sandbox whose proxy injects GitHub credentials into every outbound request.
> The tell was available and unread — an anonymous `api.github.com` call has a
> 60/hour rate limit, and that environment reports 15,000.
>
> So the floor is "every GitHub user who could see this repo", which for a
> public repo is a very large number. The ceiling — genuinely anonymous — was
> never demonstrated either way, and the window to test it closed with the
> repo.
>
> **The transferable lesson**, worth more than the resolved question: *a
> negative-access test run from an environment with credential injection proves
> nothing.* If a check is meant to show something is unreachable, run it from
> somewhere with nothing to inject, or verify the request was actually
> unauthenticated before believing the result.
>
> ### What the policy still gets right
>
> "Never commit paid data" remains correct and worth keeping: git history is
> permanent, clonable and mirrored, while an artifact expires in 30 days and
> can be deleted. Artifacts are a *shorter-lived* place, not a *private* one —
> that is the distinction the word "private" was hiding.
>
> ### What to check before adding a collector
>
> Not "is this artifact private". Ask instead: does the payload **echo
> credentials back**? Does it carry **more than the normalizer needs**? A
> provider that reflects your request is more common than it sounds, and the
> raw bank is the place it lands.
>
> Actions *logs* mask registered secrets (`BP_API_KEY: ***`). Artifacts get no
> such masking. That asymmetry is why the leak was invisible: the logs looked
> clean because they genuinely were.

| Provider | Job | History? | Secret(s) | Limit |
|---|---|---|---|---|
| **BettingPros** | Live multi-book **game lines** (spread/total/moneyline) + player props — NFL, NCAAF, MLB | ❌ live only | `BP_API_KEY`, `BP_USER_ID`, `BP_USER_KEY` | 5k calls/day |
| **The Odds API** | Historical + live odds, **line archive for CLV/backtest** | ✅ | `THE_ODDS_API` | 100k credits/month |
| **FantasyPros** | Consensus **player projections** (prop inputs) — NFL | partial | `FP_API_KEY` | not published |
| **ESPN** | **Injury reports** (all six leagues), **rosters and depth charts** | ❌ live only | *none — keyless* | none published |

The secrets are configured as **GitHub Actions repository secrets** (see the repo
Settings → Secrets and variables → Actions). They are injected only into workflow
runs — the local dev sandbox never sees them, which is why the collector runs as
an Action, not from a checkout.

## Why each provider

- **BettingPros** is the production line feed. It has **no archive** — a line
  exists only while it is live — so line *history* has to be built by snapshotting
  the current board on a schedule (see the collector below). It covers NFL,
  NCAAF and MLB, and (premium tier) carries its own projections.

  MLB joined on 2026-09-14. It had been outside `SPORTS` since the collector was
  written, which meant the in-season league carrying the largest real exposure
  (`docs/WAGERING.md` §1.3) was the one league with no second line feed — while
  the daily call spend sat at about 1% of a 5,000/day cap. Its prop board joined
  `PROP_SPORTS` at the same time; the `/props` sport enum had listed MLB all
  along and nothing had ever asked for it.
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
2. runs `collect_bettingpros.py`, which snapshots NFL + NCAAF + MLB game lines into a
   single timestamped parquet under `artifacts/bp/`, tagged with `league` and
   `collected_at`,
3. uploads that parquet as an **Actions artifact** (`retention-days: 30`) — see the
   visibility note at the top of this document.

It never commits. `artifacts/` is gitignored so a local run can't leak paid data
into git either. Off-season / empty boards are a success, not a
failure — the job still writes an (empty) artifact so the schedule keeps running.

At 3-hour cadence the collector uses ≈48 BP calls/day, far under the 5k/day cap;
tighten the cron toward kickoff windows in-season if closer-to-close snapshots
are wanted for CLV.

## The Odds API collector (`scripts/collect_theoddsapi.py` + workflow)

`.github/workflows/collect-odds.yml` runs hourly (and on manual dispatch). It
snapshots the live board for both leagues into `artifacts/odds/*.parquet` (tagged
`league` + `collected_at`), uploads it as an **Actions artifact**, and
prints the remaining monthly credits each run. Live `/odds` for 2 leagues × 3
markets is ≈6 credits/run → ~4.3k/month, well under 100k; true historical backfill
uses the pricier `/historical` endpoint on demand. Same rules as the BP collector:
never commits, `artifacts/` gitignored, empty boards succeed.

**Regions and the sharp close (2026-09).** The collector takes `--regions`
(workflow input `regions`, default `us`). `us,eu` adds Pinnacle, whose close
the grader now prefers as the CLV yardstick when it is on the board
(`close_source = "sharp"`, else the cross-book `"consensus"`;
`docs/SYSTEM_REVIEW.md` §4.3) — and doubles the credits per pull (one per
market per region). At the current cadence that is ~9k/month for two
leagues, ~25k for six: inside the 100k budget, but a standing cost, so the
switch is a dispatch input rather than the default. Flip it when the CLV
record is worth grading against the sharpest number.

### What consumes a FantasyPros snapshot (and what does not)

NFL projections feed three things: the correlated football prop sim
(`models/props_football.py`), the DFS builds, and the projection-time QB
starter map (`features/starters.py`). NFL injuries feed that starter map and
the intel injury signal.

**MLB projections feed nothing, and that is the right answer.** They have been
collected since the collector was written — up to ten requests a run once the
per-position fallback fires — and read by nothing, while a comment in
`live-slate.yml` asserted the public tier serves no MLB players at all. Nobody
was wrong on purpose; the contradiction simply had nowhere to surface. It does
now: the collector emits an Actions **warning** naming any league that returns
zero rows, and prints a standing note for any league in `UNCONSUMED_LEAGUES`.

The reason MLB stays unconsumed is that better sources already exist for both
consumers. MLB DFS prices from the contextual model (banked box scores × park ×
lineup slot × today's probables, `docs/DFS_MODEL.md`) and MLB props from the
banked starters frame — season-total consensus projections cannot improve
either. Keeping the branch costs a handful of requests against a limit that has
never bound; the decision to drop it belongs to whoever reads that warning.

A related trap closed at the same time: the runner chose its prop model from
whether `--fp-projections` was passed, not from the league. Since a snapshot
carries every banked league, passing it for MLB took the *football* prop path
on baseball players and — the dispatch being an if/elif — silently skipped the
pitcher-K slate MLB actually has. The gate keeping that from happening lived in
a shell conditional in `live-slate.yml`. It is now `FOOTBALL_PROP_LEAGUES` in
the runner, with a test that fails if the guard is removed.

### What the first coverage run found (2026-09-15)

The slug report shipped in the previous change, and its first live run turned
up three things — two of them larger than the question it was built to answer.

**1. The prop board was being truncated by ~85%, silently.** BettingPros caps
`/props` at **200 rows a page** and ignores a larger `limit` (the echoed
`_parameters.limit` reads 200 however big the request was). The collector asked
for 5,000, got 200, and banked it as the board. The response says so plainly in
`_pagination`, which nothing read:

| sport | banked | actually available | |
|---|---|---|---|
| NFL | 200 | 940 (5 pages) | **21%** |
| MLB | 200 | 1,635 (9 pages) | **12%** |

`props_all()` now follows `total_pages` to the end, with `--max-prop-pages` as
a budget guard that emits an Actions warning when it bites — a truncated board
should never again look like a small one. Cost: ~14 calls a run instead of 2,
against a 5,000/day cap.

**2. The partner key was being written to disk on every snapshot.**
BettingPros echoes the full request URL — `key`, `user` and `auth` included —
back in `_pagination.self`, and the collector banked the raw payload verbatim
into an artifact that outlives the run by 30 days. `scrub_secrets()` redacts
those parameters everywhere they appear before anything is written. It runs per
page inside `props_all` and again at the write, because a credential leak is
worth two passes.

**3. The slug table was about half right, and mostly for reasons no mapping can
fix.** Of its five reasoned entries, `passing-touchdowns` and `receptions`
served **zero rows**, while the single biggest slug on the NFL board —
`rushing-receiving-yards`, 38% of it — was absent from the map entirely. On the
MLB board all 11 slugs were unmapped and every row abstained.

But only one of those was a mapping gap. `strikeouts` → `pitcher_strikeouts` is
now mapped, confirmed against the live board. Everything else abstains
correctly:

- `rushing-receiving-yards`, `runs-hits-rbis`, `passing-attempts` are combined
  or unmodeled markets with no counterpart in `PROP_MARKETS` — they need a
  model before they need a mapping.
- `total-bases` is the largest MLB slug and `PROP_MARKETS` excludes it
  deliberately: the walk-forward found it losing at every shrink
  (`docs/WAGERING.md` §1.3). Mapping it would arm a signal for a market we
  refuse to bet.

Note the counts above are page-1 samples, taken before pagination landed. The
next run measures the whole board, and the slug mix may look different once
85% more of it is visible.

## The FantasyPros collector (`scripts/collect_fantasypros.py` + workflow)

`.github/workflows/collect-fantasypros.yml` runs weekly (and on manual dispatch).
It snapshots consensus projections for both leagues into `artifacts/fp/*.parquet`
(long `(player, stat, value)` rows tagged `league` + `collected_at`) and uploads a
**Actions artifact**. The manual dispatch runs with `--inspect`, which
prints the raw top-level keys and first-player JSON to the log — that's how we
verify the `FP_API_KEY` secret and tighten the tolerant normalizer against the
real response. Same rules: never commits, `artifacts/` gitignored.

## ESPN (`velocity/ingest/espn.py`)

Injuries reached this system from one place — FantasyPros, NFL only — so the
intel layer's availability signals, the ones that veto a bet when the player it
depends on is not playing, **abstained on every other league**. An MLB card was
priced with no idea a listed starter was on the 60-day IL.

ESPN publishes an injury report per sport at
`site.api.espn.com/apis/site/v2/sports/{sport}/{league}/injuries`. No key, no
account, no quota, and it covers all six leagues we price. Measured on the
first collection run: NFL 800 rows / 161 outs, MLB 283 / 272, NHL 85 / 44,
WNBA 42 / 42, NCAAF 3 / 1, NCAAB 0.

### The 403 was never about the IP

`build_wnba_box.py` records that "ESPN's own edge 403s datacenter IPs" and
routes around it through a sportsdataverse mirror. That conclusion was drawn
with a **browser-impersonating** User-Agent — the script sets `Mozilla/5.0
(X11; Linux x86_64) velocity-datasets` — and that is the thing being refused.
A real browser running in a data center is exactly the shape of a scraper.

Measured 2026-09-14, same host, same second, same endpoint:

| User-Agent | Result |
|---|---|
| `Mozilla/5.0 (X11; Linux x86_64) velocity-datasets` | **403** |
| `Mozilla/5.0 (Macintosh; …) AppleWebKit/537.36` | **403** |
| `velocity-datasets/1.0 (+https://github.com/EdgeCash/Velocity)` | **200** |
| `Python-urllib/3.12`, `curl/8.5.0` | **200** |

So the rule is the opposite of the usual scraping instinct: **say what you
are.** A UA naming the project and where to complain is served normally.

If this endpoint ever starts returning 403, do **not** fix it by making the
agent look more like a browser. That is the arms race this document declines
to enter under PrizePicks, and it is precisely what gets refused here.

(The WNBA box-score scripts are untouched. Their mirror works, is stable, and
serves bulk season parquets this API does not — there is nothing to gain by
moving them.)

### Statuses, and the asymmetry that matters

Vocabulary is per sport: football says `Out` / `Questionable` / `Injured
Reserve`, baseball `10-Day-IL` / `15-Day-IL` / `60-Day-IL`, basketball
`Day-To-Day`. `OUT_STATUSES` plus an injured-list shape match cover all of it.

Two deliberate choices:

- **`Questionable` and `Day-To-Day` are not outs.** They mean *probably
  playing*, and the intel contract is that availability **vetoes** rather than
  nudges. Treating a game-time decision as an out vetoes bets on players who
  take the field.
- **An unrecognized status reads as available.** A missing out costs a signal;
  an invented one kills a good bet. MLB has changed its list lengths
  repeatedly, so the failure mode is real.

### Teams

ESPN supplies both an abbreviation (`ARI`) and a display name ("Arizona
Diamondbacks") because our leagues key differently — NFL by nflverse
abbreviation, MLB and WNBA by full name, college by school. Both are offered to
the same alias machinery every venue board uses, with two guards:

- **Scoped to one league.** ESPN calls the Cardinals `ARI` and the
  Diamondbacks `ARI` too. Resolving a multi-league bank in one pass hands one
  of them the other's injury list, and there is no collision to detect because
  there is only one `ARI` to go around.
- **Collision-checked.** The alias fallback is prefix matching, which is what
  lets "San Jose St." find "San Jose State" — and also what makes "Arizona
  Cardinals" match "Arizona Diamondbacks". Two ESPN teams claiming one model
  team is that misfire's signature, so neither is kept. Same-city pairs
  (Cubs/White Sox, Yankees/Mets) resolve exactly and are unaffected.

Everything that fails to resolve is dropped **and reported** — silently
invisible is how a whole league's report goes missing unnoticed.

### Rosters and depth charts

The NFL starter map (`velocity/features/starters.py`) worked out each team's
QB1 by **inference**: read FantasyPros' projected passing yards, take the
busiest passer. That is a decent proxy that fails exactly where it matters — a
stale projection, or a backup projected high in a week his starter is expected
to sit, and the inference disagrees with the depth chart. The depth chart is
the thing that knows.

ESPN states it. Two endpoints, joined locally:

| | endpoint | per call |
|---|---|---|
| roster | site API, `/teams/{id}/roster` | every athlete: id, name, position, jersey, ESPN's own status bucket |
| depth chart | **core** API, `/seasons/{year}/teams/{id}/depthcharts` | per position, athlete ids in rank order |

The depth chart names athletes **only by `$ref`**. Left to the API that is one
fetch per player — hundreds a team; joined against the roster it is free. A
league therefore costs 2 calls per team plus a teams listing (65 for the NFL),
against no quota at all.

Depth charts exist for **NFL, MLB and NHL**. WNBA 500s and college football
400s on that route, so those leagues collect rosters only — and the collector
stops asking after the first refusal rather than repeating it 30 times.

Two things the normalizer is careful about:

- **Formations.** ESPN files several units per team ("3WR 1TE", "Nickel",
  "Base 4-3 D"). Taking whichever came first in the payload silently answers a
  different question — a nickel package's corner ordering is not the depth
  chart — so `depth_by_position` prefers the ordinary unit.
- **Team codes.** ESPN writes `LAR`/`WSH` where nflverse writes `LA`/`WAS`.
  Those are the same two divergences FantasyPros has, so the existing
  `FP_CODE_FIXUPS` serves both.

**What it changed.** `starter_map` now takes `espn_depth` and prefers it per
team, falling back to the projection inference for teams a partial snapshot did
not reach. Every disagreement is logged, because each one is either a stale
projection or a starter change we would otherwise price blind. On the first
live run it corrected three teams the fit had wrong — including KC from Chris
Oladokun to Patrick Mahomes, the exact 5.6-points-a-game error
`docs/SYSTEM_REVIEW.md` §3.1 documents — and it did so with **no FantasyPros
key involved at all**: `--espn-depth-file` plus `--espn-injuries-file` is now a
complete starter map on its own.

### Collection and wiring

`collect-injuries.yml` runs `scripts/collect_espn_injuries.py` alongside the
FantasyPros step, which is marked `continue-on-error` so an expired
`FP_API_KEY` cannot take five leagues' only injury source down with it. The
runner takes `--espn-injuries-file` for every league and `--espn-depth-file`
for the starter map; where both injury sources exist
(the NFL) the frames are **concatenated, not reconciled** — the consumer takes
genuine outs and looks them up by team, so a player both feeds call out vetoes
once anyway, and reconciling two designations into a single truth is a
judgement neither feed licenses us to make.

## The PrizePicks collector (`scripts/collect_prizepicks.py` + workflow)

`.github/workflows/collect-prizepicks.yml` snapshots the pick'em board per
league — the raw material for the future slip-EV engine (devigged book props vs
the board line, priced through the correlated prop sim). The board API is
keyless; the client is deliberately polite (one request per league, spaced
pagination, backoff).

**Its schedule is off as of 2026-09-15** — `workflow_dispatch` only. It ran
3-hourly for 180 runs and banked nothing: every one logged the 403 below and
exited green, because a known block is a deliberate clean skip. That was right
while the block might have been transient. It is not, and a green run that
collects nothing looks exactly like a working one, which is the worst shape for
a job to sit in. Nothing downstream read the artifacts, so the ~40 minutes of
Actions time a week was pure cost — and it bills now that the repo is private.
The collector is unchanged and one dispatch away when the transport is solved.

**Transport status: blocked from datacenter IPs.** The endpoint sits behind
DataDome, and the first live dispatch confirmed GitHub Actions runners are
challenged (captcha 403). The collector treats a known block as a **green
skip** so the schedule keeps running and banks boards opportunistically.
Relay options, in preference order, when this pipeline gets prioritized:

1. **A licensed aggregator** that carries pick'em lines alongside books
   (e.g. SportsGameOdds' props feed includes PrizePicks/Underdog and a
   no-vig consensus) — cleanest legally, one normalizer swap.
2. **A residential-connection cron** running this same collector from an
   operator machine and uploading the parquet (the collector itself needs
   no code change — only the network it runs from).
3. Third-party scraper relays (Apify-style) — workable, but adds a paid
   dependency of unknown reliability.

What we do *not* do: fight the bot protection with headless-browser
evasion — an arms race with ToS problems on both ends.

## How this plugs into the stack

`BettingProsClient.game_lines` returns the same canonical `Lines` frame the
backtest already consumes, so it drops straight into
[`LiveOddsAdapter`](../velocity/ingest/odds.py) as the production `fetch`
callable — swapping the historical archive for the live feed is a config change,
not a rewrite. CLV is then the live snapshot vs the closing snapshot from the
archive.

### The BettingPros board on the live card

For most of this system's life the paragraph above was aspirational: the
collector banked `bp_lines_*.parquet` every three hours and **nothing read
it**. The live board came from The Odds API alone, so a BettingPros price was
never shopped, never logged and never graded — a paid multi-book feed
accumulating in artifacts nobody opened.

`bettingpros.bp_board` closes that. It takes the banked lines + events
parquets and re-keys them onto the board the slate is already pricing, reusing
the exchange alignment path (`docs/BUILD_EXCHANGES.md` E6) rather than
inventing a second one:

1. **League filter and age gate.** The board is refused whole if the snapshot
   is older than `--bp-max-age-min` (default 200, just past the 3-hour
   cadence), or carries no stamp at all — an age we cannot establish is
   exactly the failure the gate exists to prevent. A refused board says so on
   the run log; it is never silently dropped.
2. **Sides, in BettingPros' own scope.** BP labels a selection by *nickname*
   ("Chiefs") while its events name the team in full ("Kansas City Chiefs").
   Matched as plain strings, every spread and moneyline is dropped and a board
   of totals reaches the card looking healthy. `resolve_sides_within_game`
   matches the label against that game's own two teams by word-subset — a
   two-way choice from one payload, so a looser rule is safe — and drops a
   label matching both or neither.
3. **Teams, games, books.** Events resolve to rating keys, games re-key onto
   the base board's ids by team pair and kickoff, and every book is prefixed
   `bp:` (`bp:draftkings`, or `bp:10` where the `/books` listing did not
   resolve the id). The feed is part of the book's identity: BP quotes the
   same sportsbooks The Odds API does, and a grader must be able to tell which
   feed a number came from.

**Posture: paper.** The rows are priced, logged and graded at stake zero, per
S2 — money does not follow a market whose evidence is not in yet, and these
come off a snapshot up to a cadence old. `--bp-stake` flips that once the CLV
record says something; tighten `--bp-max-age-min` hard before you do, because
a banked price that has moved is not a price we can take.

The runner takes `--bp-lines-file` / `--bp-events-file` / `--bp-books-file`,
and `live-slate.yml` passes the freshest banked set automatically.

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
- **No paid odds/props data in git, ever** (ToS + edge leak; git history is
  permanent, and the repo's visibility can change under you — it did).
  Snapshots live only in Actions artifacts (not private — see the top).
- If a key is ever exposed (e.g. pasted into a chat), rotate it with the provider.

## Spending the odds budget once

The hourly collector and the live slate used to buy the same board twice: the
collector snapshotted it, and the slate then made its own `/odds` call for a
board that was already sitting in an artifact. `client.odds()` is only
`normalize_odds_events` over `client.odds_payload()`, so banking the raw
payload alongside the parquet costs **nothing extra** — and the raw form is
the one that carries the event metadata (teams, kickoff) a board needs.

`collect_theoddsapi.py` therefore writes `raw/odds_{league}_{stamp}.json` next
to its parquet, and `live-slate.yml` prefers the freshest banked payload over a
fresh pull. Two runs a day across six leagues stop paying for data bought an
hour earlier.

**With a freshness bound, deliberately.** Only a payload younger than
`board_max_age_min` (default 75, an hour's cron plus slack) is reused;
anything older falls through to a live pull. Prices move, and betting a line
that has already gone manufactures edge that was never available — the credits
saved are worth far less than one phantom bet.

Note the distinction the runner now draws: `--snapshot-file` means only that
the *sportsbook board* comes from a banked payload, while `--offline` means no
network calls at all. Exchange boards are free and keyless, so they are still
pulled on a banked-board run; the offline test suite passes `--offline`.

## Exchange collectors (Kalshi + Polymarket — free, keyless)

The prediction-exchange feeds (docs/BUILD_EXCHANGES.md) need no secrets — market
data on both venues is unauthenticated — but the storage policy is *stricter*
than the paid feeds', not looser: Kalshi's Developer Agreement permits storing
API data only to facilitate your own trading and bars sharing it in any manner,
so exchange data lives **only** in Actions artifacts, is never
committed, and is never redistributed (BUILD_EXCHANGES.md D7). Unlike The Odds
API there is no vendor archive to re-pull everything from, so the artifacts
*are* the record:

| Collector | Workflow | Cadence | Banks |
|---|---|---|---|
| `scripts/collect_kalshi.py` | `collect-exchanges.yml` | hourly (:10) | Kalshi board: raw `/markets` JSON + normalized `Lines`/`PropLines` parquet, tagged `snapshot`/`collected_at`/`league` |
| `scripts/collect_polymarket.py` | `collect-exchanges.yml` | hourly (:10) | Polymarket board: raw Gamma events + CLOB books, plus normalized `Lines`/`PropLines` parquet (no history exists upstream — every missed hour is spread history lost) |
| `scripts/collect_kalshi_candles.py` | `collect-kalshi-candles.yml` | daily 12:00 UTC | Settled-market candles (1-min pre-close + hourly life) raw, plus normalized close rows |
| `scripts/consolidate_exchanges.py` | `consolidate-exchanges.yml` | weekly Mon | Rolls all exchange parquet into one long-lived archive artifact before the 90-day per-run retention expires |
