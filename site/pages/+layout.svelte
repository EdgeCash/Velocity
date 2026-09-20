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
  <meta name="theme-color" content="#6b5442" />
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

     The park palette (docs/SITE.md, "the park re-skin"). The surface was a
     five-step near-black ladder; it is now Ballpark Pal's ballpark — bone
     page, white cards, infield dirt on the band, grass green on the
     actions. Hairlines are BLACK at low alpha now, not white: on a light
     ground a white hairline is invisible, and every one of them had to
     flip.

     Taken from their stylesheets, and then darkened. Their own pairs do
     not clear WCAG AA — cream on dirt is 3.79:1 and white on grass is
     2.78:1 — which they get away with at 75px and this surface, at a
     quarter that size, would not. So the hues are theirs and the
     luminances are ours: `scripts/check_site_contrast.py` is the gate, and
     every pair below clears 4.5:1 on every surface it lands on.

     Grass green is the one color that has to be bright to read as theirs,
     and a bright green cannot carry small text. So it is split: `--v-grass`
     is a FILL only, always with `--v-ink` on top (6.2:1), while `--v-brand`
     is the darkened green that text is set in. Do not set type in
     `--v-grass`.

     Money colors are status, never identity: everything that wears one
     also carries a sign, an arrow or a word (docs/SITE.md). The negative is
     CLAY rather than red — the same reasoning that made it salmon on the
     dark board, in the park's own material — and true red is spent only on
     the kill switch, a board where every favourite is painted red reads as
     broken.
     ------------------------------------------------------------------ */
  :global(:root) {
    /* ground → surface ladder: bone page, white cards */
    --v-bg: #f3f0e7;
    --v-lvl-0: #ece8dc;
    --v-lvl-1: #ffffff;
    --v-lvl-2: #faf8f3;
    --v-hover: #ece8dc;
    --v-chip: #efebe0;
    --v-line: rgba(31, 26, 21, 0.12);
    --v-line-2: rgba(31, 26, 21, 0.22);

    /* ink — primary is warm near-black, not pure black */
    --v-ink: #1e1a15;
    --v-ink-2: #544c42;
    --v-ink-3: #686055;

    /* brand: interactive and identity. On a light ground "dim" means
       DARKER, so brand and brand-dim sit close together; brand-deep is the
       pale fill that active chrome wears, with brand as its text. */
    --v-brand: #2f6d32;
    --v-brand-dim: #327335;
    --v-brand-deep: #dfe8d9;
    --v-brand-tint: rgba(47, 109, 50, 0.13);

    /* the park's own materials. Fills only — `--v-ink` goes on top of each,
       never `--v-grass` or `--v-band` as type. */
    --v-grass: #4caf50;
    --v-band: #6b5442;
    --v-band-ink: #f3f0e7;
    /* The band is a ground of its own, so it carries a ground's worth of ink.
       `.topbar` re-points --v-ink and friends at these, which means every
       component inside it adapts without knowing the band exists — the
       alternative was editing eight components' colour rules by hand. Solved
       with the same `readable_on` the crests use, against #6b5442. */
    --v-band-ink-2: #d9d3c8;
    --v-band-ink-3: #d3cec3;
    --v-band-brand: #abdbad;
    --v-band-warn: #f8c772;
    --v-band-alert: #f6c2c3;
    --v-band-pos: #80e2af;
    --v-band-line: rgba(243, 240, 231, 0.18);

    /* the money axis */
    --v-pos: #187034;
    --v-pos-tint: rgba(24, 112, 52, 0.13);
    --v-neg: #8f3a1e;
    --v-neg-tint: rgba(143, 58, 30, 0.13);
    --v-warn: #855700;
    --v-warn-tint: rgba(133, 87, 0, 0.13);
    --v-info: #1f5fae;
    --v-info-tint: rgba(31, 95, 174, 0.13);
    /* true red, held in reserve: the kill switch and nothing else */
    --v-alert: #c5221f;
    /* the low-confidence slate: thin samples are desaturated, not hidden */
    --v-thin: #686055;
    --v-thin-tint: rgba(104, 96, 85, 0.1);

    /* Flatter than the dark board's, toward Ballpark Pal's 4-5px. A 12px
       radius on a white card over bone reads as a widget; theirs reads as a
       sheet of paper. */
    --v-radius: 9px;
    --v-radius-sm: 5px;
    --v-glow: 0 0 0 3px rgba(76, 175, 80, 0.3);

    --v-board: "Saira Condensed", "Inter", ui-sans-serif, system-ui, sans-serif;
    --v-num: "Inter", ui-sans-serif, system-ui, sans-serif;
  }

  /* Ballpark Pal runs a much larger type scale than a betting board does —
     75px headers, 20-28px body. Matching it literally would be absurd on a
     surface this dense, and the sizes in the components were measured against
     real reference boards (see "Legibility beats density"). So the scale is
     nudged at the root instead: every rem in the hub grows by the same 6%,
     the measured relationships between them are preserved exactly, and it is
     one number to turn if it goes too far. */
  :global(html) {
    font-size: 17px;
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
