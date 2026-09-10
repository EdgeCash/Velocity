<script>
  // The Velocity chrome. Evidence's default layout does the routing, the
  // sidebar tree and the query plumbing; everything visible is re-dressed
  // here. Two props do the branding — a text wordmark instead of the
  // Evidence logo, and no "Built with Evidence" footer — and the global
  // style block below is the design system the pages are written against.
  //
  // The design is written down in docs/SITE.md. The short version: this is a
  // board, not a BI tool. Numbers wear a condensed athletic face, prices are
  // sign-explicit with a real minus, red is held in reserve, and every state
  // is a tinted pill rather than a block of paint.
  import '@evidence-dev/tailwind/fonts.css';
  import '../app.css';
  import { EvidenceDefaultLayout } from '@evidence-dev/core-components';
  export let data;

  // The stub. Every real book prints the boring line — jurisdiction, time,
  // slip id — at the bottom of the ticket, and that bureaucratic texture is
  // what makes a page read as a book's rather than a chart's. The site is
  // prerendered, so this is the moment the board was built.
  const built = new Date().toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
</script>

<svelte:head>
  <meta name="theme-color" content="#06090d" />
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
  <div slot="content" class="shell">
    <slot />
    <footer class="stub">
      <span>Velocity</span>
      <span>Private board · not advice · no order is ever placed from here</span>
      <span class="built">Built {built}</span>
    </footer>
  </div>
</EvidenceDefaultLayout>

<style>
  /* ------------------------------------------------------------------
     The numeral face. Every board in the genre ships one — DraftKings
     licenses Saira Condensed for exactly this job — because a condensed
     athletic grotesque is what separates a betting board from a
     spreadsheet, and it does it before a single number is read. Prose
     stays in Inter; anything that is a quantity is set in this.

     Vendored as the latin subset (three weights, ~54KB total) rather
     than fetched from a font CDN, so the site has no third-party
     request and works from a cold cache behind Access.
     ------------------------------------------------------------------ */
  @font-face {
    font-family: "Saira Condensed";
    font-style: normal;
    font-weight: 500;
    font-display: swap;
    src: url("/fonts/saira-condensed-500.woff2") format("woff2");
  }
  @font-face {
    font-family: "Saira Condensed";
    font-style: normal;
    font-weight: 600;
    font-display: swap;
    src: url("/fonts/saira-condensed-600.woff2") format("woff2");
  }
  @font-face {
    font-family: "Saira Condensed";
    font-style: normal;
    font-weight: 700;
    font-display: swap;
    src: url("/fonts/saira-condensed-700.woff2") format("woff2");
  }

  /* ------------------------------------------------------------------
     Tokens.

     Depth is a five-step near-black ladder inside a 20-value luminance
     range, not #111/#222/#333 — that narrow range is what reads as a lit
     room rather than a theme toggle. Hairlines are white at low alpha,
     never a solid grey, so they read as light catching an edge and
     composite correctly over whatever sits beneath them.

     Money colors are status, never identity: everything that wears one
     also carries a sign, an arrow or a word (docs/SITE.md). The negative
     is salmon rather than red, and true red is spent only on the kill
     switch — a board where every favourite is painted red reads as
     broken.
     ------------------------------------------------------------------ */
  :global(:root) {
    /* ground → surface ladder */
    --v-bg: #06090d;
    --v-lvl-0: #0b1017;
    --v-lvl-1: #101822;
    --v-lvl-2: #16202c;
    --v-hover: #1a2531;
    --v-chip: #131c26;
    --v-line: rgba(255, 255, 255, 0.07);
    --v-line-2: rgba(255, 255, 255, 0.13);

    /* ink — primary is not pure white */
    --v-ink: rgba(233, 241, 249, 0.92);
    --v-ink-2: #8fa0b3;
    --v-ink-3: #5d6b7c;

    /* brand: interactive and identity, deliberately not the money green */
    --v-brand: #3ddad0;
    --v-brand-dim: #2bb3ab;
    --v-brand-deep: #14403d;
    --v-brand-tint: rgba(61, 218, 208, 0.13);

    /* the money axis */
    --v-pos: #35d07f;
    --v-pos-tint: rgba(53, 208, 127, 0.13);
    --v-neg: #f97289;
    --v-neg-tint: rgba(249, 114, 137, 0.13);
    --v-warn: #f5b342;
    --v-warn-tint: rgba(245, 179, 66, 0.13);
    --v-info: #5b8dff;
    --v-info-tint: rgba(91, 141, 255, 0.13);
    /* true red, held in reserve: the kill switch and nothing else */
    --v-alert: #e5484d;
    /* the low-confidence slate: thin samples are desaturated, not hidden */
    --v-thin: #7c8899;
    --v-thin-tint: rgba(124, 136, 153, 0.1);

    --v-radius: 12px;
    --v-radius-sm: 8px;
    /* The lit-edge inset: one line that makes a pill read as glass. */
    --v-lift: inset 0 -1px 3px rgba(255, 255, 255, 0.07),
      inset 0 -1px 1px rgba(255, 255, 255, 0.22);
    --v-glow: 0 0 18px rgba(61, 218, 208, 0.22);

    --v-board: "Saira Condensed", "Inter", ui-sans-serif, system-ui, sans-serif;
    --v-num: "Inter", ui-sans-serif, system-ui, sans-serif;
    --header-height: 3.25rem;
  }

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
    background: rgba(6, 9, 13, 0.82) !important;
    backdrop-filter: saturate(140%) blur(10px);
    border-bottom: 1px solid var(--v-line) !important;
  }
  /* The wordmark. Evidence renders `title` as plain text, so dress it — in
     the board face, tracked out, because that is what a wordmark on a
     scoreboard looks like. */
  :global(header a[href="/"]),
  :global(nav a[href="/"] .capitalize) {
    font-family: var(--v-board);
    font-weight: 700;
    letter-spacing: 0.22em;
    font-size: 0.95rem !important;
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
    background: var(--v-lvl-1);
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
    background: var(--v-lvl-2);
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
  /* Every quantity on the site wears the board face. Evidence tags each
     cell with its column type, so this reaches every number in every
     table without a page having to ask for it. Condensed runs narrow at
     a given size, so it is set a shade larger than the prose around it
     and the digits are widened back out very slightly. */
  :global(.markdown td.number) {
    font-family: var(--v-board);
    font-size: 0.92rem;
    font-weight: 600;
    letter-spacing: 0.015em;
    font-variant-numeric: tabular-nums;
  }
  :global(.markdown tbody tr:hover td) {
    background: var(--v-hover);
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
    background: var(--v-lvl-1) !important;
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
  /* The league filter reads as a segmented control, not a row of buttons.
     The genre is consistent about this: a filled segmented pill means a
     mode switch, and the active segment is the only lit object in it. */
  :global(.button-group) {
    background: var(--v-lvl-1);
    border: 1px solid var(--v-line);
    border-radius: 999px;
    padding: 2px;
    display: inline-flex;
    gap: 2px;
  }
  :global(.button-group button) {
    border: 0 !important;
    border-radius: 999px !important;
    padding: 0.24rem 0.78rem !important;
    color: var(--v-ink-2) !important;
    background: transparent !important;
    font-family: var(--v-board);
    font-size: 0.82rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.08em;
    transition: background 130ms ease, color 130ms ease;
  }
  :global(.button-group button[aria-checked="true"]),
  :global(.button-group button.selected) {
    background: var(--v-brand-deep) !important;
    color: var(--v-brand) !important;
    box-shadow: inset 0 0 0 1px rgba(61, 218, 208, 0.3);
  }

  /* The content wrapper is a flex child of Evidence's column. Without
     min-width:0 it is sized by its widest content, which is how one wide
     table pushed the phone view sideways again after the stub was added. */
  .shell {
    min-width: 0;
    width: 100%;
  }

  /* ---- the stub ------------------------------------------------------- */
  .stub {
    max-width: 1240px;
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem 1rem;
    margin: 3rem 0 1.6rem;
    padding-top: 0.7rem;
    border-top: 1px solid var(--v-line);
    font-family: var(--v-board);
    font-size: 0.66rem;
    font-weight: 600;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--v-ink-3);
  }
  .stub span:first-child { color: var(--v-brand); letter-spacing: 0.22em; }
  .built { margin-left: auto; }

  /* ---- print / phone -------------------------------------------------- */
  @media (max-width: 640px) {
    :global(.markdown h1.title) {
      font-size: 1.3rem;
    }
    :global(.markdown h2) {
      margin-top: 1.6rem !important;
    }
    :global(.markdown td.number) {
      font-size: 0.88rem;
    }
  }
</style>
