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
    accuracySummary, buildCard, buildGames, buildLineups, buildParlays,
    flaggedMarkets, leagueCounts, mostLikely, playerBook, playerPool, realRows,
    exportTables, matchupBoard, splitCards, weatherBoard, weatherSummary,
  } from './model.js';
  import { stampLabel, stampTime, teamIndex } from '../format.js';
  import Ticker from './Ticker.svelte';
  import GamesPanel from './GamesPanel.svelte';
  import DfsPanel from './DfsPanel.svelte';
  import PositionsPanel from './PositionsPanel.svelte';
  import CardPanel from './CardPanel.svelte';
  import RecordPanel from './RecordPanel.svelte';
  import RatingsPanel from './RatingsPanel.svelte';
  import LikelyPanel from './LikelyPanel.svelte';
  import PlayersPanel from './PlayersPanel.svelte';
  import AccuracyPanel from './AccuracyPanel.svelte';
  import WeatherPanel from './WeatherPanel.svelte';
  import MatchupsPanel from './MatchupsPanel.svelte';
  import ExportPanel from './ExportPanel.svelte';
  import Rail from './Rail.svelte';
  import SlateStrip from './SlateStrip.svelte';
  import Stamp from './Stamp.svelte';

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
  export let dfsPool = [];
  export let unitSplits = [];
  export let playerRatings = [];
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
  export let accuracy = [];
  /** The newest slate capture stamp in the build — when the DATA is from. */
  export let stamp = '';
  /** When build_site_data.py ran — when the PAGE was made. */
  export let builtAt = '';
  /** 'private' carries prices, edges, stakes and the bankroll; 'public' does not. */
  export let tier = 'private';

  $: isPrivate = tier !== 'public';

  $: identity = teamIndex(teams);
  // The monitor's flagged markets, resolved once: the rail counts them, the
  // game cards mark the market they are about, and Record explains them.
  // One source so the three can never disagree.
  $: flagged = flaggedMarkets(health);
  $: parlayRows = buildParlays(parlays);
  // Built from the GAMES rather than from `publish` directly, so the card
  // inherits the venue join `collapseMarkets` already did: publish carries a
  // price but never says which venue quoted it.
  $: cardRows = buildCard(hub);
  // Record cards carry no game_id and belong with the record they picture.
  $: leagueCards = splitCards(cards).byLeague;
  $: openPositions = realRows(ledgerOpen);
  $: allDfs = [...realRows(dfsLineup), ...realRows(dfsShowdown), ...realRows(dfsTiered)];

  $: hub = buildGames({
    games, projections, board, publish,
    positions: openPositions,
    dfs: allDfs,
    pool: dfsPool,
    weather, lineMoves, injuries, ratings, units: unitSplits, cards, props: playerProps,
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

  // The research views (docs/FOOTBALL_PAL.md), each a different cut of the
  // same joined games: what the sim is surest of, the prop board by player,
  // and the season's finals against their pregame distributions.
  $: likelySections = mostLikely(visibleGames);
  $: playerRows = playerBook(visibleGames);
  $: poolRows = playerPool(visibleGames);
  $: accuracyRows = accuracySummary(accuracy, activeLeague);
  $: weatherRows = weatherBoard(visibleGames);
  $: weatherTotals = weatherSummary(weatherRows);
  $: matchupRows = matchupBoard(visibleGames);
  // Every table the page holds, unfiltered: an export is about the data in
  // this build, not about the league chip that happens to be selected.
  $: exports = exportTables({
    games, projections, distributions, board, publish, playerProps, parlays,
    lineMoves, ratings, unitSplits, playerRatings, dfsPool, dfsLineup,
    dfsShowdown, dfsTiered, record, accuracy, units, clv, health, ledgerOpen,
    bankroll, exposure, injuries, weather, teams, modelConfig,
  });

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
    card: 'Card', games: 'Games', likely: 'Most likely', players: 'Players',
    dfs: 'DFS', positions: 'Positions', record: 'Record', accuracy: 'Accuracy',
    ratings: 'Ratings', weather: 'Weather', matchups: 'Matchups',
    export: 'Export',
  };
  // Ballpark Pal's menu, for football (docs/FOOTBALL_PAL.md): the views stay
  // one surface, and the groups say which question each answers.
  const GROUPS = [
    { label: 'Outlook', views: ['games'] },
    { label: 'Odds', views: ['card', 'likely', 'positions'] },
    { label: 'Fantasy', views: ['dfs', 'players'] },
    { label: 'Research', views: ['ratings', 'matchups', 'weather'] },
    { label: 'Model', views: ['record', 'accuracy'] },
    { label: 'Data', views: ['export'] },
  ].map((g) => ({ ...g, views: g.views.filter((v) => VIEWS.includes(v)) }));
  $: viewCount = {
    // The card counts what CLEARED, not what was priced — the number that
    // means something is "2", not "88".
    card: cardRows.plays.length,
    games: hub.length,
    dfs: lineups.length,
    positions: openPositions.length,
    record: realRows(record).length,
    ratings: realRows(ratings).length,
    likely: likelySections.reduce((sum, s) => sum + s.n, 0),
    players: playerRows.length + poolRows.length,
    accuracy: accuracyRows.n,
    weather: weatherTotals.outdoor,
    matchups: matchupRows.length,
    export: exports.filter((t) => t.n > 0).length,
  };

  // The footer's absolute reading of the same thing the topbar chip shows as
  // an age. It used to print the raw `20260912T235148Z`, which is a filename,
  // not a time. Both instants appear only when they actually differ — a
  // normal run builds a minute after the capture and repeating it is noise.
  $: builtLine = (() => {
    const slate = stampLabel(stamp);
    const at = stampTime(stamp);
    const built = stampTime(builtAt);
    const same = at !== null && built !== null && Math.abs(built - at) <= 5 * 60_000;
    if (!slate) return built === null ? '' : `Built ${stampLabel(builtAt)}`;
    if (built === null || same) return `Slate ${slate}`;
    return `Slate ${slate} · built ${stampLabel(builtAt)}`;
  })();

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
    <!-- Sticky, beside the live scores, because those two things answer the
         same question from opposite ends: the ticker says what is moving
         right now, and this says how old everything that ISN'T moving is. -->
    <Stamp {stamp} {builtAt} />
  </header>

  <nav class="cmd" aria-label="views">
    <div class="views" role="tablist">
      {#each GROUPS as g (g.label)}
        <div class="group">
          <span class="glabel">{g.label}</span>
          <div class="tiles">
            {#each g.views as v}
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
        </div>
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

  <!-- The slate, before the views (docs/SITE.md, the park re-skin). Ballpark
       Pal opens on the day rather than on a menu, and the command bar above
       is a menu: it asks what you want to look at before showing you that
       there is anything to look at. Clicking a card lands in Games with that
       game open, which is the one place the detail lives. -->
  <SlateStrip
    games={scored}
    {identity}
    openId={openGame}
    onOpen={(id) => hubState.set({ view: 'games', game: id })}
  />

  <div class="body">
    <main class="main">
      {#if view === 'card'}
        <CardPanel
          card={cardRows} {exposure} league={activeLeague} {identity} {isPrivate}
          {clv}
        />
      {:else if view === 'games'}
        <GamesPanel
          games={scored} {identity} {distributions} {openGame} {isPrivate} {flagged}
          parlays={parlayRows} league={activeLeague}
        />
      {:else if view === 'likely'}
        <LikelyPanel sections={likelySections} {isPrivate} />
      {:else if view === 'players'}
        <PlayersPanel rows={playerRows} pool={poolRows} {isPrivate} />
      {:else if view === 'accuracy'}
        <AccuracyPanel summary={accuracyRows} />
      {:else if view === 'weather'}
        <WeatherPanel rows={weatherRows} summary={weatherTotals} />
      {:else if view === 'matchups'}
        <MatchupsPanel rows={matchupRows} />
      {:else if view === 'export'}
        <ExportPanel tables={exports} {stamp} {isPrivate} />
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
        <RatingsPanel {ratings} {teams} league={activeLeague} players={playerRatings} />
      {/if}
    </main>

    <Rail
      {bankroll} {exposure} {units} positions={openPositions}
      games={hub} live={$live} {modelConfig} {health} {isPrivate} {stamp}
      {builtAt}
    />
  </div>

  <footer class="stub">
    <span>Velocity</span>
    <span>
      Model output for entertainment only · not advice · no order is ever
      placed from here
    </span>
    <span class="built">{builtLine}</span>
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
    /* Full-bleed: Ballpark Pal's masthead runs edge to edge, and a dirt band
       inset inside the page gutter reads as a widget rather than as the top
       of the site. The negative margin undoes `.hub`'s gutter and the padding
       puts the content back where it was. */
    margin: 0 calc(-1 * var(--gutter));
    padding: 0.5rem var(--gutter) 0.45rem;
    background: var(--v-band);
    border-bottom: 1px solid var(--v-band-line);

    /* The band is its own ground. Re-pointing the ink tokens here means the
       ticker and the stamp — eight colour rules between them, in two other
       components — come out right without either of them knowing they are
       sitting on dirt. Every value clears 4.5:1 on #6b5442; the gate in
       scripts/check_site_contrast.py holds them there. */
    --v-ink: var(--v-band-ink);
    --v-ink-2: var(--v-band-ink-2);
    --v-ink-3: var(--v-band-ink-3);
    --v-brand: var(--v-band-brand);
    --v-brand-dim: var(--v-band-brand);
    --v-warn: var(--v-band-warn);
    --v-alert: var(--v-band-alert);
    --v-pos: var(--v-band-pos);
    --v-line: var(--v-band-line);
    --v-line-2: rgba(243, 240, 231, 0.3);
    --v-chip: rgba(243, 240, 231, 0.14);
    --v-lvl-1: rgba(243, 240, 231, 0.1);
    --v-lvl-2: rgba(243, 240, 231, 0.16);
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
    font-size: 1.15rem;
    color: var(--v-band-ink);
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
    color: var(--v-band);
    background: var(--v-grass);
    box-shadow: none;
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
  /* Ballpark Pal's landing shape: a section header over a grid of tiles, per
     group. Theirs sits down the page and navigates; this one stays sticky and
     switches in place, because the whole argument for one page is that the
     switcher is always there — a tile grid you have to scroll back up to
     reach would be their look bought with the thing the hub exists for. */
  .views {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    gap: 0.35rem 1.15rem;
    max-width: 100%;
    min-width: 0;
  }
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
  .leagues::-webkit-scrollbar { display: none; }
  .group {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 0.26rem;
    min-width: 0;
  }
  .tiles { display: flex; gap: 0.3rem; }
  /* The section header. Dirt brown, because on their page these are the one
     thing that is neither a surface nor an action. */
  .glabel {
    padding-left: 0.2rem;
    font-family: var(--v-board);
    font-size: 0.62rem;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--v-band);
  }
  .views button {
    display: inline-flex;
    align-items: baseline;
    gap: 0.35em;
    flex: 0 0 auto;
    border: 1px solid var(--v-line);
    border-radius: var(--v-radius-sm);
    padding: 0.42rem 0.8rem;
    background: var(--v-lvl-1);
    color: var(--v-ink-2);
    font-family: var(--v-board);
    font-size: 0.9rem;
    font-weight: 600;
    letter-spacing: 0.05em;
    cursor: pointer;
    transition: background 130ms ease, color 130ms ease, border-color 130ms ease;
  }
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
  .views button:hover {
    color: var(--v-ink);
    background: var(--v-lvl-2);
    border-color: var(--v-line-2);
  }
  .leagues button:hover { color: var(--v-ink); background: var(--v-lvl-2); }
  /* The selected tile wears the park's own grass, with dark ink on it — the
     one place --v-grass is allowed, and the pair is gated at 6.2:1. */
  .views button.on {
    background: var(--v-grass);
    border-color: var(--v-grass);
    color: var(--v-ink);
    font-weight: 700;
  }
  .leagues button.on {
    background: var(--v-brand-deep);
    color: var(--v-brand);
    box-shadow: inset 0 0 0 1px rgba(47, 109, 50, 0.3);
  }
  .count {
    font-size: 0.68rem;
    font-weight: 600;
    color: var(--v-ink-3);
    font-variant-numeric: tabular-nums;
  }
  /* On the grass fill the count is ink at reduced weight, not a second green:
     --v-brand-dim on --v-grass is 1.6:1 and simply disappears. */
  .views button.on .count { color: var(--v-ink); opacity: 0.72; }

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
