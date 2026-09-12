<script>
  // The game feed: every game on the board, soonest first, grouped by day.
  //
  // Two things it does that a table cannot. It keeps LIVE games at the top
  // regardless of kickoff — a game in progress is the one you are watching,
  // and burying it under six that start later is how the old board read at
  // 8pm on a Saturday. And it opens a game in place, so the surface never
  // navigates: the list you were reading is still where you left it when the
  // sheet closes.
  import GameCard from './GameCard.svelte';
  import { groupBy } from './model.js';

  export let games = [];
  export let identity = {};
  export let distributions = [];
  export let openGame = '';
  export let isPrivate = true;

  import { hubState } from './state.js';

  // Distributions are the biggest table on the surface — twenty thousand rows
  // on a college Saturday — so they are indexed once here rather than filtered
  // per card. Only the opened game's rows are ever read.
  $: distIndex = (() => {
    const out = new Map();
    for (const row of distributions ?? []) {
      if (String(row?.league ?? '') === '__none__') continue;
      const bucket = out.get(row.game_id) ?? { margin: [], total: [] };
      if (row.kind === 'margin') bucket.margin.push(row);
      else if (row.kind === 'total') bucket.total.push(row);
      out.set(row.game_id, bucket);
    }
    return out;
  })();

  const EMPTY_DIST = { margin: [], total: [] };

  // Live first, then everything else in kickoff order (which `buildGames`
  // already sorted). Finals fall to the bottom: they are settled, and what is
  // settled belongs in the record, not at the top of the board.
  $: ordered = [...games].sort((a, b) => {
    const rank = (g) => (g.score?.state === 'in' ? 0 : g.score?.state === 'post' ? 2 : 1);
    const r = rank(a) - rank(b);
    if (r) return r;
    const at = a.kickoff ? a.kickoff.getTime() : Infinity;
    const bt = b.kickoff ? b.kickoff.getTime() : Infinity;
    return at - bt;
  });

  function dayLabel(game) {
    if (game.score?.state === 'in') return 'Live';
    if (!game.kickoff) return 'Unscheduled';
    return game.kickoff.toLocaleDateString(undefined, {
      weekday: 'long', month: 'short', day: 'numeric',
    });
  }

  $: days = [...groupBy(ordered, dayLabel)];
</script>

{#if !games.length}
  <div class="none">
    <h3>Nothing on the board</h3>
    <p>
      No games for this filter on the current build. That is a normal state
      between seasons and after a league's odds pull comes back empty — it is
      not an error.
    </p>
  </div>
{:else}
  {#each days as [label, dayGames] (label)}
    <section class="day">
      <h3 class:livehead={label === 'Live'}>
        {label}
        <span class="n">{dayGames.length}</span>
      </h3>
      <div class="cards">
        {#each dayGames as game (game.game_id)}
          <GameCard
            {game}
            {identity}
            {isPrivate}
            dists={distIndex.get(game.game_id) ?? EMPTY_DIST}
            open={openGame === game.game_id}
            onToggle={(id) => hubState.toggleGame(id)}
          />
        {/each}
      </div>
    </section>
  {/each}
{/if}

<style>
  .day { margin-bottom: 1.4rem; }
  h3 {
    display: flex;
    align-items: baseline;
    gap: 0.55rem;
    margin: 0 0 0.5rem;
    font-size: 0.64rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.13em;
    color: var(--v-ink-3);
  }
  h3.livehead { color: var(--v-warn); }
  .n {
    font-family: var(--v-board);
    font-size: 0.68rem;
    color: var(--v-ink-3);
    font-variant-numeric: tabular-nums;
  }
  .cards { display: grid; gap: 0.4rem; }

  .none {
    padding: 1.6rem 1.2rem;
    border: 1px dashed var(--v-line-2);
    border-radius: var(--v-radius);
    background: var(--v-lvl-0);
  }
  .none h3 {
    font-size: 0.95rem;
    text-transform: none;
    letter-spacing: -0.01em;
    color: var(--v-ink);
    margin-bottom: 0.3rem;
  }
  .none p { margin: 0; font-size: 0.8rem; line-height: 1.6; color: var(--v-ink-2); }
</style>
