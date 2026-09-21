# Phase 13 Stage 2 — private mobile dashboard: architecture plan

**Status: design only. Nothing here is implemented, and nothing should be
until this plan is approved.** Per the owner's Phase 13 directive: Option C
hybrid, Cloudflare-hosted, **not** GitHub Pages, with the workbook remaining
the authoritative research surface and any dashboard a convenience layer
over workbook exports rather than a replacement for Velocity outputs.

---

## 0. Why not GitHub Pages — restated, because it is the load-bearing constraint

GitHub Pages is public. There is no private tier on a personal account and
it is Enterprise-only on an organisation.

`datasets/exports/` is gitignored precisely because those files carry The
Odds API prices, BettingPros lines, DraftKings salaries, de-vigged fair
probabilities, Kelly stakes and the curated card. Publishing any of it to an
open URL would breach the feeds' terms **and** hand away the edge, and it is
the exact thing the repository's data posture exists to prevent
([`SITE.md`](SITE.md) §"the private tier", [`WORKFLOW.md`](WORKFLOW.md) §10).

Everything below therefore sits behind Cloudflare Access.

---

## 1. What already exists — this is not greenfield

The repository already runs the target infrastructure:

| Piece | Today | Reuse |
|---|---|---|
| Worker | `velocity-edge` (`site/worker.js`, `site/wrangler.toml`) | add routes |
| Static host | Evidence build in `[assets]` | untouched |
| Object store | R2 bucket `velocity-wasm`, binding `WASM` | add a prefix |
| Auth | Cloudflare Access in front of the Worker | unchanged |
| Deploy | `site/deploy.sh` from `live-slate.yml`, gated on `CLOUDFLARE_API_TOKEN` | add a step |
| Secrets | `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | already set |
| Precedent | `/api/scores` already runs Worker-first and edge-caches | same pattern |

**Cost: £0.** Workers free tier is 100k requests/day; R2 free tier is 10 GB
storage and generous Class A/B operations. One operator on one device is
three orders of magnitude inside both. Access is free for up to 50 users.

The honest read: this is **a route, a template and a publish step**, not a
new system. That materially lowers the Stage 2 estimate from my Phase 13
note (one to two days) to roughly **half a day plus review**.

---

## 2. Proposed architecture

```
live-slate.yml  (unchanged up to here)
      │
      ├─ export step  →  datasets/exports/*.csv  +  velocity.xlsx
      │                        │
      │                        ├─→ Actions artifact   (unchanged)
      │                        ├─→ email attachment   (unchanged)
      │                        └─→ NEW: wrangler r2 put → velocity-wasm/board/
      │                                   games.csv, plays.csv, team_totals.csv,
      │                                   props.csv, dashboard.csv, readiness.csv
      ▼
velocity-edge Worker  (Cloudflare Access in front)
      │
      ├─ /            existing Evidence site        unchanged
      ├─ /api/scores  existing ticker proxy         unchanged
      ├─ NEW /board            → server-rendered HTML, one page
      └─ NEW /board/refresh    → POST, proxies workflow_dispatch
```

### 2.1 Storage: R2 under a `board/` prefix

Write the CSVs the export already produces to `velocity-wasm/board/`, keyed
by filename with no stamp — same stable-name discipline the CSVs already
follow, so the Worker reads a fixed key and the newest write wins.

Deliberately **CSV, not a database.** The export contract is already stable,
tested and byte-stable; parsing it in the Worker adds no second source of
truth. A D1 or KV layer would mean transforming on write, which is exactly
the "second rendering of numbers that must not drift" risk the Phase 13 note
flagged.

Retention: overwrite in place. History belongs in the pipeline's archive,
not in a view layer.

### 2.2 Rendering: server-side HTML in the Worker

The Worker fetches the CSVs from R2, parses them, and returns one HTML
document. No client framework, no build step, no hydration.

Why server-rendered rather than a static page fetching CSVs:

* the CSVs never reach the browser as files, so a saved page or a shared
  screenshot carries markup rather than the full paid-feed dataset;
* one request instead of six on a phone connection;
* no bundler in the deploy, so nothing new to keep current.

Page content, in order — this is the **betting** surface, deliberately small:

1. **Run status** — the readiness verdict, colour-coded, with the board's age
   and the time to next kickoff. Unmissable, at the top, because a stale page
   that still loads is the most dangerous failure mode a dashboard has.
2. **The card** — A+/A/B plays: tier, selection, edge, confidence, stake,
   reason. Watch collapsed behind a disclosure.
3. **Games** — one line each, sorted by absolute total edge.
4. **Team totals** — one line per side, sorted by edge.
5. **Refresh** — a button that POSTs to `/board/refresh`.

Explicitly **not** on the page: the DFS pool, the optimizer, the
distributions, props beyond a count. Those stay in the workbook, which
already sorts and filters them properly.

### 2.3 The refresh button

`POST /board/refresh` → the Worker calls GitHub's `workflow_dispatch` for
`live-slate.yml`, using a fine-grained PAT held as a **Worker secret**
(`wrangler secret put GH_DISPATCH_TOKEN`), never in the page.

Same endpoint the iOS Shortcut uses. The Shortcut remains the primary path
and keeps working if the dashboard is never built.

### 2.4 Freshness, stated honestly

The page shows `generated_at` from the CSVs and the derived age, **not** the
time of the request. A dashboard that looks live while showing six-hour-old
numbers is worse than a file whose age you can see — and it is the specific
failure this design must not have.

If the R2 objects are missing entirely, the page says so and offers the
refresh button. It does not render an empty board.

---

## 3. What changes in the repository

Small, and additive:

| File | Change |
|---|---|
| `site/worker.js` | two routes: `GET /board`, `POST /board/refresh` |
| `site/wrangler.toml` | add `/board*` to `run_worker_first`; reuse the `WASM` R2 binding (or add a second binding to the same bucket for clarity) |
| `.github/workflows/live-slate.yml` | one step after export: `wrangler r2 object put` for six CSVs, gated on `CLOUDFLARE_API_TOKEN` like every other Cloudflare step |
| `site/tests/` | Node tests for the CSV parse and the row shaping, in the existing suite |
| `docs/SITE.md` | the new routes and the access posture |

**No Python changes. No change to the export contract. No new dependency.**
The dashboard consumes what already exists.

---

## 4. Risks, and the mitigation for each

| Risk | Mitigation |
|---|---|
| **Stale page reads as live** — the worst failure a dashboard can have | `generated_at` and the age rendered at the top, from the data not the clock; the readiness verdict leads the page |
| **A second rendering of numbers drifts from the CSVs** | the Worker formats but never computes — no edges, no probabilities, no stakes derived in JavaScript |
| **Access misconfigured → the board is public** | this already bit the repo once (`SITE.md`: an unauthenticated request reached the Worker on 2026-09-12). The existing deploy already verifies Access before publishing; the new route must be covered by the same check, and the plan should not ship without it |
| **Worker secret leaks the PAT** | fine-grained, `actions: write` on one repo only, stored via `wrangler secret`, never in `wrangler.toml` or the page |
| **Dashboard becomes the source of truth** | it renders a subset by design and omits the research surfaces entirely; the workbook stays authoritative, per the directive |
| **Upkeep on every export schema change** | the Worker reads columns by name and renders what it finds; a new column is ignored rather than fatal |

---

## 5. What this does *not* fix

Worth stating plainly, because it is the reason Stage 1 comes first:

**No front end changes the scheduling delay.** A dashboard published by
`live-slate.yml` is exactly as fresh as that run, which starts 1h48–3h00
late or not at all. The refresh button helps because it dispatches — which
is the same thing the iOS Shortcut already does, without a dashboard.

If measurement under D4 shows dropped slots rather than late ones, the
answer is in that data, not here.

---

## 6. Recommendation on sequencing

1. **Run Stage 1 for a few game days first.** If the Shortcut plus email
   plus the Bet Card tab covers it, Stage 2's upkeep is not worth paying.
2. **Build Stage 2 only if** you find yourself wanting the board without a
   file — away from home, checking quickly, or sharing a read-only view.
3. If built, ship it behind the **same Access check the site already
   enforces**, and treat the readiness verdict as the page's headline rather
   than a footnote.

Estimated effort if approved: **half a day**, plus review, plus the Access
verification.

---

## 7. Open questions for the owner

1. **Same hostname or its own?** `/board` on the existing `velocity-edge`
   Worker is simplest and inherits Access. A separate subdomain is tidier
   but is a second Access application to configure.
2. **Should the refresh button exist at all?** It duplicates the Shortcut.
   Including it makes the page self-sufficient; omitting it keeps the page
   strictly read-only, which is a smaller attack surface and one less secret.
3. **Props on the page or not?** The directive keeps research in the
   workbook, and props are arguably research — but they are also a core
   market you bet (D1). My inclination is a count and the top three by edge,
   with the full board staying in the workbook.
