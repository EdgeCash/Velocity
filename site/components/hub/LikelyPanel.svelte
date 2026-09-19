<script>
  // Most Likely: the outcomes the simulation is surest of, ranked by
  // probability, with the market's best price beside each — Ballpark Pal's
  // page of the same name, for football (docs/FOOTBALL_PAL.md).
  //
  // This is not the card. The card is what cleared the publish gate: an
  // edge with a rule behind it. This is the other question a bettor asks a
  // simulation — "what does it think is going to happen" — and the honest
  // answer includes the 80% favourite at −400 that is no bet at all. The
  // price and implied columns sit beside the sim so the two are never
  // confused: agreement is not value, and a gap is not a pick.
  import { american, kickoffLabel, pct } from '../format.js';
  import { impliedProb } from './model.js';

  /** From `mostLikely`: [{ key, label, rows, top, n }]. */
  export let sections = [];
  export let isPrivate = true;

  let showAll = {};
  const toggle = (key) => { showAll = { ...showAll, [key]: !showAll[key] }; };
  const implied = (price) => (Number.isFinite(price) ? impliedProb(price) : null);
  const edgeOf = (row) => (Number.isFinite(row.p_fair) ? row.p_model - row.p_fair : null);
  const rowKey = (r) => `${r.game_id}|${r.market}|${r.side}|${r.point}|${r.player}`;
  const width = (p) => `${Math.round(Math.max(0, Math.min(1, p)) * 100)}%`;
</script>

{#if !sections.length}
  <div class="none">
    <h3>Nothing simulated</h3>
    <p>
      Outcomes appear once a live run has priced a board for a league in this
      filter. Every row here is a probability from the simulation, ranked
      surest first; the price beside it is the market's, for comparison only.
    </p>
  </div>
{:else}
  {#each sections as s (s.key)}
    {@const shown = showAll[s.key] ? s.rows : s.top}
    <section>
      <h3>{s.label} <span class="meta">{s.n} outcomes</span></h3>
      <div class="scroller">
        <table>
          <thead>
            <tr>
              <th class="l">Outcome</th>
              <th class="l">Game</th>
              <th class="l">Sim</th>
              {#if isPrivate}
                <th>Best price</th>
                <th>Implied</th>
                <th>Sim − market</th>
              {/if}
            </tr>
          </thead>
          <tbody>
            {#each shown as r (rowKey(r))}
              {@const edge = edgeOf(r)}
              <tr>
                <td class="l strong">
                  {r.label}
                  {#if r.tier}<span class="tier">{r.tier}</span>{/if}
                </td>
                <td class="l game">
                  {r.away_team} @ {r.home_team}
                  {#if r.kickoff}<span class="when">{kickoffLabel(r.kickoff)}</span>{/if}
                </td>
                <td class="l sim">
                  <span class="bar" aria-hidden="true"><i style={`width:${width(r.p_model)}`}></i></span>
                  <span class="n">{pct(r.p_model, 1)}</span>
                </td>
                {#if isPrivate}
                  <td class="n">
                    {r.price === null ? '—' : american(r.price)}
                    {#if r.venue}<span class="venue">{r.venue}</span>{/if}
                  </td>
                  <td class="n">{pct(implied(r.price), 1)}</td>
                  <td class="n" class:pos={edge !== null && edge > 0} class:neg={edge !== null && edge < 0}>
                    {edge === null ? '—' : pct(edge, 1, true)}
                  </td>
                {/if}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      {#if s.n > s.top.length}
        <button class="more" on:click={() => toggle(s.key)}>
          {showAll[s.key] ? `Show the top ${s.top.length}` : `Show all ${s.n}`}
        </button>
      {/if}
    </section>
  {/each}

  <p class="note">
    <strong>Sim</strong> is the model's probability of the outcome from its own
    simulated distribution. <strong>Best price</strong> is the best quote across
    the books and exchanges on the board, <strong>Implied</strong> that price as a
    probability with the vig still in it, and <strong>Sim − market</strong> the gap
    against the de-vigged fair probability. A likely outcome is not a play:
    what clears the publish gate is on the Card.
  </p>
{/if}

<style>
  section { margin-bottom: 1.6rem; }
  h3 {
    display: flex;
    align-items: baseline;
    gap: 0.6rem;
    margin: 0 0 0.5rem;
    font-family: var(--v-board);
    font-size: 0.86rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--v-ink);
  }
  .meta {
    font-size: 0.66rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    color: var(--v-ink-3);
  }
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
  td.n, .n { text-align: right; font-variant-numeric: tabular-nums; }
  .strong { font-weight: 600; color: var(--v-ink); }
  .game { color: var(--v-ink-2); }
  .when, .venue {
    display: inline-block;
    margin-left: 0.45rem;
    font-family: var(--v-board);
    font-size: 0.62rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--v-ink-3);
  }
  .tier {
    display: inline-block;
    margin-left: 0.4rem;
    padding: 0.05rem 0.35rem;
    border-radius: 999px;
    font-family: var(--v-board);
    font-size: 0.6rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: var(--v-brand);
    background: var(--v-brand-deep);
  }
  .sim { min-width: 11rem; }
  .bar {
    display: inline-block;
    vertical-align: middle;
    width: 6.5rem;
    height: 0.45rem;
    margin-right: 0.5rem;
    border-radius: 999px;
    background: var(--v-lvl-2);
    overflow: hidden;
  }
  .bar i { display: block; height: 100%; background: var(--v-brand); }
  .pos { color: var(--v-up); }
  .neg { color: var(--v-down); }
  .more {
    margin-top: 0.5rem;
    border: 1px solid var(--v-line);
    border-radius: 999px;
    padding: 0.25rem 0.8rem;
    background: transparent;
    color: var(--v-ink-2);
    font-family: var(--v-board);
    font-size: 0.7rem;
    letter-spacing: 0.08em;
    cursor: pointer;
  }
  .more:hover { color: var(--v-ink); background: var(--v-lvl-2); }
  .note, .none p { color: var(--v-ink-2); font-size: 0.8rem; line-height: 1.5; }
  .none h3 { margin-bottom: 0.3rem; }
</style>
