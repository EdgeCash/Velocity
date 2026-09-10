<script>
  // "Nothing here yet" said calmly.
  //
  // Half this dashboard is fed by the morning grade, so empty is a normal
  // state most of the day — it deserves a designed answer, not a red
  // framework error or a chart drawing two years of axis over no data.
  export let title = 'Nothing yet';
  export let detail = '';
  export let icon = '·';
</script>

<div class="empty">
  <span class="mark">{icon}</span>
  <div>
    <div class="t">{title}</div>
    {#if detail}<div class="d">{detail}</div>{/if}
  </div>
</div>

<style>
  .empty {
    display: flex;
    align-items: flex-start;
    gap: 0.6rem;
    background: var(--v-surface, #10161f);
    border: 1px dashed var(--v-line-2, #26333f);
    border-radius: var(--v-radius, 10px);
    padding: 0.85rem 0.95rem;
    margin: 0.4rem 0 0.2rem;
  }
  .mark {
    color: var(--v-ink-3, #64748b);
    font-size: 0.95rem;
    line-height: 1.2;
  }
  .t {
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--v-ink-2, #93a1b1);
  }
  .d {
    font-size: 0.72rem;
    line-height: 1.5;
    color: var(--v-ink-3, #64748b);
    margin-top: 0.15rem;
    max-width: 46rem;
  }
</style>
