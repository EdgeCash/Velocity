<script>
  // The decision brief: everything needed to judge ONE play, on the play.
  //
  // The card used to be a list of calls with a price on each, and the
  // reasoning lived one click away in the game sheet. That is a fine shape for
  // a board and the wrong one for a card, because the card IS the output — a
  // reader who has to open a second surface to find out why is being handed a
  // tip rather than a model result.
  //
  // Everything here was already in the browser. `collapseMarkets` computes
  // p_model and the de-vigged p_fair and keeps every venue's quote;
  // `buildGames` attaches moves, injuries, weather and ratings per game.
  // `buildCard` now carries them onto the row instead of dropping them.
  import { american, isNum, num, pct, signed } from '../format.js';

  export let play;
  export let clv = null;
  export let isPrivate = true;
  /** Resolved team marks from the card, so these columns read as the short
      codes the matchup line above already uses rather than a truncated club
      name ("KAN +6.4" beats "Kansas City Chi… +6.4"). */
  export let away = null;
  export let home = null;

  $: awayName = away?.code || play?.away_team || '';
  $: homeName = home?.code || play?.home_team || '';

  $: vs = play?.vs ?? null;
  $: venues = (play?.venues ?? []).filter((v) => Number.isFinite(v.price));
  $: move = play?.move ?? null;
  $: out = play?.injuries ?? [];
  $: wx = play?.weather ?? null;
  $: rat = play?.ratings ?? { away: null, home: null };

  // Outdoors AND actually weather worth reading. An indoor game has weather
  // rows too and they mean nothing, so `covered` gates the whole block rather
  // than each number in it.
  $: showWx = !!wx && wx.covered === false
    && (isNum(wx.temp_f) || isNum(wx.wind_mph) || isNum(wx.precip_pct));

  $: outAway = out.filter((r) => r.side === 'away');
  $: outHome = out.filter((r) => r.side === 'home');

  const who = (rows) => rows.map((r) => r.player_name).filter(Boolean).join(', ');
</script>

<div class="brief">

  <!-- ---- model vs market --------------------------------------------
       The argument for the bet, in one subtraction. A zero-based bar, so
       the levels are honest, with the market as a reference tick rather
       than a second coloured series: identity is carried by the two direct
       labels, and a neutral tick cannot be confused with the fill. -->
  {#if vs}
    <section class="blk wide">
      <h5>Model vs market</h5>
      <div class="gauge">
        <div class="track" role="img"
             aria-label={`Model ${pct(vs.pModel, 1)}, de-vigged market ${pct(vs.pFair, 1)}`}>
          <div class="fill" style={`width:${Math.max(0, Math.min(vs.pModel, 1)) * 100}%`}></div>
          <div class="ref" style={`left:${Math.max(0, Math.min(vs.pFair, 1)) * 100}%`}></div>
        </div>
        <div class="keys">
          <span class="key model"><i class="sw model" aria-hidden="true"></i>Model {pct(vs.pModel, 1)}</span>
          <span class="key mkt"><i class="sw mkt" aria-hidden="true"></i>Market {pct(vs.pFair, 1)}</span>
          <span class="gap" class:pos={vs.gap > 0} class:neg={vs.gap < 0}>
            {signed(vs.gap * 100, 1)} pts
          </span>
        </div>
      </div>
      <p class="fine">
        Market is the de-vigged consensus, so the gap is the edge before
        staking — not the book's raw implied number.
      </p>
    </section>
  {/if}

  <!-- ---- where to get it ---------------------------------------------- -->
  {#if isPrivate && venues.length}
    <section class="blk">
      <h5>Price <span class="sub">{venues.length} quoting</span></h5>
      <ul class="venues">
        {#each venues.slice(0, 5) as v, i (`${v.venue}-${i}`)}
          <li class:best={i === 0}>
            <span class="vn">{v.label || v.venue}</span>
            <span class="vp">{american(v.price)}</span>
            <!-- "best" as a word, not a colour: the row above it is the same
                 shape and a reader scanning fast needs the label. -->
            {#if i === 0}<span class="tag">best</span>{/if}
            {#if v.exchange}<span class="tag ex">exch</span>{/if}
          </li>
        {/each}
      </ul>
      {#if venues.length > 1}
        <p class="fine">
          Spread of {american(venues[0].price)} to {american(venues[venues.length - 1].price)}
          across the board.
        </p>
      {/if}
    </section>
  {/if}

  <!-- ---- how it got here -----------------------------------------------
       Direction is never colour alone. pos↔warn measures ΔE 6.2 under
       protanopia, so the glyph and the sentence carry it and the colour
       only agrees with them. -->
  {#if move}
    <section class="blk">
      <h5>Moved since open</h5>
      <div class="move" class:better={move.direction === 'better'}
           class:worse={move.direction === 'worse'}>
        <span class="mg" aria-hidden="true">{move.glyph}</span>
        <span class="mv">
          {move.kind === 'price' ? american(move.from) : signed(move.from, 1)}
          <span class="to">→</span>
          {move.kind === 'price' ? american(move.to) : signed(move.to, 1)}
        </span>
        <span class="ml">{move.label}</span>
      </div>
    </section>
  {/if}

  <!-- ---- who is not playing --------------------------------------------- -->
  {#if out.length}
    <section class="blk">
      <h5>Ruled out <span class="sub">{out.length}</span></h5>
      <ul class="outs">
        {#if outAway.length}
          <li><span class="side">{awayName}</span> {who(outAway)}</li>
        {/if}
        {#if outHome.length}
          <li><span class="side">{homeName}</span> {who(outHome)}</li>
        {/if}
      </ul>
    </section>
  {/if}

  <!-- ---- conditions ------------------------------------------------------ -->
  {#if showWx}
    <section class="blk">
      <h5>Conditions</h5>
      <div class="wx">
        {#if isNum(wx.temp_f)}<span><b>{num(wx.temp_f, 0)}°</b>F</span>{/if}
        {#if isNum(wx.wind_mph)}<span><b>{num(wx.wind_mph, 0)}</b> mph wind</span>{/if}
        {#if isNum(wx.precip_pct)}<span><b>{num(wx.precip_pct, 0)}%</b> precip</span>{/if}
      </div>
    </section>
  {/if}

  <!-- ---- how good the two teams are -------------------------------------- -->
  {#if rat.away || rat.home}
    <section class="blk">
      <h5>Net rating</h5>
      <ul class="rats">
        {#each [['away', awayName, rat.away], ['home', homeName, rat.home]] as [key, team, r] (key)}
          {#if r}
            <li>
              <span class="side">{team}</span>
              <span class="rv">{signed(r.net, 1)}</span>
              {#if isNum(r.rank)}<span class="rk">#{num(r.rank, 0)}</span>{/if}
            </li>
          {/if}
        {/each}
      </ul>
    </section>
  {/if}

  <!-- ---- whether to trust the close on this market ------------------------
       The one number the doctrine says to read per market, with its own
       caveat attached. Averaging a meaningful CLV with a meaningless one is
       exactly the mistake this flag exists to stop. -->
  {#if isPrivate && clv}
    <section class="blk">
      <h5>Beating the close</h5>
      {#if clv.trusted}
        <div class="clv">
          <span class="cv" class:pos={clv.meanClv > 0} class:neg={clv.meanClv < 0}>
            {signed(clv.meanClv, 1)}<small>¢</small>
          </span>
          <span class="cn">over {num(clv.nBets, 0)} settled {clv.market} bets</span>
        </div>
      {:else}
        <p class="fine">
          This market does not close efficiently enough for CLV to mean
          skill — judged on P/L instead ({num(clv.nBets, 0)} settled).
        </p>
      {/if}
    </section>
  {/if}
</div>

<style>
  .brief {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
    gap: 0.5rem 1.1rem;
    padding: 0.7rem 0.85rem 0.8rem;
    border-top: 1px solid var(--v-line);
    background: var(--v-lvl-0);
  }
  .blk { min-width: 0; }
  .blk.wide { grid-column: 1 / -1; }

  h5 {
    display: flex;
    align-items: baseline;
    gap: 0.4rem;
    margin: 0 0 0.3rem;
    font-size: 0.54rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--v-ink-3);
  }
  .sub {
    font-family: var(--v-board);
    letter-spacing: 0.04em;
    color: var(--v-ink-3);
  }
  .fine {
    margin: 0.3rem 0 0;
    font-size: 0.66rem;
    line-height: 1.5;
    color: var(--v-ink-3);
  }

  /* ---- the gauge -------------------------------------------------------
     Zero-based: a probability bar that starts anywhere else invents its own
     edge. The fill is the model, the tick is the de-vigged market, and the
     4px rounded data-end sits on the baseline. */
  .gauge { display: grid; gap: 0.3rem; }
  .track {
    position: relative;
    height: 10px;
    border-radius: 3px;
    background: var(--v-lvl-2);
    overflow: hidden;
  }
  .fill {
    height: 100%;
    border-radius: 3px 4px 4px 3px;
    background: var(--v-brand-dim);
  }
  .ref {
    position: absolute;
    top: -2px;
    bottom: -2px;
    width: 2px;
    /* A 2px surface ring so the tick reads against the fill it overlaps. */
    box-shadow: 0 0 0 2px var(--v-lvl-0);
    background: var(--v-ink);
    transform: translateX(-1px);
  }

  .keys {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.2rem 0.85rem;
    font-size: 0.68rem;
    color: var(--v-ink-2);
  }
  .key { display: inline-flex; align-items: center; gap: 0.3rem; white-space: nowrap; }
  .sw { width: 9px; height: 9px; border-radius: 2px; display: inline-block; }
  .sw.model { background: var(--v-brand-dim); }
  .sw.mkt { width: 2px; height: 11px; border-radius: 0; background: var(--v-ink); }
  .gap {
    margin-left: auto;
    font-family: var(--v-board);
    font-size: 0.86rem;
    font-weight: 700;
    color: var(--v-ink-2);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .gap.pos { color: var(--v-pos); }
  .gap.neg { color: var(--v-neg); }

  /* ---- venues ---------------------------------------------------------- */
  .venues { margin: 0; padding: 0; list-style: none; display: grid; gap: 0.1rem; }
  .venues li {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto auto;
    align-items: baseline;
    gap: 0.4rem;
    font-size: 0.72rem;
    color: var(--v-ink-3);
  }
  .venues li.best { color: var(--v-ink); }
  .vn { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .vp {
    font-family: var(--v-board);
    font-size: 0.82rem;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .venues li.best .vp { color: var(--v-pos); }
  .tag {
    padding: 0.02rem 0.26rem;
    border-radius: 3px;
    font-size: 0.54rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    background: var(--v-pos-tint);
    color: var(--v-pos);
  }
  .tag.ex { background: var(--v-info-tint); color: var(--v-info); }

  /* ---- movement -------------------------------------------------------- */
  .move {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 0.35rem;
    font-size: 0.72rem;
    color: var(--v-ink-2);
  }
  .mg { font-size: 0.7rem; color: var(--v-ink-3); }
  .move.better .mg { color: var(--v-pos); }
  .move.worse .mg { color: var(--v-warn); }
  .mv {
    font-family: var(--v-board);
    font-size: 0.86rem;
    font-weight: 600;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .to { color: var(--v-ink-3); margin: 0 0.1rem; }
  .ml { color: var(--v-ink-3); }

  /* ---- outs, weather, ratings ------------------------------------------ */
  .outs, .rats { margin: 0; padding: 0; list-style: none; display: grid; gap: 0.12rem; }
  .outs li {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 0.4rem;
    font-size: 0.72rem;
    color: var(--v-ink-2);
    min-width: 0;
  }
  /* A grid, not `margin-left: auto` inside a flex row: pushing the value with
     auto margin let a long club name shove it past the block's own column and
     onto the neighbouring one. The name truncates instead. */
  .rats li {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto;
    align-items: baseline;
    gap: 0.4rem;
    font-size: 0.72rem;
    color: var(--v-ink-2);
  }
  .side {
    font-family: var(--v-board);
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    color: var(--v-ink-3);
    white-space: nowrap;
  }
  .rats .side { overflow: hidden; text-overflow: ellipsis; }
  .rv {
    font-family: var(--v-board);
    font-size: 0.82rem;
    font-weight: 600;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
  }
  .rk { font-size: 0.64rem; color: var(--v-ink-3); font-variant-numeric: tabular-nums; }

  .wx { display: flex; flex-wrap: wrap; gap: 0.2rem 0.8rem; font-size: 0.72rem; color: var(--v-ink-3); }
  .wx b {
    font-family: var(--v-board);
    font-size: 0.86rem;
    font-weight: 600;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
  }

  /* ---- clv -------------------------------------------------------------- */
  .clv { display: grid; gap: 0.05rem; }
  .cv {
    font-family: var(--v-board);
    font-size: 0.95rem;
    font-weight: 700;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
  }
  .cv small { font-size: 0.6em; color: var(--v-ink-3); }
  .cv.pos { color: var(--v-pos); }
  .cv.neg { color: var(--v-neg); }
  .cn { font-size: 0.66rem; color: var(--v-ink-3); }

  @media (max-width: 720px) {
    .brief { grid-template-columns: 1fr; }
  }
</style>
