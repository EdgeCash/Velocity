<script>
  // The graded record: what actually happened, and whether the prices were any
  // good.
  //
  // Closing-line value leads rather than profit, and that ordering is the
  // argument, not decoration. A few hundred graded bets say almost nothing
  // about edge — the noise swamps it — while CLV over the same sample says
  // whether the model was beating the number it bet into. Profit is reported
  // beside it, honestly, as the thing with the wider error bar.
  import { num, pct, signed, marketLabel, tone } from '../format.js';
  import { dailyCurve } from './model.js';
  import HealthPanel from './HealthPanel.svelte';

  export let record = [];
  export let units = [];
  export let clv = [];
  export let health = [];
  export let league = 'all';
  export let isPrivate = true;

  const real = (rows) => (rows ?? []).filter((r) => String(r?.league ?? '') !== '__none__');

  $: graded = real(record).filter(
    (r) => ['win', 'loss', 'push'].includes(String(r.result ?? '')),
  );
  $: scoped = league === 'all' ? graded : graded.filter((r) => r.league === league);
  $: clvScoped = league === 'all'
    ? real(clv) : real(clv).filter((r) => r.league === league);
  $: unitsScoped = league === 'all'
    ? real(units) : real(units).filter((r) => r.league === league);

  $: summary = (() => {
    let wins = 0, losses = 0, pushes = 0, profit = 0, staked = 0;
    let clvSum = 0, clvN = 0, beat = 0;
    for (const row of scoped) {
      const result = String(row.result);
      if (result === 'win') wins += 1;
      else if (result === 'loss') losses += 1;
      else pushes += 1;
      profit += Number(row.profit_sized ?? row.profit ?? 0) || 0;
      staked += Number(row.stake_sized ?? row.stake ?? 0) || 0;
      const c = Number(row.price_clv);
      if (Number.isFinite(c)) {
        clvSum += c;
        clvN += 1;
        if (c > 0) beat += 1;
      }
    }
    const decided = wins + losses;
    return {
      wins, losses, pushes, profit, staked,
      winRate: decided ? wins / decided : null,
      roi: staked > 0 ? profit / staked : null,
      clv: clvN ? clvSum / clvN : null,
      clvN,
      beatRate: clvN ? beat / clvN : null,
    };
  })();

  // The curve, drawn the same way the game sparkline is — one path, no chart
  // runtime — because this is a shape, not a table people read values off. Its
  // last point reconciles with the Units card above it by construction; see
  // `dailyCurve` for the trap that is avoiding.
  $: curve = dailyCurve(unitsScoped);

  const W = 640, H = 110, PAD = 6;
  $: curvePath = (() => {
    if (curve.length < 2) return '';
    const lo = Math.min(...curve.map((p) => p.v), 0);
    const hi = Math.max(...curve.map((p) => p.v), 0);
    const span = hi - lo || 1;
    const t0 = curve[0].t, t1 = curve[curve.length - 1].t;
    const tspan = t1 - t0 || 1;
    const x = (t) => PAD + ((t - t0) / tspan) * (W - PAD * 2);
    const y = (v) => H - PAD - ((v - lo) / span) * (H - PAD * 2);
    return {
      line: curve.map((p, i) => `${i ? 'L' : 'M'} ${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`).join(' '),
      zero: lo <= 0 && hi >= 0 ? y(0) : null,
      last: curve[curve.length - 1].v,
    };
  })();

  $: byMarket = (() => {
    const out = new Map();
    for (const row of scoped) {
      const key = String(row.market ?? '');
      const cur = out.get(key) ?? { market: key, n: 0, wins: 0, profit: 0, staked: 0, clv: 0, clvN: 0 };
      cur.n += 1;
      if (String(row.result) === 'win') cur.wins += 1;
      cur.profit += Number(row.profit_sized ?? row.profit ?? 0) || 0;
      cur.staked += Number(row.stake_sized ?? row.stake ?? 0) || 0;
      const c = Number(row.price_clv);
      if (Number.isFinite(c)) { cur.clv += c; cur.clvN += 1; }
      out.set(key, cur);
    }
    return [...out.values()]
      .map((m) => ({ ...m, meanClv: m.clvN ? m.clv / m.clvN : null, roi: m.staked > 0 ? m.profit / m.staked : null }))
      .sort((a, b) => b.n - a.n);
  })();
</script>

{#if !scoped.length}
  <div class="none">
    <h3>Nothing graded yet</h3>
    <p>
      The record fills as slates settle. Nothing here for this filter means no
      bet on it has been graded against a final score.
    </p>
  </div>
{:else}
  <div class="cards">
    <div class="card">
      <span class="lab">Closing-line value</span>
      <span class="big {tone(summary.clv)}">
        {summary.clv === null ? '—' : pct(summary.clv, 2, true)}
      </span>
      <span class="sub">
        mean, over {summary.clvN} priced bets
        {#if summary.beatRate !== null} · beat the close {pct(summary.beatRate, 0)}{/if}
      </span>
    </div>

    <div class="card">
      <span class="lab">Record</span>
      <span class="big">{summary.wins}–{summary.losses}{summary.pushes ? `–${summary.pushes}` : ''}</span>
      <span class="sub">
        {summary.winRate === null ? '' : `${pct(summary.winRate, 1)} of decided`}
      </span>
    </div>

    {#if isPrivate}
      <div class="card">
        <span class="lab">Units</span>
        <span class="big {tone(summary.profit)}">{signed(summary.profit, 2)}</span>
        <span class="sub">
          {summary.roi === null ? '' : `${pct(summary.roi, 1, true)} on ${num(summary.staked, 1)}u staked`}
        </span>
      </div>
    {/if}
  </div>

  {#if isPrivate && curvePath.line}
    <section>
      <h4>Cumulative units</h4>
      <svg viewBox={`0 0 ${W} ${H}`} class="curve" role="img" aria-label="cumulative units">
        {#if curvePath.zero !== null}
          <line x1={PAD} x2={W - PAD} y1={curvePath.zero} y2={curvePath.zero}
                stroke="var(--v-line-2)" stroke-width="1" stroke-dasharray="3 3" />
        {/if}
        <path d={curvePath.line} fill="none" stroke-width="1.5"
              stroke={curvePath.last >= 0 ? 'var(--v-pos)' : 'var(--v-neg)'} />
      </svg>
      <p class="cap">
        {curve.length} settled days · finishing {signed(curvePath.last, 2)}u
      </p>
    </section>
  {/if}

  <section>
    <h4>By market</h4>
    <div class="scroller">
      <table>
        <thead>
          <tr>
            <th class="l">Market</th>
            <th>Bets</th>
            <th>Won</th>
            <th>CLV</th>
            {#if isPrivate}<th>Units</th><th>ROI</th>{/if}
          </tr>
        </thead>
        <tbody>
          {#each byMarket as m (m.market)}
            <tr>
              <td class="l">{marketLabel(m.market)}</td>
              <td class="n">{m.n}</td>
              <td class="n">{m.wins}</td>
              <td class="n {tone(m.meanClv)}">
                {m.meanClv === null ? '—' : pct(m.meanClv, 2, true)}
              </td>
              {#if isPrivate}
                <td class="n {tone(m.profit)}">{signed(m.profit, 2)}</td>
                <td class="n {tone(m.roi)}">{m.roi === null ? '—' : pct(m.roi, 1, true)}</td>
              {/if}
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  </section>

  {#if clvScoped.length}
    <p class="cap">
      A market is marked CLV-trusted once enough of its bets have a banked
      closing price to mean anything; an untrusted mean is a small sample, not
      a verdict.
    </p>
  {/if}

  <!-- The table above is the whole record; this is the trailing window, which
       is the one that can tell you a market has STOPPED working. Same question
       at a shorter horizon, so it belongs here rather than in a view of its
       own. -->
  {#if isPrivate}
    <HealthPanel {health} {league} />
  {/if}
{/if}

<style>
  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
    gap: 0.6rem;
    margin-bottom: 1.2rem;
  }
  .card {
    display: grid;
    gap: 0.15rem;
    padding: 0.7rem 0.8rem;
    border: 1px solid var(--v-line);
    border-radius: var(--v-radius);
    background: var(--v-lvl-0);
  }
  .lab {
    font-size: 0.58rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.11em;
    color: var(--v-ink-3);
  }
  .big {
    font-family: var(--v-board);
    font-size: 1.6rem;
    font-weight: 700;
    line-height: 1.1;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
  }
  .big.pos { color: var(--v-pos); }
  .big.neg { color: var(--v-neg); }
  .sub { font-size: 0.68rem; color: var(--v-ink-3); }

  section { margin-bottom: 1.3rem; }
  h4 {
    margin: 0 0 0.5rem;
    font-size: 0.62rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.13em;
    color: var(--v-ink-3);
  }
  .curve { display: block; width: 100%; height: auto; }
  .cap { margin: 0.35rem 0 0; font-size: 0.7rem; color: var(--v-ink-3); line-height: 1.55; }

  .scroller { overflow-x: auto; scrollbar-width: thin; max-width: 100%; }
  table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 0.82rem; }
  th {
    text-align: right;
    padding: 0.45rem 0.6rem;
    font-family: var(--v-board);
    font-size: 0.6rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--v-ink-3);
    border-bottom: 1px solid var(--v-line-2);
    white-space: nowrap;
  }
  th.l { text-align: left; }
  td {
    padding: 0.5rem 0.6rem;
    border-bottom: 1px solid var(--v-line);
    color: var(--v-ink-2);
    white-space: nowrap;
  }
  td.l { color: var(--v-ink); }
  td.n {
    text-align: right;
    font-family: var(--v-board);
    font-size: 0.98rem;
    font-weight: 600;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
  }
  td.n.pos { color: var(--v-pos); }
  td.n.neg { color: var(--v-neg); }
  tbody tr:last-child td { border-bottom: 0; }

  .none {
    padding: 1.6rem 1.2rem;
    border: 1px dashed var(--v-line-2);
    border-radius: var(--v-radius);
    background: var(--v-lvl-0);
  }
  .none h3 { margin: 0 0 0.3rem; font-size: 0.95rem; color: var(--v-ink); }
  .none p { margin: 0; font-size: 0.8rem; line-height: 1.6; color: var(--v-ink-2); }
</style>
