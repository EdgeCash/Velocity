<script>
  // The simulated outcome distribution, with the market's line drawn on it.
  //
  // This is the richest thing the model produces and until now it only
  // existed on the matchup detail page: ~80 bins per game of simulated
  // totals and margins, written every run and read by almost nothing.
  //
  // What it shows is deliberately *not* the number beside it on the card.
  // The board's belief is anchored to the market — `belief = market + 0.2 ×
  // (model − market)` — so the shaded mass here is the raw simulation,
  // before it is talked down. Seeing the two together is the whole point:
  // the shaded area is what the model thinks, the belief is what it is
  // willing to bet after conceding that the close is usually right.
  export let bins = [];
  /** The market number the bet is struck at. */
  export let line = null;
  /** True when the bet wins on the upside (over, or home covering). */
  export let above = true;
  /* Count half of the bin sitting exactly on the cut. Only the moneyline
     asks for this: its `0` bin is a rounding artifact of a continuous
     margin, and the model prices it as a coin flip. See distThreshold(). */
  export let splitTie = false;
  export let height = 46;
  /** Buckets to draw. The raw bins are 1-point wide and read as noise. */
  export let buckets = 34;

  const W = 300;

  function clean(rows) {
    return (rows ?? [])
      .map((r) => ({ value: Number(r.value), prob: Number(r.prob) }))
      .filter((r) => Number.isFinite(r.value) && Number.isFinite(r.prob) && r.prob > 0);
  }

  // Mass on the bet's side, computed from the *raw* bins rather than the
  // drawn buckets, so the printed number is exact and the picture is only
  // ever a picture.
  function coveredMass(rows, at, up, split) {
    if (at === null || at === undefined || !Number.isFinite(Number(at))) return null;
    const t = Number(at);
    return rows.reduce((acc, r) => {
      if (up ? r.value > t : r.value < t) return acc + r.prob;
      if (split && r.value === t) return acc + r.prob / 2;
      return acc;
    }, 0);
  }

  function shape(rows, at, up, n) {
    if (rows.length === 0) return null;
    const total = rows.reduce((a, r) => a + r.prob, 0) || 1;
    const mean = rows.reduce((a, r) => a + r.value * r.prob, 0) / total;
    const sd = Math.sqrt(
      rows.reduce((a, r) => a + (r.value - mean) ** 2 * r.prob, 0) / total) || 1;
    // Window on the distribution, widened if the line sits outside it — a
    // deep ladder rung is exactly the case where you need to see how far
    // into the tail the bet is.
    let lo = mean - 3 * sd;
    let hi = mean + 3 * sd;
    if (at !== null && at !== undefined && Number.isFinite(Number(at))) {
      const t = Number(at);
      lo = Math.min(lo, t - 0.5 * sd);
      hi = Math.max(hi, t + 0.5 * sd);
    }
    const span = hi - lo || 1;
    const step = span / n;
    const cells = Array.from({ length: n }, (_, i) => ({
      x0: lo + i * step, x1: lo + (i + 1) * step, prob: 0,
    }));
    // Nothing outside the window is dropped; it lands in the end bucket, so
    // the drawn mass still totals one.
    for (const r of rows) {
      let i = Math.floor((r.value - lo) / step);
      i = Math.max(0, Math.min(n - 1, i));
      cells[i].prob += r.prob;
    }
    const peak = Math.max(...cells.map((c) => c.prob)) || 1;
    const bw = W / n;
    return {
      lo, hi, span, peak, bw,
      cells: cells.map((c, i) => ({
        ...c,
        mid: (c.x0 + c.x1) / 2,
        x: i * bw,
        h: (c.prob / peak),
      })),
      lineX: at === null || at === undefined || !Number.isFinite(Number(at))
        ? null : ((Number(at) - lo) / span) * W,
    };
  }

  $: rows = clean(bins);
  $: s = shape(rows, line, above, buckets);
  $: mass = coveredMass(rows, line, above, splitTie);
  $: pct = mass === null ? null : `${(mass * 100).toFixed(1)}%`;
</script>

{#if s}
  <div class="dist" style={`--h:${height}px`}>
    <svg viewBox={`0 0 ${W} 100`} preserveAspectRatio="none" role="img"
      aria-label={pct ? `simulated distribution, ${pct} on this side of ${line}` : 'simulated distribution'}>
      {#each s.cells as c}
        <rect
          x={c.x + 0.35}
          y={100 - c.h * 100}
          width={Math.max(s.bw - 0.7, 0.6)}
          height={Math.max(c.h * 100, 0.6)}
          class={s.lineX === null
            ? 'bar'
            : (above ? c.mid > line : c.mid < line) ? 'bar on' : 'bar off'}
        />
      {/each}
      {#if s.lineX !== null}
        <line x1={s.lineX} x2={s.lineX} y1="0" y2="100" class="rule" />
      {/if}
    </svg>
    {#if pct}
      <div class="foot">
        <span class="k">Sim</span>
        <span class="v">{pct}</span>
        <span class="at">of simulated outcomes land {above ? 'past' : 'under'} {line}</span>
      </div>
    {/if}
  </div>
{/if}

<style>
  .dist { margin-top: 0.4rem; }
  svg {
    display: block;
    width: 100%;
    height: var(--h, 46px);
    overflow: visible;
  }
  /* The covered side is lit; everything else is the same shape in the
     ground's own grey, so the eye reads the *area* rather than a chart. */
  .bar { fill: var(--v-ink-3, #5d6b7c); opacity: 0.32; }
  .bar.on { fill: var(--v-brand, #3ddad0); opacity: 0.72; }
  .bar.off { fill: var(--v-ink-3, #5d6b7c); opacity: 0.26; }
  .rule {
    stroke: var(--v-warn, #f5b342);
    stroke-width: 1.4;
    vector-effect: non-scaling-stroke;
  }
  .foot {
    display: flex;
    align-items: baseline;
    gap: 0.36rem;
    margin-top: 0.2rem;
    font-size: 0.55rem;
    text-transform: uppercase;
    letter-spacing: 0.11em;
    font-weight: 700;
    color: var(--v-ink-3, #5d6b7c);
  }
  .foot .v {
    font-family: var(--v-board, "Saira Condensed", sans-serif);
    font-size: 0.86rem;
    letter-spacing: 0.01em;
    color: var(--v-ink-2, #8fa0b3);
    font-variant-numeric: tabular-nums;
  }
  .foot .at { letter-spacing: 0.06em; font-weight: 600; text-transform: none; }
</style>
