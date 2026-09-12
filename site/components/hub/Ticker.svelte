<script>
  // The scores crawl, in the top bar where it never scrolls away.
  //
  // It rides the same store as the game cards, so a score shown here and a
  // score shown on a card can never disagree — which is the whole reason the
  // live layer is a store rather than a fetch per component.
  //
  // Live games lead. A crawl that opens with three finals and a 7pm tip-off
  // buries the only rows anyone is watching.
  export let games = [];
  export let ok = true;
  export let tried = false;

  // Two copies of the list scroll past as one, so the loop has no seam. With
  // very few games the animation would be a fast twitch, hence the floor.
  $: duration = Math.max(games.length * 4.5, 22);
  $: liveCount = games.filter((g) => g.state === 'in').length;

  function tipoff(game) {
    try {
      return new Date(game.start).toLocaleTimeString([], {
        hour: 'numeric', minute: '2-digit',
      });
    } catch {
      return '';
    }
  }
</script>

<div class="wrap">
  {#if games.length}
    <div class="band" aria-label="live scoreboard">
      <div class="track" style={`animation-duration:${duration}s`}>
        {#each [0, 1] as copy}
          <div class="half" aria-hidden={copy === 1}>
            {#each games as g (`${copy}-${g.event_id || `${g.league}${g.away.abbr}${g.home.abbr}`}`)}
              <span class="item" class:live={g.state === 'in'} class:final={g.state === 'post'}>
                <span class="lg">{g.league.toUpperCase()}</span>
                {#if g.state === 'pre'}
                  <span class="team">{g.away.abbr}</span>
                  <span class="at">@</span>
                  <span class="team">{g.home.abbr}</span>
                  <span class="detail">{tipoff(g)}</span>
                {:else}
                  <span class="team">{g.away.abbr}</span>
                  <span class="score">{g.away_score}</span>
                  <span class="team">{g.home.abbr}</span>
                  <span class="score">{g.home_score}</span>
                  <span class="detail">
                    {#if g.state === 'in'}<span class="dot"></span>{/if}{g.detail}
                  </span>
                {/if}
              </span>
            {/each}
          </div>
        {/each}
      </div>
    </div>
    {#if liveCount}
      <span class="tally" title="games in progress">{liveCount} live</span>
    {/if}
  {:else if tried && !ok}
    <!-- Honest about the one failure mode this has: the scores come from
         ESPN's public feed, fetched by the browser because their edge blocks
         datacenter IPs. A viewer on a network that blocks it sees this rather
         than an empty bar that looks like a quiet night. -->
    <span class="note">Live scores unavailable from this network</span>
  {:else if tried}
    <span class="note">No games on the board right now</span>
  {/if}
</div>

<style>
  .wrap {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    min-width: 0;
    flex: 1 1 auto;
  }
  .band {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    -webkit-mask-image: linear-gradient(90deg, transparent, #000 2.5%, #000 97.5%, transparent);
    mask-image: linear-gradient(90deg, transparent, #000 2.5%, #000 97.5%, transparent);
  }
  .track {
    display: inline-flex;
    white-space: nowrap;
    animation: crawl linear infinite;
    will-change: transform;
  }
  .band:hover .track,
  .band:focus-within .track { animation-play-state: paused; }
  .half { display: inline-flex; }
  @keyframes crawl {
    to { transform: translateX(-50%); }
  }
  .item {
    display: inline-flex;
    align-items: baseline;
    gap: 0.42em;
    padding: 0.2rem 0.95rem;
    border-right: 1px solid var(--v-line);
    font-size: 0.78rem;
    font-variant-numeric: tabular-nums;
    color: var(--v-ink-2);
  }
  .lg {
    font-family: var(--v-board);
    font-size: 0.6rem;
    font-weight: 700;
    letter-spacing: 0.09em;
    color: var(--v-ink-3);
  }
  .team { font-weight: 600; color: var(--v-ink); }
  .score {
    font-family: var(--v-board);
    font-size: 0.95rem;
    font-weight: 700;
    color: var(--v-ink);
  }
  /* A live score is the lit one; a final settles back to ink. */
  .live .score { color: var(--v-brand); }
  .final .score { color: var(--v-ink-2); }
  .at, .detail { color: var(--v-ink-3); font-size: 0.7rem; }
  .live .detail { color: var(--v-warn); }
  .dot {
    display: inline-block;
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--v-alert);
    margin-right: 0.35em;
    animation: pulse 1.6s ease-in-out infinite;
  }
  @keyframes pulse { 50% { opacity: 0.25; } }
  .tally {
    flex: 0 0 auto;
    font-family: var(--v-board);
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--v-warn);
  }
  .note {
    font-size: 0.72rem;
    color: var(--v-ink-3);
  }
  @media (prefers-reduced-motion: reduce) {
    .track { animation: none; }
    .band { overflow-x: auto; scrollbar-width: none; }
    .band :global(.half:last-child) { display: none; }
  }
  @media (max-width: 640px) {
    .tally { display: none; }
  }
</style>
