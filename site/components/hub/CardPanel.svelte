<script>
  // The card: what the algorithm actually suggests today.
  //
  // This is the primary output of the whole system, and the first version of
  // the rebuild lost it — `publish` reached the browser and nothing rendered
  // it, so the two plays that cleared the gate on a 101-game slate were
  // findable only by opening game cards one at a time.
  //
  // It earns a view rather than a block inside a game sheet for one reason:
  // the plays are scattered across games BY DEFINITION, and gathering them is
  // the product. It is the default landing for the same reason.
  //
  // The held-back half is not an appendix. "Conviction 0.72 below 0.72" and
  // "edge 0.028 below floor 0.030" are the gate working exactly as designed,
  // and they are also the only way to see whether the thresholds are set
  // where you want them. A card with no rejects is a gate you cannot audit.
  import {
    american, isNum, kickoffLabel, marketLabel, num, pct, signed,
    sideLabel, teamMark, venueColor, venueMark,
  } from '../format.js';
  import TeamMark from '../TeamMark.svelte';
  import PlayBrief from './PlayBrief.svelte';
  import { clvTrust, heldGroups, realRows } from './model.js';
  import { hubState } from './state.js';

  export let card = { plays: [], held: [] };
  export let exposure = [];
  export let league = 'all';
  export let identity = {};
  export let isPrivate = true;
  /** Per-market CLV with its trust flag; private builds only. */
  export let clv = [];

  const scope = (rows) => (league === 'all'
    ? rows : rows.filter((r) => String(r.league ?? '') === league));

  $: plays = scope(card.plays ?? []);
  $: held = scope(card.held ?? []);
  $: groups = heldGroups(held);

  // What the CARD puts at risk — the published rows only.
  $: cardStake = plays.reduce((sum, p) => sum + (Number(p.stake_sized) || 0), 0);
  $: exp = scope(realRows(exposure));
  // ...against what the whole SIZED board puts at risk, which is a superset:
  // Kelly sizes rows the gate then declines to publish, so on the Sept 12
  // slate the card is 2.02u of a 9.34u sized board. Showing only the card
  // total beside a cap reads as "4% of allowance" and is wrong by 4.6x.
  $: boardStake = exp.reduce((sum, e) => sum + (Number(e.stake_sized) || 0), 0);
  // The cap is NOT additive. `cap_units` is bankroll x the slate fraction,
  // computed per league with the SAME fraction, so every league's row carries
  // an identical ceiling — summing two of them presented twice the real cap.
  // It is only an unambiguous number when one league is in scope.
  $: cap = exp.length === 1 ? Number(exp[0].cap_units) || 0 : 0;

  let open = {};
  const toggle = (rule) => { open = { ...open, [rule]: !open[rule] }; };

  // The brief opens in place. The card is the output, so the default gesture
  // has to keep the reader on it — the game sheet is still one click away for
  // the full matchup, but answering "why this play" no longer costs the card.
  let shown = {};
  const key = (row, i) => `${row.game_id}-${row.market}-${row.side}-${i}`;
  const reveal = (k) => { shown = { ...shown, [k]: !shown[k] }; };

  /** The call, phrased the way a ticket phrases it. */
  function call(row) {
    const kind = String(row.market ?? '');
    const home = teamMark(identity, row.home_team, row.league);
    const away = teamMark(identity, row.away_team, row.league);
    const side = String(row.side ?? '');
    if (row.player) {
      return `${row.player} ${sideLabel(side)}${isNum(row.point) ? ` ${num(row.point, 1).replace('.0', '')}` : ''}`;
    }
    if (kind === 'moneyline') return `${side === 'home' ? home.code : away.code} ML`;
    if (kind === 'spread') {
      const team = side === 'home' ? home.code : away.code;
      return isNum(row.point)
        ? `${team} ${signed(row.point, 1).replace('.0', '')}` : `${team} spread`;
    }
    if (kind === 'total') {
      return isNum(row.point)
        ? `${sideLabel(side).slice(0, 1)} ${num(row.point, 1).replace('.0', '')}`
        : `${sideLabel(side)} total`;
    }
    if (kind.startsWith('team_total')) {
      const team = kind.endsWith('home') ? home.code : away.code;
      return `${team} ${sideLabel(side).slice(0, 1)}${isNum(row.point) ? ` ${num(row.point, 1).replace('.0', '')}` : ''}`;
    }
    return `${marketLabel(kind)} ${sideLabel(side)}`;
  }
</script>

{#if !plays.length && !held.length}
  <div class="none">
    <h3>Nothing priced</h3>
    <p>
      The card fills when a run prices a board. Nothing here for this filter
      means the slate came back empty — a normal state between seasons and on
      an off night, not an error.
    </p>
  </div>
{:else}
  <!-- ---- the card ---------------------------------------------------- -->
  <section class="cardsec">
    <h3>
      The card
      <span class="meta">
        {plays.length} of {plays.length + held.length} priced markets cleared the gate
      </span>
    </h3>

    {#if plays.length}
      {#if isPrivate}
        <div class="totals">
          <span class="pair">
            <span class="k">On the card</span>
            <span class="v stake">{num(cardStake, 2)}<small>u</small></span>
          </span>
          {#if boardStake > cardStake + 1e-9}
            <span class="pair">
              <span class="k">Sized board</span>
              <span class="v cap">{num(boardStake, 2)}<small>u</small></span>
            </span>
          {/if}
          {#if cap > 0}
            <span class="pair">
              <span class="k">Slate cap</span>
              <span class="v cap">{num(cap, 2)}<small>u</small></span>
            </span>
            <span class="pair grow">
              <span class="k">Board of cap</span>
              <span class="bar" aria-hidden="true">
                <span style={`width:${Math.min((boardStake / cap) * 100, 100)}%`}></span>
              </span>
            </span>
          {/if}
        </div>
        {#if boardStake > cardStake + 1e-9}
          <p class="subtle">
            Kelly sizes every row it prices; the gate then publishes a subset.
            The card is what cleared{cap > 0 ? '' : ' — the slate cap is per league, so it is shown when one is selected'}.
          </p>
        {/if}
      {/if}

      <div class="plays">
        {#each plays as row, i (`${row.game_id}-${row.market}-${row.side}-${i}`)}
          {@const home = teamMark(identity, row.home_team, row.league)}
          {@const away = teamMark(identity, row.away_team, row.league)}
          {@const k = key(row, i)}
          {@const play = row}
          <!-- The row opens the BRIEF, not the game. The reasoning used to
               live one surface away, which made the card a list of tips; it
               is on the row now, so the row keeps the reader. -->
          <div class="play" class:open={shown[k]}>
            <button
              class="head"
              aria-expanded={!!shown[k]}
              on:click={() => reveal(k)}
              title="why this play"
            >
              <span class="rank">{i + 1}</span>

              <span class="who">
                <span class="callline">
                  {call(row)}
                  {#if row.tier}<span class="tier t{row.tier}">{row.tier}</span>{/if}
                </span>
                <span class="match">
                  <TeamMark code={away.code} logo={away.logo} color={away.color}
                            label={row.away_team} size={16} />
                  {away.code}
                  <span class="at">@</span>
                  <TeamMark code={home.code} logo={home.logo} color={home.color}
                            label={row.home_team} size={16} />
                  {home.code}
                  <span class="lg">{String(row.league).toUpperCase()}</span>
                  <span class="when">{kickoffLabel(row.kickoff)}</span>
                </span>

                <!-- The one-line why: the gap that argues for the bet, and the
                     single strongest thing to say about it. Everything else
                     waits for the expand. -->
                <span class="why">
                  {#if row.vs}
                    <span class="mini" aria-hidden="true">
                      <span class="mfill" style={`width:${Math.max(0, Math.min(row.vs.pModel, 1)) * 100}%`}></span>
                      <span class="mref" style={`left:${Math.max(0, Math.min(row.vs.pFair, 1)) * 100}%`}></span>
                    </span>
                    <span class="mgap" class:pos={row.vs.gap > 0} class:neg={row.vs.gap < 0}>
                      {signed(row.vs.gap * 100, 1)} pts
                    </span>
                    <span class="mlab">model over market</span>
                  {/if}
                  {#if row.headline}
                    <span class="hl">{row.headline}</span>
                  {/if}
                </span>
              </span>

              {#if isPrivate}
                <span class="nums">
                  <span class="pair">
                    <span class="k">Price</span>
                    <span class="v">{american(row.price)}</span>
                  </span>
                  {#if row.best}
                    <span class="pair">
                      <span class="k">Venue</span>
                      <span
                        class="venue"
                        class:exch={row.best.exchange}
                        style={venueColor(row.best.venue) ? `--vc:${venueColor(row.best.venue)}` : ''}
                        title={row.best.label}
                      >{venueMark(row.best.venue)}</span>
                    </span>
                  {/if}
                  {#if isNum(row.edge)}
                    <span class="pair">
                      <span class="k">Edge</span>
                      <span class="v pos">{pct(row.edge, 1, true)}</span>
                    </span>
                  {/if}
                  {#if isNum(row.conviction)}
                    <span class="pair">
                      <span class="k">Conv</span>
                      <span class="v">{num(row.conviction, 2)}</span>
                    </span>
                  {/if}
                  <span class="pair">
                    <span class="k">Stake</span>
                    <span class="v stake">{num(row.stake_sized, 2)}<small>u</small></span>
                  </span>
                </span>
              {:else}
                <span class="nums">
                  {#if isNum(row.conviction)}
                    <span class="pair">
                      <span class="k">Conv</span>
                      <span class="v">{num(row.conviction, 2)}</span>
                    </span>
                  {/if}
                </span>
              {/if}

              <span class="chev" aria-hidden="true">{shown[k] ? '\u2212' : '+'}</span>
            </button>

            {#if shown[k]}
              <PlayBrief
                {play}
                {isPrivate}
                {away}
                {home}
                clv={clvTrust(clv, row.league, row.market)}
              />
              <button
                class="tosheet"
                on:click={() => hubState.set({ view: 'games', game: row.game_id })}
              >Open the full matchup sheet &rarr;</button>
            {/if}
          </div>
        {/each}
      </div>
    {:else}
      <p class="clear">
        <strong>Nothing cleared the gate.</strong> That is a real answer, not a
        missing one — on most slates the model's own thresholds reject
        everything it priced. What it rejected, and by how much, is below.
      </p>
    {/if}
  </section>

  <!-- ---- what it held back -------------------------------------------- -->
  {#if groups.length}
    <section class="heldsec">
      <h3>
        Held back
        <span class="meta">{held.length} priced, not played — with the rule that stopped each</span>
      </h3>

      <div class="groups">
        {#each groups as group (group.rule)}
          <div class="group" class:near={group.rank === 0}>
            <button
              class="ghead"
              aria-expanded={!!open[group.rule]}
              on:click={() => toggle(group.rule)}
            >
              <span class="grule">{group.rule}</span>
              <span class="gn">{group.rows.length}</span>
              {#if group.rank === 0}<span class="nearflag">near miss</span>{/if}
              <span class="chev" aria-hidden="true">{open[group.rule] ? '−' : '+'}</span>
            </button>

            {#if open[group.rule]}
              <ul class="grows">
                {#each group.rows as row, i (`${row.game_id}-${row.market}-${row.side}-${i}`)}
                  {@const home = teamMark(identity, row.home_team, row.league)}
                  {@const away = teamMark(identity, row.away_team, row.league)}
                  <li>
                    <button
                      class="hrow"
                      on:click={() => hubState.set({ view: 'games', game: row.game_id })}
                    >
                      <span class="hcall">{call(row)}</span>
                      <span class="hmatch">{away.code} @ {home.code}</span>
                      <!-- The gate's own sentence, numbers and all. The group
                           heading is the rule; this is the evidence. -->
                      <span class="hwhy">{row.reason}</span>
                      {#if isPrivate && isNum(row.edge)}
                        <span class="hedge">{pct(row.edge, 1, true)}</span>
                      {/if}
                    </button>
                  </li>
                {/each}
              </ul>
            {/if}
          </div>
        {/each}
      </div>
    </section>
  {/if}

  <p class="note">
    Model output, for entertainment only — it is not advice, and no order is
    ever placed from here. A play is what the gate published at the price it
    was priced at; the market moves. Clicking any row opens the game it is on.
  </p>
{/if}

<style>
  section { margin-bottom: 1.5rem; }
  h3 {
    display: flex;
    align-items: baseline;
    gap: 0.6rem;
    flex-wrap: wrap;
    margin: 0 0 0.6rem;
    font-size: 0.64rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.13em;
    color: var(--v-ink-3);
  }
  .cardsec h3 { color: var(--v-brand); }
  .meta {
    text-transform: none;
    letter-spacing: 0.02em;
    font-weight: 600;
    color: var(--v-ink-3);
  }

  .totals {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 1.2rem;
    padding: 0.6rem 0.8rem;
    margin-bottom: 0.6rem;
    border: 1px solid var(--v-line);
    border-radius: var(--v-radius);
    background: var(--v-lvl-0);
  }
  .totals .grow { flex: 1 1 8rem; min-width: 6rem; }
  .bar {
    display: block;
    height: 4px;
    border-radius: 999px;
    background: var(--v-lvl-2);
    overflow: hidden;
    margin-top: 0.35rem;
  }
  .bar span { display: block; height: 100%; background: var(--v-brand-dim); }

  .pair { display: grid; gap: 0.02rem; }
  .k {
    font-size: 0.54rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    color: var(--v-ink-3);
    white-space: nowrap;
  }
  .v {
    font-family: var(--v-board);
    font-size: 0.95rem;
    font-weight: 600;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .v small { font-size: 0.6em; color: var(--v-ink-3); margin-left: 0.1em; }
  .v.pos { color: var(--v-pos); }
  .v.stake { color: var(--v-pos); }
  .v.cap { color: var(--v-ink-2); }

  /* ---- the plays ------------------------------------------------------
     The card is the one list on this surface where every row is lit: these
     are the rows that cleared, and there are two of them on a typical slate.
     Everything else the board holds is desaturated by comparison. */
  .plays { display: grid; gap: 0.4rem; }
  .play {
    border: 1px solid rgba(47, 109, 50, 0.28);
    border-radius: var(--v-radius);
    background: var(--v-brand-tint);
    overflow: hidden;
    transition: border-color 130ms ease;
  }
  .play.open { border-color: var(--v-brand-dim); }
  .head {
    display: grid;
    grid-template-columns: 1.6rem minmax(0, 1fr) auto auto;
    align-items: center;
    gap: 0.8rem;
    width: 100%;
    padding: 0.6rem 0.8rem;
    border: 0;
    background: transparent;
    text-align: left;
    color: inherit;
    font: inherit;
    cursor: pointer;
    transition: background 130ms ease;
  }
  .head:hover { background: rgba(47, 109, 50, 0.1); }
  .head:focus-visible { outline: 2px solid var(--v-brand); outline-offset: -2px; }
  .chev {
    font-family: var(--v-board);
    font-size: 1rem;
    font-weight: 600;
    color: var(--v-brand-dim);
  }

  /* ---- the one-line why -------------------------------------------------
     A miniature of the brief's gauge: same zero baseline, same neutral tick
     for the market, sized so a 4-point gap is still a visible slice rather
     than a rounding error. */
  .why {
    display: flex;
    align-items: center;
    gap: 0.4rem 0.55rem;
    flex-wrap: wrap;
    margin-top: 0.18rem;
    font-size: 0.68rem;
    color: var(--v-ink-3);
    min-width: 0;
  }
  .mini {
    position: relative;
    display: block;
    width: 5.5rem;
    height: 5px;
    border-radius: 2px;
    background: var(--v-lvl-2);
    overflow: hidden;
    flex: none;
  }
  .mfill { display: block; height: 100%; border-radius: 2px; background: var(--v-brand-dim); }
  .mref {
    position: absolute;
    top: -1px;
    bottom: -1px;
    width: 2px;
    background: var(--v-ink);
    box-shadow: 0 0 0 1px var(--v-lvl-0);
    transform: translateX(-1px);
  }
  .mgap {
    font-family: var(--v-board);
    font-size: 0.78rem;
    font-weight: 700;
    color: var(--v-ink-2);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .mgap.pos { color: var(--v-pos); }
  .mgap.neg { color: var(--v-neg); }
  .mlab { white-space: nowrap; }
  .hl {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--v-ink-2);
  }

  .tosheet {
    display: block;
    width: 100%;
    padding: 0.45rem 0.85rem 0.55rem;
    border: 0;
    border-top: 1px solid var(--v-line);
    background: var(--v-lvl-0);
    text-align: left;
    font: inherit;
    font-size: 0.68rem;
    color: var(--v-brand);
    cursor: pointer;
  }
  .tosheet:hover { background: var(--v-hover); }
  .tosheet:focus-visible { outline: 2px solid var(--v-brand); outline-offset: -2px; }
  .rank {
    font-family: var(--v-board);
    font-size: 0.9rem;
    font-weight: 700;
    color: var(--v-brand-dim);
    font-variant-numeric: tabular-nums;
  }
  .who { display: grid; gap: 0.16rem; min-width: 0; }
  .callline { display: flex; align-items: baseline; gap: 0.5rem; flex-wrap: wrap; }
  .callline {
    font-family: var(--v-board);
    font-size: 1.12rem;
    font-weight: 700;
    color: var(--v-ink);
  }
  .match {
    display: flex;
    align-items: center;
    gap: 0.32rem;
    flex-wrap: wrap;
    font-family: var(--v-board);
    font-size: 0.74rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: var(--v-ink-2);
  }
  .at { color: var(--v-ink-3); }
  .match .lg, .match .when {
    font-weight: 600;
    letter-spacing: 0.08em;
    color: var(--v-ink-3);
    font-size: 0.66rem;
  }
  .match .when { letter-spacing: 0.02em; }
  .tier {
    padding: 0.05rem 0.34rem;
    border-radius: 4px;
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.06em;
  }
  .tA { background: var(--v-pos-tint); color: var(--v-pos); }
  .tB { background: var(--v-info-tint); color: var(--v-info); }
  .tC { background: var(--v-thin-tint); color: var(--v-thin); }

  .nums { display: flex; flex-wrap: wrap; gap: 0.9rem; justify-content: flex-end; }
  .venue {
    display: inline-grid;
    place-items: center;
    width: 1.4rem;
    height: 1.25rem;
    border-radius: 4px;
    font-family: var(--v-board);
    font-size: 0.6rem;
    font-weight: 700;
    color: var(--vc, var(--v-ink-2));
    background: rgba(31, 26, 21, 0.05);
  }
  .venue.exch { box-shadow: inset 0 0 0 1px var(--vc, var(--v-line-2)); }

  .subtle {
    margin: 0.1rem 0 0.6rem;
    font-size: 0.7rem;
    line-height: 1.5;
    color: var(--v-ink-3);
  }
  .clear {
    margin: 0;
    padding: 0.9rem 1rem;
    border: 1px dashed var(--v-line-2);
    border-radius: var(--v-radius);
    background: var(--v-lvl-0);
    font-size: 0.82rem;
    line-height: 1.6;
    color: var(--v-ink-2);
  }
  .clear strong { color: var(--v-ink); font-weight: 600; }

  /* ---- held back ------------------------------------------------------ */
  .groups { display: grid; gap: 0.35rem; }
  .group {
    border: 1px solid var(--v-line);
    border-radius: var(--v-radius-sm);
    background: var(--v-lvl-0);
    overflow: hidden;
  }
  /* A near miss is the only held group worth a decision; the rest are the
     gate working as designed on rows that were never candidates. */
  .group.near { border-color: rgba(133, 87, 0, 0.3); }
  .ghead {
    display: flex;
    align-items: baseline;
    gap: 0.6rem;
    width: 100%;
    padding: 0.45rem 0.7rem;
    border: 0;
    background: transparent;
    text-align: left;
    cursor: pointer;
    color: inherit;
    font: inherit;
  }
  .ghead:hover { background: var(--v-hover); }
  .ghead:focus-visible { outline: 2px solid var(--v-brand); outline-offset: -2px; }
  .grule { font-size: 0.82rem; color: var(--v-ink); }
  .gn {
    font-family: var(--v-board);
    font-size: 0.88rem;
    font-weight: 700;
    color: var(--v-ink-2);
    font-variant-numeric: tabular-nums;
  }
  .nearflag {
    padding: 0.05rem 0.36rem;
    border-radius: 999px;
    font-size: 0.6rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    background: var(--v-warn-tint);
    color: var(--v-warn);
  }
  .chev {
    margin-left: auto;
    font-family: var(--v-board);
    font-size: 0.95rem;
    color: var(--v-ink-3);
  }

  .grows { margin: 0; padding: 0 0.4rem 0.4rem; list-style: none; display: grid; gap: 0.1rem; }
  .hrow {
    display: grid;
    grid-template-columns: minmax(6rem, auto) minmax(4.5rem, auto) minmax(0, 1fr) auto;
    align-items: baseline;
    gap: 0.7rem;
    width: 100%;
    padding: 0.3rem 0.5rem;
    border: 0;
    border-radius: 5px;
    background: transparent;
    text-align: left;
    color: inherit;
    font: inherit;
    cursor: pointer;
  }
  .hrow:hover { background: var(--v-hover); }
  .hrow:focus-visible { outline: 2px solid var(--v-brand); outline-offset: -2px; }
  .hcall {
    font-family: var(--v-board);
    font-size: 0.9rem;
    font-weight: 600;
    color: var(--v-ink-2);
    white-space: nowrap;
  }
  .hmatch {
    font-family: var(--v-board);
    font-size: 0.72rem;
    letter-spacing: 0.05em;
    color: var(--v-ink-3);
    white-space: nowrap;
  }
  .hwhy {
    font-size: 0.74rem;
    color: var(--v-ink-3);
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .hedge {
    font-family: var(--v-board);
    font-size: 0.82rem;
    font-weight: 600;
    color: var(--v-ink-2);
    font-variant-numeric: tabular-nums;
  }

  .note {
    margin: 1rem 0 0;
    font-size: 0.72rem;
    line-height: 1.6;
    color: var(--v-ink-3);
  }

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

  @media (max-width: 720px) {
    /* Three columns, with the numbers on their own row: leaving the chevron to
       flow after a full-width .nums stranded it alone on a third row. */
    .head { grid-template-columns: 1.4rem minmax(0, 1fr) auto; }
    .chev { grid-column: 3; grid-row: 1; }
    .nums { grid-column: 1 / -1; grid-row: 2; justify-content: flex-start; }
    /* The one-line why earns a second line here rather than an ellipsis. */
    .hl { white-space: normal; overflow: visible; }
    .hrow { grid-template-columns: minmax(0, 1fr) auto; }
    .hmatch, .hwhy { grid-column: 1 / -1; white-space: normal; }
  }
</style>
