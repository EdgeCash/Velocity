<script>
  // The bet tracker: what is actually riding, against the live score.
  //
  // This is the view the old site never had. The ledger knew what was open and
  // the ticker knew the score, and nothing put them in the same row — so the
  // one question a bettor asks all evening ("am I winning?") was the one
  // question the surface could not answer.
  //
  // It answers it carefully. A spread or a total can be read off the score
  // directly and is reported as covering or not; a moneyline likewise. What it
  // does NOT do is price a live position — the model prices pre-game games,
  // not in-progress ones, and printing a live win probability it did not
  // compute would be making a number up.
  import { american, marketLabel, num, sideLabel, teamMark } from '../format.js';
  import TeamMark from '../TeamMark.svelte';

  export let positions = [];
  export let league = 'all';
  export let identity = {};
  export let live = { byGame: {} };
  export let isPrivate = true;

  $: visible = league === 'all'
    ? positions
    : positions.filter((p) => String(p.league ?? '') === league);

  /** Where a position stands against the score so far.
   *
   * Returns null when the game has not started or the market is one the score
   * alone cannot settle (a team total's own line is in the data, but a prop is
   * not). `final` marks a settled game, where this is the result rather than a
   * running state.
   */
  function standing(position, score) {
    if (!score || score.state === 'pre') return null;
    const home = Number(score.home_score);
    const away = Number(score.away_score);
    if (!Number.isFinite(home) || !Number.isFinite(away)) return null;
    const market = String(position.market ?? '');
    const side = String(position.side ?? '');
    const point = Number(position.point);
    const final = score.state === 'post';

    if (market === 'moneyline') {
      const margin = home - away;
      if (margin === 0) return { text: final ? 'Push' : 'Level', tone: 'flat', final };
      const winning = side === 'home' ? margin > 0 : margin < 0;
      return { text: final ? (winning ? 'Won' : 'Lost') : (winning ? 'Ahead' : 'Behind'), tone: winning ? 'pos' : 'neg', final };
    }
    if (market === 'spread' && Number.isFinite(point)) {
      // The home side covers when the home margin clears the negated handicap.
      const margin = (home - away) + (side === 'home' ? point : -point);
      if (margin === 0) return { text: final ? 'Push' : 'On the number', tone: 'flat', final };
      const covering = margin > 0;
      return {
        text: final ? (covering ? 'Won' : 'Lost') : (covering ? `Covering by ${num(Math.abs(margin), 1).replace('.0', '')}` : `Short by ${num(Math.abs(margin), 1).replace('.0', '')}`),
        tone: covering ? 'pos' : 'neg',
        final,
      };
    }
    if (market === 'total' && Number.isFinite(point)) {
      const total = home + away;
      const over = side === 'over';
      if (total === point) return { text: final ? 'Push' : 'On the number', tone: 'flat', final };
      const winning = over ? total > point : total < point;
      if (final) return { text: winning ? 'Won' : 'Lost', tone: winning ? 'pos' : 'neg', final };
      // Mid-game, an over that is already through is decided; an under is not
      // until the clock runs out, and saying "winning" of it would be wrong.
      if (over) {
        return total > point
          ? { text: 'Through', tone: 'pos', final: false }
          : { text: `${num(point - total, 1).replace('.0', '')} to go`, tone: 'flat', final: false };
      }
      return total > point
        ? { text: 'Gone', tone: 'neg', final: false }
        : { text: `${num(point - total, 1).replace('.0', '')} of room`, tone: 'flat', final: false };
    }
    if (market.startsWith('team_total') && Number.isFinite(point)) {
      const scored = market.endsWith('home') ? home : away;
      const over = side === 'over';
      if (final) {
        if (scored === point) return { text: 'Push', tone: 'flat', final };
        const won = over ? scored > point : scored < point;
        return { text: won ? 'Won' : 'Lost', tone: won ? 'pos' : 'neg', final };
      }
      if (over) {
        return scored > point
          ? { text: 'Through', tone: 'pos', final: false }
          : { text: `${num(point - scored, 1).replace('.0', '')} to go`, tone: 'flat', final: false };
      }
      return scored > point
        ? { text: 'Gone', tone: 'neg', final: false }
        : { text: `${num(point - scored, 1).replace('.0', '')} of room`, tone: 'flat', final: false };
    }
    return { text: score.detail || 'In play', tone: 'flat', final };
  }

  $: rows = visible.map((p) => {
    const score = live.byGame?.[p.game_id] ?? null;
    return { p, score, stand: standing(p, score) };
  }).sort((a, b) => {
    // Live positions first, then the ones still to start, then settled.
    const rank = (r) => (r.score?.state === 'in' ? 0 : r.score?.state === 'post' ? 2 : 1);
    return rank(a) - rank(b);
  });

  $: staked = visible.reduce((sum, p) => sum + (Number(p.stake) || 0), 0);
</script>

{#if !visible.length}
  <div class="none">
    <h3>No open positions</h3>
    <p>
      The tracker reads the bankroll ledger. Nothing open means nothing is
      currently riding for this filter — the graded history is under Record.
    </p>
  </div>
{:else}
  <div class="head">
    <span>{visible.length} open</span>
    {#if isPrivate && staked > 0}<span>{num(staked, 2)}u at risk</span>{/if}
  </div>

  <div class="rows">
    {#each rows as { p, score, stand } (p.bet_id)}
      {@const home = teamMark(identity, p.home_team, p.league)}
      {@const away = teamMark(identity, p.away_team, p.league)}
      <article class="pos" class:live={score?.state === 'in'}>
        <div class="game">
          <span class="lg">{String(p.league ?? '').toUpperCase()}</span>
          <span class="matchup">
            <TeamMark code={away.code} logo={away.logo} color={away.color}
                      label={p.away_team} size={18} />
            <span class="t">{away.code}</span>
            {#if score && score.state !== 'pre'}<span class="s">{score.away_score}</span>{/if}
            <span class="at">@</span>
            <TeamMark code={home.code} logo={home.logo} color={home.color}
                      label={p.home_team} size={18} />
            <span class="t">{home.code}</span>
            {#if score && score.state !== 'pre'}<span class="s">{score.home_score}</span>{/if}
          </span>
          {#if score?.detail}
            <span class="clock" class:on={score.state === 'in'}>{score.detail}</span>
          {/if}
        </div>

        <div class="bet">
          <span class="market">{marketLabel(p.market)}</span>
          <span class="side">
            {p.player ? p.player : sideLabel(p.side)}
            {#if p.point !== null && p.point !== undefined && Number.isFinite(Number(p.point))}
              <span class="point">{num(p.point, 1).replace('.0', '')}</span>
            {/if}
          </span>
          {#if isPrivate}
            <span class="price">{american(p.price)}</span>
            <span class="stake">{num(p.stake, 2)}u</span>
          {/if}
          <span class="book">{p.book}</span>
        </div>

        <div class="stand">
          {#if stand}
            <span class="verdict {stand.tone}" class:settled={stand.final}>{stand.text}</span>
          {:else}
            <span class="verdict flat">Not started</span>
          {/if}
        </div>
      </article>
    {/each}
  </div>

  <p class="caveat">
    Standing is read straight off the score. A position in a running game is
    not re-priced — the model prices games before they start, and a live win
    probability it never computed would be an invention.
  </p>
{/if}

<style>
  .head {
    display: flex;
    gap: 1rem;
    margin-bottom: 0.6rem;
    font-family: var(--v-board);
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.11em;
    text-transform: uppercase;
    color: var(--v-ink-3);
  }
  .rows { display: grid; gap: 0.4rem; }
  .pos {
    display: grid;
    grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr) auto;
    align-items: center;
    gap: 0.8rem;
    padding: 0.55rem 0.75rem;
    border: 1px solid var(--v-line);
    border-radius: var(--v-radius);
    background: var(--v-lvl-0);
  }
  .pos.live { box-shadow: inset 2px 0 0 var(--v-warn); }

  .game { display: grid; gap: 0.16rem; min-width: 0; }
  .lg {
    font-family: var(--v-board);
    font-size: 0.58rem;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: var(--v-ink-3);
  }
  .matchup { display: flex; align-items: center; gap: 0.3rem; flex-wrap: wrap; }
  .t { font-family: var(--v-board); font-weight: 600; color: var(--v-ink); font-size: 0.85rem; }
  .s {
    font-family: var(--v-board);
    font-weight: 700;
    font-size: 1rem;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
  }
  .at { color: var(--v-ink-3); font-size: 0.7rem; }
  .clock { font-size: 0.66rem; color: var(--v-ink-3); }
  .clock.on { color: var(--v-warn); }

  .bet { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; min-width: 0; }
  .market { font-size: 0.7rem; color: var(--v-ink-3); }
  .side { font-size: 0.84rem; color: var(--v-ink); font-weight: 600; }
  .point, .price, .stake {
    font-family: var(--v-board);
    font-weight: 600;
    color: var(--v-ink);
    font-variant-numeric: tabular-nums;
  }
  .stake { color: var(--v-pos); }
  .book { font-size: 0.66rem; color: var(--v-ink-3); }

  .stand { justify-self: end; }
  .verdict {
    display: inline-block;
    padding: 0.18rem 0.55rem;
    border-radius: 999px;
    font-family: var(--v-board);
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    white-space: nowrap;
    background: var(--v-chip);
    color: var(--v-ink-2);
  }
  .verdict.pos { background: var(--v-pos-tint); color: var(--v-pos); }
  .verdict.neg { background: var(--v-neg-tint); color: var(--v-neg); }
  /* A settled verdict is stated flatly; a running one is a status. The ring
     is the second channel, so "Won" and "Ahead" never rely on hue alone. */
  .verdict.settled { box-shadow: inset 0 0 0 1px currentColor; }

  .caveat {
    margin: 0.9rem 0 0;
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
  .none h3 { margin: 0 0 0.3rem; font-size: 0.95rem; color: var(--v-ink); }
  .none p { margin: 0; font-size: 0.8rem; line-height: 1.6; color: var(--v-ink-2); }

  @media (max-width: 720px) {
    .pos { grid-template-columns: minmax(0, 1fr) auto; }
    .bet { grid-column: 1 / -1; }
  }
</style>
