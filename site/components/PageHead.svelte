<script>
  // The page's identity line: what this is, how fresh it is.
  //
  // Replaces the gradient hero band, which was the most template-looking
  // thing on the site. A flat title with the freshness stamp set against it
  // reads as a product; a glowing box reads as a demo.
  import { stampLabel } from './format.js';

  export let title = '';
  export let subtitle = '';
  /** A run stamp (20260909T191129Z) or any pre-formatted string. */
  export let stamp = '';
  export let stampLabelText = 'as of';
</script>

<header class="ph">
  <div class="left">
    <h1>{title}</h1>
    {#if subtitle}<p>{subtitle}</p>{/if}
  </div>
  {#if stamp}
    <div class="stamp">
      <span class="k">{stampLabelText}</span>
      <span class="v">{stampLabel(stamp)}</span>
    </div>
  {/if}
</header>

<style>
  .ph {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 1rem;
    flex-wrap: wrap;
    padding-bottom: 0.7rem;
    border-bottom: 1px solid var(--v-line, #1c2733);
    margin-bottom: 0.2rem;
  }
  h1 {
    font-size: 1.45rem;
    font-weight: 700;
    letter-spacing: -0.022em;
    color: #f2f7fb;
    margin: 0;
    line-height: 1.15;
  }
  p {
    margin: 0.25rem 0 0;
    font-size: 0.78rem;
    line-height: 1.5;
    color: var(--v-ink-3, #64748b);
    max-width: 44rem;
  }
  .stamp {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    white-space: nowrap;
    padding-top: 0.15rem;
  }
  .k {
    font-size: 0.55rem;
    text-transform: uppercase;
    letter-spacing: 0.13em;
    color: var(--v-ink-3, #64748b);
    font-weight: 700;
  }
  .v {
    font-size: 0.74rem;
    font-weight: 600;
    color: var(--v-brand, #3ddad0);
    font-variant-numeric: tabular-nums;
  }
  @media (max-width: 560px) {
    h1 { font-size: 1.25rem; }
    .stamp { align-items: flex-start; }
  }
</style>
