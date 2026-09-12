<script>
  // The rail: bankroll, what is riding, and what is live — always on screen.
  //
  // Not a drawer. The two things a bettor should never have to go and ask for
  // are how much is at risk and whether the kill switch has tripped, and a
  // collapsed panel makes both of those a deliberate act. On a phone it stops
  // being a column and becomes a strip of the same chips above the panel,
  // which keeps them visible without stealing the width.
  import { num, pct, signed, tone, stampLabel } from '../format.js';
  import { realRows, toTime } from './model.js';

  export let bankroll = [];
  export let exposure = [];
  export let units = [];
  export let positions = [];
  export let games = [];
  export let live = { games: [], updated: null, ok: true, tried: false };
  export let modelConfig = [];
  export let isPrivate = true;
  export let stamp = '';

  $: bank = realRows(bankroll)[0] ?? null;
  $: exp = realRows(exposure);
  $: staked = positions.reduce((sum, p) => sum + (Number(p.stake) || 0), 0);

  // Today's own result, not the season's: the number you actually want at
  // 11pm. Taken from the last settled slate date present.
  //
  // `profit_sized`, not `units_sized`: the units table's `units*` columns are
  // a per-league RUNNING total (build_site_data.build_units), so reading one
  // here would print the season to date under a label that says one day.
  $: today = (() => {
    const rows = realRows(units);
    if (!rows.length) return null;
    let latest = null;
    for (const row of rows) {
      const t = toTime(row.slate_date);
      if (t === null) continue;
      if (latest === null || t > latest) latest = t;
    }
    if (latest === null) return null;
    let value = 0;
    let bets = 0;
    for (const row of rows) {
      if (toTime(row.slate_date) !== latest) continue;
      value += Number(row.profit_sized ?? row.profit ?? 0) || 0;
      bets += Number(row.bets ?? 0) || 0;
    }
    return { date: new Date(latest), value, bets };
  })();

  $: liveNow = (live.games ?? []).filter((g) => g.state === 'in');

  // A league that is on the board but whose games are all unpriced is worth
  // saying out loud — it is the quiet failure the health check exists for.
  $: unpriced = games.filter((g) => g.n_markets === 0).length;
</script>

<aside class="rail">
  {#if isPrivate && bank}
    <section class="bank" class:halted={bank.halted}>
      <span class="lab">Bankroll</span>
      <span class="big">{num(bank.current, 2)}<small>u</small></span>
      <div class="sub">
        {#if Number(bank.seed) > 0}
          <span class={tone(Number(bank.current) / Number(bank.seed) - 1)}>
            {pct(Number(bank.current) / Number(bank.seed) - 1, 1, true)}
          </span>
          <span>from {num(bank.seed, 0)}u</span>
        {/if}
      </div>
      {#if Number(bank.drawdown) > 0}
        <div class="dd">
          <div class="ddbar">
            <span style={`width:${Math.min(Number(bank.drawdown) / (Number(bank.halt_threshold) || 1) * 100, 100)}%`}></span>
          </div>
          <span class="ddlab">
            {pct(bank.drawdown, 0)} down · halt at {pct(bank.halt_threshold, 0)}
          </span>
        </div>
      {/if}
      {#if bank.halted}
        <p class="halt">Staking halted — the drawdown threshold has tripped.</p>
      {/if}
    </section>
  {/if}

  <section>
    <span class="lab">Riding now</span>
    {#if positions.length}
      <span class="big">{positions.length}</span>
      <div class="sub">
        {#if isPrivate}<span>{num(staked, 2)}u at risk</span>{/if}
      </div>
    {:else}
      <p class="quiet">Nothing open.</p>
    {/if}
  </section>

  {#if today}
    <section>
      <span class="lab">
        Last settled · {today.date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
      </span>
      {#if isPrivate}
        <span class="big {tone(today.value)}">{signed(today.value, 2)}<small>u</small></span>
      {/if}
      <div class="sub"><span>{today.bets} bets</span></div>
    </section>
  {/if}

  {#if isPrivate && exp.length}
    <section>
      <span class="lab">Exposure</span>
      <ul class="expo">
        {#each exp as e (e.league)}
          <li>
            <span class="el">{String(e.league).toUpperCase()}</span>
            <span class="ebar">
              <span style={`width:${Math.min((Number(e.stake_sized) / (Number(e.cap_units) || 1)) * 100, 100)}%`}></span>
            </span>
            <span class="ev">{num(e.stake_sized, 2)}u</span>
          </li>
        {/each}
      </ul>
      <p class="quiet">Against each league's own slate cap.</p>
    </section>
  {/if}

  <section>
    <span class="lab">Live now</span>
    {#if liveNow.length}
      <ul class="livelist">
        {#each liveNow.slice(0, 6) as g (g.event_id || `${g.league}${g.away.abbr}${g.home.abbr}`)}
          <li>
            <span class="lteam">{g.away.abbr}</span>
            <span class="lscore">{g.away_score}</span>
            <span class="lteam">{g.home.abbr}</span>
            <span class="lscore">{g.home_score}</span>
            <span class="ldetail">{g.detail}</span>
          </li>
        {/each}
      </ul>
      {#if liveNow.length > 6}
        <p class="quiet">and {liveNow.length - 6} more</p>
      {/if}
    {:else if live.tried && !live.ok}
      <p class="quiet">Scores unreachable from this network.</p>
    {:else}
      <p class="quiet">No games in progress.</p>
    {/if}
  </section>

  {#if unpriced > 0}
    <section class="warn">
      <span class="lab">Unpriced</span>
      <p class="quiet">
        {unpriced} scheduled {unpriced === 1 ? 'game has' : 'games have'} no
        market on this build.
      </p>
    </section>
  {/if}

  {#if modelConfig?.length}
    <section class="cfgbox">
      <span class="lab">Model</span>
      <ul class="cfg">
        {#each realRows(modelConfig).slice(0, 8) as row, i (`${row.league}-${row.label}-${i}`)}
          <li>
            <span class="ck">{String(row.league ?? '').toUpperCase()} {row.label}</span>
            <span class="cv">{row.detail}</span>
          </li>
        {/each}
      </ul>
    </section>
  {/if}

  <p class="built">Built {stampLabel(stamp)}</p>
</aside>

<style>
  .rail {
    display: grid;
    gap: 0.6rem;
    align-content: start;
    position: sticky;
    top: 6.2rem;
    min-width: 0;
  }
  section {
    padding: 0.65rem 0.75rem;
    border: 1px solid var(--v-line);
    border-radius: var(--v-radius);
    background: var(--v-lvl-0);
    display: grid;
    gap: 0.12rem;
  }
  section.warn { border-color: rgba(245, 179, 66, 0.28); background: var(--v-warn-tint); }
  .bank.halted { border-color: var(--v-alert); }

  .lab {
    font-size: 0.56rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--v-ink-3);
  }
  .big {
    font-family: var(--v-board);
    font-size: 1.5rem;
    font-weight: 700;
    line-height: 1.15;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
  }
  .big small { font-size: 0.55em; color: var(--v-ink-3); margin-left: 0.1em; }
  .big.pos { color: var(--v-pos); }
  .big.neg { color: var(--v-neg); }
  .sub { display: flex; gap: 0.5rem; font-size: 0.68rem; color: var(--v-ink-3); }
  .sub .pos { color: var(--v-pos); }
  .sub .neg { color: var(--v-neg); }
  .quiet { margin: 0.1rem 0 0; font-size: 0.7rem; color: var(--v-ink-3); line-height: 1.5; }

  .dd { margin-top: 0.35rem; display: grid; gap: 0.2rem; }
  .ddbar { height: 3px; background: var(--v-lvl-2); border-radius: 999px; overflow: hidden; }
  .ddbar span { display: block; height: 100%; background: var(--v-warn); }
  .ddlab { font-size: 0.62rem; color: var(--v-ink-3); }
  .halt {
    margin: 0.35rem 0 0;
    font-size: 0.7rem;
    font-weight: 600;
    color: var(--v-alert);
    line-height: 1.45;
  }

  .expo { margin: 0.25rem 0 0.2rem; padding: 0; list-style: none; display: grid; gap: 0.28rem; }
  .expo li {
    display: grid;
    grid-template-columns: 2.6rem minmax(0, 1fr) auto;
    align-items: center;
    gap: 0.4rem;
  }
  .el {
    font-family: var(--v-board);
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.07em;
    color: var(--v-ink-3);
  }
  .ebar { height: 4px; background: var(--v-lvl-2); border-radius: 999px; overflow: hidden; }
  .ebar span { display: block; height: 100%; background: var(--v-brand-dim); }
  .ev {
    font-family: var(--v-board);
    font-size: 0.74rem;
    font-weight: 600;
    color: var(--v-ink-2);
    font-variant-numeric: tabular-nums;
  }

  .livelist { margin: 0.25rem 0 0; padding: 0; list-style: none; display: grid; gap: 0.22rem; }
  .livelist li {
    display: grid;
    grid-template-columns: auto auto auto auto minmax(0, 1fr);
    align-items: baseline;
    gap: 0.32rem;
    font-size: 0.74rem;
  }
  .lteam { font-family: var(--v-board); font-weight: 600; color: var(--v-ink-2); }
  .lscore {
    font-family: var(--v-board);
    font-weight: 700;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
  }
  .ldetail {
    font-size: 0.62rem;
    color: var(--v-warn);
    justify-self: end;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .cfg { margin: 0.25rem 0 0; padding: 0; list-style: none; display: grid; gap: 0.2rem; }
  .cfg li { display: grid; gap: 0.02rem; }
  .ck {
    font-family: var(--v-board);
    font-size: 0.6rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    color: var(--v-ink-3);
  }
  .cv { font-size: 0.7rem; color: var(--v-ink-2); line-height: 1.4; }

  .built {
    margin: 0.2rem 0 0;
    font-family: var(--v-board);
    font-size: 0.6rem;
    font-weight: 600;
    letter-spacing: 0.11em;
    text-transform: uppercase;
    color: var(--v-ink-3);
  }

  /* On a phone the rail stops being a column and becomes a row of the same
     cards, scrolled horizontally — visible without taking the width. */
  @media (max-width: 1080px) {
    .rail {
      position: static;
      grid-auto-flow: column;
      grid-auto-columns: minmax(11rem, auto);
      /* Without this every card stretches to the tallest one in the row, and
         "Nothing open." gets the height of the model block — which is what it
         did. `start` lets each card be its own content's height. */
      align-items: start;
      overflow-x: auto;
      scrollbar-width: thin;
      padding-bottom: 0.3rem;
      order: -1;
    }
    .rail .built { display: none; }
    /* The model block is reference detail, not a glanceable number, and it is
       by far the longest thing here. It belongs in the column, not in a strip
       you swipe past on the way to the board. */
    .rail .cfgbox { display: none; }
  }
</style>
