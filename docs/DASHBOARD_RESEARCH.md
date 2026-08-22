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

Research recorded; **no build started** — awaiting the owner's pick
between #1 (Evidence) and #2 (custom Astro).
