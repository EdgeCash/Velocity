<script>
  // The page-top identity band: gradient surface, teal edge glow, and a
  // right-aligned freshness stamp. Pure presentation.
  export let title = 'Velocity';
  export let subtitle = '';
  export let stamp = '';

  function pretty(s) {
    // 20260823T032400Z → Aug 23, 03:24 UTC
    const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})/.exec(s ?? '');
    if (!m) return s ?? '';
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return `${months[+m[2] - 1]} ${+m[3]}, ${m[4]}:${m[5]} UTC`;
  }
</script>

<div class="hb">
  <div class="hb-main">
    <div class="hb-title">{title}</div>
    {#if subtitle}<div class="hb-sub">{subtitle}</div>{/if}
  </div>
  {#if stamp}
    <div class="hb-stamp">
      <span class="hb-stamp-label">as of</span>
      <span class="hb-stamp-value">{pretty(stamp)}</span>
    </div>
  {/if}
</div>

<style>
  .hb {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 1rem;
    flex-wrap: wrap;
    padding: 1.15rem 1.25rem;
    margin: 0.25rem 0 0.9rem;
    border-radius: 10px;
    background:
      radial-gradient(120% 160% at 0% 0%, rgba(61, 218, 208, 0.14), transparent 55%),
      linear-gradient(135deg, #0d141c 0%, #0b0f14 100%);
    border: 1px solid #1d2733;
    box-shadow: inset 0 1px 0 rgba(230, 236, 241, 0.04);
  }
  .hb-title {
    font-size: 1.5rem;
    font-weight: 800;
    letter-spacing: 0.01em;
    color: #e6ecf1;
    line-height: 1.15;
  }
  .hb-sub {
    margin-top: 0.25rem;
    font-size: 0.82rem;
    color: #8fa0af;
    max-width: 46rem;
  }
  .hb-stamp {
    display: flex;
    align-items: baseline;
    gap: 0.45em;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .hb-stamp-label {
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: #5b6b7a;
  }
  .hb-stamp-value {
    font-size: 0.82rem;
    font-weight: 600;
    color: #3ddad0;
  }
</style>
