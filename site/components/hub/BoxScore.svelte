<script>
  // The live box score for one game, fetched when the game is opened.
  //
  // Two deliberate properties:
  //
  // **It names no columns.** ESPN's summary endpoint returns each stat block
  // with its own `labels` array, and this renders those. So it works for
  // baseball's AB/R/H/RBI and basketball's MIN/PTS/REB without a per-sport
  // table, and — more to the point — a column ESPN renames or reorders shows
  // up renamed rather than silently mislabelling a row of numbers.
  //
  // **It only fetches while open.** A college Saturday is eighty games; one
  // summary request each on page load would be eighty requests to a public
  // endpoint that would rightly rate-limit us. The request starts on expand
  // and stops on collapse.
  import { onDestroy } from 'svelte';
  import { fetchSummary } from './live.js';

  export let league = '';
  export let eventId = '';
  /** Only poll while the game is actually in progress. */
  export let live = false;

  let box = [];
  let plays = [];
  let state = 'idle'; // idle | loading | ready | empty | failed
  let timer = null;
  let controller = null;
  let loadedKey = '';

  async function load(key) {
    controller?.abort();
    controller = new AbortController();
    state = state === 'ready' ? 'ready' : 'loading';
    const result = await fetchSummary(league, eventId, controller.signal);
    // A late response for a game that has since been collapsed must not
    // overwrite the one now showing.
    if (key !== loadedKey) return;
    if (!result.ok) {
      state = 'failed';
      return;
    }
    box = result.box;
    plays = result.plays;
    state = box.length || plays.length ? 'ready' : 'empty';
  }

  function stop() {
    clearInterval(timer);
    timer = null;
    controller?.abort();
    controller = null;
  }

  $: key = `${league}|${eventId}`;
  $: if (eventId && key !== loadedKey) {
    loadedKey = key;
    box = [];
    plays = [];
    state = 'loading';
    stop();
    load(key);
  }

  // Poll only while the game is running. A final does not change.
  $: if (eventId && live && !timer) {
    timer = setInterval(() => load(loadedKey), 45_000);
  } else if (!live && timer) {
    clearInterval(timer);
    timer = null;
  }

  onDestroy(stop);

  // A stat block is worth showing in full when it is short; the long ones
  // (every batter, every skater) are capped with the rest behind a toggle, so
  // one opened game does not push the next one off the screen.
  const CAP = 6;
  let expanded = {};
  const toggle = (i) => { expanded = { ...expanded, [i]: !expanded[i] }; };
</script>

<div class="box">
  {#if state === 'loading'}
    <p class="note">Loading the box score…</p>
  {:else if state === 'failed'}
    <p class="note">
      Box score unavailable — the live feed is fetched by your browser from
      ESPN's public endpoint, and this network is not reaching it.
    </p>
  {:else if state === 'empty'}
    <p class="note">No box score posted for this game yet.</p>
  {:else if state === 'ready'}
    {#if plays.length}
      <div class="plays">
        <h5>Scoring</h5>
        <ol>
          {#each plays.slice(-6).reverse() as play (play.id)}
            <li>
              <span class="pteam">{play.team}</span>
              <span class="ptext">{play.text}</span>
              <span class="pscore">{play.away_score}–{play.home_score}</span>
            </li>
          {/each}
        </ol>
      </div>
    {/if}

    {#each box as block, i (`${block.team}-${block.heading}-${i}`)}
      <div class="block">
        <h5>
          <span class="bteam">{block.team}</span>
          {#if block.heading}<span class="bhead">{block.heading}</span>{/if}
        </h5>
        <div class="scroller">
          <table>
            <thead>
              <tr>
                <th class="who">Player</th>
                {#each block.columns as col}<th>{col}</th>{/each}
              </tr>
            </thead>
            <tbody>
              {#each (expanded[i] ? block.rows : block.rows.slice(0, CAP)) as row (row.id)}
                <tr>
                  <td class="who">
                    {row.name}
                    {#if row.position}<span class="pos">{row.position}</span>{/if}
                  </td>
                  {#each row.stats as value}<td class="n">{value}</td>{/each}
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        {#if block.rows.length > CAP}
          <button class="more" on:click={() => toggle(i)}>
            {expanded[i] ? 'Show fewer' : `Show all ${block.rows.length}`}
          </button>
        {/if}
      </div>
    {/each}
  {/if}
</div>

<style>
  .box { display: grid; gap: 0.9rem; }
  .note { font-size: 0.76rem; color: var(--v-ink-3); margin: 0; }

  h5 {
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
    margin: 0 0 0.35rem;
    font-size: 0.62rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--v-ink-3);
  }
  .bteam { color: var(--v-ink-2); }
  .bhead { color: var(--v-ink-3); letter-spacing: 0.09em; }

  .scroller { overflow-x: auto; scrollbar-width: thin; max-width: 100%; }
  table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 0.78rem; }
  th {
    text-align: right;
    padding: 0.3rem 0.5rem;
    font-family: var(--v-board);
    font-size: 0.6rem;
    font-weight: 700;
    letter-spacing: 0.09em;
    color: var(--v-ink-3);
    border-bottom: 1px solid var(--v-line-2);
    white-space: nowrap;
  }
  th.who { text-align: left; }
  td {
    padding: 0.32rem 0.5rem;
    border-bottom: 1px solid var(--v-line);
    color: var(--v-ink-2);
    white-space: nowrap;
  }
  td.who { color: var(--v-ink); }
  td.n {
    text-align: right;
    font-family: var(--v-board);
    font-size: 0.92rem;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    color: var(--v-ink);
  }
  .pos {
    margin-left: 0.35em;
    font-size: 0.62rem;
    color: var(--v-ink-3);
  }
  tbody tr:last-child td { border-bottom: 0; }

  .plays ol { margin: 0; padding: 0; list-style: none; display: grid; gap: 0.28rem; }
  .plays li {
    display: grid;
    grid-template-columns: 2.6rem minmax(0, 1fr) auto;
    gap: 0.5rem;
    align-items: baseline;
    font-size: 0.76rem;
    color: var(--v-ink-2);
  }
  .pteam {
    font-family: var(--v-board);
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: var(--v-ink-3);
  }
  .ptext { min-width: 0; }
  .pscore {
    font-family: var(--v-board);
    font-weight: 700;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
  }

  .more {
    margin-top: 0.3rem;
    border: 0;
    background: transparent;
    padding: 0.15rem 0;
    color: var(--v-brand);
    font-size: 0.72rem;
    cursor: pointer;
  }
  .more:hover { text-decoration: underline; }
</style>
