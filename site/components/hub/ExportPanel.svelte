<script>
  // Export: every table the page holds, offered as a file.
  //
  // The rows are written in the BROWSER from what the page already loaded,
  // not linked to the parquet on disk. Evidence addresses those files by a
  // content hash it mints at build time, which this page has no honest way
  // to know — and exporting what the page holds has the better property
  // anyway: the file can never carry more than the tier does. A public build
  // has already emptied its private tables in the data build, so a download
  // inherits that rather than re-deciding it here.
  import { num } from '../format.js';
  import { toCsv } from './model.js';

  /** From `exportTables`. */
  export let tables = [];
  /** The slate stamp, so a downloaded file says which board it came from. */
  export let stamp = '';
  export let isPrivate = true;

  let busy = '';

  function download(table) {
    if (typeof window === 'undefined' || !table.n) return;
    busy = table.key;
    try {
      const csv = toCsv(table.rows);
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `velocity-${table.key}${stamp ? `-${stamp}` : ''}.csv`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      // Revoking on the next tick rather than immediately: Safari has not
      // finished reading the blob when click() returns.
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } finally {
      busy = '';
    }
  }

  $: filled = tables.filter((t) => t.n > 0);
  $: empty = tables.filter((t) => !t.n);
  $: rows = filled.reduce((sum, t) => sum + t.n, 0);
</script>

<p class="lede">
  Every table behind this board, as CSV. Files are written here in the
  browser from the data the page already holds, so a download carries exactly
  what you can see and nothing more.{#if stamp} Each file is named for the
  slate it came from.{/if}
</p>

{#if !filled.length}
  <div class="none">
    <h3>Nothing to export</h3>
    <p>
      Tables fill when a run publishes a board. Nothing here means this build
      carries no rows yet.
    </p>
  </div>
{:else}
  <div class="scroller">
    <table>
      <thead>
        <tr>
          <th class="l">Table</th>
          <th class="l">What it holds</th>
          <th>Rows</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {#each filled as t (t.key)}
          <tr>
            <td class="l strong">{t.label}</td>
            <td class="l note-cell">{t.note}</td>
            <td class="n">{num(t.n, 0)}</td>
            <td class="n">
              <button class="get" on:click={() => download(t)} disabled={busy === t.key}>
                CSV
              </button>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  {#if empty.length}
    <p class="note">
      Empty in this build: {#each empty as t, i (t.key)}{i ? ', ' : ''}{t.label}{/each}.
    </p>
  {/if}

  <p class="note">
    {num(rows, 0)} rows across {filled.length} table{filled.length === 1 ? '' : 's'}.
    {#if !isPrivate}
      This is the public tier: the tables derived from a paid odds feed are
      emptied in the data build, so they are not merely hidden here — the
      numbers are not in the file.
    {:else}
      These carry paid-odds-derived prices, edges, stakes and the bankroll.
      They are for your own research and are not ours to redistribute.
    {/if}
  </p>
{/if}

<style>
  .lede { color: var(--v-ink-2); font-size: 0.82rem; line-height: 1.5; margin: 0 0 1rem; }
  .scroller { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th, td { padding: 0.4rem 0.55rem; border-bottom: 1px solid var(--v-line); }
  th {
    font-family: var(--v-board);
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--v-ink-3);
    text-align: right;
    white-space: nowrap;
  }
  th.l, td.l { text-align: left; }
  td.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .strong { font-weight: 600; color: var(--v-ink); white-space: nowrap; }
  .note-cell { color: var(--v-ink-2); }
  .get {
    border: 1px solid var(--v-line);
    border-radius: 999px;
    padding: 0.2rem 0.7rem;
    background: transparent;
    color: var(--v-ink-2);
    font-family: var(--v-board);
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    cursor: pointer;
  }
  .get:hover { color: var(--v-brand); background: var(--v-brand-deep); }
  .get:disabled { opacity: 0.5; cursor: default; }
  .note, .none p { color: var(--v-ink-2); font-size: 0.8rem; line-height: 1.5; }
  .none h3 { margin-bottom: 0.3rem; }
</style>
