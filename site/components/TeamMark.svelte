<script>
  // A team's mark: the club logo on a plate, or its code when there is no
  // logo to show.
  //
  // Two reasons it is a component and not an <img>. First, it has to survive
  // the image not arriving — marks are hot-linked from ESPN's public CDN
  // rather than vendored into a public repo, so a page must read correctly
  // with every logo missing. Second, the plate: club logos run from a pale
  // gold (NO) to a near-black navy (NE) and the site's surface is almost
  // black, so a logo dropped straight onto it half-disappears. A faint plate
  // behind every mark gives them all the same footprint and keeps the dark
  // ones visible, which is also what stops a row of logos looking ragged.
  export let code = '';
  /** ESPN CDN url; empty or unreachable falls back to the code chip. */
  export let logo = '';
  /** The club's brand colour, already lifted for the dark surface. */
  export let color = '';
  /** Full team name, for the hover title. */
  export let label = '';
  export let size = 26;

  let broken = false;
  // A changed logo is a different team, so give it its own chance to load.
  $: if (logo) broken = false;
  $: accent = color || 'rgba(255, 255, 255, 0.14)';
</script>

<span
  class="mark"
  style="--mark-size: {size}px; --mark-accent: {accent};"
  title={label || code}
>
  {#if logo && !broken}
    <!-- alt is empty on purpose: every placement prints the team's name or
         code as text beside it, so a described image would read twice. -->
    <img src={logo} alt="" width={size} height={size} loading="lazy"
         on:error={() => (broken = true)} />
  {:else if code}
    <span class="chip">{code}</span>
  {/if}
</span>

<style>
  .mark {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex: 0 0 auto;
    width: var(--mark-size, 26px);
    height: var(--mark-size, 26px);
    border-radius: 7px;
    background: rgba(255, 255, 255, 0.055);
    /* The club colour enters as a hairline rather than a fill: a saturated
       brand block next to a brand logo fights it, and the sheet already has
       one saturated band. */
    box-shadow: inset 0 0 0 1px var(--mark-accent, rgba(255, 255, 255, 0.14));
    overflow: hidden;
  }
  .mark img {
    width: 78%;
    height: 78%;
    object-fit: contain;
    display: block;
  }
  .chip {
    font-family: var(--v-board, sans-serif);
    font-size: calc(var(--mark-size, 26px) * 0.36);
    font-weight: 700;
    letter-spacing: 0.02em;
    line-height: 1;
    color: var(--mark-accent, #8fa0b3);
    white-space: nowrap;
  }
</style>
