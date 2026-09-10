<script>
  // The KPI row. Auto-fits its cards, collapses to two columns on a phone
  // and never to one — two short numbers side by side read faster than a
  // vertical stack of four.
  export let min = '9.5rem';
</script>

<div class="row" style={`--min:${min}`}>
  <slot />
</div>

<style>
  .row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(var(--min), 1fr));
    gap: 0.6rem;
    margin: 0.9rem 0 0.4rem;
  }
  @media (max-width: 640px) {
    .row {
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 0.45rem;
    }
  }
</style>
