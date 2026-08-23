<script>
  // Scrolling live scoreboard. Reads /api/scores (the Worker's edge-cached
  // ESPN proxy) on mount and every 60s; renders nothing when the endpoint
  // is unreachable (local dev) or every league is dark.
  import { onMount, onDestroy } from 'svelte';

  let games = [];
  let timer;

  async function load() {
    try {
      const res = await fetch('/api/scores');
      if (!res.ok) return;
      const data = await res.json();
      if (Array.isArray(data.games)) games = data.games;
    } catch {
      // static preview or offline — the ticker just stays hidden
    }
  }

  onMount(() => {
    load();
    timer = setInterval(load, 60000);
  });
  onDestroy(() => clearInterval(timer));

  function tipoff(g) {
    try {
      return new Date(g.start).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    } catch {
      return '';
    }
  }
</script>

{#if games.length}
  <div class="vt-band" aria-label="live scoreboard">
    <div class="vt-track" style={`animation-duration: ${Math.max(games.length * 5, 25)}s`}>
      {#each [games, games] as half, h}
        <div class="vt-half" aria-hidden={h === 1}>
          {#each half as g}
            <span class="vt-item" class:vt-live={g.state === 'in'} class:vt-final={g.state === 'post'}>
              <span class="vt-lg">{g.lg}</span>
              {#if g.state === 'pre'}
                <span class="vt-team">{g.away}</span>
                <span class="vt-at">@</span>
                <span class="vt-team">{g.home}</span>
                <span class="vt-detail">{tipoff(g)}</span>
              {:else}
                <span class="vt-team">{g.away}</span>
                <span class="vt-score">{g.as}</span>
                <span class="vt-team">{g.home}</span>
                <span class="vt-score">{g.hs}</span>
                <span class="vt-detail">
                  {#if g.state === 'in'}<span class="vt-dot"></span>{/if}
                  {g.detail}
                </span>
              {/if}
            </span>
          {/each}
        </div>
      {/each}
    </div>
  </div>
{/if}

<style>
  .vt-band {
    overflow: hidden;
    background: #0b0f14;
    border: 1px solid #1d2733;
    border-radius: 8px;
    margin: 0.25rem 0 1rem;
    -webkit-mask-image: linear-gradient(90deg, transparent, #000 3%, #000 97%, transparent);
    mask-image: linear-gradient(90deg, transparent, #000 3%, #000 97%, transparent);
  }
  .vt-track {
    display: inline-flex;
    white-space: nowrap;
    animation: vt-scroll linear infinite;
    will-change: transform;
  }
  .vt-band:hover .vt-track {
    animation-play-state: paused;
  }
  .vt-half {
    display: inline-flex;
  }
  @keyframes vt-scroll {
    to {
      transform: translateX(-50%);
    }
  }
  .vt-item {
    display: inline-flex;
    align-items: baseline;
    gap: 0.45em;
    padding: 0.55rem 1.1rem;
    border-right: 1px solid #1d2733;
    font-size: 0.8rem;
    font-variant-numeric: tabular-nums;
    color: #b7c2cc;
  }
  .vt-lg {
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: #5b6b7a;
  }
  .vt-team {
    font-weight: 600;
    color: #e6ecf1;
  }
  .vt-score {
    font-weight: 700;
    color: #3ddad0;
  }
  .vt-final .vt-score {
    color: #b7c2cc;
  }
  .vt-at,
  .vt-detail {
    color: #5b6b7a;
    font-size: 0.72rem;
  }
  .vt-live .vt-detail {
    color: #d97706;
  }
  .vt-dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #ef4444;
    margin-right: 0.3em;
    animation: vt-pulse 1.6s ease-in-out infinite;
  }
  @keyframes vt-pulse {
    50% {
      opacity: 0.25;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .vt-track {
      animation: none;
    }
    .vt-band {
      overflow-x: auto;
    }
  }
</style>
