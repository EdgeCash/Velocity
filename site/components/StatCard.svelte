<script>
  // One number, said properly. The dashboard's unit of headline.
  //
  // Evidence's BigValue renders a red "error" box when its dataset is empty,
  // which is how a page that is simply waiting for tomorrow's grade ends up
  // looking broken. This takes a plain value, renders a real em-dash when it
  // is missing, and never errors.
  import { signed, num, pct, arrow, tone } from './format.js';

  export let label = '';
  /** The number itself. */
  export let value = null;
  /** 'units' | 'percent' | 'number' | 'text' */
  export let format = 'number';
  export let dp = 2;
  /** Show a leading +/− on the value (money that can go either way). */
  export let sign = false;
  /** Small line under the value: a comparison, a denominator, a date. */
  export let sub = '';
  /** A second number rendered as an arrow + delta beside `sub`. */
  export let delta = null;
  export let deltaFormat = 'percent';
  /** Fill 0..1 for a meter under the value (exposure against its cap). */
  export let meter = null;
  /** 'brand' | 'money' | 'plain' — what the value is allowed to be colored by. */
  export let accent = 'plain';
  export let hint = '';

  function render(v, f, d, s) {
    if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) {
      return f === 'text' ? (v || '—') : '—';
    }
    if (f === 'text') return String(v);
    if (f === 'percent') return pct(v, d, s);
    if (f === 'units') return `${s ? signed(v, d) : num(v, d)}u`;
    return s ? signed(v, d) : num(v, d);
  }

  $: shown = render(value, format, dp, sign);
  $: valueTone = accent === 'money' ? tone(value) : '';
  $: fill = meter === null || meter === undefined || Number.isNaN(Number(meter))
    ? null
    : Math.max(0, Math.min(1, Number(meter)));
  // A meter past ~90% of its cap is the thing you want to notice.
  $: meterTone = fill === null ? '' : fill >= 0.9 ? 'hot' : fill >= 0.6 ? 'warm' : '';
</script>

<div class="card" title={hint}>
  <div class="label">{label}</div>
  <div class="value {valueTone} {accent}">{shown}</div>

  {#if fill !== null}
    <div class="meter {meterTone}" role="img" aria-label={`${Math.round(fill * 100)}% of cap`}>
      <div class="fill" style={`width:${Math.max(fill * 100, 1.5)}%`}></div>
    </div>
  {/if}

  {#if delta !== null && delta !== undefined && !Number.isNaN(Number(delta))}
    <div class="sub">
      <span class={`delta ${tone(delta)}`}>
        <span class="arrow">{arrow(delta)}</span>
        {deltaFormat === 'percent' ? pct(delta, 1, true) : signed(delta, dp)}
      </span>
      {#if sub}<span class="sub-text">{sub}</span>{/if}
    </div>
  {:else if sub}
    <div class="sub"><span class="sub-text">{sub}</span></div>
  {/if}
</div>

<style>
  /* The card is lit from its top-left corner rather than filled flat. One
     radial wash per card is what stops a row of tiles reading as four grey
     rectangles; it costs nothing and it is the difference between a
     dashboard and a product. */
  .card {
    position: relative;
    overflow: hidden;
    background: var(--v-lvl-1, #101822);
    border: 1px solid var(--v-line, rgba(255, 255, 255, 0.07));
    border-radius: var(--v-radius, 12px);
    padding: 0.72rem 0.85rem 0.78rem;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 0.28rem;
  }
  .card::before {
    content: "";
    position: absolute;
    inset: 0;
    pointer-events: none;
    background: radial-gradient(
      340px 120px at 0% 0%,
      rgba(255, 255, 255, 0.05),
      transparent 70%
    );
  }
  .card > * { position: relative; }
  .label {
    font-size: 0.6rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--v-ink-3, #5d6b7c);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  /* The number is the product, and the typography admits it: the board
     face, one weight step and roughly 3x the size of the label above it. */
  .value {
    font-family: var(--v-board, "Saira Condensed", sans-serif);
    font-size: 1.95rem;
    font-weight: 700;
    line-height: 1;
    letter-spacing: 0.005em;
    color: var(--v-ink, rgba(233, 241, 249, 0.92));
    font-variant-numeric: tabular-nums;
  }
  .value.brand { color: var(--v-brand, #3ddad0); }
  .value.pos { color: var(--v-pos, #35d07f); }
  .value.neg { color: var(--v-neg, #f97289); }
  .sub {
    display: flex;
    align-items: baseline;
    gap: 0.4rem;
    font-size: 0.7rem;
    line-height: 1.3;
    min-height: 1rem;
  }
  .sub-text { color: var(--v-ink-3, #5d6b7c); }
  .delta {
    font-family: var(--v-board, "Saira Condensed", sans-serif);
    font-size: 0.82rem;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .delta.pos { color: var(--v-pos, #35d07f); }
  .delta.neg { color: var(--v-neg, #f97289); }
  .delta.flat { color: var(--v-ink-3, #5d6b7c); }
  /* The arrow is the point: it says up or down without relying on hue. */
  .arrow { font-size: 0.7em; }
  .meter {
    height: 3px;
    border-radius: 999px;
    background: var(--v-line-2, rgba(255, 255, 255, 0.13));
    overflow: hidden;
    margin: 0.1rem 0 0.05rem;
  }
  .meter .fill {
    height: 100%;
    background: var(--v-brand-dim, #2bb3ab);
    border-radius: 999px;
  }
  .meter.warm .fill { background: var(--v-warn, #f5b342); }
  .meter.hot .fill { background: var(--v-neg, #f97289); }

  @media (max-width: 640px) {
    .value { font-size: 1.6rem; }
    .card { padding: 0.6rem 0.7rem 0.65rem; }
  }
</style>
