<script>
  // The league filter — one pill group, in both places that need it.
  //
  // It used to live only in the command bar, which the landing hides. That
  // made the filter INVISIBLE on the landing while it was still being
  // applied: `#league=nfl` is a shareable link, the wordmark takes you home
  // keeping it, and five of the twelve tile counts are built from the
  // filtered games (Most likely, Players, Accuracy, Weather, Matchups). You
  // would be reading NFL-only numbers with nothing on the page saying so,
  // and no way to clear it without first entering a view.
  //
  // So it is a component rather than a copy: the landing and the command bar
  // render the same control, and a change to one is a change to both.

  /** `[{ league, n }]`, already ordered. */
  export let leagues = [];
  /** The league in force — the resolved one, not the raw hash value. */
  export let active = 'all';
  /** `(league) => void` */
  export let onPick = () => {};
</script>

<!-- One league is not a filter, it is a label. -->
{#if leagues.length > 1}
  <div class="leagues" aria-label="league filter">
    <button class:on={active === 'all'} on:click={() => onPick('all')}>All</button>
    {#each leagues as l (l.league)}
      <button class:on={active === l.league} on:click={() => onPick(l.league)}>
        {l.league.toUpperCase()}
      </button>
    {/each}
  </div>
{/if}

<style>
  .leagues {
    display: inline-flex;
    gap: 2px;
    padding: 2px;
    background: var(--v-lvl-1);
    border: 1px solid var(--v-line);
    border-radius: 999px;
    max-width: 100%;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .leagues::-webkit-scrollbar { display: none; }

  .leagues button {
    display: inline-flex;
    align-items: baseline;
    gap: 0.35em;
    flex: 0 0 auto;
    border: 0;
    border-radius: 999px;
    padding: 0.3rem 0.8rem;
    background: transparent;
    color: var(--v-ink-2);
    font-family: var(--v-board);
    font-size: 0.84rem;
    font-weight: 600;
    letter-spacing: 0.07em;
    cursor: pointer;
    transition: background 130ms ease, color 130ms ease;
  }
  .leagues button:hover { color: var(--v-ink); background: var(--v-lvl-2); }
  .leagues button.on {
    background: var(--v-brand-deep);
    color: var(--v-brand);
    box-shadow: inset 0 0 0 1px rgba(47, 109, 50, 0.3);
  }
  .leagues button:focus-visible {
    outline: none;
    box-shadow: var(--v-glow);
  }
</style>
