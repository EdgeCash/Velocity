<script>
  // Weather: conditions per outdoor game, and what the model did about them.
  //
  // The framing is the point, so the panel states it rather than implying
  // otherwise. The lab measured wind on NFL totals over 2014–2025 and
  // promoted it as a BIAS CORRECTION, not an edge (docs/MODEL_LAB.md Round
  // 5): the bare model over-projected windy totals — 46.3% against the close
  // on windy games — and the adjustment recovers about 1.8 points of that. It
  // makes a windy total honest. It does not beat the close on windy games,
  // and a surface that ranked these rows as plays would be inventing an edge
  // the lab explicitly declined to claim.
  //
  // Two wind numbers, never folded into one: the kickoff-hour forecast is the
  // condition a reader is picturing, the daily max is what the adjustment was
  // fitted on and priced from. They routinely differ by several mph, and
  // showing one under the other's meaning is the kind of quiet wrongness this
  // board exists to avoid.
  import { isNum, kickoffLabel, num, signed } from '../format.js';
  import { WIND_THRESHOLD_MPH } from './model.js';

  /** From `weatherBoard`. */
  export let rows = [];
  /** From `weatherSummary`. */
  export let summary = { n: 0, outdoor: 0, covered: 0, windy: 0, adjusted: 0, points_moved: 0 };

  $: outdoor = rows.filter((r) => !r.covered);
  $: covered = rows.filter((r) => r.covered);

  const windOf = (r) => Math.max(
    isNum(r.wind_mph) ? r.wind_mph : -Infinity,
    isNum(r.wind_model_mph) ? r.wind_model_mph : -Infinity,
  );
  const isWindy = (r) => Number.isFinite(windOf(r)) && windOf(r) >= WIND_THRESHOLD_MPH;
</script>

{#if !rows.length}
  <div class="none">
    <h3>No forecast</h3>
    <p>
      Conditions are read per venue from Open-Meteo for the games on the
      board. Nothing here means no league in this filter has a game at a
      mapped stadium — <code>velocity/report/venues.py</code> carries NFL and
      MLB coordinates, and no college ones yet, so a college-only slate shows
      an empty board rather than a guess.
    </p>
  </div>
{:else}
  <div class="tiles">
    <div class="tile">
      <span class="k">Outdoor games</span>
      <span class="v">{num(summary.outdoor, 0)}</span>
      <span class="s">{num(summary.covered, 0)} under a roof</span>
    </div>
    <div class="tile">
      <span class="k">Wind at {WIND_THRESHOLD_MPH}+ mph</span>
      <span class="v">{num(summary.windy, 0)}</span>
      <span class="s">the threshold the effect was measured past</span>
    </div>
    <div class="tile">
      <span class="k">Totals adjusted</span>
      <span class="v">{num(summary.adjusted, 0)}</span>
      <span class="s">games the model moved for weather</span>
    </div>
    <div class="tile">
      <span class="k">Points taken off</span>
      <span class="v" class:neg={summary.points_moved < 0}>{signed(summary.points_moved, 1)}</span>
      <span class="s">across every adjusted total</span>
    </div>
  </div>

  {#if outdoor.length}
    <section>
      <h3>Outdoor <span class="meta">windiest first</span></h3>
      <div class="scroller">
        <table>
          <thead>
            <tr>
              <th class="l">Game</th>
              <th>Temp</th>
              <th>Wind</th>
              <th>Model wind</th>
              <th>Rain</th>
              <th>Total moved</th>
              <th>Model total</th>
              <th>Market</th>
            </tr>
          </thead>
          <tbody>
            {#each outdoor as r (r.game_id)}
              <tr class:windy={isWindy(r)}>
                <td class="l strong">
                  {r.away_team} @ {r.home_team}
                  {#if r.kickoff}<span class="when">{kickoffLabel(r.kickoff)}</span>{/if}
                  {#if isWindy(r)}<span class="flag">wind</span>{/if}
                </td>
                <td class="n">{r.temp_f === null ? '—' : `${num(r.temp_f, 0)}°`}</td>
                <td class="n">{r.wind_mph === null ? '—' : `${num(r.wind_mph, 0)}`}</td>
                <td class="n model">{r.wind_model_mph === null ? '—' : `${num(r.wind_model_mph, 0)}`}</td>
                <td class="n">
                  {#if r.precip_in !== null}
                    {num(r.precip_in, 2)}"
                  {:else if r.precip_pct !== null}
                    {num(r.precip_pct, 0)}%
                  {:else}
                    —
                  {/if}
                </td>
                <td class="n" class:neg={r.total_points !== null && r.total_points < 0}>
                  {r.total_points === null ? '—' : signed(r.total_points, 1)}
                </td>
                <td class="n">{r.model_total === null ? '—' : num(r.model_total, 1)}</td>
                <td class="n">{r.market_total === null ? '—' : num(r.market_total, 1)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </section>
  {/if}

  {#if covered.length}
    <section>
      <h3>Under a roof <span class="meta">{covered.length} game{covered.length === 1 ? '' : 's'}</span></h3>
      <p class="roofed">
        {#each covered as r, i (r.game_id)}{i ? ' · ' : ''}{r.away_team} @ {r.home_team}{/each}
      </p>
    </section>
  {/if}

  <p class="note">
    <strong>Wind</strong> is the kickoff-hour forecast; <strong>Model wind</strong>
    is the daily maximum the adjustment was fitted on and priced from, so the two
    differ and are not interchangeable. <strong>Total moved</strong> is what the
    weather took off this game's total, from the model that applied it.
  </p>
  <p class="note caveat">
    Measured and promoted as a <strong>bias correction, not an edge</strong>
    (docs/MODEL_LAB.md Round 5): over 2014–2025 the unadjusted model went 46.3%
    against the close on windy games, and the correction recovers about 1.8
    points of that. It makes a windy total honest — it does not beat the close
    on windy games, and nothing here is a play. Only NFL games carry an
    adjustment; there is no college stadium coordinate table yet.
  </p>
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
  .meta {
    font-size: 0.66rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    color: var(--v-ink-3);
    text-transform: none;
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
  td.n { text-align: right; font-variant-numeric: tabular-nums; }
  .strong { font-weight: 600; color: var(--v-ink); }
  .model { color: var(--v-ink-2); }
  tr.windy td { background: rgba(224, 160, 61, 0.06); }
  .when {
    display: inline-block;
    margin-left: 0.45rem;
    font-family: var(--v-board);
    font-size: 0.62rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--v-ink-3);
  }
  .flag {
    display: inline-block;
    margin-left: 0.4rem;
    padding: 0.05rem 0.35rem;
    border-radius: 999px;
    font-family: var(--v-board);
    font-size: 0.58rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--v-warn, #e0a03d);
    background: rgba(224, 160, 61, 0.14);
  }
  .roofed { color: var(--v-ink-2); font-size: 0.82rem; }
  .note, .none p { color: var(--v-ink-2); font-size: 0.8rem; line-height: 1.5; }
  .note.caveat {
    margin-top: 0.6rem;
    padding: 0.6rem 0.75rem;
    border-left: 2px solid var(--v-line);
    background: var(--v-lvl-1);
    border-radius: 0 0.4rem 0.4rem 0;
  }
  .none h3 { margin-bottom: 0.3rem; }
  code { font-size: 0.78rem; color: var(--v-ink); }
</style>
