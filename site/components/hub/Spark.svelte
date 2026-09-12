<script>
  // One simulated distribution, drawn small.
  //
  // A projection is a distribution, not a number — that is the claim the whole
  // model rests on — and a card that prints only "PIT −6, total 42" throws
  // that away. This is the shape behind those two numbers: ten thousand
  // simulated games binned by margin or by total, with the market's line drawn
  // through it so the disagreement is visible rather than asserted.
  //
  // Deliberately not a charting library. Evidence ships ECharts, but an
  // ECharts instance per game card on a 84-game college Saturday is a page
  // that janks on scroll; this is one path element and no runtime.
  export let rows = [];
  /** Draw a vertical rule here — the market's line, when there is one. */
  export let mark = null;
  export let width = 220;
  export let height = 44;
  export let label = '';

  // The tails of a simulated distribution are long and empty; drawing them
  // wastes most of the width on nothing. Trim to the central mass, but always
  // keep the mark in frame — a line sitting outside the plotted range is
  // exactly the case worth seeing.
  const TAIL = 0.004;

  $: clean = (rows ?? [])
    .map((r) => ({ value: Number(r.value), prob: Number(r.prob) }))
    .filter((r) => Number.isFinite(r.value) && Number.isFinite(r.prob))
    .sort((a, b) => a.value - b.value);

  $: trimmed = (() => {
    if (!clean.length) return [];
    let lo = 0;
    let hi = clean.length - 1;
    let acc = 0;
    while (lo < hi && acc + clean[lo].prob < TAIL) acc += clean[lo++].prob;
    acc = 0;
    while (hi > lo && acc + clean[hi].prob < TAIL) acc += clean[hi--].prob;
    const slice = clean.slice(lo, hi + 1);
    if (mark === null || mark === undefined || !Number.isFinite(Number(mark))) return slice;
    // Widen rather than clip when the line sits outside the kept range.
    const m = Number(mark);
    const first = slice[0]?.value ?? m;
    const last = slice[slice.length - 1]?.value ?? m;
    if (m >= first && m <= last) return slice;
    return clean.filter((r) => r.value >= Math.min(first, m) && r.value <= Math.max(last, m));
  })();

  $: peak = Math.max(...trimmed.map((r) => r.prob), 0);
  $: minValue = trimmed[0]?.value ?? 0;
  $: maxValue = trimmed[trimmed.length - 1]?.value ?? 1;
  $: span = maxValue - minValue || 1;

  const PAD = 2;
  $: x = (value) => PAD + ((value - minValue) / span) * (width - PAD * 2);
  $: y = (prob) => (peak > 0 ? height - PAD - (prob / peak) * (height - PAD * 2) : height - PAD);

  $: area = trimmed.length
    ? `M ${x(minValue)} ${height - PAD} `
      + trimmed.map((r) => `L ${x(r.value).toFixed(2)} ${y(r.prob).toFixed(2)}`).join(' ')
      + ` L ${x(maxValue)} ${height - PAD} Z`
    : '';

  $: markX = mark !== null && mark !== undefined && Number.isFinite(Number(mark))
    && Number(mark) >= minValue && Number(mark) <= maxValue
    ? x(Number(mark))
    : null;

  // `id` has to be unique per instance: two gradients sharing an id on one
  // page means every sparkline after the first paints with the first one's.
  const uid = `spark-${Math.random().toString(36).slice(2, 9)}`;
</script>

{#if trimmed.length}
  <svg
    class="spark"
    viewBox={`0 0 ${width} ${height}`}
    width={width}
    height={height}
    role="img"
    aria-label={label || 'simulated distribution'}
    preserveAspectRatio="none"
  >
    <defs>
      <linearGradient id={uid} x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="var(--v-brand)" stop-opacity="0.42" />
        <stop offset="100%" stop-color="var(--v-brand)" stop-opacity="0.03" />
      </linearGradient>
    </defs>
    <path d={area} fill={`url(#${uid})`} stroke="var(--v-brand)" stroke-width="1" />
    {#if markX !== null}
      <line
        x1={markX} x2={markX} y1={PAD} y2={height - PAD}
        stroke="var(--v-warn)" stroke-width="1" stroke-dasharray="2 2"
      />
    {/if}
  </svg>
{/if}

<style>
  .spark {
    display: block;
    width: 100%;
    height: auto;
    max-width: 100%;
    overflow: visible;
  }
</style>
