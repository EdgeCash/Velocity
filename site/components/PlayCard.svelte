<script>
  // One recommended bet, as a blotter row rather than a table cell.
  //
  // A wide DataTable was the old home page: nine columns that clipped on a
  // laptop and showed two of them on a phone, with the rationale wrapping
  // rows to six lines. Everything a decision needs is here in one object —
  // who, what number, what price, what it is worth, what to stake — laid out
  // so the eye lands on the market and the stake first.
  import {
    american, kickoffLabel, line, marketLabel, num, pct, sideLabel,
  } from './format.js';

  export let league = '';
  export let home_team = '';
  export let away_team = '';
  export let kickoff = null;
  export let market = '';
  export let side = '';
  export let point = null;
  export let price = null;
  export let book = '';
  export let venue = '';
  export let edge = null;
  export let stake = null;
  export let tier = '';
  export let player = null;
  /** Set on a priced-but-unstaked row; carries the reason it sat. */
  export let note = null;
  export let game_id = null;
  /** The intel layer's one-line argument, shown under the row when present. */
  export let rationale = '';

  $: paper = !(Number(stake) > 0);
  $: href = game_id ? `/matchup/${game_id}` : null;
  $: matchup = away_team && home_team ? `${away_team} @ ${home_team}` : (game_id ?? '');
  $: call = [sideLabel(side), line(point, market)].filter(Boolean).join(' ');
  $: at = kickoffLabel(kickoff);
</script>

<article class="play" class:paper>
  <div class="head">
    <span class="lg">{String(league).toUpperCase()}</span>
    {#if at}<span class="at">{at}</span>{/if}
    {#if tier}<span class="tier tier-{String(tier).toLowerCase()}">{tier}</span>{/if}
  </div>

  <div class="who">
    {#if href}
      <a {href}>{matchup}</a>
    {:else}
      {matchup}
    {/if}
    {#if player}<span class="player">{player}</span>{/if}
  </div>

  <div class="body">
    <div class="call">
      <span class="market">{marketLabel(market)}</span>
      <span class="pick">{call}</span>
    </div>

    <div class="price">
      <span class="odds">{american(price)}</span>
      <span class="book">{venue && venue !== 'sportsbook' ? venue : book}</span>
    </div>

    <div class="edge">
      <span class="k">Edge</span>
      <span class="v">{edge === null || edge === undefined ? '—' : pct(edge, 1)}</span>
    </div>

    <div class="stake">
      {#if paper}
        <span class="paper-tag">Paper</span>
      {:else}
        <span class="k">Stake</span>
        <span class="v">{num(stake, 2)}u</span>
      {/if}
    </div>
  </div>

  {#if paper && note}
    <div class="why">{note}</div>
  {:else if rationale}
    <div class="why">{rationale}</div>
  {/if}
</article>

<style>
  .play {
    background: var(--v-surface, #10161f);
    border: 1px solid var(--v-line, #1c2733);
    border-radius: var(--v-radius, 10px);
    padding: 0.6rem 0.8rem 0.65rem;
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    transition: border-color 120ms ease, background 120ms ease;
  }
  .play:hover { border-color: var(--v-line-2, #26333f); }
  .play.paper { background: transparent; border-style: dashed; }

  .head {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.6rem;
    letter-spacing: 0.11em;
    text-transform: uppercase;
    color: var(--v-ink-3, #64748b);
    font-weight: 700;
  }
  .lg { color: var(--v-brand, #3ddad0); }
  .at { font-weight: 600; letter-spacing: 0.06em; }
  .tier {
    margin-left: auto;
    border-radius: 4px;
    padding: 0.05rem 0.34rem;
    font-size: 0.6rem;
    border: 1px solid var(--v-line-2, #26333f);
    color: var(--v-ink-2, #93a1b1);
  }
  .tier-a { color: var(--v-brand, #3ddad0); border-color: rgba(61, 218, 208, 0.45); }
  .tier-x { color: var(--v-neg, #f0616a); border-color: rgba(240, 97, 106, 0.4); }

  .who {
    font-size: 0.88rem;
    font-weight: 600;
    color: var(--v-ink, #dfe7ef);
    line-height: 1.25;
  }
  .who a { color: inherit; text-decoration: none; border: 0; }
  .who a:hover { color: var(--v-brand, #3ddad0); }
  .player {
    display: block;
    font-size: 0.72rem;
    font-weight: 500;
    color: var(--v-ink-2, #93a1b1);
  }

  /* The decision line. Four blocks, each with its own alignment so the
     numbers stack into columns down a list of plays. */
  .body {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto auto auto;
    align-items: baseline;
    gap: 0.5rem 1rem;
    margin-top: 0.12rem;
  }
  .call { display: flex; align-items: baseline; gap: 0.45rem; min-width: 0; }
  .market {
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--v-ink-3, #64748b);
    font-weight: 700;
  }
  .pick {
    font-size: 0.92rem;
    font-weight: 650;
    color: var(--v-ink, #dfe7ef);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .price { display: flex; flex-direction: column; align-items: flex-end; }
  .odds {
    font-size: 0.88rem;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    color: var(--v-ink, #dfe7ef);
  }
  .book {
    font-size: 0.6rem;
    color: var(--v-ink-3, #64748b);
    text-transform: capitalize;
  }
  .edge, .stake { display: flex; flex-direction: column; align-items: flex-end; }
  .k {
    font-size: 0.55rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--v-ink-3, #64748b);
    font-weight: 700;
  }
  .v {
    font-size: 0.88rem;
    font-weight: 650;
    font-variant-numeric: tabular-nums;
    color: var(--v-ink, #dfe7ef);
  }
  .stake .v { color: var(--v-brand, #3ddad0); }
  .paper-tag {
    font-size: 0.6rem;
    font-weight: 700;
    letter-spacing: 0.09em;
    text-transform: uppercase;
    color: var(--v-ink-3, #64748b);
    border: 1px solid var(--v-line-2, #26333f);
    border-radius: 4px;
    padding: 0.06rem 0.34rem;
  }
  .why {
    font-size: 0.7rem;
    line-height: 1.45;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    color: var(--v-ink-3, #64748b);
    border-top: 1px solid var(--v-line, #1c2733);
    padding-top: 0.35rem;
    margin-top: 0.15rem;
  }

  @media (max-width: 560px) {
    /* Two rows of two: the pick and its price, then what it is worth. */
    .body { grid-template-columns: minmax(0, 1fr) auto; gap: 0.35rem 0.75rem; }
    .edge, .stake { align-items: flex-start; }
    .stake { align-items: flex-end; }
  }
</style>
