<script>
  // Players: the prop board turned around, one row per player line with
  // both sides on it, so a reader can look a PLAYER up rather than a game —
  // Ballpark Pal's "PrizePicks & Underdog" grammar (line, over %, under %,
  // odds), against the sportsbooks this repo actually prices
  // (docs/FOOTBALL_PAL.md). The DFS salary and projection ride along when
  // the player is on a built lineup; a full player pool is the next step.
  import { american, isNum, kickoffLabel, marketLabel, num, pct } from '../format.js';

  /** From `playerBook`: one row per (player, market, line). */
  export let rows = [];
  export let isPrivate = true;

  let query = '';
  let sureFirst = false;
  $: needle = query.trim().toLowerCase();
  $: shown = (needle
    ? rows.filter((r) => `${r.player} ${r.team} ${r.away_team} ${r.home_team}`.toLowerCase().includes(needle))
    : rows
  ).slice().sort((a, b) => (sureFirst ? b.sure - a.sure : 0));
  const rowKey = (r) => `${r.game_id}|${r.player}|${r.market}|${r.point}`;
  const price = (side) => (side && Number.isFinite(side.price) ? american(side.price) : '—');
</script>

{#if !rows.length}
  <div class="none">
    <h3>No player lines</h3>
    <p>
      Player rows come from the prop board the live run priced. Nothing here
      means no league in this filter carried a priced prop board on its last
      run — the college board prices from the committed player bank and the
      NFL board from the FantasyPros-fed simulation.
    </p>
  </div>
{:else}
  <div class="bar">
    <input type="search" bind:value={query} placeholder="Find a player or team…" aria-label="find a player" />
    <label class="toggle">
      <input type="checkbox" bind:checked={sureFirst} />
      surest first
    </label>
    <span class="found">{shown.length} of {rows.length} lines</span>
  </div>

  <div class="scroller">
    <table>
      <thead>
        <tr>
          <th class="l">Player</th>
          <th class="l">Game</th>
          <th class="l">Stat</th>
          <th>Line</th>
          <th>Over</th>
          <th>Under</th>
          {#if isPrivate}
            <th>Best O</th>
            <th>Best U</th>
            <th>DFS</th>
          {/if}
        </tr>
      </thead>
      <tbody>
        {#each shown as r (rowKey(r))}
          <tr>
            <td class="l strong">
              {r.player}
              {#if r.team || r.position}
                <span class="tag">{[r.team, r.position].filter(Boolean).join(' · ')}</span>
              {/if}
            </td>
            <td class="l game">
              {r.away_team} @ {r.home_team}
              {#if r.kickoff}<span class="when">{kickoffLabel(r.kickoff)}</span>{/if}
            </td>
            <td class="l">{marketLabel(r.market)}</td>
            <td class="n">{isNum(r.point) ? num(r.point, 1) : '—'}</td>
            <td class="n" class:lean={r.lean === 'over'}>{pct(r.p_over, 1)}</td>
            <td class="n" class:lean={r.lean === 'under'}>{pct(r.p_under, 1)}</td>
            {#if isPrivate}
              <td class="n">{price(r.over)}{#if r.over?.venue}<span class="venue">{r.over.venue}</span>{/if}</td>
              <td class="n">{price(r.under)}{#if r.under?.venue}<span class="venue">{r.under.venue}</span>{/if}</td>
              <td class="n dfs">
                {#if r.salary !== null || r.dfs_points !== null}
                  {r.salary === null ? '—' : `$${num(r.salary, 0)}`} · {r.dfs_points === null ? '—' : num(r.dfs_points, 1)}
                {:else}
                  —
                {/if}
              </td>
            {/if}
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  <p class="note">
    <strong>Over</strong> and <strong>Under</strong> are the simulation's
    probabilities at the posted line; the leaning side is marked. <strong>Best
    O / U</strong> are the best quotes across the books on the board. The
    <strong>DFS</strong> column shows salary and projected points when the
    player sits on a built lineup.
  </p>
{/if}

<style>
  .bar { display: flex; flex-wrap: wrap; align-items: center; gap: 0.7rem; margin-bottom: 0.9rem; }
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
  .toggle {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    font-family: var(--v-board);
    font-size: 0.68rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--v-ink-2);
    cursor: pointer;
  }
  .found { font-family: var(--v-board); font-size: 0.68rem; letter-spacing: 0.1em; color: var(--v-ink-3); }
  .scroller { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th, td { padding: 0.38rem 0.55rem; border-bottom: 1px solid var(--v-line); white-space: nowrap; }
  th {
    font-family: var(--v-board);
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--v-ink-3);
    text-align: right;
  }
  th.l, td.l { text-align: left; }
  td.n { text-align: right; font-variant-numeric: tabular-nums; }
  .strong { font-weight: 600; color: var(--v-ink); }
  .game { color: var(--v-ink-2); }
  .lean { color: var(--v-brand); font-weight: 600; }
  .tag, .when, .venue {
    display: inline-block;
    margin-left: 0.45rem;
    font-family: var(--v-board);
    font-size: 0.62rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--v-ink-3);
  }
  .dfs { color: var(--v-ink-2); }
  .note, .none p { color: var(--v-ink-2); font-size: 0.8rem; line-height: 1.5; }
  .none h3 { margin-bottom: 0.3rem; }
</style>
