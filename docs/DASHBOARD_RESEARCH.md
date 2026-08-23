# Velocity — Dashboard Replacement Research (2026-08)

Four research agents: an inventory of the current presentation surfaces, a
framework survey, a best-in-class sports-analytics UX survey, and a
hosting/privacy study. This records their converged findings and the
recommendation. Sources and full detail live in the agents' reports; the
load-bearing claims here were verified against 2025–2026 primary docs.

## 1. Where we are

- The Streamlit app (`app/streamlit_app.py`) is already ~all hand-written
  HTML pushed through `st.markdown(unsafe_allow_html=True)` — Streamlit
  contributes hosting and a sidebar, not the aesthetics. Its ceiling,
  even with 2025+ theming and shadcn components, is "nicely themed
  Streamlit," visibly short of the bettorsheets-style reference.
- Everything the app shows is flat parquet keyed by
  `{kind}_{league}_{stamp}` in one private Actions artifact per run —
  no database, no manifest, filename-pattern resolution. A replacement
  front-end reads the same files.
- Two fragilities worth fixing in any migration: the **season record
  chain only survives inside the 60-day artifact retention window**
  (each run re-downloads the last 6 runs to extend it), and the app's
  league list is missing NCAAB.
- Display state hardcoded in the app that must migrate with it:
  `MODEL_CONFIG` (the transparency block), `PICKEM_BREAKEVENS`, team
  display naming, prop abbreviations.

## 2. The privacy constraint, corrected

The Odds API's actual terms (fetched, primary source): using their data
in **user-facing websites and dashboards is permitted, including
commercially** — what's prohibited is redistributing it as a standalone
data product (their example includes *downloadable files serving as a
raw data source*). So:

- Rendered odds in our own UI: allowed, even on a public page.
- Raw odds parquets exposed for download: **not allowed** publicly.
- Conservative posture (recommended): keep anything showing book
  prices/stakes behind auth anyway; publish freely the model-facts
  surfaces (projections, distributions, graded record, matchup cards
  minus the verdict band — already designed as public artifacts).

## 3. Hosting: the free path with real auth

2026 reality check: Vercel password protection is a **$150/mo add-on**;
Netlify password/Basic-Auth is Pro ($20/mo) for new accounts; GitHub
Pages access control is Enterprise-only (free Pages = always public);
Fly.io's free tier is gone. The standout:

**Cloudflare Workers static assets (Pages' successor) + Cloudflare
Access — $0.** Access is free for ≤50 users with email one-time-PIN
login, sits in front of every request (protects the parquet/PNG files
themselves, not just pages), and `wrangler deploy` from the existing
Actions cron bypasses any build quota. Public site on one hostname,
private on another, both from the same repo's workflow.

## 4. Framework verdict

| Option | Aesthetic ceiling | Effort | Ruling |
|---|---|---|---|
| **Evidence.dev** (static, parquet-native, markdown+SQL pages) | ~85–90% of the reference out of the box; themes + `app.css` | Days | **#1 — recommended** |
| **Astro/Next custom site** (bettorsheets' own architecture) | Unlimited | Weeks; owns a Node toolchain | #2 — the graduation path |
| Observable Framework | Best default dashboard theme | Every chart is hand-written D3/Plot; framework plateaued (v1.13.4, Mar 2025) | #3 |
| Streamlit re-themed (+shadcn-ui) | ~7/10 | A weekend | Fallback only |
| Quarto dashboards | Handsome-but-Bootstrap | Low | Fallback only |
| Dash/Panel/NiceGUI/Reflex | Varies | Needs an always-on server — nothing here is live-interactive | Eliminated |
| Grafana/Metabase/Superset | Ops-panel genre | Server + care | Eliminated |
| marimo | Notebook-ish | — | Wrong role (keep for exploration) |

Why Evidence: parquet is its native format (a DuckDB source queries our
outputs directly, zero transformation); pages are markdown + SQL, so a
pandas-fluent operator is productive immediately with no JavaScript;
its component set (BigValue tiles, conditional-format DataTables,
sparklines, card grids, first-class dark mode) is SaaS-grade by
default; output is a static site, so hosting is $0 with no server to
maintain. Risk — a small company steering toward its paid Studio — is
mitigated by verified active OSS commits (Aug 2026) and full
portability (markdown + SQL + parquet).

## 5. Design spec (from the best-of-breed survey)

Seven pages: **Today** (all-league board, sortable by edge), **League
boards** (one template ×5, with power-ratings table), **Matchup page**
(kenpom InstaGamePrep pattern: Open/Current/Model header, side-by-side
stats with percentile chips, verdict block), **Picks** (tier chips +
stake + two-sentence why), **Performance** (the trust centerpiece:
monospace stat tiles, cumulative units, CLV chart, per-league/tier
splits), **Methods** (glossary — every metric defined), **Bet log**.

The five patterns that separate great sites from Streamlit-default:

1. **Market vs Model vs Open, always adjacent** — show disagreements,
   never bare projections (nfelo, Unabated).
2. **Value + rank/percentile in the same cell** (kenpom superscript,
   Cleaning the Glass chips).
3. **Discrete named signals over raw decimals** — tier pills, "+EV"
   count badges (Action Network, nfelo).
4. **Freshness made visible** — as-of timestamp on every board, decaying
   highlight on changed lines (Unabated's yellow fade).
5. **An honest record page in tabular numerals** — units, ROI, CLV,
   losses as plain as wins; the credibility centerpiece (nfelo +
   PredictionTracker).

Theme: dark near-black surfaces, one accent (the existing teal), a
semantic green/red pair with a desaturated 5-step magnitude ramp,
Inter/IBM Plex with `tabular-nums` everywhere numbers appear.

## 6. Recommended architecture

```
existing Actions cron (unchanged)
  └─ publish job:
       site/ (Evidence project)
         sources/velocity → DuckDB over the run's parquet outputs
         static/cards/    → social/deepdive/simcheck PNGs
       npm run sources && npm run build
       wrangler deploy  →  public host   (projections, record, cards)
                        →  private host  (prices, stakes, pick'em,
                                          portfolio) behind CF Access
```

Migration order: (1) stand up the Evidence project reading one league's
artifacts locally; (2) Performance + Today pages first (the two highest-
value surfaces; fixes the record-chain fragility by persisting the
cumulative record into the site's data dir); (3) matchup pages + card
galleries; (4) private section (pick'em, portfolio, full price board);
(5) retire the Streamlit app once at parity. The Streamlit app keeps
running untouched throughout.

## 7. Status

The owner picked **Evidence** — the site lives in `site/` and its
operating guide is docs/SITE.md. Build note discovered en route: Evidence's
two DuckDB-WASM binaries exceed Cloudflare's 25 MiB per-asset cap, so the
deploy parks them in an R2 bucket and a tiny Worker serves them back
(`site/worker.js` + `site/deploy.sh`) — still $0.

## 8. Addendum: DFS pricing sources and the DFS page (2026-08)

**DraftKings is the free source, and it covers everything.** The
collector already uses DK's own unauthenticated endpoints (lobby
`getcontests?sport=X` → draft groups → `draftgroups/v1/.../draftables`);
live-verified today the same endpoints serve **NFL, CFB, MLB, WNBA, NBA,
CBB** (CBB empty only in the offseason), and the draftables payload is
byte-identical across sports — extending coverage is a `SPORT_CODES`
addition, not new plumbing. Roster rules come from DK's own
`lineups/v1/gametypes/{id}/rules` API (also unauthenticated): all Classic
formats cap at $50k (NFL 9 slots QB/2RB/3WR/TE/FLEX/DST; CFB 8, no
TE/DST; MLB 10 with 2P and ≤5 hitters/team; NBA 8; WNBA 6 = 2G/3F/UTIL;
CBB 8 = 3G/3F/2UTIL); all Showdowns are CPT(1.5× pts and salary)+5 at
$50k, both teams required.

**Status (2026-08-23): MLB classic is live.** `SPORT_CODES` now covers all
seven leagues (salary history banks daily for every board DK posts);
`MLB_CLASSIC` prices the 10-slot roster with the ≤5-hitters-per-team rule
enforced by iterative cuts, scored from FantasyPros season-total MLB
projections normalized to per-game (hitters ÷ G) / per-start (pitchers ÷ GS)
rates — `velocity/dfs/scoring.py:dk_expected_points_mlb`. WNBA/NBA/CBB
still lack a projections source (FP's public API has none), so those boards
bank salaries only.

**FanDuel has no free feed in 2026.** `api.fanduel.com` requires a
logged-in session token (the old public client key is dead; programmatic
login is 2FA-gated and ToS-gray). The practical FD salary source is
**DailyFantasyFuel** (server-rendered pages + an unauthenticated slates
JSON, live-verified; NFL/MLB/NBA/WNBA — no college). FD roster constants,
if added: $60k NFL/CFB/NBA, **$35k MLB** (1P, C/1B combined), $40k WNBA
(3G/4F); FD single-game changed in Aug 2025 to MVP(1.5×/1.5×)+5 FLEX.

**FP/BP APIs carry no salaries** (verified against both OpenAPI specs).
Two incidental wins: FantasyPros `/players?external_ids=draftkings:fanduel`
solves cross-site player-ID mapping, and BettingPros `/events` carries
confirmed MLB batting orders (late-swap signal).

**The DFS page** (mocked on the canvas, patterns from
DFF/SaberSim/FantasyLabs/LineupHQ): cash-optimal as a slot-row card
(slot chip · player · game · salary · proj · value) with a salary-cap
usage bar; the ranked GPP set as compact stack-badged rows (top lineup
expanded, stacked teams tinted, bring-back in amber); a read-only
exposure summary with bars; and a filterable player-pool table whose
"in lineups" column ties pool to output. Deliberately omitted: exposure
sliders, build settings, ownership columns (no ownership model), and
150-lineup grids.
