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
    index.md              Today board (market · model · edge · tier)
    picks.md              tiered picks + intel vetoes
    performance.md        units, win rate, cumulative chart, graded slate
    matchup/[game_id].md  per-game page (markets, sim distributions)
    dfs.md                cash lineup + GPP set
    methods.md            the "what's live" transparency block + glossary
  worker.js + wrangler.toml + deploy.sh   Cloudflare deploy
```

`scripts/build_site_data.py` finds the **latest stamp per league** for each
artifact family in `--slate-dir`, joins what the pages need (slate ×
games × projections × intel tiers), derives the running-units table, and
writes stable-named parquets into `site/sources/velocity/data/`. Leagues
with no artifacts contribute typed empty frames, so every page builds and
renders its empty state.

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
`CLOUDFLARE_API_TOKEN` being set.

## Retirement plan for the Streamlit app

The app (`app/streamlit_app.py`) keeps running untouched until the site
has covered its surfaces (board, pick'em, cards gallery, performance) for
a couple of weeks of real slates; then docs/LAUNCH.md's app section gets
swapped for this page.
