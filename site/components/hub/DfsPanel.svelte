<script>
  // The DFS slate: every lineup the optimiser built, with its own players.
  //
  // DFS is a peer view rather than something nested in the game cards for a
  // concrete reason: the two do not line up. A Friday in September is an MLB
  // and WNBA DFS slate against an NFL and college BOARD, so a DFS panel that
  // only existed inside game cards would be empty on exactly the days it has
  // the most to say. Games that ARE on both surfaces still carry their DFS
  // players on the card, so nothing is only here.
  import { num } from '../format.js';

  export let lineups = [];
  export let league = 'all';

  $: visible = league === 'all'
    ? lineups
    : lineups.filter((l) => l.league === league);

  const KIND_LABEL = {
    classic: 'Classic',
    showdown: 'Showdown',
    tiered: 'Tiers',
  };

  // Salary caps are per contest and not in the data, so the bar is drawn
  // against the slate's own spend rather than an assumed 50,000 — a made-up
  // denominator that happened to be right for classic and wrong for showdown
  // would be worse than none.
  $: maxSalary = Math.max(...visible.map((l) => l.salary || 0), 1);
</script>

{#if !visible.length}
  <div class="none">
    <h3>No DFS slate built</h3>
    <p>
      The optimiser runs against DraftKings' published draft groups. Nothing
      here means no group was open for this filter when the build ran — most
      often an off-day for the sports that have DFS, which is not the same set
      as the sports on the board.
    </p>
  </div>
{:else}
  <div class="lineups">
    {#each visible as lineup, i (`${lineup.league}-${lineup.kind}-${lineup.slate}-${i}`)}
      <article class="lineup">
        <header>
          <span class="lg">{lineup.league.toUpperCase()}</span>
          <span class="kind">{KIND_LABEL[lineup.kind] ?? lineup.kind}</span>
          {#if lineup.slate}<span class="slate">{lineup.slate}</span>{/if}
          <span class="totals">
            <span class="pts">{num(lineup.points, 1)}<small>pts</small></span>
            {#if lineup.salary > 0}
              <span class="sal">${lineup.salary.toLocaleString()}</span>
            {/if}
          </span>
        </header>

        {#if lineup.salary > 0}
          <div class="bar" aria-hidden="true">
            <span style={`width:${Math.min((lineup.salary / maxSalary) * 100, 100)}%`}></span>
          </div>
        {/if}

        <ul class="roster">
          {#each lineup.players as p, j (`${p.player_name}-${p.slot ?? j}`)}
            <li>
              <span class="slot">{p.slot || p.position || (p.tier ? `T${num(p.tier, 0)}` : '')}</span>
              <span class="who">
                <span class="pname">{p.player_name}</span>
                <span class="pteam">{p.team}</span>
              </span>
              <span class="ppts">{num(p.points, 1)}</span>
              {#if p.salary}
                <span class="psal">${Number(p.salary).toLocaleString()}</span>
              {/if}
            </li>
          {/each}
        </ul>

        {#if lineup.game_time}
          <footer>{lineup.game_time}</footer>
        {/if}
      </article>
    {/each}
  </div>
{/if}

<style>
  .lineups {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr));
    gap: 0.7rem;
  }
  .lineup {
    border: 1px solid var(--v-line);
    border-radius: var(--v-radius);
    background: var(--v-lvl-0);
    padding: 0.65rem 0.75rem 0.55rem;
    display: grid;
    gap: 0.5rem;
    align-content: start;
  }
  header { display: flex; align-items: baseline; gap: 0.45rem; flex-wrap: wrap; }
  .lg, .kind, .slate {
    font-family: var(--v-board);
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.09em;
    text-transform: uppercase;
  }
  .lg { color: var(--v-brand); }
  .kind { color: var(--v-ink-2); }
  .slate {
    color: var(--v-ink-3);
    letter-spacing: 0.04em;
    text-transform: none;
    font-weight: 600;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .totals { margin-left: auto; display: flex; align-items: baseline; gap: 0.5rem; }
  .pts {
    font-family: var(--v-board);
    font-size: 1.15rem;
    font-weight: 700;
    color: var(--v-brand);
    font-variant-numeric: tabular-nums;
  }
  .pts small { font-size: 0.58rem; margin-left: 0.15em; color: var(--v-ink-3); }
  .sal {
    font-family: var(--v-board);
    font-size: 0.78rem;
    color: var(--v-ink-3);
    font-variant-numeric: tabular-nums;
  }

  .bar { height: 3px; background: var(--v-lvl-2); border-radius: 999px; overflow: hidden; }
  .bar span { display: block; height: 100%; background: var(--v-brand-dim); }

  .roster { margin: 0; padding: 0; list-style: none; display: grid; gap: 0.1rem; }
  .roster li {
    display: grid;
    grid-template-columns: 2.5rem minmax(0, 1fr) auto auto;
    align-items: baseline;
    gap: 0.5rem;
    padding: 0.25rem 0;
    border-bottom: 1px solid var(--v-line);
  }
  .roster li:last-child { border-bottom: 0; }
  .slot {
    font-family: var(--v-board);
    font-size: 0.6rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    color: var(--v-ink-3);
  }
  .who { min-width: 0; display: grid; }
  .pname {
    font-size: 0.8rem;
    color: var(--v-ink);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .pteam { font-size: 0.62rem; color: var(--v-ink-3); }
  .ppts {
    font-family: var(--v-board);
    font-size: 0.92rem;
    font-weight: 600;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
  }
  .psal {
    font-family: var(--v-board);
    font-size: 0.72rem;
    color: var(--v-ink-3);
    font-variant-numeric: tabular-nums;
  }

  footer { font-size: 0.66rem; color: var(--v-ink-3); }

  .none {
    padding: 1.6rem 1.2rem;
    border: 1px dashed var(--v-line-2);
    border-radius: var(--v-radius);
    background: var(--v-lvl-0);
  }
  .none h3 {
    margin: 0 0 0.3rem;
    font-size: 0.95rem;
    color: var(--v-ink);
  }
  .none p { margin: 0; font-size: 0.8rem; line-height: 1.6; color: var(--v-ink-2); }
</style>
