<script>
  // Market health: whether any market has stopped working.
  //
  // It sits inside Record rather than in a view of its own, because it is the
  // same question at a longer horizon — Record says what happened, this says
  // whether one of the markets producing it has gone bad — and because a
  // fifth tab costs more than this is worth on a surface whose whole argument
  // is that there are few enough views to hold in your head.
  //
  // What it will not do is turn a flag into a verdict. The monitor is
  // explicit that a flag is a question: an exclusion needs the same flag in
  // two review windows, and the operator decides. So the flags are listed
  // with the number that produced them and the language stays "needs a look".
  import { isNum, num, pct, signed, marketLabel, tone } from '../format.js';
  import { healthRows, LONG_WINDOW } from './model.js';

  export let health = [];
  export let league = 'all';

  const SHORT_WINDOW = 7;

  $: long = healthRows(health, league, LONG_WINDOW);
  $: short = healthRows(health, league, SHORT_WINDOW);

  $: flagged = long.filter((r) => !r.thin && String(r.flags ?? ''));
  $: measured = long.filter((r) => !r.thin);
  $: thin = long.filter((r) => r.thin);
  $: candidates = long.filter((r) => r.flag_exclusion);

  // The 7-day row for a market, so the long window can say whether the short
  // one agrees with it. A flag in both windows is the thing the monitor asks
  // the operator to act on; a flag in one is a question.
  $: shortByMarket = new Map(short.map((r) => [`${r.league}|${r.market}`, r]));

  /** CLV is only a yardstick where the close is one. */
  const clv = (row) => (row.clv_trusted ? row.mean_line_clv : null);

  function readOf(row) {
    if (row.thin) return { text: 'thin', tone: 'thin' };
    const flags = String(row.flags ?? '');
    if (!flags) return { text: 'clean', tone: 'ok' };
    return { text: flags, tone: row.flag_exclusion ? 'bad' : 'warn' };
  }
</script>

{#if !long.length}
  <section class="empty">
    <h4>Market health</h4>
    <p>
      The monitor fills as graded days accumulate on the season chain. Nothing
      here means nothing has settled in the window for this filter.
    </p>
  </section>
{:else}
  <section>
    <h4>
      Market health
      <span class="sub">trailing {LONG_WINDOW} days · a flag is a question, not a verdict</span>
    </h4>

    <div class="tiles">
      <div class="tile">
        <span class="lab">Measured</span>
        <span class="big">{measured.length}</span>
        <span class="tsub">enough bets to judge</span>
      </div>
      <div class="tile" class:hot={flagged.length > 0}>
        <span class="lab">Flagged</span>
        <span class="big">{flagged.length}</span>
        <span class="tsub">needs a look</span>
      </div>
      <div class="tile" class:bad={candidates.length > 0}>
        <span class="lab">Exclusion candidates</span>
        <span class="big">{candidates.length}</span>
        <span class="tsub">confirmed {LONG_WINDOW}-day loser</span>
      </div>
      <div class="tile">
        <span class="lab">Too thin</span>
        <span class="big">{thin.length}</span>
        <span class="tsub">under 20 bets</span>
      </div>
    </div>

    {#if flagged.length}
      <div class="flags">
        {#each flagged as row (`${row.league}-${row.market}`)}
          {@const echo = shortByMarket.get(`${row.league}|${row.market}`)}
          <div class="flagrow" class:bad={row.flag_exclusion}>
            <span class="fmarket">
              <span class="flg">{String(row.league).toUpperCase()}</span>
              {marketLabel(row.market)}
            </span>
            <span class="fwhy">{row.flags}</span>
            <span class="fnums">
              <span class="pair">
                <span class="k">Bets</span>
                <span class="v">{row.n_bets}</span>
              </span>
              <span class="pair">
                <span class="k">ROI</span>
                <span class="v {tone(row.roi)}">{pct(row.roi, 1, true)}</span>
              </span>
              {#if isNum(clv(row))}
                <span class="pair">
                  <span class="k">Line CLV</span>
                  <span class="v {tone(clv(row))}">{signed(clv(row), 2)}</span>
                </span>
              {/if}
              {#if isNum(row.drift)}
                <span class="pair">
                  <span class="k">Real − claim</span>
                  <span class="v {tone(row.drift)}">{signed(row.drift, 2)}</span>
                </span>
              {/if}
            </span>
            <!-- Whether the short window agrees is the single most useful
                 thing to say next to a flag: the monitor asks for the same
                 flag in two review windows before an exclusion, so a market
                 flagged in both is further along than one flagged in one. -->
            <span class="fecho" class:agrees={echo && String(echo.flags ?? '') && !echo.thin}>
              {#if echo && echo.thin}
                7-day too thin to confirm
              {:else if echo && String(echo.flags ?? '')}
                also flagged over 7 days
              {:else if echo}
                clean over 7 days
              {:else}
                nothing settled in 7 days
              {/if}
            </span>
          </div>
        {/each}
      </div>
    {:else}
      <p class="clean">
        No market is flagged — every settled market with enough bets is inside
        its bands.
      </p>
    {/if}

    <div class="scroller">
      <table>
        <thead>
          <tr>
            <th class="l">Market</th>
            <th>Bets</th>
            <th>Staked</th>
            <th>Profit</th>
            <th>ROI</th>
            <th>Line CLV</th>
            <th>Beat close</th>
            <th>Claimed</th>
            <th>Realized</th>
            <th class="l">Read</th>
          </tr>
        </thead>
        <tbody>
          {#each long as row (`${row.league}-${row.market}`)}
            {@const read = readOf(row)}
            <tr class:dim={row.thin}>
              <td class="l">
                <span class="flg">{String(row.league).toUpperCase()}</span>
                {marketLabel(row.market)}
              </td>
              <td class="n">{row.n_bets}</td>
              <td class="n">{num(row.staked, 1)}u</td>
              <td class="n {tone(row.profit)}">{signed(row.profit, 2)}</td>
              <td class="n {tone(row.roi)}">{pct(row.roi, 1, true)}</td>
              <td class="n {tone(clv(row))}">
                {isNum(clv(row)) ? signed(clv(row), 2) : '—'}
              </td>
              <td class="n">
                {row.clv_trusted && isNum(row.pct_beat_close)
                  ? pct(row.pct_beat_close, 0) : '—'}
              </td>
              <td class="n">{num(row.claimed, 2)}</td>
              <td class="n">{num(row.realized, 2)}</td>
              <td class="l"><span class="read {read.tone}">{read.text}</span></td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>

    <details class="how">
      <summary>How to read it</summary>
      <ul>
        <li>
          <strong>CLV is the yardstick</strong> on spreads, totals and
          moneylines. A market losing to its close is flagged
          <em>negative CLV</em> only where the test survives Benjamini–Hochberg
          across the window's markets, so a flag by chance is rare. An
          <em>unconfirmed</em> flag is a raw rejection that did not survive.
        </li>
        <li>
          <strong>P/L is the read</strong> on props and team totals, whose
          closes few sharps price. <em>Negative ROI</em> is the same one-sided
          test on per-bet return, and an untrusted market with a confirmed
          negative {LONG_WINDOW}-day ROI is an <em>exclusion candidate</em>.
        </li>
        <li>
          <strong>Overclaims</strong> means the mean claimed probability on
          decided bets ran more than 0.05 ahead of the realized win rate: the
          shrink or the anchoring weight has drifted from what the record
          earns.
        </li>
        <li>
          <strong>Thin</strong> is fewer than 20 bets in the window. Nothing
          else is said about it, and every other flag is suppressed.
        </li>
        <li>
          Exclusion itself needs the flag to persist across two review
          windows. The monitor names candidates; you decide.
        </li>
      </ul>
    </details>
  </section>
{/if}

<style>
  /* This is a new subject, not a continuation of the record above it — it
     answers "has a market stopped working", where everything above answers
     "what happened". Without the rule it read as a caption on the table. */
  section {
    margin: 1.8rem 0 1.3rem;
    padding-top: 1.2rem;
    border-top: 1px solid var(--v-line);
  }
  h4 {
    display: flex;
    align-items: baseline;
    gap: 0.6rem;
    flex-wrap: wrap;
    margin: 0 0 0.6rem;
    font-size: 0.62rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.13em;
    color: var(--v-ink-3);
  }
  .sub { letter-spacing: 0.03em; text-transform: none; color: var(--v-ink-2); }

  .empty p { margin: 0; font-size: 0.8rem; line-height: 1.6; color: var(--v-ink-2); }
  .clean { margin: 0 0 0.8rem; font-size: 0.78rem; color: var(--v-ink-2); }

  .tiles {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(8.5rem, 1fr));
    gap: 0.5rem;
    margin-bottom: 0.8rem;
  }
  .tile {
    display: grid;
    gap: 0.08rem;
    padding: 0.6rem 0.7rem;
    border: 1px solid var(--v-line);
    border-radius: var(--v-radius);
    background: var(--v-lvl-0);
  }
  .tile.hot { border-color: rgba(245, 179, 66, 0.32); background: var(--v-warn-tint); }
  .tile.bad { border-color: rgba(229, 72, 77, 0.4); }
  .lab {
    font-size: 0.56rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.11em;
    color: var(--v-ink-3);
  }
  .big {
    font-family: var(--v-board);
    font-size: 1.4rem;
    font-weight: 700;
    line-height: 1.1;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
  }
  .tile.hot .big { color: var(--v-warn); }
  .tile.bad .big { color: var(--v-alert); }
  .tsub { font-size: 0.64rem; color: var(--v-ink-3); }

  .flags { display: grid; gap: 0.4rem; margin-bottom: 0.9rem; }
  .flagrow {
    display: grid;
    gap: 0.3rem;
    padding: 0.55rem 0.7rem;
    border-radius: var(--v-radius-sm);
    background: var(--v-warn-tint);
    box-shadow: inset 2px 0 0 var(--v-warn);
  }
  .flagrow.bad {
    background: var(--v-neg-tint);
    box-shadow: inset 2px 0 0 var(--v-alert);
  }
  .fmarket {
    display: flex;
    align-items: baseline;
    gap: 0.45rem;
    font-size: 0.9rem;
    font-weight: 600;
    color: var(--v-ink);
  }
  .flg {
    font-family: var(--v-board);
    font-size: 0.6rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: var(--v-ink-3);
  }
  .fwhy { font-size: 0.76rem; color: var(--v-warn); }
  .flagrow.bad .fwhy { color: var(--v-neg); }
  .fnums { display: flex; flex-wrap: wrap; gap: 0.9rem; }
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
  .fecho { font-size: 0.68rem; color: var(--v-ink-3); }
  .fecho.agrees { color: var(--v-warn); font-weight: 600; }

  .scroller { overflow-x: auto; scrollbar-width: thin; max-width: 100%; }
  table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 0.8rem; }
  th {
    text-align: right;
    padding: 0.42rem 0.55rem;
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
  td {
    padding: 0.45rem 0.55rem;
    border-bottom: 1px solid var(--v-line);
    color: var(--v-ink-2);
    white-space: nowrap;
  }
  td.l { color: var(--v-ink); }
  td.n {
    text-align: right;
    font-family: var(--v-board);
    font-size: 0.94rem;
    font-weight: 600;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
  }
  td.n.pos { color: var(--v-pos); }
  td.n.neg { color: var(--v-neg); }
  tbody tr:last-child td { border-bottom: 0; }
  /* A market too thin to judge is desaturated rather than hidden — it is a
     real market with a real record, just not enough of one to read. */
  tr.dim td { opacity: 0.62; }

  .read {
    display: inline-block;
    padding: 0.1rem 0.4rem;
    border-radius: 999px;
    font-family: var(--v-board);
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    background: var(--v-chip);
    color: var(--v-ink-3);
  }
  .read.ok { background: var(--v-pos-tint); color: var(--v-pos); }
  .read.warn { background: var(--v-warn-tint); color: var(--v-warn); }
  .read.bad { background: var(--v-neg-tint); color: var(--v-neg); }
  .read.thin { background: var(--v-thin-tint); color: var(--v-thin); }

  .how { margin-top: 0.9rem; }
  .how summary {
    cursor: pointer;
    font-size: 0.68rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.11em;
    color: var(--v-ink-3);
  }
  .how summary:hover { color: var(--v-ink-2); }
  .how ul {
    margin: 0.5rem 0 0;
    padding-left: 1.1rem;
    display: grid;
    gap: 0.35rem;
    font-size: 0.76rem;
    line-height: 1.55;
    color: var(--v-ink-2);
  }
  .how strong { color: var(--v-ink); font-weight: 600; }
  .how em { font-style: normal; color: var(--v-warn); }
</style>
