<script>
  // Accuracy: the season's Sim Checks, summarised — is the sim biased, how
  // wide is it wrong, and is its WIDTH right. Ballpark Pal's Accuracy page
  // reports "simulated runs have been 0.6% lower than actual"; this is the
  // football version, built from the accuracy chain the grader writes after
  // every game (docs/FOOTBALL_PAL.md).
  //
  // The decile strip is the honest check a simulation owes its reader. If
  // the pregame total distributions are calibrated, the actual totals land
  // uniformly across their deciles and half of them inside the middle 50%.
  // Bias and error say how good the centre is; the strip says whether the
  // spread of the belief is real, which is the thing a point estimate can
  // never tell you.
  import { num, pct, signed } from '../format.js';

  /** From `accuracySummary`. */
  export let summary = { n: 0, games: [], deciles: [] };

  $: peak = Math.max(1, ...(summary.deciles ?? []));
  const day = (ms) => (Number.isFinite(ms)
    ? new Date(ms).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
    : '—');
  const height = (count) => `${Math.round((count / peak) * 100)}%`;
  const ordinalDecile = (i) => ['1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th', '9th', '10th'][i];
</script>

{#if !summary.n}
  <div class="none">
    <h3>Nothing graded yet</h3>
    <p>
      The accuracy chain fills as games finish: each grading run pins every
      final onto the pregame distribution it was simulated from. A league in
      this filter has no graded game yet.
    </p>
  </div>
{:else}
  <div class="tiles">
    <div class="tile">
      <span class="k">Games graded</span>
      <span class="v">{num(summary.n, 0)}</span>
    </div>
    <div class="tile">
      <span class="k">Favourite won</span>
      <span class="v">{pct(summary.fav_won, 1)}</span>
      <span class="s">the side the sim had above 50%</span>
    </div>
    <div class="tile">
      <span class="k">Brier</span>
      <span class="v">{num(summary.brier, 3)}</span>
      <span class="s">on the home win probability · 0.250 is a coin</span>
    </div>
    <div class="tile">
      <span class="k">Total bias</span>
      <span class="v" class:pos={summary.total_bias > 0} class:neg={summary.total_bias < 0}>
        {signed(summary.total_bias, 1)}
      </span>
      <span class="s">sim − actual, pts per game ({pct(summary.total_bias_pct, 1, true)})</span>
    </div>
    <div class="tile">
      <span class="k">Total error</span>
      <span class="v">{num(summary.total_mae, 1)}</span>
      <span class="s">mean absolute, pts</span>
    </div>
    <div class="tile">
      <span class="k">Margin error</span>
      <span class="v">{num(summary.margin_mae, 1)}</span>
      <span class="s">mean absolute, pts</span>
    </div>
    <div class="tile">
      <span class="k">Inside the middle 50%</span>
      <span class="v">{pct(summary.middle_share, 1)}</span>
      <span class="s">actual totals between the sim's 25th and 75th · 50% is calibrated</span>
    </div>
  </div>

  <section>
    <h3>Where the finals landed <span class="meta">decile of the pregame total distribution · {num(summary.n_pct, 0)} games</span></h3>
    <div class="strip" role="img" aria-label="actual totals by decile of the simulated distribution">
      {#each summary.deciles as count, i}
        <div class="col">
          <div class="fill" style={`height:${height(count)}`} title={`${ordinalDecile(i)} decile: ${count}`}></div>
          <span class="lbl">{i + 1}</span>
        </div>
      {/each}
    </div>
    <p class="note">
      Flat is calibrated. A hump in the middle means the sim is too wide; ends
      that fill first mean it is too narrow; a lean to the right means games
      score more than it expects.
    </p>
  </section>

  <section>
    <h3>Every graded game <span class="meta">newest first</span></h3>
    <div class="scroller">
      <table>
        <thead>
          <tr>
            <th class="l">Date</th>
            <th class="l">Game</th>
            <th>Sim</th>
            <th>Final</th>
            <th>Total sim</th>
            <th>Total</th>
            <th>Pct</th>
            <th>Winner pregame</th>
            <th>Pct</th>
          </tr>
        </thead>
        <tbody>
          {#each summary.games as g (g.game_id)}
            <tr>
              <td class="l">{day(g.date)}</td>
              <td class="l strong">{g.away_name} @ {g.home_name}</td>
              <td class="n">{num(g.mu_away, 1)}–{num(g.mu_home, 1)}</td>
              <td class="n strong">{num(g.away_score, 0)}–{num(g.home_score, 0)}</td>
              <td class="n">{num(g.fair_total, 1)}</td>
              <td class="n">{num(g.actual_total, 0)}</td>
              <td class="n">{pct(g.total_percentile, 0)}</td>
              <td class="n">{g.winner_code} {pct(g.p_winner_pregame, 0)}</td>
              <td class="n">{pct(g.winner_percentile, 0)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <p class="note">
      <strong>Pct</strong> is where the actual number sat on the pregame
      distribution — a total at the 90th percentile scored more than nine in ten
      simulated games. <strong>Winner pregame</strong> is the probability the sim
      gave the team that went on to win.
    </p>
  </section>
{/if}

<style>
  .tiles {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(10.5rem, 1fr));
    gap: 0.6rem;
    margin-bottom: 1.4rem;
  }
  .tile {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    padding: 0.7rem 0.8rem;
    border: 1px solid var(--v-line);
    border-radius: 0.6rem;
    background: var(--v-lvl-1);
  }
  .k {
    font-family: var(--v-board);
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--v-ink-3);
  }
  .v { font-size: 1.35rem; font-weight: 600; color: var(--v-ink); font-variant-numeric: tabular-nums; }
  .s { font-size: 0.7rem; color: var(--v-ink-3); }
  .pos { color: var(--v-up); }
  .neg { color: var(--v-down); }
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
  .meta { font-size: 0.66rem; font-weight: 600; letter-spacing: 0.1em; color: var(--v-ink-3); text-transform: none; }
  .strip {
    display: grid;
    grid-template-columns: repeat(10, 1fr);
    gap: 0.3rem;
    height: 7rem;
    padding: 0.4rem 0.4rem 0;
    border: 1px solid var(--v-line);
    border-radius: 0.6rem;
    background: var(--v-lvl-1);
  }
  .col { display: flex; flex-direction: column; justify-content: flex-end; align-items: stretch; min-width: 0; }
  .fill { background: var(--v-brand); border-radius: 0.2rem 0.2rem 0 0; min-height: 2px; }
  .lbl { text-align: center; font-family: var(--v-board); font-size: 0.6rem; color: var(--v-ink-3); padding: 0.2rem 0; }
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
  .note, .none p { color: var(--v-ink-2); font-size: 0.8rem; line-height: 1.5; }
  .none h3 { margin-bottom: 0.3rem; }
</style>
