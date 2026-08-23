<script>
  // One-line kickoff-conditions banner for the matchup dossier.
  // `row` is a velocity.weather row (or undefined when none is banked).
  export let row = undefined;

  $: hasForecast =
    row && !row.covered && row.wind_mph != null && !Number.isNaN(row.wind_mph);
</script>

{#if row}
  <p class="wl">
    {#if row.covered}
      <span class="wl-label">Conditions</span> indoors
    {:else if hasForecast}
      <span class="wl-label">Conditions at kickoff</span>
      <span class="wl-val">{Math.round(row.temp_f)}°F</span> ·
      wind <span class="wl-val">{Math.round(row.wind_mph)} mph</span> ·
      precip <span class="wl-val">{Math.round(row.precip_pct)}%</span>
      <span class="wl-src">Open-Meteo, stadium site</span>
    {/if}
  </p>
{/if}

<style>
  .wl {
    font-size: 0.82rem;
    color: #8fa0af;
    margin: 0.25rem 0 0.75rem;
  }
  .wl-label {
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    color: #5b6b7a;
    margin-right: 0.4em;
  }
  .wl-val {
    color: #e6ecf1;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .wl-src {
    font-size: 0.68rem;
    color: #5b6b7a;
    margin-left: 0.5em;
  }
</style>
