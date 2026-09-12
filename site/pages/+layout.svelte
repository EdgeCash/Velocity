<script>
  // The chrome — or what is left of it.
  //
  // The site is one page now, so Evidence's sidebar, header, breadcrumbs and
  // table of contents are all navigation for a thing there is nothing to
  // navigate. They are off, and the hub draws its own top bar. What remains
  // here is the part that is genuinely global: the vendored numeral face and
  // the design tokens every component is written against.
  //
  // The design is written down in docs/SITE.md. The short version: this is a
  // board, not a BI tool. Numbers wear a condensed athletic face, prices are
  // sign-explicit with a real minus, red is held in reserve, and every state
  // is a tinted pill rather than a block of paint.
  import '@evidence-dev/tailwind/fonts.css';
  import '../app.css';
  import { EvidenceDefaultLayout } from '@evidence-dev/core-components';
  export let data;
</script>

<svelte:head>
  <meta name="theme-color" content="#06090d" />
</svelte:head>

<EvidenceDefaultLayout
  {data}
  title="VELOCITY"
  builtWithEvidence={false}
  hideSidebar={true}
  hideHeader={true}
  hideTOC={true}
  hideBreadcrumbs={true}
  fullWidth={true}
>
  <div slot="content" class="shell">
    <slot />
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
    --v-glow: 0 0 18px rgba(61, 218, 208, 0.22);

    --v-board: "Saira Condensed", "Inter", ui-sans-serif, system-ui, sans-serif;
    --v-num: "Inter", ui-sans-serif, system-ui, sans-serif;
  }

  :global(body),
  :global(.markdown) {
    font-feature-settings: "tnum" 1, "cv02" 1, "ss01" 1;
    background: var(--v-bg);
    color: var(--v-ink);
  }
  /* Every wide thing on the surface scrolls inside its own box, so the page
     itself never pans sideways on a phone. */
  :global(body) { overflow-x: hidden; }

  /* The hub sets its own width and gutters; Evidence's article wrapper must
     not re-impose a prose column on it. */
  :global(article.markdown) {
    max-width: none;
    width: 100%;
    min-width: 0;
    padding: 0;
  }
  /* The content column is a flex child. Without min-width:0 a flex item is
     sized by its widest content, so one wide row stretches the whole page and
     the phone view loses everything past it. */
  :global(.flex-grow.overflow-x-hidden) { min-width: 0; }

  /* Evidence renders a red box when a query's dataset is empty. On a surface
     fed by yesterday's grade, "no data yet" is a normal state and every panel
     carries its own empty state, so the framework's error chrome is quieted
     to a line. */
  :global(.markdown .error),
  :global(.markdown .inline-error) {
    background: var(--v-lvl-1) !important;
    border: 1px dashed var(--v-line-2) !important;
    border-radius: var(--v-radius-sm);
    color: var(--v-ink-3) !important;
    font-size: 0.72rem !important;
    padding: 0.5rem 0.7rem !important;
  }

  .shell {
    min-width: 0;
    width: 100%;
  }
</style>
