<script>
  // A team's mark: the club logo on a plate, with its code underneath.
  //
  // Two reasons it is a component and not an <img>. First, it has to survive
  // the image not arriving — marks are hot-linked from ESPN's public CDN
  // rather than vendored into a public repo, so a page must read correctly
  // with every logo missing. Second, the plate: club logos run from a pale
  // gold (NO) to a near-black navy (NE) and the site's surface is almost
  // black, so a logo dropped straight onto it half-disappears. A faint plate
  // behind every mark gives them all the same footprint and keeps the dark
  // ones visible, which is also what stops a row of logos looking ragged.
  //
  // The code chip is *underneath* the logo rather than instead of it, because
  // there are three ways to have no logo and only one of them is an error. A
  // blocked or slow CDN leaves the request pending with no `error` event ever
  // fired — an `on:error` fallback alone shows an empty plate for as long as
  // the browser is willing to wait. Painting the chip first and revealing the
  // image over it only once it has actually decoded covers all of it with two
  // booleans and no timers: no logo, not yet, never, and loaded.
  export let code = '';
  /** ESPN CDN url; empty, slow or unreachable leaves the code chip showing. */
  export let logo = '';
  /** The club's brand colour, already lifted for the dark surface. */
  export let color = '';
  /** Full team name, for the hover title. */
  export let label = '';
  export let size = 26;

  let broken = false;
  let loaded = false;
  // A changed logo is a different team: it gets its own chance to load, and
  // must not inherit the last one's decoded state.
  $: if (logo) {
    broken = false;
    loaded = false;
  }
  $: accent = color || 'rgba(255, 255, 255, 0.14)';

  /** Settle an image that finished before the listeners went on.
   *
   * A cached logo — the second card on the page showing the same club, or a
   * revisit — can decode synchronously as the element is created, so `load`
   * has already fired by the time Svelte binds to it and the chip never
   * clears. Reading `complete` once on mount is the standard close for that
   * race; `naturalWidth` separates a decoded image from a failed one.
   */
  function settle(img) {
    if (img.complete) {
      if (img.naturalWidth > 0) loaded = true;
      else broken = true;
    }
  }
</script>

<!-- Nothing at all when there is nothing to draw. An empty plate is worse
     than no plate: it reads as a logo that failed rather than as a team the
     identity table has never heard of. -->
{#if logo || code}
  <span
    class="team-mark"
    style="--mark-size: {size}px; --mark-accent: {accent};"
    title={label || code}
  >
    {#if code}<span class="chip" class:hidden={loaded}>{code}</span>{/if}
    {#if logo && !broken}
      <!-- alt is empty on purpose: every placement prints the team's name or
           code as text beside it, so a described image would read twice. -->
      <img src={logo} alt="" width={size} height={size} loading="lazy"
           class:shown={loaded}
           use:settle
           on:load={() => (loaded = true)}
           on:error={() => (broken = true)} />
    {/if}
  </span>
{/if}

<style>
  .team-mark {
    position: relative;
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
  .team-mark img {
    position: absolute;
    inset: 11%;
    width: 78%;
    height: 78%;
    object-fit: contain;
    /* Hidden until it has decoded, so a pending request shows the chip rather
       than a blank plate — and a half-drawn logo never flashes over it. */
    opacity: 0;
    /* The plate's own ground, so a loaded logo covers the chip completely
       even where `contain` leaves the corners clear. */
    background: var(--v-lvl-1, #101822);
    border-radius: 4px;
  }
  .team-mark img.shown { opacity: 1; }
  .chip {
    font-family: var(--v-board, sans-serif);
    font-size: calc(var(--mark-size, 26px) * 0.36);
    font-weight: 700;
    letter-spacing: 0.02em;
    line-height: 1;
    color: var(--mark-accent, #8fa0b3);
    white-space: nowrap;
  }
  .chip.hidden { visibility: hidden; }
</style>
