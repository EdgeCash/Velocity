<script>
  // Power ratings: the per-team strengths behind every projection.
  //
  // This is the one view that is a reference list rather than a decision
  // surface, and that is why a fifth view is defensible where a fifth was not
  // for market health. The join a bettor actually needs — these two teams,
  // this game — is already done inside the game sheet; this is for the other
  // question, "who does the model think is good", which no game card can
  // answer because it only ever shows two teams.
  //
  // It carries leagues the board does not. The model rates college basketball
  // and hockey without pricing them today, and showing that is honest: it
  // says what the model knows, not only what it is betting.
  import { isNum, num, signed } from '../format.js';
  import { ratingAliases, realRows } from './model.js';

  export let ratings = [];
  export let teams = [];
  export let league = 'all';

  /** Start collapsed at this many per league; college runs to 1,653 teams. */
  const TOP = 25;

  let query = '';
  let expanded = {};
  const toggle = (lg) => { expanded = { ...expanded, [lg]: !expanded[lg] }; };

  // The fits name teams their own way — `PIT` for the NFL — so each row
  // carries both the spelling it was rated under and the club name a reader
  // would search for. Both are matched; the recognisable one is shown.
  $: alias = ratingAliases(teams);
  $: rows = realRows(ratings)
    .filter((r) => league === 'all' || String(r.league) === league)
    .map((r) => {
      const fit = String(r.team ?? '');
      const name = alias.get(`${String(r.league).toLowerCase()}|${fit}`) ?? fit;
      return { ...r, fit, name, _hay: `${fit} ${name}`.toLowerCase() };
    });

  $: needle = query.trim().toLowerCase();
  $: matched = needle ? rows.filter((r) => r._hay.includes(needle)) : rows;

  $: byLeague = (() => {
    const out = new Map();
    for (const row of matched) {
      const lg = String(row.league);
      const bucket = out.get(lg) ?? [];
      bucket.push(row);
      out.set(lg, bucket);
    }
    for (const bucket of out.values()) {
      bucket.sort((a, b) => Number(a.rank ?? 1e9) - Number(b.rank ?? 1e9));
    }
    // Biggest league last: NCAAB's 1,653 rows should not be the first thing
    // between you and the NFL's 32.
    return [...out.entries()].sort((a, b) => a[1].length - b[1].length);
  })();

  // A league whose fit reports pace (the basketball ones) gets the column;
  // the football and baseball fits do not, and an all-em-dash column is
  // worse than no column.
  const hasPace = (bucket) => bucket.some((r) => isNum(r.pace));
  // Movement needs a previous run to compare against. On the first build of
  // a season — or any build without `--prev-dir` — there is none, and the
  // columns are dropped rather than printed empty.
  const hasMoves = (bucket) => bucket.some((r) => isNum(r.rank_prev) || isNum(r.net_prev));

  /** Ranks improve by going DOWN, so the delta is previous minus current. */
  const rankMove = (row) => (isNum(row.rank_prev) ? Number(row.rank_prev) - Number(row.rank) : null);
  const netMove = (row) => (isNum(row.net_prev) ? Number(row.net) - Number(row.net_prev) : null);
</script>

{#if !rows.length}
  <div class="none">
    <h3>No ratings</h3>
    <p>
      Ratings publish with each live run, straight from the league's promoted
      fit. Nothing here means no league in this filter has priced a slate yet.
    </p>
  </div>
{:else}
  <div class="bar">
    <input
      type="search"
      bind:value={query}
      placeholder="Find a team…"
      aria-label="find a team"
    />
    {#if needle}
      <span class="found">{matched.length} of {rows.length}</span>
    {/if}
  </div>

  {#if needle && !matched.length}
    <p class="nohit">No team matches “{query}”.</p>
  {/if}

  {#each byLeague as [lg, bucket] (lg)}
    {@const pace = hasPace(bucket)}
    {@const moves = hasMoves(bucket)}
    <!-- A search narrows to what you asked for; capping that would hide the
         answer. Only the unsearched list collapses. -->
    {@const shown = (needle || expanded[lg]) ? bucket : bucket.slice(0, TOP)}
    <section>
      <h3>
        {lg.toUpperCase()}
        <span class="meta">{bucket.length} rated · {bucket[0]?.scale ?? ''}</span>
      </h3>
      <div class="scroller">
        <table>
          <thead>
            <tr>
              <th class="r">#</th>
              <th class="l">Team</th>
              <th>Net</th>
              <th>Off</th>
              <th>Def</th>
              {#if pace}<th>Pace</th>{/if}
              {#if moves}<th>Δ rank</th><th>Δ net</th>{/if}
            </tr>
          </thead>
          <tbody>
            {#each shown as row (row.fit)}
              <tr>
                <td class="r rank">{num(row.rank, 0)}</td>
                <td class="l team">
                  {row.name}
                  <!-- The spelling the fit rates under, kept beside the name
                       it is shown as: it is the key every other table joins
                       on, so hiding it makes a mismatch impossible to spot. -->
                  {#if row.fit !== row.name}<span class="fit">{row.fit}</span>{/if}
                </td>
                <td class="n strong">{signed(row.net, 2)}</td>
                <td class="n">{signed(row.off, 2)}</td>
                <!-- Def is points allowed against average, so a NEGATIVE
                     number is the good one. It deliberately wears no money
                     colour: green-for-positive here would mark the worse
                     defence as the better one. -->
                <td class="n">{signed(row.def, 2)}</td>
                {#if pace}<td class="n">{isNum(row.pace) ? num(row.pace, 1) : '—'}</td>{/if}
                {#if moves}
                  {@const rm = rankMove(row)}
                  {@const nm = netMove(row)}
                  <td class="n" class:pos={rm > 0} class:neg={rm < 0}>
                    {rm === null ? '—' : signed(rm, 0)}
                  </td>
                  <td class="n" class:pos={nm > 0} class:neg={nm < 0}>
                    {nm === null ? '—' : signed(nm, 2)}
                  </td>
                {/if}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      {#if !needle && bucket.length > TOP}
        <button class="more" on:click={() => toggle(lg)}>
          {expanded[lg] ? `Show the top ${TOP}` : `Show all ${bucket.length}`}
        </button>
      {/if}
    </section>
  {/each}

  <p class="note">
    Straight from each league's promoted fit — never hand-tuned. <strong>Off</strong>
    and <strong>Def</strong> are deviations from league average, so a negative
    Def is the good one; <strong>Net</strong> is the expected margin against an
    average opponent on a neutral floor. Scales differ by league because each
    fit has its own natural unit, and <strong>Pace</strong> — the expected
    possessions against an average opponent — exists only where the fit is per
    possession. Δ columns track movement since the previous run.
  </p>
{/if}

<style>
  .bar {
    display: flex;
    align-items: center;
    gap: 0.7rem;
    margin-bottom: 0.9rem;
  }
  input[type="search"] {
    flex: 0 1 18rem;
    min-width: 0;
    padding: 0.35rem 0.7rem;
    border: 1px solid var(--v-line);
    border-radius: 999px;
    background: var(--v-lvl-1);
    color: var(--v-ink);
    font-size: 0.82rem;
  }
  input[type="search"]::placeholder { color: var(--v-ink-3); }
  input[type="search"]:focus {
    outline: none;
    border-color: var(--v-brand-dim);
    box-shadow: 0 0 0 1px rgba(61, 218, 208, 0.25);
  }
  .found {
    font-family: var(--v-board);
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.09em;
    text-transform: uppercase;
    color: var(--v-ink-3);
  }
  .nohit { font-size: 0.8rem; color: var(--v-ink-3); }

  section { margin-bottom: 1.5rem; }
  h3 {
    display: flex;
    align-items: baseline;
    gap: 0.6rem;
    flex-wrap: wrap;
    margin: 0 0 0.5rem;
    font-size: 0.64rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.13em;
    color: var(--v-brand);
  }
  .meta {
    text-transform: none;
    letter-spacing: 0.02em;
    font-weight: 600;
    color: var(--v-ink-3);
  }

  .scroller { overflow-x: auto; scrollbar-width: thin; max-width: 100%; }
  table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 0.82rem; }
  th {
    text-align: right;
    padding: 0.4rem 0.6rem;
    font-family: var(--v-board);
    font-size: 0.58rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--v-ink-3);
    border-bottom: 1px solid var(--v-line-2);
    white-space: nowrap;
  }
  th.l { text-align: left; }
  th.r { text-align: right; width: 2.6rem; }
  td {
    padding: 0.4rem 0.6rem;
    border-bottom: 1px solid var(--v-line);
    color: var(--v-ink-2);
    white-space: nowrap;
  }
  td.l { color: var(--v-ink); }
  td.rank {
    text-align: right;
    font-family: var(--v-board);
    font-size: 0.86rem;
    font-weight: 700;
    color: var(--v-ink-3);
    font-variant-numeric: tabular-nums;
  }
  .team { min-width: 9rem; }
  .fit {
    margin-left: 0.45em;
    font-family: var(--v-board);
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: var(--v-ink-3);
  }
  td.n {
    text-align: right;
    font-family: var(--v-board);
    font-size: 0.96rem;
    font-weight: 600;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
  }
  td.n.strong { font-weight: 700; }
  td.n.pos { color: var(--v-pos); }
  td.n.neg { color: var(--v-neg); }
  tbody tr:hover td { background: var(--v-hover); }
  tbody tr:last-child td { border-bottom: 0; }

  .more {
    margin-top: 0.35rem;
    border: 0;
    background: transparent;
    padding: 0.15rem 0;
    color: var(--v-brand);
    font-size: 0.74rem;
    cursor: pointer;
  }
  .more:hover { text-decoration: underline; }

  .note {
    margin: 1rem 0 0;
    font-size: 0.74rem;
    line-height: 1.6;
    color: var(--v-ink-3);
  }
  .note strong { color: var(--v-ink-2); font-weight: 600; }

  .none {
    padding: 1.6rem 1.2rem;
    border: 1px dashed var(--v-line-2);
    border-radius: var(--v-radius);
    background: var(--v-lvl-0);
  }
  .none h3 {
    margin: 0 0 0.3rem;
    font-size: 0.95rem;
    text-transform: none;
    letter-spacing: -0.01em;
    color: var(--v-ink);
  }
  .none p { margin: 0; font-size: 0.8rem; line-height: 1.6; color: var(--v-ink-2); }
</style>
