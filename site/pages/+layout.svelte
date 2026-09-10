<script>
  // The Velocity chrome. Evidence's default layout does the routing, the
  // sidebar tree and the query plumbing; everything visible is re-dressed
  // here. Two props do the branding — a text wordmark instead of the
  // Evidence logo, and no "Built with Evidence" footer — and the global
  // style block below is the design system the pages are written against.
  import '@evidence-dev/tailwind/fonts.css';
  import '../app.css';
  import { EvidenceDefaultLayout } from '@evidence-dev/core-components';
  export let data;
</script>

<svelte:head>
  <meta name="theme-color" content="#0a0e13" />
</svelte:head>

<EvidenceDefaultLayout
  {data}
  title="VELOCITY"
  builtWithEvidence={false}
  homePageName="Today"
  hideTOC={true}
  hideBreadcrumbs={true}
  fullWidth={true}
>
  <slot slot="content" />
</EvidenceDefaultLayout>

<style>
  /* ------------------------------------------------------------------
     Tokens. One ground, three elevations, two hairlines, three inks.
     Money colors are status, never identity: everything that wears them
     also carries a sign or an arrow (docs/SITE.md).
     ------------------------------------------------------------------ */
  :global(:root) {
    --v-bg: #0a0e13;
    --v-surface: #10161f;
    --v-surface-2: #151d27;
    --v-line: #1c2733;
    --v-line-2: #26333f;
    --v-ink: #dfe7ef;
    --v-ink-2: #93a1b1;
    --v-ink-3: #64748b;
    --v-brand: #3ddad0;
    --v-brand-dim: #2bb3ab;
    --v-pos: #3fb950;
    --v-neg: #f0616a;
    --v-warn: #d29922;
    --v-radius: 10px;
    --v-radius-sm: 7px;
    /* Numbers are the product: tabular figures everywhere, no exceptions. */
    --v-num: "Inter", ui-sans-serif, system-ui, sans-serif;
    --header-height: 3.25rem;
  }

  /* Every digit on the site is tabular and slashed-zero. Columns of money
     that do not line up read as a spreadsheet accident. */
  :global(body),
  :global(.markdown) {
    font-feature-settings: "tnum" 1, "cv02" 1, "ss01" 1;
    background: var(--v-bg);
    color: var(--v-ink);
  }
  /* A table's own scroller inflates the document's scroll width, which lets
     a phone pan sideways into empty space. Every wide thing on the site
     scrolls inside its own box, so the page itself never needs to. */
  :global(body) {
    overflow-x: hidden;
  }

  /* ---- chrome -------------------------------------------------------- */
  :global(header) {
    background: rgba(10, 14, 19, 0.82) !important;
    backdrop-filter: saturate(140%) blur(10px);
    border-bottom: 1px solid var(--v-line) !important;
  }
  /* The wordmark: Evidence renders `title` as plain text, so dress it. */
  :global(header a[href="/"]),
  :global(nav a[href="/"] .capitalize) {
    font-weight: 800;
    letter-spacing: 0.16em;
    font-size: 0.78rem !important;
    color: var(--v-brand) !important;
  }
  :global(aside),
  :global(nav#sidebar) {
    background: var(--v-bg) !important;
    border-right: 1px solid var(--v-line) !important;
  }
  /* Sidebar links: quiet until they matter. */
  :global(#sidebar a),
  :global(aside a) {
    border-radius: var(--v-radius-sm);
    font-size: 0.82rem;
    letter-spacing: 0.005em;
    transition: background 120ms ease, color 120ms ease;
  }
  :global(#sidebar a:hover),
  :global(aside a:hover) {
    background: var(--v-surface);
    color: var(--v-ink) !important;
  }
  /* Section headers in the sidebar (folder labels). */
  :global(#sidebar .font-semibold),
  :global(aside .font-semibold) {
    font-size: 0.62rem !important;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--v-ink-3) !important;
  }

  /* The content column. Wide enough for a ten-column blotter, capped so
     the prose does not run to 2000px on an ultrawide monitor. */
  :global(article.markdown) {
    max-width: 1240px;
    width: 100%;
    min-width: 0;
    padding-right: 1.25rem;
  }
  /* The content column is a flex child. Without min-width:0 a flex item is
     sized by its widest content, so one wide table stretched the whole page
     and the phone view lost every column after the matchup. */
  :global(.flex-grow.overflow-x-hidden) {
    min-width: 0;
  }

  /* ---- typography ---------------------------------------------------- */
  :global(.markdown h1.title) {
    font-size: 1.55rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: #f2f7fb;
    margin-bottom: 0.15rem;
  }
  :global(.markdown h2) {
    font-size: 0.72rem !important;
    font-weight: 700 !important;
    text-transform: uppercase;
    letter-spacing: 0.13em;
    color: var(--v-ink-3) !important;
    border: 0 !important;
    margin: 2.1rem 0 0.7rem !important;
    padding: 0 !important;
  }
  :global(.markdown h3) {
    font-size: 0.95rem !important;
    font-weight: 650 !important;
    color: var(--v-ink) !important;
    margin: 1.4rem 0 0.5rem !important;
  }
  :global(.markdown p) {
    font-size: 0.85rem;
    line-height: 1.6;
    color: var(--v-ink-2);
  }
  /* A paragraph that is nothing but emphasis is a footnote — the small grey
     line under a table or a chart. Emphasis *inside* a sentence stays
     inline and keeps its size. */
  :global(.markdown p > em:only-child) {
    color: var(--v-ink-3);
    font-style: normal;
    font-size: 0.78rem;
    line-height: 1.55;
    display: inline-block;
  }
  :global(.markdown li em),
  :global(.markdown p em:not(:only-child)) {
    font-style: normal;
    color: var(--v-ink-2);
  }
  :global(.markdown a) {
    color: var(--v-brand);
    text-decoration: none;
    border-bottom: 1px solid rgba(61, 218, 208, 0.28);
  }
  :global(.markdown a:hover) {
    border-bottom-color: var(--v-brand);
  }
  :global(.markdown code) {
    background: var(--v-surface-2);
    border: 1px solid var(--v-line);
    border-radius: 5px;
    padding: 0.08em 0.36em;
    font-size: 0.78em;
    color: var(--v-ink-2);
  }
  :global(.markdown ul) {
    font-size: 0.85rem;
    color: var(--v-ink-2);
  }

  /* ---- tables --------------------------------------------------------
     Evidence's DataTable is a BI table: light rules, roomy rows, and it
     overflows its container rather than scrolling. Re-dressed as a
     blotter — hairline rules, tight rows, uppercase micro headers — and
     wrapped in its own scroller so a wide table never clips its last
     columns or pushes the page sideways on a phone.
     -------------------------------------------------------------------- */
  :global(.markdown table) {
    font-size: 0.8rem;
    border-collapse: separate;
    border-spacing: 0;
  }
  :global(.markdown thead th) {
    font-size: 0.6rem !important;
    font-weight: 700 !important;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--v-ink-3) !important;
    border-bottom: 1px solid var(--v-line-2) !important;
    padding-top: 0.35rem !important;
    padding-bottom: 0.45rem !important;
    background: transparent !important;
    white-space: nowrap;
  }
  :global(.markdown tbody td) {
    border-bottom: 1px solid var(--v-line) !important;
    padding-top: 0.42rem !important;
    padding-bottom: 0.42rem !important;
    color: var(--v-ink);
    vertical-align: middle;
  }
  :global(.markdown tbody tr:hover td) {
    background: var(--v-surface);
  }
  :global(.markdown tbody tr:last-child td) {
    border-bottom: 0 !important;
  }
  /* The scroller. Evidence wraps every table in .table-container >
     .scrollbox; without a width ceiling on the container the table simply
     grows past the content column and its last columns are lost off the
     right edge — which is what made the old board unreadable on a laptop
     and useless on a phone. Cap the container, let the box scroll. */
  :global(.table-container) {
    max-width: 100%;
    width: 100%;
    min-width: 0;
    overflow: hidden;
  }
  :global(.table-container .scrollbox) {
    overflow-x: auto;
    width: 100%;
    max-width: 100%;
    scrollbar-width: thin;
    scrollbar-color: var(--v-line-2) transparent;
  }
  :global(.table-container .scrollbox::-webkit-scrollbar) { height: 7px; }
  :global(.table-container .scrollbox::-webkit-scrollbar-thumb) {
    background: var(--v-line-2);
    border-radius: 999px;
  }
  /* A cell of prose never gets to set the table's width. */
  :global(.markdown tbody td) { max-width: 26rem; }
  /* Pagination + the table's own controls, quieted. */
  :global(.markdown table + div),
  :global(.pagination) {
    font-size: 0.7rem !important;
    color: var(--v-ink-3) !important;
  }

  /* ---- Evidence value + chart surfaces -------------------------------- */
  :global(.markdown .chart-container),
  :global(.echarts-container) {
    background: transparent !important;
  }
  /* Evidence renders a red box when a BigValue's dataset is empty. On a
     dashboard that is fed by yesterday's grade, "no data yet" is a normal
     state, not an error — the pages carry their own empty states, so the
     framework's error chrome is suppressed to a quiet line. */
  :global(.markdown .error),
  :global(.markdown .inline-error) {
    background: var(--v-surface) !important;
    border: 1px dashed var(--v-line-2) !important;
    border-radius: var(--v-radius-sm);
    color: var(--v-ink-3) !important;
    font-size: 0.72rem !important;
    padding: 0.5rem 0.7rem !important;
  }

  /* ---- inputs -------------------------------------------------------- */
  :global(.markdown button),
  :global(.markdown select) {
    font-size: 0.75rem !important;
  }
  /* The league filter reads as a segmented control, not a row of buttons. */
  :global(.button-group) {
    background: var(--v-surface);
    border: 1px solid var(--v-line);
    border-radius: 999px;
    padding: 2px;
    display: inline-flex;
    gap: 2px;
  }
  :global(.button-group button) {
    border: 0 !important;
    border-radius: 999px !important;
    padding: 0.24rem 0.72rem !important;
    color: var(--v-ink-2) !important;
    background: transparent !important;
    font-weight: 600 !important;
    letter-spacing: 0.04em;
  }
  :global(.button-group button[aria-checked="true"]),
  :global(.button-group button.selected) {
    background: var(--v-surface-2) !important;
    color: var(--v-brand) !important;
    box-shadow: inset 0 0 0 1px var(--v-line-2);
  }

  /* ---- print / phone -------------------------------------------------- */
  @media (max-width: 640px) {
    :global(.markdown h1.title) {
      font-size: 1.3rem;
    }
    :global(.markdown h2) {
      margin-top: 1.6rem !important;
    }
  }
</style>
