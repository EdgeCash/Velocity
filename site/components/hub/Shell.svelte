<script>
  // The hub. One surface, four views, no navigation.
  //
  // What was wrong with the old site was never the data — it was that ten
  // pages made you hold the join in your head. The projection was on one page,
  // the price on another, the DFS board on a third and what you actually had
  // riding on it on a fourth, and the only thing that could put them back
  // together was you, with four tabs open. This is the same data with the join
  // already done: the unit is a GAME, and a game carries its own projection,
  // its own prices across books and exchanges, its own DFS plays, its own open
  // positions and its own live score.
  //
  // The three things that are not games — the DFS slate, the position blotter
  // and the graded record — are views on the same surface rather than pages,
  // because DFS and the board genuinely do not line up: a Friday in September
  // is an MLB and WNBA DFS slate against an NFL and college BOARD. Nesting DFS
  // inside game cards alone would have buried it on exactly the days it
  // matters.
  import { onMount, onDestroy } from 'svelte';
  import { live } from './liveStore.js';
  import { hubState, VIEWS } from './state.js';
  import {
    buildGames, buildLineups, buildParlays, flaggedMarkets, leagueCounts,
    realRows, splitCards,
  } from './model.js';
  import { teamIndex } from '../format.js';
  import Ticker from './Ticker.svelte';
  import GamesPanel from './GamesPanel.svelte';
  import DfsPanel from './DfsPanel.svelte';
  import PositionsPanel from './PositionsPanel.svelte';
  import RecordPanel from './RecordPanel.svelte';
  import RatingsPanel from './RatingsPanel.svelte';
  import Rail from './Rail.svelte';

  export let games = [];
  export let projections = [];
  export let board = [];
  export let publish = [];
  export let distributions = [];
  export let teams = [];
  export let weather = [];
  export let playerProps = [];
  export let lineMoves = [];
  export let injuries = [];
  export let dfsLineup = [];
  export let dfsShowdown = [];
  export let dfsTiered = [];
  export let ledgerOpen = [];
  export let bankroll = [];
  export let record = [];
  export let units = [];
  export let clv = [];
  export let exposure = [];
  export let modelConfig = [];
  export let health = [];
  export let ratings = [];
  export let cards = [];
  export let parlays = [];
  export let stamp = '';
  /** 'private' carries prices, edges, stakes and the bankroll; 'public' does not. */
  export let tier = 'private';

  $: isPrivate = tier !== 'public';

  $: identity = teamIndex(teams);
  // The monitor's flagged markets, resolved once: the rail counts them, the
  // game cards mark the market they are about, and Record explains them.
  // One source so the three can never disagree.
  $: flagged = flaggedMarkets(health);
  $: parlayRows = buildParlays(parlays);
  // Record cards carry no game_id and belong with the record they picture.
  $: leagueCards = splitCards(cards).byLeague;
  $: openPositions = realRows(ledgerOpen);
  $: allDfs = [...realRows(dfsLineup), ...realRows(dfsShowdown), ...realRows(dfsTiered)];

  $: hub = buildGames({
    games, projections, board, publish,
    positions: openPositions,
    dfs: allDfs,
    weather, lineMoves, injuries, ratings, cards, props: playerProps,
    parlays: parlayRows,
  });

  $: lineups = [
    ...buildLineups(dfsLineup, 'classic'),
    ...buildLineups(dfsShowdown, 'showdown'),
    ...buildLineups(dfsTiered, 'tiered'),
  ];

  // The league filter spans every view, so it is built from everything the
  // surface holds rather than from the games alone — otherwise switching to
  // DFS on an MLB slate would leave the filter with no MLB in it.
  $: boardLeagues = leagueCounts(hub);
  $: dfsLeagues = leagueCounts(lineups.map((l) => ({ league: l.league })));
  $: leagues = (() => {
    const merged = new Map();
    for (const { league, n } of [...boardLeagues, ...dfsLeagues]) {
      merged.set(league, (merged.get(league) ?? 0) + n);
    }
    return [...merged.entries()]
      .map(([league, n]) => ({ league, n }))
      .sort((a, b) => b.n - a.n || a.league.localeCompare(b.league));
  })();

  $: view = $hubState.view;
  $: league = $hubState.league;
  $: openGame = $hubState.game;

  // A league filter that no longer matches anything (yesterday's link, a
  // league that went dark overnight) falls back to showing everything rather
  // than an empty board with no explanation.
  $: activeLeague = league !== 'all' && !leagues.some((l) => l.league === league)
    ? 'all' : league;

  $: visibleGames = activeLeague === 'all'
    ? hub : hub.filter((g) => g.league === activeLeague);

  // The live store keyed by our game_id, merged onto the cards.
  $: scored = visibleGames.map((g) => ({ ...g, score: $live.byGame[g.game_id] ?? null }));

  let detachState;
  onMount(() => {
    detachState = hubState.attach();
  });
  onDestroy(() => {
    detachState?.();
    live.stop();
  });

  // Restart polling whenever the slate changes — a new build swaps the games
  // under us and the plan's dates go with them.
  let started = '';
  $: if (typeof window !== 'undefined' && hub.length) {
    const key = `${hub.length}|${hub[0]?.game_id ?? ''}|${stamp}`;
    if (key !== started) {
      started = key;
      live.start(hub, identity);
    }
  }

  const VIEW_LABEL = {
    games: 'Games', dfs: 'DFS', positions: 'Positions', record: 'Record',
    ratings: 'Ratings',
  };
  $: viewCount = {
    games: hub.length,
    dfs: lineups.length,
    positions: openPositions.length,
    record: realRows(record).length,
    ratings: realRows(ratings).length,
  };

  function setView(next) { hubState.set({ view: next, game: '' }); }
  function setLeague(next) { hubState.set({ league: next }); }
</script>

<div class="hub">
  <header class="topbar">
    <div class="brand">
      <span class="wordmark">VELOCITY</span>
      <span class="tierpill" class:pub={!isPrivate}>{isPrivate ? 'Private' : 'Public'}</span>
    </div>
    <Ticker games={$live.games} ok={$live.ok} tried={$live.tried} />
  </header>

  <nav class="cmd" aria-label="views">
    <div class="views" role="tablist">
      {#each VIEWS as v}
        <button
          role="tab"
          aria-selected={view === v}
          class:on={view === v}
          on:click={() => setView(v)}
        >
          {VIEW_LABEL[v]}
          <span class="count">{viewCount[v] ?? 0}</span>
        </button>
      {/each}
    </div>

    {#if leagues.length > 1}
      <div class="leagues" aria-label="league filter">
        <button class:on={activeLeague === 'all'} on:click={() => setLeague('all')}>All</button>
        {#each leagues as l}
          <button class:on={activeLeague === l.league} on:click={() => setLeague(l.league)}>
            {l.league.toUpperCase()}
          </button>
        {/each}
      </div>
    {/if}
  </nav>

  <div class="body">
    <main class="main">
      {#if view === 'games'}
        <GamesPanel
          games={scored} {identity} {distributions} {openGame} {isPrivate} {flagged}
          parlays={parlayRows} league={activeLeague}
        />
      {:else if view === 'dfs'}
        <DfsPanel {lineups} league={activeLeague} />
      {:else if view === 'positions'}
        <PositionsPanel
          positions={openPositions}
          league={activeLeague}
          {identity}
          live={$live}
          {isPrivate}
        />
      {:else if view === 'record'}
        <RecordPanel
          {record} {units} {clv} {health} cards={leagueCards}
          league={activeLeague} {isPrivate}
        />
      {:else}
        <RatingsPanel {ratings} {teams} league={activeLeague} />
      {/if}
    </main>

    <Rail
      {bankroll} {exposure} {units} positions={openPositions}
      games={hub} live={$live} {modelConfig} {health} {isPrivate} {stamp}
    />
  </div>

  <footer class="stub">
    <span>Velocity</span>
    <span>
      Model output for entertainment only · not advice · no order is ever
      placed from here
    </span>
    <span class="built">{stamp}</span>
  </footer>
</div>

<style>
  .hub {
    --gutter: 1.1rem;
    min-width: 0;
    width: 100%;
    padding: 0 var(--gutter);
  }

  /* ---- top bar ------------------------------------------------------
     Sticky, because the live scores are the one thing that should never
     scroll away: the whole argument for a single surface is that you can
     see the game and its numbers at the same time. */
  .topbar {
    position: sticky;
    top: 0;
    z-index: 40;
    display: flex;
    align-items: center;
    gap: 1rem;
    min-width: 0;
    padding: 0.5rem 0 0.45rem;
    background: linear-gradient(var(--v-bg) 78%, rgba(6, 9, 13, 0.88));
    backdrop-filter: saturate(140%) blur(10px);
    border-bottom: 1px solid var(--v-line);
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex: 0 0 auto;
  }
  .wordmark {
    font-family: var(--v-board);
    font-weight: 700;
    letter-spacing: 0.22em;
    font-size: 0.95rem;
    color: var(--v-brand);
  }
  .tierpill {
    font-family: var(--v-board);
    font-size: 0.58rem;
    font-weight: 700;
    letter-spacing: 0.13em;
    text-transform: uppercase;
    padding: 0.12rem 0.4rem;
    border-radius: 999px;
    color: var(--v-ink-3);
    background: var(--v-chip);
    box-shadow: inset 0 0 0 1px var(--v-line);
  }
  .tierpill.pub {
    color: var(--v-brand);
    background: var(--v-brand-deep);
    box-shadow: inset 0 0 0 1px rgba(61, 218, 208, 0.3);
  }

  /* ---- command bar --------------------------------------------------- */
  .cmd {
    position: sticky;
    top: 2.6rem;
    z-index: 30;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 0.6rem;
    padding: 0.6rem 0;
    background: var(--v-bg);
    border-bottom: 1px solid var(--v-line);
  }
  .views,
  .leagues {
    display: inline-flex;
    gap: 2px;
    padding: 2px;
    background: var(--v-lvl-1);
    border: 1px solid var(--v-line);
    border-radius: 999px;
    max-width: 100%;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .views::-webkit-scrollbar,
  .leagues::-webkit-scrollbar { display: none; }
  .views button,
  .leagues button {
    display: inline-flex;
    align-items: baseline;
    gap: 0.35em;
    flex: 0 0 auto;
    border: 0;
    border-radius: 999px;
    padding: 0.3rem 0.8rem;
    background: transparent;
    color: var(--v-ink-2);
    font-family: var(--v-board);
    font-size: 0.84rem;
    font-weight: 600;
    letter-spacing: 0.07em;
    cursor: pointer;
    transition: background 130ms ease, color 130ms ease;
  }
  .views button:hover,
  .leagues button:hover { color: var(--v-ink); background: var(--v-lvl-2); }
  .views button.on,
  .leagues button.on {
    background: var(--v-brand-deep);
    color: var(--v-brand);
    box-shadow: inset 0 0 0 1px rgba(61, 218, 208, 0.3);
  }
  .count {
    font-size: 0.68rem;
    font-weight: 600;
    color: var(--v-ink-3);
    font-variant-numeric: tabular-nums;
  }
  .views button.on .count { color: var(--v-brand-dim); }

  /* ---- body ----------------------------------------------------------
     The rail is a real column on a desktop and stacks under the panel on a
     phone. It is NOT a drawer: what it holds — what you have riding, and
     what the bankroll is doing — is the thing you least want to have to go
     and ask for. */
  .body {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 20rem;
    gap: 1.4rem;
    align-items: start;
    padding-top: 1rem;
  }
  .main { min-width: 0; }

  @media (max-width: 1080px) {
    .body { grid-template-columns: minmax(0, 1fr); }
  }

  /* ---- the stub ------------------------------------------------------ */
  .stub {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem 1rem;
    margin: 2.4rem 0 1.4rem;
    padding-top: 0.7rem;
    border-top: 1px solid var(--v-line);
    font-family: var(--v-board);
    font-size: 0.66rem;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--v-ink-3);
  }
  .stub span:first-child { color: var(--v-brand); letter-spacing: 0.22em; }
  .built { margin-left: auto; }

  @media (max-width: 640px) {
    .hub { --gutter: 0.75rem; }
    .cmd { top: 2.4rem; }
    .stub .built { margin-left: 0; }
  }
</style>
