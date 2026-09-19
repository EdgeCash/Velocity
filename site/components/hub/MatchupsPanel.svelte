<script>
  // Matchups: each game's units against the ones that have to stop them.
  //
  // The ratings view answers "how good is this team" in one number per side.
  // That number is what prices the game, and it deliberately hides the
  // SHAPE — a defense stout against the run and porous against the pass
  // rates the same as an evenly average one. This is that shape, paired up:
  // the football read of a batter-versus-pitcher page.
  //
  // Both columns are EPA per play centered on the league average, so an
  // offense's positive number is good, a defense's negative number is good,
  // and the two ADD. Net is what the pairing expects per play against an
  // average one, and it is the only column that combines them, because
  // "+0.21 against −0.20" is a comparison a reader should not be asked to
  // do in their head sixteen times.
  import { isNum, kickoffLabel, num, pct, signed } from '../format.js';

  /** From `matchupBoard`. */
  export let rows = [];

  let open = {};
  const toggle = (id) => { open = { ...open, [id]: !open[id] } };

  // The window every row was measured over, stated once rather than per
  // cell: it is the same for a whole league and a reader needs it to know
  // whether they are looking at this year.
  $: window = (() => {
    for (const row of rows) {
      for (const unit of row.units) {
        const cell = unit.off ?? unit.def;
        if (cell && cell.season_from) {
          return cell.season_from === cell.season_to
            ? `${cell.season_from}`
            : `${cell.season_from}–${cell.season_to}`;
        }
      }
    }
    return '';
  })();

  const phaseLabel = (phase) => (phase === 'pass' ? 'Pass' : 'Rush');
  const cellText = (cell) => (cell && cell.epa !== null ? signed(cell.epa, 3) : '—');
</script>

{#if !rows.length}
  <div class="none">
    <h3>No matchups</h3>
    <p>
      Unit splits are read from the committed play-by-play for the two
      football leagues. Nothing here means no game in this filter has a
      projection to key on, or the season has not banked enough plays to
      split yet.
    </p>
  </div>
{:else}
  <p class="lede">
    Every offense against the unit that has to stop it. Both columns are EPA
    per play against league average, so an offense's <strong>positive</strong>
    is good and a defense's <strong>negative</strong> is good; <strong>Net</strong>
    adds them, and is what the pairing expects per play against an average
    one.{#if window} Measured over {window}.{/if}
  </p>

  {#each rows as row (row.game_id)}
    <section>
      <button class="head" on:click={() => toggle(row.game_id)} aria-expanded={!!open[row.game_id]}>
        <span class="game">{row.away_team} @ {row.home_team}</span>
        {#if row.kickoff}<span class="when">{kickoffLabel(row.kickoff)}</span>{/if}
        <span class="edge">sharpest {signed(row.edge, 3)}</span>
      </button>
      <div class="scroller">
        <table>
          <thead>
            <tr>
              <th class="l">Offense</th>
              <th class="l">vs defense</th>
              <th>Off</th>
              <th>Def</th>
              <th>Net</th>
              {#if open[row.game_id]}
                <th>Off plays</th>
                <th>Def plays</th>
                <th>Off succ.</th>
              {/if}
            </tr>
          </thead>
          <tbody>
            {#each row.units as u (`${u.offense}-${u.phase}`)}
              <tr>
                <td class="l strong">{u.offense} <span class="phase">{phaseLabel(u.phase)}</span></td>
                <td class="l dim">{u.defense}</td>
                <td class="n" class:pos={u.off && u.off.epa > 0} class:neg={u.off && u.off.epa < 0}>
                  {cellText(u.off)}
                </td>
                <!-- No money colour on the defensive cell: green-for-positive
                     would mark the WORSE defense as the better one. -->
                <td class="n">{cellText(u.def)}</td>
                <td class="n strong" class:pos={u.net > 0} class:neg={u.net < 0}>
                  {u.net === null ? '—' : signed(u.net, 3)}
                </td>
                {#if open[row.game_id]}
                  <td class="n dim">{u.off && u.off.plays !== null ? num(u.off.plays, 0) : '—'}</td>
                  <td class="n dim">{u.def && u.def.plays !== null ? num(u.def.plays, 0) : '—'}</td>
                  <td class="n dim">{u.off && u.off.success !== null ? pct(u.off.success, 0) : '—'}</td>
                {/if}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </section>
  {/each}

  <p class="note caveat">
    <strong>Descriptive, not priced.</strong> The fitted ratings are what
    project a game; nothing on this view reaches a number anybody bets. The
    opponent correction is <strong>one pass</strong> — each unit is adjusted by
    the season-long average of the units it actually faced, which removes most
    of a soft schedule but is not the simultaneous ridge fit the ratings use.
    Open a game for the play counts behind each cell, and read a thin one
    accordingly: early in a season the correction has little to work with.
  </p>
{/if}

<style>
  .lede { color: var(--v-ink-2); font-size: 0.82rem; line-height: 1.5; margin: 0 0 1rem; }
  section { margin-bottom: 1.1rem; }
  .head {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.6rem;
    width: 100%;
    padding: 0.35rem 0;
    border: 0;
    background: transparent;
    color: var(--v-ink);
    text-align: left;
    cursor: pointer;
  }
  .game {
    font-family: var(--v-board);
    font-size: 0.84rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .when, .edge {
    font-family: var(--v-board);
    font-size: 0.64rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--v-ink-3);
  }
  .edge { margin-left: auto; font-variant-numeric: tabular-nums; }
  .scroller { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  th, td { padding: 0.32rem 0.55rem; border-bottom: 1px solid var(--v-line); white-space: nowrap; }
  th {
    font-family: var(--v-board);
    font-size: 0.6rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--v-ink-3);
    text-align: right;
  }
  th.l, td.l { text-align: left; }
  td.n { text-align: right; font-variant-numeric: tabular-nums; }
  .strong { font-weight: 600; color: var(--v-ink); }
  .dim { color: var(--v-ink-3); }
  .phase {
    display: inline-block;
    margin-left: 0.35rem;
    font-family: var(--v-board);
    font-size: 0.6rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--v-ink-3);
  }
  .pos { color: var(--v-up); }
  .neg { color: var(--v-down); }
  .note, .none p { color: var(--v-ink-2); font-size: 0.8rem; line-height: 1.5; }
  .note.caveat {
    margin-top: 0.8rem;
    padding: 0.6rem 0.75rem;
    border-left: 2px solid var(--v-line);
    background: var(--v-lvl-1);
    border-radius: 0 0.4rem 0.4rem 0;
  }
  .none h3 { margin-bottom: 0.3rem; }
</style>
