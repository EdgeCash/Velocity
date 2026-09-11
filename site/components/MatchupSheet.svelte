<script>
  // The head-to-head sheet: two teams, a context band, and every stat the
  // model rates them on with the advantage marked in the middle.
  //
  // This is the shape a bettor actually researches with — the away team's
  // numbers reading leftward, the home team's rightward, and a column down
  // the centre saying who wins each row. A list of markets tells you what
  // to bet; this tells you why, which is what the matchup page was missing.
  import { distinctPair, num, signed, teamIndex, teamMark } from './format.js';
  import TeamMark from './TeamMark.svelte';

  export let away = '';
  export let home = '';
  /* Short codes for the dense parts. Full names head the sheet; a table
     column and an advantage tag need `SEA`, not `Seattle Seahawks`. */
  export let awayCode = '';
  export let homeCode = '';
  /** One row from velocity.projections. */
  export let proj = null;
  /** Rows from velocity.ratings for each side. */
  export let awayRating = null;
  export let homeRating = null;
  /** One row from velocity.weather, optional. */
  export let weather = null;
  export let kickoff = null;
  /** Counts of players ruled out, per side. */
  export let awayOut = 0;
  export let homeOut = 0;
  /* Rows from `velocity.teams` for this game's two sides. The sheet resolves
     them itself because an Evidence markdown page cannot import a plain JS
     helper — the lookup has to live in a component, so it lives here.
     Identity is optional throughout: with no rows the mark degrades to the
     code chip and the rule under each name falls back to the hairline. */
  export let marks = [];

  $: index = teamIndex(marks);
  $: awaySeal = teamMark(index, away);
  $: homeSeal = teamMark(index, home);
  // Clubs share colours — NE and SEA wear the same navy — so the pair is
  // separated before either is drawn, or the sheet shows two identical rules
  // and reads as a bug.
  $: [awayClub, homeClub] = distinctPair(awaySeal.color, homeSeal.color);

  const n = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? null : Number(v));

  // `net = off - def`, so a *lower* defensive number is the better one, and
  // a lower power rank is better. Getting this backwards would mark the
  // wrong side as the advantage on half the sheet.
  function row(label, a, h, { lowerWins = false, dp = 2, sign = false } = {}) {
    const av = n(a), hv = n(h);
    let edge = null;
    if (av !== null && hv !== null && av !== hv) {
      edge = (lowerWins ? av < hv : av > hv) ? 'away' : 'home';
    }
    const show = (v) => v === null ? '—' : (sign ? signed(v, dp) : num(v, dp));
    return { label, away: show(av), home: show(hv), edge };
  }

  $: rows = [
    row('Projected points', proj?.mu_away, proj?.mu_home, { dp: 1 }),
    row('Net rating', awayRating?.net, homeRating?.net, { sign: true }),
    row('Offence', awayRating?.off, homeRating?.off, { sign: true }),
    row('Defence', awayRating?.def, homeRating?.def, { sign: true, lowerWins: true }),
    row('Power rank', awayRating?.rank, homeRating?.rank, { dp: 0, lowerWins: true }),
  ].filter((r) => r.away !== '—' || r.home !== '—');

  $: band = [
    { k: 'Kickoff', v: kickoff
        ? new Date(kickoff).toLocaleString(undefined,
            { weekday: 'short', hour: 'numeric', minute: '2-digit' })
        : '—' },
    { k: 'Weather', v: weather && n(weather.temp_f) !== null
        ? `${Math.round(n(weather.temp_f))}°F · ${Math.round(n(weather.wind_mph) ?? 0)}mph`
        : (weather?.covered ? 'Indoors' : '—') },
    // `fair_spread` is already the home side's line: negative means the
    // home team is laying points. Negating it put the favourite on the
    // wrong side of the sheet.
    { k: 'Fair spread', v: n(proj?.fair_spread) === null
        ? '—' : `${homeCode || home} ${signed(n(proj.fair_spread), 1)}` },
    { k: 'Fair total', v: n(proj?.fair_total) === null ? '—' : num(proj.fair_total, 1) },
    { k: 'Home win', v: n(proj?.p_home_win) === null
        ? '—' : `${(n(proj.p_home_win) * 100).toFixed(1)}%` },
    { k: 'Ruled out', v: `${awayOut} / ${homeOut}` },
  ];
</script>

<section class="sheet">
  <!-- The club's own colour rules the line under its name. It is deliberately
       not the name's colour: a brand primary lifted just far enough to be
       visible is still too dark for 1.1rem of text, and two clubs' colours
       competing as body type is what made the first attempt unreadable. -->
  <header class="teams">
    <div class="side away">
      <TeamMark code={awaySeal.code || awayCode} logo={awaySeal.logo}
                color={awayClub} label={away} size={40} />
      <div class="ident">
        <span class="name" style="--club: {awayClub || 'var(--v-line-2, rgba(255,255,255,0.13))'}">{away}</span>
        {#if homeRating || awayRating}
          <span class="sub">{awayRating?.rank ? `#${num(awayRating.rank, 0)}` : ''}
            {awayRating?.net !== undefined && awayRating?.net !== null
              ? `· net ${signed(awayRating.net, 2)}` : ''}</span>
        {/if}
      </div>
    </div>
    <span class="at">at</span>
    <div class="side home">
      <div class="ident">
        <span class="name" style="--club: {homeClub || 'var(--v-line-2, rgba(255,255,255,0.13))'}">{home}</span>
        {#if homeRating}
          <span class="sub">{homeRating?.rank ? `#${num(homeRating.rank, 0)}` : ''}
            {homeRating?.net !== undefined && homeRating?.net !== null
              ? `· net ${signed(homeRating.net, 2)}` : ''}</span>
        {/if}
      </div>
      <TeamMark code={homeSeal.code || homeCode} logo={homeSeal.logo}
                color={homeClub} label={home} size={40} />
    </div>
  </header>

  <!-- The one saturated band on the page. Everything a bettor checks before
       looking at a price sits on it, label over value, evenly spaced. -->
  <div class="band">
    {#each band as item}
      <div class="cell"><span class="k">{item.k}</span><span class="v">{item.v}</span></div>
    {/each}
  </div>

  <table class="h2h">
    <thead>
      <tr><th class="num">{awayCode || away}</th><th class="stat">Statistic</th>
        <th class="adv">Adv</th><th class="num">{homeCode || home}</th></tr>
    </thead>
    <tbody>
      {#each rows as r}
        <tr>
          <td class="num" class:win={r.edge === 'away'}>{r.away}</td>
          <td class="stat">{r.label}</td>
          <td class="adv">
            {#if r.edge}
              <span class="tag">{r.edge === 'away' ? (awayCode || away) : (homeCode || home)}</span>
            {/if}
          </td>
          <td class="num" class:win={r.edge === 'home'}>{r.home}</td>
        </tr>
      {/each}
    </tbody>
  </table>
</section>

<style>
  .sheet {
    background: var(--v-lvl-0, #0b1017);
    border: 1px solid var(--v-line, rgba(255, 255, 255, 0.07));
    border-radius: var(--v-radius, 12px);
    overflow: hidden;
    margin: 0.6rem 0 1.1rem;
  }
  .teams {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: center;
    gap: 0.8rem;
    padding: 0.95rem 1.1rem;
  }
  .side { display: flex; align-items: center; gap: 0.6rem; min-width: 0; }
  .side.home { justify-content: flex-end; }
  .ident { display: flex; flex-direction: column; gap: 0.16rem; min-width: 0; }
  .side.home .ident { text-align: right; align-items: flex-end; }
  .name {
    font-size: 1.12rem;
    font-weight: 700;
    letter-spacing: -0.01em;
    color: #f2f7fb;
    line-height: 1.15;
    border-bottom: 2px solid var(--club, rgba(255, 255, 255, 0.13));
    padding-bottom: 0.14rem;
  }
  .sub {
    font-family: var(--v-board, sans-serif);
    font-size: 0.8rem;
    letter-spacing: 0.03em;
    color: var(--v-ink-3, #5d6b7c);
  }
  .at {
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.16em;
    color: var(--v-ink-3, #5d6b7c);
    font-weight: 700;
  }

  /* The context band. One saturated horizontal strip is the broadcast
     lower-third move, and it is what makes a sheet read as a sheet. */
  .band {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
    gap: 0.1rem 0.4rem;
    background: var(--v-brand-deep, #14403d);
    border-top: 1px solid rgba(61, 218, 208, 0.28);
    border-bottom: 1px solid rgba(61, 218, 208, 0.28);
    padding: 0.6rem 1.1rem;
  }
  .band .cell { display: flex; flex-direction: column; gap: 0.1rem; min-width: 0; }
  .band .k {
    font-size: 0.56rem;
    text-transform: uppercase;
    letter-spacing: 0.13em;
    font-weight: 700;
    color: rgba(61, 218, 208, 0.75);
  }
  .band .v {
    font-family: var(--v-board, sans-serif);
    font-size: 0.95rem;
    font-weight: 600;
    letter-spacing: 0.01em;
    color: #eafcfa;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .h2h { width: 100%; border-collapse: collapse; }
  .h2h th {
    font-size: 0.6rem !important;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    font-weight: 700;
    color: var(--v-ink-3, #5d6b7c);
    padding: 0.6rem 1.1rem 0.5rem;
    border-bottom: 1px solid var(--v-line, rgba(255, 255, 255, 0.07));
  }
  .h2h td {
    padding: 0.7rem 1.1rem;
    border-bottom: 1px solid var(--v-line, rgba(255, 255, 255, 0.07));
  }
  .h2h tbody tr:last-child td { border-bottom: 0; }
  .h2h tbody tr:nth-child(even) td { background: rgba(255, 255, 255, 0.016); }
  .num {
    font-family: var(--v-board, sans-serif);
    font-size: 1.02rem;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    color: var(--v-ink-2, #8fa0b3);
    width: 26%;
  }
  th.num:first-child, td.num:first-child { text-align: left; }
  th.num:last-child, td.num:last-child { text-align: right; }
  /* The winning side of each row is the only lit number on it. */
  td.num.win { color: var(--v-brand, #3ddad0); font-weight: 700; }
  .stat {
    font-size: 0.82rem;
    color: var(--v-ink, rgba(233, 241, 249, 0.92));
    text-align: center;
  }
  .adv { text-align: center; width: 15%; }
  .adv .tag {
    font-family: var(--v-board, sans-serif);
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    padding: 0.1rem 0.42rem;
    border-radius: 5px;
    background: var(--v-brand-tint, rgba(61, 218, 208, 0.13));
    border: 1px solid rgba(61, 218, 208, 0.3);
    color: var(--v-brand, #3ddad0);
    white-space: nowrap;
  }

  @media (max-width: 640px) {
    .name { font-size: 0.95rem; }
    .side { gap: 0.42rem; }
    .h2h td, .h2h th { padding-left: 0.6rem; padding-right: 0.6rem; }
    .band { padding-left: 0.6rem; padding-right: 0.6rem; }
    .stat { font-size: 0.74rem; }
  }
</style>
