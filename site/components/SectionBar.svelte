<script>
  // A section header that carries its own summary on the right, so a heading
  // is never just a word — "The card · 6 plays · 12.5u" says what is below
  // before you read it.
  export let title = '';
  export let meta = '';
  export let tone = 'plain';
</script>

<div class="bar">
  <h2 class="t">{title}</h2>
  {#if meta}<span class="m {tone}">{meta}</span>{/if}
  <slot />
</div>

<style>
  .bar {
    display: flex;
    align-items: baseline;
    gap: 0.15rem 0.7rem;
    flex-wrap: wrap;
    border-bottom: 1px solid var(--v-line, #1c2733);
    padding-bottom: 0.4rem;
    margin: 1.9rem 0 0.75rem;
  }
  .t {
    font-size: 0.72rem !important;
    font-weight: 700 !important;
    text-transform: uppercase;
    letter-spacing: 0.13em;
    color: var(--v-ink-2, #93a1b1) !important;
    margin: 0 !important;
    padding: 0 !important;
    border: 0 !important;
  }
  .m {
    margin-left: auto;
    flex: 0 1 auto;
    font-size: 0.7rem;
    font-variant-numeric: tabular-nums;
    color: var(--v-ink-3, #64748b);
    text-align: right;
    min-width: 0;
  }
  .m.brand { color: var(--v-brand, #3ddad0); }
  .m.pos { color: var(--v-pos, #3fb950); }
  .m.neg { color: var(--v-neg, #f0616a); }
  /* On a phone the summary drops under the title rather than being
     squeezed off the right edge. */
  @media (max-width: 560px) {
    .m { margin-left: 0; text-align: left; width: 100%; }
  }
</style>
