<script>
  // The slate, across the top — Ballpark Pal's above-the-fold element.
  //
  // Theirs is the first thing on the page and it is the reason their home
  // page reads as a day rather than as a menu: one card per game, both
  // crests, the sim's number under each, the time between them. You see the
  // whole slate before you have chosen anything to look at.
  //
  // The hub had no equivalent. Its first row was a command bar — a list of
  // views, which is a question ("what do you want?") where their strip is an
  // answer ("here is today"). This is that answer, on football's numbers:
  // projected points rather than projected runs, kickoff rather than first
  // pitch.
  //
  // It is deliberately NOT a second game list. The Games view owns the
  // detail; this owns the glance, so a card carries four facts and nothing
  // else, and clicking one hands off to the view that does carry detail.
  import TeamMark from '../TeamMark.svelte';
  import { kickoffLabel, num, teamMark } from '../format.js';

  export let games = [];
  export let identity = {};
  /** `(gameId) => void` — open this game in the Games view. */
  export let onOpen = () => {};
  /** The game currently expanded, so the strip can mark it. */
  export let openId = '';

  // A slate is a day, in kickoff order. `buildGames` sorts for the Games
  // view's own grouping; the strip wants the plain running order and must not
  // inherit whatever that grouping happened to leave behind.
  $: slate = [...(games ?? [])].sort((a, b) => {
    const at = Date.parse(a?.kickoff ?? '') || 0;
    const bt = Date.parse(b?.kickoff ?? '') || 0;
    return at - bt;
  });

  // The live feed's shape, not a guess at it: `{away_score, home_score,
  // state, detail}` (live.js), where `state` is 'pre' | 'in' | 'post'. A
  // 'pre' game carries zeros, so reading the score without checking the state
  // would put 0–0 under every crest on the morning slate.
  const scoreOf = (game, side) => {
    const score = game?.score;
    if (!score || score.state === 'pre') return null;
    const value = Number(score[`${side}_score`]);
    return Number.isFinite(value) ? value : null;
  };

  /** What goes under a crest: the real score once there is one, else the sim's.
   *
   * A finished game showing its projection is the strip lying about the one
   * thing it is for. Once a score exists it wins, and the card says which it
   * is showing by wearing `live` or `final`.
   */
  function figure(game, side) {
    const actual = scoreOf(game, side);
    if (actual !== null) return { value: String(actual), kind: 'score' };
    const projected = game?.proj?.[side === 'home' ? 'mu_home' : 'mu_away'];
    return Number.isFinite(Number(projected))
      ? { value: num(projected, 1), kind: 'proj' }
      : { value: '—', kind: 'none' };
  }

  const stateOf = (game) => {
    const state = game?.score?.state;
    if (state === 'in') return 'live';
    if (state === 'post') return 'final';
    return '';
  };

  /** The middle line: where the game is if it is running, else the kickoff. */
  function when(game) {
    const state = stateOf(game);
    if (state === 'final') return game?.score?.detail || 'Final';
    if (state === 'live') return game?.score?.detail || 'Live';
    return kickoffLabel(game?.kickoff) || '';
  }
</script>

{#if slate.length}
  <!-- A horizontal list, and a real one: the strip scrolls on a phone rather
       than wrapping into a second grid, so the slate stays one gesture wide. -->
  <nav class="slate" aria-label="Today's slate">
    {#each slate as game (game.game_id)}
      {@const away = teamMark(identity, game.away_team, game.league)}
      {@const home = teamMark(identity, game.home_team, game.league)}
      {@const awayFig = figure(game, 'away')}
      {@const homeFig = figure(game, 'home')}
      {@const state = stateOf(game)}
      <button
        type="button"
        class="game {state}"
        class:on={game.game_id === openId}
        aria-pressed={game.game_id === openId}
        on:click={() => onOpen(game.game_id)}
      >
        <span class="side">
          <TeamMark code={away.code} logo={away.logo} color={away.color}
                    label={game.away_team} size={24} />
          <span class="fig {awayFig.kind}">{awayFig.value}</span>
        </span>
        <span class="mid">
          <span class="when">{when(game)}</span>
          <span class="at">@</span>
          <span class="sim">{state === 'final' ? 'Grade' : 'Sim'}</span>
        </span>
        <span class="side">
          <TeamMark code={home.code} logo={home.logo} color={home.color}
                    label={game.home_team} size={24} />
          <span class="fig {homeFig.kind}">{homeFig.value}</span>
        </span>
      </button>
    {/each}
  </nav>
{/if}

<style>
  .slate {
    display: flex;
    gap: 0.5rem;
    padding: 0.7rem 0 0.75rem;
    overflow-x: auto;
    scrollbar-width: none;
    border-bottom: 1px solid var(--v-line);
  }
  .slate::-webkit-scrollbar { display: none; }

  .game {
    display: flex;
    align-items: center;
    gap: 0.6rem;
    flex: 0 0 auto;
    padding: 0.5rem 0.75rem;
    border: 1px solid var(--v-line);
    border-radius: var(--v-radius-sm);
    background: var(--v-lvl-1);
    color: var(--v-ink);
    cursor: pointer;
    transition: border-color 130ms ease, background 130ms ease;
  }
  .game:hover { background: var(--v-lvl-2); border-color: var(--v-line-2); }
  .game.on {
    border-color: var(--v-brand);
    background: var(--v-brand-deep);
  }
  .game:focus-visible {
    outline: none;
    box-shadow: var(--v-glow);
    border-color: var(--v-brand);
  }

  .side {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.2rem;
  }
  /* The number under the crest. Tabular, so two cards side by side line up
     even when one reads 7.5 and the next 24.1. */
  .fig {
    font-family: var(--v-board);
    font-variant-numeric: tabular-nums;
    font-size: 0.95rem;
    font-weight: 700;
    line-height: 1;
    color: var(--v-ink);
  }
  /* A projection is the model talking and a score is the game talking. The
     strip says which without a legend: the projection is quieter. */
  .fig.proj { color: var(--v-ink-2); font-weight: 600; }
  .fig.none { color: var(--v-ink-3); }

  .mid {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.1rem;
    min-width: 3.6rem;
  }
  .when {
    font-family: var(--v-board);
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: var(--v-ink-2);
    white-space: nowrap;
  }
  .at {
    font-size: 0.68rem;
    line-height: 1;
    color: var(--v-ink-3);
  }
  .sim {
    font-family: var(--v-board);
    font-size: 0.55rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--v-brand);
  }

  /* Live is the one thing on this strip allowed to shout, and it still only
     gets a tinted label — the dot-and-paint treatment belongs to the ticker. */
  .game.live .when { color: var(--v-alert); font-weight: 700; }
  .game.final .when { color: var(--v-ink-3); }
  .game.final .sim { color: var(--v-ink-3); }

  @media (max-width: 640px) {
    .game { padding: 0.45rem 0.6rem; gap: 0.45rem; }
    .fig { font-size: 0.88rem; }
    .mid { min-width: 3.2rem; }
  }
</style>
