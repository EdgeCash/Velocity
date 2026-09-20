<script>
  // The landing — Ballpark Pal's front page, on this data.
  //
  // The hub shipped its tile grid as a STICKY BAR: every view, always on
  // screen, switching in place. That is the right thing for a surface whose
  // whole argument is "no navigation", and it is not what Ballpark Pal looks
  // like. Theirs is a page: the slate across the top, then section after
  // section of big tiles down the page, and you leave it to get anywhere.
  // Scrolling it is how you find out what the site has.
  //
  // So the hub now has both, and they are not the same object. This is the
  // landing, and it only exists at `#` — full width, no rail, no command bar,
  // because a landing that still carries the switcher is a menu with a header
  // on it. Every other view keeps the sticky bar, so once you are inside the
  // data you are still one click from any other cut of it. You come back here
  // the way you come back on their site: the wordmark.
  //
  // Nothing here is a second source of truth. The groups, the labels and the
  // blurbs all come out of state.js, which is also what the command bar reads
  // — a view added to VIEWS and forgotten in GROUPS would vanish from this
  // page silently, so site/tests/hub.test.mjs pins that it cannot.
  import { GROUPS, VIEW_BLURB, VIEW_LABEL } from './nav.js';
  import LeagueChips from './LeagueChips.svelte';

  /** `{ [view]: number }` — the same counts the command bar's tiles carry. */
  export let counts = {};
  /** `(view) => void` — go there. */
  export let onOpen = () => {};
  /** The slate's own label, e.g. "Sat 20 Sep". Empty when the build has none. */
  export let slate = '';
  /** `[{ label, value }]` — the day in three numbers, above the tiles. */
  export let lede = [];
  /** `[{ league, n }]` for the filter, the league in force, and the setter.
   *
   * The landing carries the filter rather than hiding it, because it is
   * already APPLYING it: five of the counts below are built from the
   * filtered games. A filter you cannot see is worse than one you cannot
   * change. */
  export let leagues = [];
  export let league = 'all';
  export let onLeague = () => {};
</script>

<div class="landing">
  <!-- The day, stated. Their front page opens by telling you what today is;
       the hub used to open by asking what you wanted to look at. -->
  <section class="head">
    <div class="line">
      <h1>Today</h1>
      <LeagueChips {leagues} active={league} onPick={onLeague} />
    </div>
    {#if slate}<p class="when">{slate}</p>{/if}
    {#if lede.length}
      <dl class="stats">
        {#each lede as stat (stat.label)}
          <div class="stat">
            <dt>{stat.label}</dt>
            <dd>{stat.value}</dd>
          </div>
        {/each}
      </dl>
    {/if}
  </section>

  {#each GROUPS as group (group.label)}
    <section class="band">
      <h2>{group.label}</h2>
      <!-- A section with one view gets a banner rather than a card in the
           corner of an empty row. Outlook is Games and Data is Export, and a
           15rem card alone on a 72rem row reads as a grid that failed to
           load. Keyed on the count, not on which group it is, so adding a
           second Fantasy view or a second Data view needs nothing here. -->
      <div class="grid" class:solo={group.views.length === 1}>
        {#each group.views as view (view)}
          <button type="button" class="tile" on:click={() => onOpen(view)}>
            <span class="row">
              <span class="name">{VIEW_LABEL[view]}</span>
              <span class="n">{counts[view] ?? 0}</span>
            </span>
            <span class="blurb">{VIEW_BLURB[view]}</span>
          </button>
        {/each}
      </div>
    </section>
  {/each}
</div>

<style>
  /* Its own measure. Every other view is a board and wants the full width;
     a landing is something you read down, and tiles stretched across 1440px
     stop being a column of sections and become wallpaper. */
  .landing {
    max-width: 72rem;
    margin: 0 auto;
    padding: 0.4rem 0 1rem;
  }

  /* ---- the day ------------------------------------------------------- */
  .head {
    padding: 1.4rem 0 1.6rem;
  }
  /* The chips ride the baseline of the day, not a bar of their own: on the
     landing the filter is context for the numbers under it, not a toolbar. */
  .line {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.6rem 1rem;
  }
  h1 {
    margin: 0;
    font-family: var(--v-board);
    font-size: 2.1rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    line-height: 1;
    color: var(--v-ink);
  }
  .when {
    margin: 0.4rem 0 0;
    font-family: var(--v-board);
    font-size: 0.82rem;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--v-ink-3);
  }
  .stats {
    display: flex;
    flex-wrap: wrap;
    gap: 0 2.4rem;
    margin: 1.2rem 0 0;
  }
  .stat {
    display: flex;
    flex-direction: column-reverse; /* value above label, source order kept */
    gap: 0.15rem;
  }
  .stats dt {
    font-family: var(--v-board);
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--v-ink-3);
  }
  .stats dd {
    margin: 0;
    font-family: var(--v-board);
    font-variant-numeric: tabular-nums;
    font-size: 1.7rem;
    font-weight: 700;
    line-height: 1;
    color: var(--v-brand);
  }

  /* ---- a section ------------------------------------------------------
     Their section headers are a small-caps word with the page ruled across
     from it. The rule is what makes a run of tile grids read as a page with
     parts rather than as one long undifferentiated grid. */
  .band {
    padding: 1.1rem 0 0.2rem;
  }
  h2 {
    display: flex;
    align-items: center;
    gap: 0.9rem;
    margin: 0 0 0.75rem;
    font-family: var(--v-board);
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: var(--v-band);
  }
  h2::after {
    content: '';
    flex: 1 1 auto;
    height: 1px;
    background: var(--v-line);
  }

  /* Three across at the measure above, which is what the two biggest
     sections hold — a track count no section ever fills leaves a hole at the
     end of every row and reads as a tile that failed to render. */
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(18rem, 1fr));
    gap: 0.7rem;
  }
  /* The banner: name and count keep their line, and the blurb moves up beside
     them rather than leaving a 72rem card two-thirds empty. */
  .grid.solo { grid-template-columns: minmax(0, 1fr); }
  .grid.solo .tile {
    flex-direction: row;
    align-items: baseline;
    gap: 1.2rem;
  }
  .grid.solo .row { flex: 0 0 auto; gap: 1rem; }
  .grid.solo .blurb { flex: 1 1 auto; }

  /* ---- a tile ---------------------------------------------------------
     Big enough to be a destination. The count sits on the same line as the
     name because it is the one thing that changes between builds — a tile
     reading 0 is telling you not to bother, and that is worth a glance
     rather than a click. */
  .tile {
    display: flex;
    flex-direction: column;
    gap: 0.45rem;
    padding: 0.95rem 1rem 1rem;
    text-align: left;
    border: 1px solid var(--v-line);
    border-radius: var(--v-radius);
    background: var(--v-lvl-1);
    cursor: pointer;
    transition: border-color 130ms ease, background 130ms ease,
                transform 130ms ease;
  }
  .tile:hover {
    background: var(--v-lvl-2);
    border-color: var(--v-line-2);
    transform: translateY(-1px);
  }
  .tile:focus-visible {
    outline: none;
    box-shadow: var(--v-glow);
    border-color: var(--v-brand);
  }
  /* A tile that moves on hover is a tile that jitters for anyone who asked
     the system not to animate. */
  @media (prefers-reduced-motion: reduce) {
    .tile { transition: none; }
    .tile:hover { transform: none; }
  }

  .row {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.6rem;
  }
  .name {
    font-family: var(--v-board);
    font-size: 1.05rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    color: var(--v-ink);
  }
  .n {
    font-family: var(--v-board);
    font-variant-numeric: tabular-nums;
    font-size: 1.15rem;
    font-weight: 700;
    line-height: 1;
    color: var(--v-brand);
  }
  .blurb {
    font-size: 0.82rem;
    line-height: 1.45;
    color: var(--v-ink-2);
  }

  @media (max-width: 640px) {
    .head { padding: 1rem 0 1.2rem; }
    h1 { font-size: 1.75rem; }
    .stats { gap: 0 1.6rem; }
    .stats dd { font-size: 1.4rem; }
    .grid { grid-template-columns: repeat(auto-fill, minmax(12rem, 1fr)); }
    /* On a phone the banner is just a tile again — there is no width to
       spread into, and a name, a count and a sentence on one line is three
       things fighting for 12rem. */
    .grid.solo .tile { flex-direction: column; align-items: stretch; gap: 0.45rem; }
    .grid.solo .row { flex: 1 1 auto; gap: 0.6rem; }
  }
</style>
