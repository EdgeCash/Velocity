# The Velocity Site

A static [Evidence](https://docs.evidence.dev) build over the daily run's
parquet, deployed by the `live-slate` workflow to a Cloudflare Worker behind
Cloudflare Access. Total hosting cost: $0.

It is **one page**. That is the whole design, and everything below is a
consequence of it.

## Why one page

The site was ten pages, and it was the wrong shape for what it holds. A
game's projection was on one page, the price on another, the DFS plays from
that same game on a third, and what you actually had riding on it on a
fourth — so the only thing that could put those four back together was the
reader, with four tabs open. The data was never the problem; the
information architecture was.

The hub's unit is a **game**, and a game carries its own projection, its own
prices across sportsbooks and exchanges, its own DFS players, its own open
positions and its own live score. Opening one expands it in place. Nothing
navigates.

Four things are not games — the DFS slate, the position blotter, the graded
record and the power ratings — and they are **views** on the same surface
rather than pages. DFS in particular has to be a peer view rather than only
living inside game cards, because the two genuinely do not line up: a Friday
in September is an MLB and WNBA DFS slate against an NFL and college *board*,
so a DFS surface nested only in game cards would be empty on exactly the days
it has the most to say.

Adding a view has to justify itself against that. Market health did not, and
went inside Record; **ratings** did, because they are the only thing here
that answers a question no game card can — a card shows two teams, and "who
does the model think is good" is about all of them.

View, league filter and opened game live in the URL hash, so "no tab
switching" does not also mean "no back button" and a view is still something
you can link to.

## Tiers

Every price, edge, stake and bankroll number on this site is derived from a
**paid** odds feed, so the private tier deploys to ONE Access-gated host and
is never public.

The **public tier** is a build flag, not a second site:
`scripts/build_site_data.py --tier public`. It is enforced in the DATA BUILD
rather than in the pages, and that distinction is the whole point — a page
that merely declines to render a column still ships the column, sitting in a
parquet the browser downloads and anyone can open with DuckDB. The public
tier empties the private tables and blanks the private columns before
anything is written, so the bytes do not exist to leak.

One rule decides what is private: **anything derived from a paid or licensed
feed**. That is every sportsbook price and everything measured against one —
a de-vigged fair probability, an edge, a Kelly stake, closing-line value, the
bankroll those stakes move. What survives is the model's own output
(projections, simulated distributions, ratings, the win/loss result of a
graded bet) and the **exchanges**: Kalshi and Polymarket prices are public
market data and stay, with their prices intact, because a public tier that
stripped them would have no market on it at all.

Sportsbook ROWS are dropped rather than merely blanked — that a book has a
line on this game at all is the feed's information, and the row count alone
would carry it. `edge` cannot stay even on an exchange row, because
publishing it beside `p_model` lets the best paid price be solved for
exactly.

`tests/test_site_tier.py` checks all of this against the written frames, not
against the rendering.

## Layout

```
site/
  package.json            Evidence classic (static build) — lockfile committed
  evidence.config.yaml    theme: the trading-desk tokens from the mockup
  sources/velocity/       DuckDB source; one .sql per table over data/*.parquet
    data/                 assembled per-run by scripts/build_site_data.py (gitignored)
  pages/
    +layout.svelte        THE CHROME — and almost nothing else now: Evidence's
                          sidebar, header, breadcrumbs and TOC are all off,
                          because they are navigation for a thing with nothing
                          to navigate. What is left is the vendored numeral
                          face and the design tokens.
    index.md              THE WHOLE SITE: every query, then <Hub />.
  components/
    format.js             the number rules (a real minus, a sign on anything
                          coloured) — every component formats through it
    TeamMark.svelte       club logo on a plate, with the code chip beneath
    Hub.svelte            the one auto-imported name; wraps hub/Shell
    hub/
      Shell.svelte        chrome, view routing, league filter, live wiring
      live.js             ESPN scoreboards + box scores, joined to our game_id
      model.js            the joins: flat rows -> game objects (pure, tested)
      state.js            view/league/game in the URL hash
      Ticker.svelte       the scores crawl, in the top bar
      GamesPanel.svelte   the feed, grouped by day, live games first
      GameCard.svelte     a game row and its expand-in-place dossier
      Spark.svelte        one simulated distribution, drawn small
      BoxScore.svelte     live player stats, rendered from ESPN's own labels
      DfsPanel.svelte     lineups as a peer view
      PositionsPanel.svelte  the bet tracker, read against the live score
      RecordPanel.svelte  graded results, CLV first
      HealthPanel.svelte  the monitor's trailing per-market flags
      RatingsPanel.svelte every rated team, searchable, grouped by league
      ParlayBlock.svelte  cross-game parlays; a leg opens its own game
      CardShelf.svelte    the rendered PNGs, with their post captions
      Rail.svelte         bankroll, what is riding, what is live, what is flagged
  tests/                  node --test; the joins and the formatters
  static/                 favicon + icon set (the V drawn as a bankroll curve)
    cards/                newest-stamp card PNGs (gitignored, per-run)
  worker.js + wrangler.toml + deploy.sh   Cloudflare deploy + /api/scores
```

## Live scores and box scores

ESPN's public JSON sends `access-control-allow-origin: *` but its Akamai edge
**403s datacenter IPs** — Cloudflare Worker egress included. So the browser
fetches ESPN directly (viewer IPs are served fine) and the Worker's
`/api/scores` proxy is only a fallback for a viewer whose own network blocks
it. `site/components/hub/live.js` holds all of it, as one store, so a score in
the ticker and a score on a card cannot disagree.

Three things it does that the old marquee ticker did not:

- **The right dates.** The scoreboard defaults to today in US/Eastern, so a
  Friday board carrying Sunday's NFL slate would come back empty. The dates
  are driven by the games we hold.
- **The whole college board.** College football and men's college basketball
  default to the top 25; `groups=80` / `groups=50` opens them to every FBS /
  D-I game, which is most of our board.
- **A join back to `game_id`.** A score is only useful here if it can be
  attached to the projection and to an open position, and the two sides name
  teams differently. `indexGames` registers every spelling we can derive
  (our own team strings and the identity table's codes) and ESPN is tried
  against all of them, so the match never rests on one being right.

The **box score names no columns**. ESPN's summary endpoint returns each stat
block with its own `labels` array and this renders those — so it works for
baseball's AB/R/H/RBI and basketball's MIN/PTS/REB with no per-sport table,
and a column ESPN renames shows up renamed rather than silently mislabelling
a row of numbers. It fetches only while a game is open, and polls only while
that game is actually in progress: a college Saturday is eighty games, and
one summary request each on load would be eighty requests to a public
endpoint.

## Venues

Sportsbook and exchange prices ride the **same board** — there is no separate
Kalshi page, because an exchange contract competes for the same bet as a
sportsbook line and the only question that matters is which venue has the
better number. The `venue` column carries `sportsbook` or the exchange's own
name, and the Board table and the play card both show the venue in place of
the book whenever it is not a sportsbook.

Exchange rows are **priced and graded at stake zero** by default
(`docs/BUILD_EXCHANGES.md` E6), so they appear on the board with a `paper`
status and never on Today's card.

## The design

Dark only, on purpose: `appearance.switcher` is off and every surface is
picked for the near-black ground. A light mode would be a second design to
keep honest for no one.

The reference is the genre, not a BI tool. What follows is the short version
of what a scouting pass across Outlier, BettorSheets, Mongoose Bets, the
sportsbook apps and the pro odds screens found to be *shared* by all of them,
and which of it this site adopts.

### The board face

Every serious board in this genre licenses a **condensed athletic grotesque**
for its numerals — DraftKings ships Saira Condensed — and prose lives in a
separate humanist sans. That single split does more for "this is a board"
than any amount of colour.

`Saira Condensed` (500/600/700, latin subset, ~54KB) is **vendored** into
`site/static/fonts/` rather than fetched from a font CDN: a private board
should not put a third-party request in front of every page load, and a cold
cache would otherwise render the whole board in the fallback face. It is
bound to `--v-board` and applied to every quantity — Evidence tags each cell
with its column type, so `td.number` reaches every number in every table
without a page having to ask.

### Legibility beats density

An earlier pass set table rows at `0.42rem` padding and called it a
blotter. It was measured against real reference boards — BettorSheets,
Outlier — and they run roughly double that. A row nobody wants to read is
not information density, it is just small. Rows are `0.78rem`, the board
face sits at `1.02rem`, and the table body at `0.86rem`.

The same rule decides what a cell contains. `46bb732d224f9da07f9e3bb2f32281cc`
is not a bet; **Cleveland Browns @ Jacksonville Jaguars** is. The ledger
records only a `game_id`, so `game_directory()` in
`scripts/build_site_data.py` walks every `games_*.parquet` it can reach —
today's slate *and* the previous ones, because a bet placed two days ago
still needs its name — and open positions carry the matchup from there.
A bet is also spoken as one string (`OVER 8.5`, `HOME +1.5`), not spread
across four columns of market/side/line.

### Six rules the pages inherit from `pages/+layout.svelte`

1. **Numbers are the product, and the typography says so.** The board face,
   tabular figures, and a value roughly twice the size of the micro-label
   under it. That label-over-value pair is the atomic unit of the design.
2. **A negative price is not a loss.** Prices are sign-explicit and set with
   a **real minus (U+2212)**, never a hyphen — a hyphen is narrower than a
   digit, so a column of `−110` beside `+140` visibly fails to align. And
   American prices are never grouped: `+2400`, not `+2,400`.
3. **Money colour is never the only channel, and red is held in reserve.**
   Profit green and loss salmon are indistinguishable under deuteranopia, so
   every number wearing them also carries a sign. The loss colour is
   **salmon `#f97289`, not red**: it sits beside a positive on nearly every
   row, and a fire-alarm red there makes an ordinary losing market look like
   a fault. True red (`--v-alert`) is spent on the kill switch alone.
4. **One lit object.** Depth is a five-step near-black ladder inside a 20-value
   luminance range with white-alpha hairlines, so almost everything is
   dark-on-dark; exactly one thing per list gets the inverted brand pill — the
   best-priced venue on a market, the live game in the feed. A list where
   every price is filled is the genre's clearest cheap tell: if everything is
   lit, nothing is.
5. **Nothing clips.** The content column and its wrapper are flex children
   with `min-width: 0`, and every table scrolls inside its own box, so a wide
   blotter never steals the page's width — which is exactly what made the old
   board show two columns on a phone.
6. **Empty is a designed state, and so is low confidence.** Half the surface is
   fed by the morning grade, so most of the day something is legitimately
   empty; every panel carries its own empty state that says *why* it is empty,
   and the framework's red error boxes are suppressed. Output the model
   declined to fund is **desaturated, never hidden** — a paper row is a real
   opinion and still has to be legible next to the ones that cleared.

### The bet object

A market inside a game sheet is a **nested block** one elevation step lighter
than the card holding it. The nesting is what separates "the game" from "the
bet" without a rule or a heading, and it is the move the whole genre shares.
Inside it: the call and its tier, then a strip of model / market / best price
/ edge / sized, each value over its own micro-label, then the venue row.

The venue row is where the exchanges earn their place. A Kalshi contract and
a FanDuel line on the same total are the **same bet at two venues**, so they
sit side by side on one market rather than being filed apart, with the
best-priced one lit and the exchanges carrying a lighter edge to say they are
not sportsbook lines.

The venue rides in the price pill as a **two-letter monogram in the venue's
own colour** (`venueMark` / `venueColor` in `components/format.js`). No board
in the genre spells a book's name out inside a dense row — it shows a mark —
and a tile we draw ourselves needs nobody's logo asset. The full name sits
under the pill, and `sources/velocity/board.sql` resolves the raw feed codes
(`williamhill_us`, `mybookieag`) to the name the venue uses for itself, once,
so every page inherits it.

### Three probabilities, three different numbers

Every card carries `Market`, `Belief` and — under the distribution — `Sim`,
and they are not the same quantity. The board's staking belief is anchored to
the market:

```
belief = market + w × (sim − market)          # NFL: w = 0.2
```

so `Belief` is four fifths market and one fifth model, while `Sim` is the raw
Monte Carlo probability read straight off the simulated distribution. The gap
between them is routinely 0.09–0.13 and it is the anchoring doing its job, not
an error. `Edge` is `Belief − Market`, never `Sim − Market`.

This mattered enough to relabel: the strip used to say **Model** for a number
that is mostly the market, which is the sort of label that quietly teaches you
the wrong thing about your own system.

### The distribution is drawn, not just stored

`distributions` is ~80 bins per game of simulated totals and margins — the
richest thing the model produces, and for a long time it reached only two bare
bare `BarChart`s on a per-game page, with no line drawn on them. A
distribution without the number marked on it is decoration.

`Spark.svelte` draws it inside the game sheet — margin and total side by side,
with the leading market's own number struck through it as a dashed rule, so
the disagreement between the model and the price is *visible* rather than
asserted. The cut point is the fiddly part: a home bet at −1.5 covers when the
margin clears **+1.5**, so the cut is the negated handicap, while an away bet
at +1.5 covers *below* its own handicap. Moneylines are the same object cut at
zero; team totals have no matching distribution and draw no rule rather than
guess at one.

Two things about it are deliberate. It trims the empty tails to the central
mass but always keeps the marked line in frame — a number sitting outside the
plotted range is exactly the case worth seeing. And it is **one SVG path with
no charting runtime**: Evidence ships ECharts, but an ECharts instance per
game card on an eighty-game college Saturday is a page that janks on scroll.

The page hands each card both of the game's distributions and the card picks
the one it is struck against, because Evidence's queries live on the page and
a component cannot run one.

### A grid minimum cannot shrink

`repeat(auto-fill, minmax(400px, 1fr))` does not fall back below 400px, so
inside the 342px content column of a phone it overflows and clips every card's
price. Card grids here are one column with a `min-width: 1000px` media query
promoting them to two — the same pattern `.play-list` already used. Verified by
measuring card rects against the article's, not by looking at a screenshot.

### Things deliberately not done

- **No gold, felt, or card-suit ornament.** None of the reference products
  has any, and "Vegas" in this genre means a lit trading desk, not a casino
  floor. The one piece of literal Vegas texture is the stub at the foot of
  every page — the boring jurisdiction/timestamp line every real book prints.
- **No boxes around every number.** Dense grids in the genre box *nothing*;
  only the tap target gets a box. `chip={true}` on a `Column` renders bare
  text in Evidence 40.1.8 anyway, so the micro-caps do the work.
- **No arrow on a number that already prints its sign.** Colour plus sign
  plus arrow is three channels for one fact.

Chart marks are stepped into the dark band rather than reusing the brighter
UI accents, and single-series charts (the bankroll curve, cumulative units)
take the brand teal directly instead of a categorical palette.

### A source may not assume its own column *presence* either

The schema block in `scripts/build_site_data.py` is a **floor, not a filter**:
it adds any listed column a frame is missing and writes the sentinel row for
an empty family, but it never drops an extra. So a column the producer writes
and the schema omits exists on a busy day and vanishes on a quiet one.

`props` was exactly that. `prop_slate_to_frame` writes fourteen columns
including `book` and `note`; the schema listed twelve. A page selecting
`book` would have rendered all season and then shown a red box on the first
slate with no props — the same failure as the `tier` cast below, arriving by
a different route. Anything a producer writes has to be listed in the schema.

### A source may not assume its own column types

`tier` and `rationale` are written by the intel layer, which does not run on
every slate. When a board carries none of them, pandas writes the column as
all-NaN `float64`, and a `coalesce(tier, '')` in the page SQL then dies with
`Could not convert string '' to DOUBLE` — a red box where the board should
be, for a slate that is otherwise perfectly good. `board.sql` casts both to
`varchar` so an untiered board renders as an untiered board. Any optional
text column added later needs the same treatment.

### Two markdown traps

Evidence runs its **markdown pass before Svelte compiles the page**, so inside
a paragraph:

- a pair of underscores becomes `<em>` — *including inside a `{...}`
  expression, and including across two lines*. `{bank[0]?.mode_note}` compiled
  to `{bank[0]?.mode<em>note}` and the build died with `Expected }`.
- `''` is smart-quoted into typographic quotes, so `{x ?? ''}` becomes invalid
  JS and the build dies with `Unexpected character '”'`.

Component attributes are not prose and are unaffected. Keep underscores and
quote literals out of prose interpolations: rename the SQL alias
(`mode_note` → `modenote`) or build the string in the query.
`tests/test_site_pages.py` fails on both, plus a missing `title` or
`sidebar_position`, a hyphen where a minus belongs, a grouped price, a
colour-only delta, and a board face that is fetched rather than vendored — so
a bad page breaks pytest instead of the nightly deploy.

`scripts/build_site_data.py` finds the **latest stamp per league** for each
artifact family in `--slate-dir`, joins what the pages need (slate ×
games × projections × intel tiers), derives the running-units table, reads
the bankroll ledger when `--ledger` names one (`bankroll`, `bankroll_curve`,
`ledger_open` — docs/WAGERING.md §7), collects the grade's monitor parquet
(`market_health` — §8), and
writes stable-named parquets into `site/sources/velocity/data/`. An absent
family writes a typed **one-row sentinel** (`league = '__none__'`) rather
than an empty frame — Evidence's source runner writes no parquet at all
for a zero-row query and the build then fails reading the missing
extraction — and every page query filters `league != '__none__'`, so
empty states still render.

The same script copies the newest-stamp card PNGs per (kind, league) —
social, deepdive, simcheck, recordcard — into `site/static/cards/` with a
`cards` manifest table (matchup + post caption parsed from the captions
files). The hub does not surface these yet — see *Deliberately not carried
over* below.

**Live scoreboard:** the Worker also serves `/api/scores` — a fan-out to
ESPN's public scoreboard JSON for the five leagues, trimmed to ticker fields
and edge-cached ~45s. It is the **fallback**, not the primary: see *Live
scores and box scores* above for why the browser fetches ESPN directly.

## The game sheet

Opening a game expands it in place — no route, no navigation, and the feed
you were reading is still where you left it when it closes. Inside, in order:

- **Model.** The two expected scores, the home win probability, the fair
  spread and total, and the simulation count. Then the two distributions.
- **Markets.** One block per market rather than one per quote. The board
  carries a row per venue per market and on a typical slate ~68 of 88 rows
  are exchange contracts, so the flat list is mostly the same six markets
  quoted over and over; collapsing them is what makes it readable and is also
  the only way to say the thing a bettor actually wants — **who is best on
  this line right now**. Sorted by tier, then by the model's own conviction:
  sorting by edge alone leads with whatever is noisiest.
- **Your position**, when there is one, and **DFS plays from this game**, when
  the sport has a slate.
- **Live**, once the game is under way: the box score and recent scoring.

One thing it gets right that is easy to get wrong: **`fair_spread` is already
the home side's line**, so a negative number means the home team lays points.
Negating it puts the favourite on the wrong side.

## Team marks and colours

Every surface that names a team can also show its mark and wear its colour:
the matchup sheet's two team blocks, and both ends of a play card's matchup
line. `velocity.teams` carries one row per team on the slate —
`code`, `color`, `color_dark`, `logo` — built by `build_teams` in
`scripts/build_site_data.py` and resolved through
`velocity.report.assets.team_identity`, which is the card renderers' own
resolution shared rather than copied a fourth time.

Four things this had to get right:

- **Marks are hot-linked from ESPN's public CDN, never vendored.** The repo
  is public and club marks are not ours to redistribute — the same line the
  card renderers draw. That makes "the image did not arrive" a normal state,
  not an error — and there are three ways to have no logo, only one of which
  fires an `error`. A blocked or slow CDN just leaves the request **pending**,
  so an `on:error` fallback alone shows an empty plate for as long as the
  browser is willing to wait. `TeamMark.svelte` therefore paints the code chip
  first and reveals the image over it only once it has actually decoded, which
  covers no logo, not yet, never, and loaded with two booleans and no timers.
  `use:settle` reads `complete` on mount, because a cached logo can decode
  before Svelte binds its listeners and would otherwise leave the chip up over
  a perfectly good image. It renders nothing at all when there is neither a
  logo nor a code — an empty plate reads as a logo that failed rather than as
  a team nothing knows about, and lands on the page as a few-pixel sliver.
  A test refuses any club mark committed under `site/static/`.
- **A team is spelled two ways in our own data.** `projections` names NFL
  clubs by code (`SEA`); `games` names them in full (`Seattle Seahawks`).
  Looking a display name up in `TEAM_META` directly misses all thirty-two,
  silently, with a trigram that happens to be right for Seattle and wrong for
  New England. `team_identity` runs both through `resolve_team`.
- **Contrast has to be measured, not assumed.** The card renderer's
  `lighten_for_dark` raises HLS *lightness* to a floor, which is the right
  idea in the wrong space — lightness is not luminance. Against this site's
  panel that floor leaves fifteen of thirty-two clubs under 3:1 and the
  Ravens' purple at 1.5:1, and raising the floor does not fix it: at 0.50 the
  deep blues and purples are still under the bar and the bright hues have gone
  garish. `readable_on` searches lightness *for the contrast* instead, holding
  hue and saturation, so a navy stays navy and every club clears 3:1 — WCAG's
  bar for non-text graphics, which is what a rule and a hairline are.
- **Clubs share colours.** New England and Seattle wear the same navy,
  Cincinnati and Denver the same orange, so a sheet can put two identical
  rules on the page and look broken. `distinctPair` is the card renderer's
  de-collision rule on the web surface: the away side steps lighter and a
  little less saturated. Lighter is the safe direction on a dark panel — it
  only adds contrast — and identity never rests on colour anyway, since the
  mark and the name are right beside it.

The colour is a rule under each team's name rather than the name's own
colour: a brand primary lifted just far enough to be visible is still too
dark for 1.1rem of text.

College identity rides `CFBD_API_KEY` **or** its cached payload, and degrades
to bare codes without either — a missing key costs colour, never a build. Two
things make it actually resolve on a deploy, and both were wrong first:
`build_teams` reads the cache the slate run already warmed under
`<slate-dir>/.assets` (hidden, outside the artifact upload globs), and the
workflow's *Build the site* step carries the key for a build whose cache is
cold. Without either, every college team on the board is a bare trigram with
no mark and no colour — half the slate, silently, with nothing failing.

## Why a bet is smaller than its own Kelly

The Board's **Sizing** section answers the question the site could not
previously answer: Kelly said 1.04u and the card staked 0.52u — why?

Almost always the answer is not a cap. It is the **same-game correlation
de-scaling**: two bets on one game are not two independent bets, so a game's
bets are scaled together by `1 / (1 + (n − 1)ρ)` *before* any cap applies.
At the default `ρ = 0.5` that is 100% for one bet, 67% for two and 50% for
three. Only what survives that meets the per-game, per-class and slate caps.

This was verified against a real slate rather than read off the constant:
every game on the board had exactly one distinct `stake / stake_solo` ratio,
and the ratios came out 1, ⅔ and ½ for 1, 2 and 3 bets — which is
`correlation_scale()` exactly.

The Methods page's Staking row now carries `ρ` too. It used to be a **string
literal**, which is precisely the drift the rest of that block exists to
prevent: it hardcoded the slate cap that `--max-slate-fraction` moves, and it
never mentioned the de-scaling at all — the term that most often decides a
stake. `tests/test_runner_policy.py` fails if it goes back to being written
down rather than read.

## What moved

Every game sheet carries a **Moved since open** block: the markets the hourly
archive has seen change, with the opening number and price beside the current
one. A market that did not move is left out — an unmoved line is not news, and
a block full of unchanged numbers trains you to stop reading it.

It deliberately makes **no claim about whether a move helped or hurt**. That
judgement is the closing-line calculation under Record, which measures
against the actual close rather than inferring from the direction of travel —
and getting the sign right depends on the side and the market's own
convention, which is exactly the sort of thing that reads plausibly and is
wrong.

## The prop board

`props` is a separate family from the game board — `slate_{league}_props`,
priced off the correlated per-game simulation rather than the game model —
and it has its own page. Two things differ from a game market and the page
says both:

- **A prop carries no closing-line value.** Few sharps price a receptions
  line, so its close is not a yardstick. The monitor judges props on realised
  profit instead (docs/WAGERING.md §8), which is why a prop market needs a
  much bigger sample before it says anything.
- **The gate wants more edge.** A prop line is thinner and the price is
  worse, so `min_edge_for` asks for more of it than a game market does.

The football props ride on the FantasyPros pull: a slate without one prices
the game markets only, and the page's empty state says so rather than
implying the model had no opinion.

## Local preview

```bash
python scripts/run_live_slate.py --league nfl --data datasets/nfl \
    --snapshot-file tests/fixtures/theoddsapi_nfl.json \
    --min-edge 0.0 --max-days 0 --out /tmp/demo_slate     # offline demo data
python scripts/build_site_data.py --slate-dir /tmp/demo_slate
cd site && npm ci && npm run sources && npm run dev
```

Add `--tier public` to the build step to see what a public surface would
carry. The joins and the number rules are also testable without a browser:

```bash
node --test "site/tests/**/*.test.mjs"    # runs in CI
```

(When iterating locally, `rm -rf site/.evidence/template/.evidence-queries`
forces the sources step to re-read changed parquet — it caches by query
text, not file contents. CI always starts fresh.)

## One-time Cloudflare setup (the owner does this once)

1. **Cloudflare account** (free plan). In the dashboard, enable **R2** and
   create a bucket named `velocity-wasm` — Evidence's two DuckDB-WASM
   binaries (~33/38 MiB) exceed the 25 MiB per-asset cap, so
   `site/deploy.sh` parks them there and `worker.js` serves them back.
   (R2's free tier covers this; Cloudflare may ask for billing details to
   enable R2.)
2. **API token**: dashboard → My Profile → API Tokens → Create Token →
   "Edit Cloudflare Workers" template, plus R2 read/write for the bucket.
3. **Repo secrets** (GitHub → Settings → Secrets → Actions):
   `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`. The `live-slate`
   workflow's site steps stay skipped until the token secret exists, so
   nothing breaks beforehand.
4. First deploy creates the Worker `velocity-edge` at
   `velocity-edge.<account>.workers.dev` (or attach a custom domain).
5. **Lock it down with Cloudflare Access** (Zero Trust → Access →
   Applications → Add): application domain = the Worker's hostname,
   policy = allow → your email(s), login via one-time PIN. Free for up to
   50 users. Do this BEFORE sharing the URL — until Access is attached the
   workers.dev URL is public-but-unlisted.

## Daily publish

The `live-slate` workflow (after building slates): assemble
`site/sources/velocity/data/` from the run's artifacts → `npm ci` →
`npm run sources` → `npm run build` → `site/deploy.sh` (park oversized
wasm in R2, `wrangler deploy`). Both site steps are gated on
`CLOUDFLARE_API_TOKEN` being set, and they run **last** — after the slate
artifact and email are delivered — so a site failure marks the run red
(the honest signal the site didn't publish) without costing the slate.

## Where the ten pages went

The rebuild moved four surfaces into the game sheet where they belong — player
props, line movement, the injury report and the weather — because each is a
fact *about a game*, and the old site's separation of them from the game was
the thing being fixed. The rest followed the same rule.

Nothing was dropped. Every surface the ten pages carried is on the hub, and most
of them are no longer surfaces at all — they are facts attached to the game
they are about:

| Was a page | Is now |
|---|---|
| Board, props, line movement, injuries, weather | The game sheet |
| Matchup dossier | The game sheet, expanded in place |
| Performance | Record |
| Market health | Inside Record — same question, shorter horizon |
| Ratings | A view, **and** the head-to-head in every game sheet |
| Card room | Each game's own card, in its sheet; the record cards in Record |
| DFS | A view |
| Methods | The rail's model block |

The two that had nowhere obvious to go are **parlays** and the **record
cards**, and both are below.

## Market health

The monitor (`velocity/report/monitor.py`) reads the season chain back as a
per-market trailing table over 7- and 30-day windows and flags a market that
is losing to its close, losing money, or claiming more than it earns. It
reaches the surface in three places, all off one index (`flaggedMarkets`), so
they cannot disagree:

1. **Inside Record**, not as a view of its own. It is the same question at a
   shorter horizon — Record says what happened, this says whether one of the
   markets producing it has gone bad.
2. **In the rail**, as a count and the first few flags. A market that has
   stopped working is the one thing on this surface nobody would think to go
   and look for, so it is pushed rather than waited for; clicking it switches
   to Record, which on a single surface is a view switch rather than a page
   load.
3. **On the market itself**, in the game sheet — the strongest form it can
   take, because it is the moment you would act on it.

Three things it is careful about:

- **Only the 30-day window flags.** The 7-day is an early warning, and
  treating it as a flag puts an amber mark on a market the monitor has not
  called. What the 7-day *is* used for is the line under each flag saying
  whether it agrees — the monitor wants the same flag in two review windows
  before an exclusion, so a market flagged in both is further along than one
  flagged in one.
- **A thin market is never flagged.** `thin` is under twenty bets, the
  monitor suppresses every other flag on one, and the string it writes
  ("thin (1 bets)") is a statement that it *cannot judge* — the opposite of a
  warning. A truthy-`flags` test alone would surface those beside real ones,
  which is how a warning stops meaning anything. Thin rows stay in the table,
  desaturated, because they are real markets with real records.
- **A flag is a question, not a verdict.** The monitor names exclusion
  candidates; the operator decides, and the language keeps it that way.

## Power ratings

Two surfaces, and the split is the point:

- **In the game sheet**, as a head-to-head — the two teams mirrored with the
  advantage marked down the middle. This is the one the old matchup page
  existed for: a list of markets says *what* the model thinks, the
  head-to-head says *why*.
- **As a view**, for the question a game card structurally cannot answer.
  A card shows two teams; "who does the model think is good" is about all of
  them.

Three things it has to get right, all of which were wrong somewhere first:

- **The join key.** Ratings are keyed by the string each league's fit uses —
  `PIT` for the NFL, `Alabama` for college, `Minnesota Lynx` for the WNBA —
  and the `games` table carries none of those, only full club names. The
  `projections` table carries the fit's own spelling and matches ratings on
  every team in all three live leagues. Joining ratings to **games** matches
  almost nothing and renders a head-to-head with both sides blank.
- **Which way is better.** `net = off − def`, so a **lower Def is the better
  one**, and a lower rank is better. A naive "higher wins" marks the wrong
  side on two of the five rows. Pace is neither — it is context, and marking
  a side on it would invent a claim the model does not make.
- **A column the fit does not report.** The football and baseball fits have
  no pace and the column arrives as NULL, which `Number()` turns into a
  perfectly finite 0 — so the head-to-head printed "Pace 0.0 / 0.0" for two
  teams that play a normal number of possessions. Everything numeric on this
  surface guards with `isNum` for exactly that reason.

The view carries leagues the board does not — the model rates college
basketball and hockey without pricing them today — because that is honest
about what the model knows rather than only what it is betting. Each league
gets only the columns its own fit reports: pace where the fit is per
possession, movement where a previous run exists to compare against.

Team names are resolved through the identity table, so the NFL's `PIT` shows
and searches as "Pittsburgh Steelers" with the fit's own spelling kept beside
it. A team the identity table has not seen — college, or a club not playing
this week — keeps the fit's spelling rather than being guessed at with a
prefix match that would confidently map "Miami" to the wrong school.

## Parlays

A parlay is the one row on the board that is not about a single game, so it
has nowhere to sit inside one. It goes above the feed, **collapsed**: on most
slates it is a handful of rows and it must not be the thing between you and
the board.

What makes it more than a table is that `legs_json` carries a `game_id` per
leg. So a leg is a control — clicking it opens that game in the feed below —
and the reverse join gives every game card a count of the parlays it has a
leg in. That is the single-surface argument in miniature: on the old site,
reading a parlay meant writing down three matchups and going to find them.

One thing it is careful about: **`same_game` means two or more legs share a
game, not that the whole parlay is one game.** A three-leg parlay with two
MIA@LV legs and one WAS@PHI leg is flagged true. Reading it as "all one game"
would file cross-game parlays inside a single game's sheet, so the label says
*correlated legs* instead.

A row whose JSON will not parse keeps its rendered one-line string and gets
no leg links — degraded, not dropped, because the price and the model's
probability are still true. The source carries both `legs` (the string) and
`legs_json` (the structure); the parsed array takes the name and the string
is kept under `legs_string`, or the degraded path prints `[object Object]`.

## Cards

The rendered PNGs are made to be **posted** — that is the only reason they
exist as images rather than as the numbers already on the page — so the shelf
is built around the two things anyone does with one: open it full size, and
take the caption. Everything else is chrome.

They split by whether they belong to a game:

- **Per-matchup** (the pre-game sheet, the sim check) carry a `game_id` and
  live in that game's sheet, last — they are the takeaway, not the analysis,
  and everything on them is already above in numbers.
- **Record cards** are one per league and carry no `game_id` at all. They are
  a picture of exactly the Record view, so they sit in it.

There is no gallery, because a room full of ninety-eight matchup graphics is
a browsing surface for a thing nobody browses: you want the card for the game
you are looking at, and that is where it now is.

A card whose file did not make it into the build drops out of the shelf
rather than leaving a broken frame.

## The Streamlit app

`app/streamlit_app.py` is superseded — the hub covers every surface it had —
but it is still on disk, because `build_site_data.py` imports
`app/format_plays.py` for the `MODEL_CONFIG` fallback used when a slate
artifact predates the runner writing its own config frame. Deleting the app
means moving that table first; it is not worth coupling to this change.
