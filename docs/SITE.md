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

Four rules the pages inherit from `pages/+layout.svelte`:

1. **Numbers are the product.** Tabular figures everywhere
   (`font-feature-settings: "tnum"`), so columns of money line up.
2. **Money color is never the only channel.** Profit green and loss red are
   indistinguishable under deuteranopia, so every number wearing them also
   carries a sign or an arrow. They are status colors, never categorical
   slots (`evidence.config.yaml` says so at the top).
3. **Nothing clips.** The content column is a flex child with `min-width: 0`
   and every table scrolls inside its own box, so a wide blotter never steals
   the page's width — which is exactly what made the old board show two
   columns on a phone.
4. **Empty is a designed state.** Half the site is fed by the morning grade,
   so most of the day something is legitimately empty; pages carry
   `<EmptyNote>` and the framework's red error boxes are suppressed.

Chart marks are stepped into the dark band rather than reusing the brighter
UI accents, and single-series charts (the bankroll curve, cumulative units)
take the brand teal directly instead of a categorical palette.

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
`sidebar_position`, so a bad page breaks pytest instead of the nightly deploy.

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
