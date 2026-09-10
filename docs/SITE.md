# The Velocity Site

The dashboard replacement for the Streamlit app (docs/DASHBOARD_RESEARCH.md):
a static [Evidence](https://docs.evidence.dev) build over the daily run's
parquet, deployed by the `live-slate` workflow to a Cloudflare Worker that
sits behind Cloudflare Access. Total hosting cost: $0.

**Privacy posture:** every page carries paid-odds-derived numbers (prices,
edges, stakes), so the site deploys to ONE Access-gated host and is never
public. A public model-facts site (projections, graded record, cards — no
prices) is a later phase.

## Layout

```
site/
  package.json            Evidence classic (static build) — lockfile committed
  evidence.config.yaml    theme: the trading-desk tokens from the mockup
  sources/velocity/       DuckDB source; one .sql per table over data/*.parquet
    data/                 assembled per-run by scripts/build_site_data.py (gitignored)
  pages/
    +layout.svelte        THE CHROME: wordmark, no Evidence footer, and the
                          global design system every page is written against
    index.md          (1) Today — the decision: bankroll, exposure, the card
                          as PlayCards, and the held-back rows beneath it
    board.md          (2) the whole priced board, exposure and parlays
    performance.md    (3) the record: bankroll curve, per-market CLV, settled
    health.md         (4) per-market trailing 7/30-day ROI, CLV and
                          claimed-vs-realized, with the monitor's flags
    ratings.md        (5) per-league power ratings with movement
    dfs.md            (6) cash lineup + GPP set
    methods.md        (7) what is live in each league's model
    graphics/         (8) card room — per-league sheet galleries
    matchup/[game_id].md  the game dossier: line movement, markets, sims,
                          weather, injury report, the game's own cards
  components/
    PageHead / StatRow / StatCard / PlayCard / SectionBar / EmptyNote
                          the design system's own pieces (format.js holds the
                          number rules); LiveTicker / CardGallery / WeatherLine
  static/                 favicon + icon set (the V drawn as a bankroll curve)
    cards/                newest-stamp card PNGs (gitignored, per-run)
  worker.js + wrangler.toml + deploy.sh   Cloudflare deploy + /api/scores
```

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
   dark-on-dark; exactly one thing per list gets the inverted brand pill.
   `PlayCard` takes a `lead` prop for precisely this. A list where every price
   is filled is the genre's clearest cheap tell — if everything is lit,
   nothing is.
5. **Nothing clips.** The content column and its wrapper are flex children
   with `min-width: 0`, and every table scrolls inside its own box, so a wide
   blotter never steals the page's width — which is exactly what made the old
   board show two columns on a phone.
6. **Empty is a designed state, and so is low confidence.** Half the site is
   fed by the morning grade, so most of the day something is legitimately
   empty; pages carry `<EmptyNote>` and the framework's red error boxes are
   suppressed. Output the model declined to fund is **desaturated, never
   hidden** — a paper row is a real opinion and still has to be legible next
   to the ones that cleared.

### The bet object

`PlayCard` is a card carrying a **nested ticket** one elevation step lighter.
The nesting is what separates "the game" from "the bet" without a rule or a
heading, and it is the move the whole genre shares. Inside the ticket: the
call on the left, the price on the right, and a four-up strip of
edge / model / fair / stake, each value over its own micro-label.

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
`BarChart`s on the matchup page with no line drawn on them. A distribution
without the number marked on it is decoration.

`DistStrip.svelte` puts it on the bet object itself: the covered side lit, the
market's number as a rule, and the exact covered mass printed beneath. The cut
point comes from `distThreshold()` in `components/format.js`, which is the
fiddly part — a home bet at −1.5 covers when the margin clears **+1.5**, so the
cut is the negated handicap, while an away bet at +1.5 covers *below* its own
handicap. Moneylines are the same object cut at zero; team totals have no
matching distribution and return `null` rather than guess.

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
all-NaN `float64`, and the Board page's `coalesce(tier, '')` then dies with
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
files), which the Graphics page galleries with save/copy-caption actions.

**Live scoreboard:** the Worker also serves `/api/scores` — a fan-out to
ESPN's public scoreboard JSON for the five leagues, trimmed to ticker
fields and edge-cached ~45s. `LiveTicker.svelte` polls it every 60s and
renders the scrolling crawl on the Today page; it hides itself when the
endpoint is unreachable (local preview) or all leagues are dark.

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

## Retirement plan for the Streamlit app

The app (`app/streamlit_app.py`) keeps running untouched until the site
has covered its surfaces (board, pick'em, cards gallery, performance) for
a couple of weeks of real slates; then docs/LAUNCH.md's app section gets
swapped for this page.
