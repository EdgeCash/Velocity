<script>
  // One recommended bet, as a betting object rather than a table cell.
  //
  // A wide DataTable was the old home page: nine columns that clipped on a
  // laptop and showed two of them on a phone, with the rationale wrapping
  // rows to six lines. Everything a decision needs is here in one object.
  //
  // The shape is the genre's, and it is the same shape everywhere: a card
  // carrying a *nested* ticket one elevation step lighter, and inside that
  // ticket the two things you actually decide on — what the bet is, on the
  // left, and what it costs, on the right — over a strip of four numbers
  // that each sit above their own micro-label. The price is the visual
  // anchor: a dark pill when the row is only being watched, and a filled
  // brand pill with near-black text when the model is actually staking it,
  // so the one actionable number on a screen of dark-on-dark is the only
  // bright object on it.
  import {
    american, distThreshold, isExchange, kickoffLabel, line, marketLabel,
    num, pct, sideLabel, venueColor, venueLabel, venueMark,
  } from './format.js';
  import DistStrip from './DistStrip.svelte';

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
  export let conviction = null;
  /** The model's probability and the de-vigged market's, for the strip. */
  export let p_model = null;
  export let p_fair = null;
  export let player = null;
  /** Set on a priced-but-unstaked row; carries the reason it sat. */
  export let note = null;
  export let game_id = null;
  /** The intel layer's one-line argument, shown under the row when present. */
  export let rationale = '';
  /* Exactly one card in a list is the lead, and its price is the only
     inverted pill on the page. A list where every price is filled is the
     genre's clearest cheap tell: if everything is lit, nothing is. */
  export let lead = false;
  /* The simulated outcome distribution for this game, as {value, prob}
     rows. Passed in rather than queried because the card is rendered from
     a row and Evidence's queries live on the page. */
  export let dist = [];
  /** Drop the matchup line — the page above already names the game. */
  export let compact = false;

  $: paper = !(Number(stake) > 0);
  $: href = game_id ? `/matchup/${game_id}` : null;
  $: matchup = away_team && home_team ? `${away_team} @ ${home_team}` : (game_id ?? '');
  $: call = [sideLabel(side), line(point, market)].filter(Boolean).join(' ');
  $: at = kickoffLabel(kickoff);
  // The venue is whichever of the two actually priced it: an exchange names
  // itself, a sportsbook is named by its book code.
  $: source = venue && venue !== 'sportsbook' ? venue : book;
  $: mark = venueMark(source);
  $: markColor = venueColor(source);
  $: sourceName = venueLabel(source);
  $: contract = isExchange(source);
  $: grade = String(tier || '').toUpperCase();
  $: gradeHint = conviction === null || conviction === undefined
    ? '' : `conviction ${num(conviction, 2)}`;
  // Where the bet sits on the simulated distribution, and which way it wins.
  $: cut = distThreshold(market, side, point);
  // The page hands over both of the game's distributions; the bet picks the
  // one it is actually struck against.
  $: bins = cut === null
    ? []
    : (dist ?? []).filter((d) => String(d.kind) === cut.kind);
</script>

<article class="play" class:paper>
  {#if !compact || grade || contract || paper}
  <header class="head">
    {#if grade}
      <span class="grade grade-{grade.toLowerCase()}" title={gradeHint}>{grade}</span>
    {/if}
    {#if !compact}<span class="lg">{String(league).toUpperCase()}</span>{/if}
    {#if at}<span class="at">{at}</span>{/if}
    {#if contract}<span class="tag">Contract</span>{/if}
    {#if paper}<span class="state">Paper</span>{/if}
  </header>
  {/if}

  {#if !compact}
  <div class="who">
    {#if href}
      <a {href}>{matchup}</a>
    {:else}
      {matchup}
    {/if}
    {#if player}<span class="player">{player}</span>{/if}
  </div>
  {/if}

  <div class="ticket">
    <div class="row">
      <div class="call">
        <span class="market">{marketLabel(market)}</span>
        <span class="pick">{call}</span>
      </div>

      <div class="price">
        <span class="pill" class:take={lead && !paper}>
          {#if mark}
            <span
              class="venue"
              class:branded={!!markColor}
              style={markColor ? `--mark:${markColor}` : ''}
            >{mark}</span>
          {/if}
          <span class="odds">{american(price)}</span>
        </span>
        {#if sourceName}<span class="book">{sourceName}</span>{/if}
      </div>
    </div>

    <div class="strip">
      <div class="cell">
        <span class="v edge">{pct(edge, 1, true)}</span>
        <span class="k">Edge</span>
      </div>
      <div class="cell">
        <span class="v">{pct(p_fair, 1)}</span>
        <span class="k">Market</span>
      </div>
      <div class="cell">
        <span class="v" title="Market anchored: belief = market + 0.2 x (model - market)"
          >{pct(p_model, 1)}</span>
        <span class="k">Belief</span>
      </div>
      <div class="cell">
        <span class="v" class:brand={!paper}>{paper ? '—' : `${num(stake, 2)}u`}</span>
        <span class="k">Stake</span>
      </div>
    </div>

    {#if bins.length > 0}
      <DistStrip bins={bins} line={cut.at} above={cut.above} splitTie={cut.splitTie} />
    {/if}
  </div>

  {#if paper && note}
    <div class="why">{note}</div>
  {:else if rationale}
    <div class="why">{rationale}</div>
  {/if}
</article>

<style>
  /* The card is lit from its top-left corner. A list of these reads as a
     row of objects under a light; a list of flat fills reads as a table
     that lost its rules. */
  .play {
    position: relative;
    overflow: hidden;
    background: var(--v-lvl-0, #0b1017);
    border: 1px solid var(--v-line, rgba(255, 255, 255, 0.07));
    border-radius: var(--v-radius, 12px);
    padding: 0.62rem 0.72rem 0.7rem;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    transition: border-color 140ms ease;
  }
  .play::before {
    content: "";
    position: absolute;
    inset: 0;
    pointer-events: none;
    background: radial-gradient(
      420px 130px at 0% 0%,
      rgba(255, 255, 255, 0.055),
      transparent 70%
    );
  }
  .play > * { position: relative; }
  .play:hover { border-color: var(--v-line-2, rgba(255, 255, 255, 0.13)); }
  /* Low-confidence output is desaturated, never hidden: a paper row is a
     real opinion the gate declined to fund, and it still has to be legible
     next to the ones that cleared. */
  .play.paper { opacity: 0.82; }
  .play.paper:hover { opacity: 1; }

  /* ---- head ---------------------------------------------------------- */
  .head {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    font-size: 0.6rem;
    letter-spacing: 0.11em;
    text-transform: uppercase;
    color: var(--v-ink-3, #5d6b7c);
    font-weight: 700;
  }
  /* The grade tile: the category badge every board in the genre puts at the
     far left of a bet object, so the tier is read before anything else. */
  .grade {
    font-family: var(--v-board, "Saira Condensed", sans-serif);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1.15rem;
    height: 1.15rem;
    border-radius: 5px;
    font-size: 0.76rem;
    font-weight: 700;
    letter-spacing: 0;
    background: var(--v-chip, #131c26);
    border: 1px solid var(--v-line-2, rgba(255, 255, 255, 0.13));
    color: var(--v-ink-2, #8fa0b3);
  }
  .grade-a {
    background: var(--v-brand-tint, rgba(61, 218, 208, 0.13));
    border-color: rgba(61, 218, 208, 0.32);
    color: var(--v-brand, #3ddad0);
  }
  .grade-b {
    background: var(--v-info-tint, rgba(91, 141, 255, 0.13));
    border-color: rgba(91, 141, 255, 0.3);
    color: var(--v-info, #5b8dff);
  }
  .grade-x {
    background: var(--v-neg-tint, rgba(249, 114, 137, 0.13));
    border-color: rgba(249, 114, 137, 0.3);
    color: var(--v-neg, #f97289);
  }
  .lg { color: var(--v-brand, #3ddad0); }
  .at { font-weight: 600; letter-spacing: 0.06em; }
  .tag,
  .state {
    border-radius: 999px;
    padding: 0.06rem 0.42rem;
    font-size: 0.55rem;
    letter-spacing: 0.1em;
    background: var(--v-chip, #131c26);
    border: 1px solid var(--v-line-2, rgba(255, 255, 255, 0.13));
    color: var(--v-ink-3, #5d6b7c);
  }
  .state { margin-left: auto; }
  .state.on {
    background: var(--v-brand-tint, rgba(61, 218, 208, 0.13));
    border-color: rgba(61, 218, 208, 0.3);
    color: var(--v-brand, #3ddad0);
  }

  /* ---- who ----------------------------------------------------------- */
  .who {
    font-size: 0.88rem;
    font-weight: 600;
    color: var(--v-ink, rgba(233, 241, 249, 0.92));
    line-height: 1.25;
  }
  .who a { color: inherit; text-decoration: none; border: 0; }
  .who a:hover { color: var(--v-brand, #3ddad0); }
  .player {
    display: block;
    font-size: 0.72rem;
    font-weight: 500;
    color: var(--v-ink-2, #8fa0b3);
  }

  /* ---- the ticket ----------------------------------------------------
     One elevation step lighter than the card that holds it. This nesting
     is what separates "the game" from "the bet" without a rule or a
     heading, and it is the move the whole genre shares.
     -------------------------------------------------------------------- */
  .ticket {
    background: var(--v-lvl-2, #16202c);
    border: 1px solid var(--v-line, rgba(255, 255, 255, 0.07));
    border-radius: var(--v-radius-sm, 8px);
    padding: 0.5rem 0.6rem 0.42rem;
  }
  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.6rem;
  }
  .call { display: flex; flex-direction: column; min-width: 0; gap: 0.05rem; }
  .market {
    font-size: 0.58rem;
    text-transform: uppercase;
    letter-spacing: 0.11em;
    color: var(--v-ink-3, #5d6b7c);
    font-weight: 700;
  }
  .pick {
    font-family: var(--v-board, "Saira Condensed", sans-serif);
    font-size: 1.08rem;
    font-weight: 700;
    letter-spacing: 0.01em;
    color: var(--v-ink, rgba(233, 241, 249, 0.92));
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .price { display: flex; flex-direction: column; align-items: flex-end; gap: 0.14rem; }
  /* The price pill. Dark and quiet while the row is only watched; inverted
     to a filled brand pill the moment the model is staking it, so the one
     number worth acting on is the only lit object in the list. */
  .pill {
    display: inline-flex;
    align-items: center;
    gap: 0.34rem;
    padding: 0.16rem 0.42rem 0.16rem 0.22rem;
    border-radius: 7px;
    background: var(--v-chip, #131c26);
    border: 1px solid var(--v-line-2, rgba(255, 255, 255, 0.13));
    box-shadow: var(--v-lift);
  }
  .pill.take {
    background: var(--v-brand, #3ddad0);
    border-color: var(--v-brand, #3ddad0);
    box-shadow: var(--v-glow, 0 0 18px rgba(61, 218, 208, 0.22));
  }
  .odds {
    font-family: var(--v-board, "Saira Condensed", sans-serif);
    font-size: 1.05rem;
    font-weight: 700;
    letter-spacing: 0.02em;
    font-variant-numeric: tabular-nums;
    color: var(--v-ink, rgba(233, 241, 249, 0.92));
    white-space: nowrap;
  }
  .pill.take .odds { color: #05100f; }
  /* The venue mark. Two letters in the venue's own colour, because the
     genre never spells a book's name out inside a dense row — and a tile
     we draw ourselves needs nobody's logo asset. */
  .venue {
    font-family: var(--v-board, "Saira Condensed", sans-serif);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 1.35rem;
    height: 1.2rem;
    padding: 0 0.18rem;
    border-radius: 5px;
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    background: rgba(255, 255, 255, 0.06);
    color: var(--v-ink-2, #8fa0b3);
  }
  .venue.branded {
    background: color-mix(in srgb, var(--mark) 20%, transparent);
    color: var(--mark);
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--mark) 38%, transparent);
  }
  .pill.take .venue {
    background: rgba(5, 16, 15, 0.16);
    color: #05100f;
    box-shadow: none;
  }
  .book {
    font-size: 0.58rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-weight: 700;
    color: var(--v-ink-3, #5d6b7c);
  }

  /* ---- the stat strip -------------------------------------------------
     Four numbers, each over its own micro-label, divided by hairlines.
     Value first and roughly twice the label's size: the numbers are the
     product and the typography says so.
     -------------------------------------------------------------------- */
  .strip {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    margin-top: 0.45rem;
    border-top: 1px solid var(--v-line, rgba(255, 255, 255, 0.07));
    padding-top: 0.38rem;
  }
  .cell {
    display: flex;
    flex-direction: column;
    gap: 0.02rem;
    padding-left: 0.55rem;
    border-left: 1px solid var(--v-line, rgba(255, 255, 255, 0.07));
    min-width: 0;
  }
  .cell:first-child { padding-left: 0; border-left: 0; }
  .v {
    font-family: var(--v-board, "Saira Condensed", sans-serif);
    font-size: 1rem;
    font-weight: 700;
    line-height: 1.1;
    letter-spacing: 0.01em;
    font-variant-numeric: tabular-nums;
    color: var(--v-ink, rgba(233, 241, 249, 0.92));
  }
  .v.edge { color: var(--v-pos, #35d07f); }
  .v.brand { color: var(--v-brand, #3ddad0); }
  .k {
    font-size: 0.53rem;
    text-transform: uppercase;
    letter-spacing: 0.13em;
    color: var(--v-ink-3, #5d6b7c);
    font-weight: 700;
  }

  .why {
    font-size: 0.7rem;
    line-height: 1.45;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    color: var(--v-ink-3, #5d6b7c);
  }

  @media (max-width: 560px) {
    .pick { font-size: 1rem; }
    .cell { padding-left: 0.4rem; }
    .v { font-size: 0.92rem; }
    .k { font-size: 0.5rem; letter-spacing: 0.09em; }
  }
</style>
