<script>
  // Parlays: the one thing on the board that is not about a single game.
  //
  // Which is why it sits above the feed rather than inside a card, and why it
  // starts collapsed — on most slates it is a handful of rows, and it should
  // not be the thing between you and the board.
  //
  // Each leg carries its own `game_id`, so a leg is a control: clicking it
  // opens that game in the feed below. That is the whole argument for a single
  // surface in miniature — on the old site, reading a parlay meant writing
  // down three matchups and going to find them.
  import { american, isNum, num, pct, signed, marketLabel, sideLabel } from '../format.js';
  import { hubState } from './state.js';

  export let parlays = [];
  export let league = 'all';
  export let isPrivate = true;

  let open = false;

  $: visible = league === 'all'
    ? parlays
    : parlays.filter((p) => String(p.league ?? '') === league);

  function legPhrase(leg) {
    const market = marketLabel(leg?.market);
    const side = leg?.player ? String(leg.player) : sideLabel(leg?.side);
    const point = isNum(leg?.point) ? ` ${num(leg.point, 1).replace('.0', '')}` : '';
    return `${market} ${side}${point}`;
  }
</script>

{#if visible.length}
  <section class="parlays" class:open>
    <button class="head" aria-expanded={open} on:click={() => (open = !open)}>
      <span class="title">Parlays</span>
      <span class="count">{visible.length}</span>
      <span class="hint">
        {open ? 'built from the same board' : 'priced across games'}
      </span>
      <span class="chev" aria-hidden="true">{open ? '−' : '+'}</span>
    </button>

    {#if open}
      <div class="list">
        {#each visible as parlay, i (`${parlay.league}-${i}`)}
          <article class="parlay">
            <header>
              <span class="lg">{String(parlay.league).toUpperCase()}</span>
              <span class="nlegs">{parlay.n_legs}-leg</span>
              {#if parlay.correlated}
                <!-- The producer's flag means two or more legs share a game,
                     not that the whole parlay is one game. Saying "correlated
                     legs" keeps that honest; "same game" would not. -->
                <span class="corr" title="two or more legs share a game">
                  correlated legs
                </span>
              {/if}
              <span class="nums">
                {#if isPrivate}
                  <span class="pair">
                    <span class="k">Price</span>
                    <span class="v">{american(parlay.price)}</span>
                  </span>
                {/if}
                <span class="pair">
                  <span class="k">Model</span>
                  <span class="v">{pct(parlay.p_win, 1)}</span>
                </span>
                {#if isPrivate}
                  <span class="pair">
                    <span class="k">EV</span>
                    <span class="v" class:pos={parlay.ev > 0} class:neg={parlay.ev < 0}>
                      {signed(parlay.ev, 2)}
                    </span>
                  </span>
                  {#if Number(parlay.stake) > 0}
                    <span class="pair">
                      <span class="k">Sized</span>
                      <span class="v staked">{num(parlay.stake, 2)}u</span>
                    </span>
                  {/if}
                {/if}
              </span>
            </header>

            {#if parlay.legs.length}
              <ol class="legs">
                {#each parlay.legs as leg, j (`${leg.game_id}-${leg.market}-${leg.side}-${j}`)}
                  <li>
                    {#if leg.game_id}
                      <button
                        class="leg"
                        on:click={() => hubState.set({ view: 'games', game: leg.game_id })}
                        title="open this game"
                      >
                        <span class="match">{leg.label}</span>
                        <span class="call">{legPhrase(leg)}</span>
                        {#if isPrivate}<span class="lp">{american(leg.price)}</span>{/if}
                      </button>
                    {:else}
                      <span class="leg flat">
                        <span class="match">{leg.label}</span>
                        <span class="call">{legPhrase(leg)}</span>
                        {#if isPrivate}<span class="lp">{american(leg.price)}</span>{/if}
                      </span>
                    {/if}
                  </li>
                {/each}
              </ol>
            {:else}
              <!-- The legs JSON did not parse. The price and the model's
                   probability are still true, so the row degrades to the
                   rendered string rather than disappearing. -->
              <p class="rawlegs">{parlay.legs_string}</p>
            {/if}
          </article>
        {/each}
      </div>

      <p class="note">
        A parlay's legs are not independent, and the model prices the
        correlation rather than multiplying the legs together. Clicking a leg
        opens that game below.
      </p>
    {/if}
  </section>
{/if}

<style>
  .parlays {
    margin-bottom: 0.9rem;
    border: 1px solid var(--v-line);
    border-radius: var(--v-radius);
    background: var(--v-lvl-0);
    overflow: hidden;
  }
  .parlays.open { border-color: var(--v-line-2); }

  .head {
    display: flex;
    align-items: baseline;
    gap: 0.6rem;
    width: 100%;
    padding: 0.55rem 0.8rem;
    border: 0;
    background: transparent;
    text-align: left;
    cursor: pointer;
    color: inherit;
    font: inherit;
  }
  .head:hover { background: var(--v-hover); }
  .head:focus-visible { outline: 2px solid var(--v-brand); outline-offset: -2px; }
  .title {
    font-family: var(--v-board);
    font-size: 0.66rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.13em;
    color: var(--v-ink-2);
  }
  .count {
    font-family: var(--v-board);
    font-size: 0.9rem;
    font-weight: 700;
    color: var(--v-brand);
    font-variant-numeric: tabular-nums;
  }
  .hint { font-size: 0.68rem; color: var(--v-ink-3); }
  .chev {
    margin-left: auto;
    font-family: var(--v-board);
    font-size: 1rem;
    color: var(--v-ink-3);
  }

  .list {
    display: grid;
    gap: 0.4rem;
    padding: 0 0.8rem 0.2rem;
  }
  .parlay {
    display: grid;
    gap: 0.35rem;
    padding: 0.55rem 0.65rem;
    background: var(--v-lvl-2);
    border-radius: var(--v-radius-sm);
  }
  header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; }
  .lg, .nlegs {
    font-family: var(--v-board);
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--v-ink-3);
  }
  .corr {
    padding: 0.05rem 0.36rem;
    border-radius: 999px;
    font-size: 0.62rem;
    background: var(--v-info-tint);
    color: var(--v-info);
  }
  .nums { margin-left: auto; display: flex; flex-wrap: wrap; gap: 0.8rem; }
  .pair { display: grid; gap: 0.02rem; }
  .k {
    font-size: 0.54rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    color: var(--v-ink-3);
  }
  .v {
    font-family: var(--v-board);
    font-size: 0.9rem;
    font-weight: 600;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
  }
  .v.pos { color: var(--v-pos); }
  .v.neg { color: var(--v-neg); }
  .v.staked { color: var(--v-pos); }

  .legs { margin: 0; padding: 0; list-style: none; display: grid; gap: 0.16rem; }
  .leg {
    display: grid;
    grid-template-columns: minmax(5rem, auto) minmax(0, 1fr) auto;
    align-items: baseline;
    gap: 0.6rem;
    width: 100%;
    padding: 0.22rem 0.4rem;
    border: 0;
    border-radius: 5px;
    background: transparent;
    text-align: left;
    color: inherit;
    font: inherit;
    cursor: pointer;
  }
  .leg.flat { cursor: default; }
  button.leg:hover { background: var(--v-hover); }
  button.leg:hover .match { color: var(--v-brand); }
  button.leg:focus-visible { outline: 2px solid var(--v-brand); outline-offset: -2px; }
  .match {
    font-family: var(--v-board);
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.03em;
    color: var(--v-ink-2);
    white-space: nowrap;
  }
  .call { font-size: 0.78rem; color: var(--v-ink); }
  .lp {
    font-family: var(--v-board);
    font-size: 0.82rem;
    font-weight: 600;
    color: var(--v-ink-2);
    font-variant-numeric: tabular-nums;
  }
  .rawlegs { margin: 0; font-size: 0.76rem; color: var(--v-ink-2); line-height: 1.5; }

  .note {
    margin: 0.5rem 0 0;
    padding: 0 0.8rem 0.7rem;
    font-size: 0.7rem;
    line-height: 1.55;
    color: var(--v-ink-3);
  }
</style>
