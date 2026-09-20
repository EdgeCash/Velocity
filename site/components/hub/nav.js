// What the hub's views are called, how they are grouped, and how one of them
// is written into a URL. No store, no Svelte, no dependencies at all.
//
// The split from `state.js` is not tidiness. CI runs the hub's suite with
// `node --test` against these source files directly — no `npm ci`, no
// node_modules, so it costs seconds — and that only holds while every module
// the suite imports stays free of runtime dependencies. `state.js` imports
// `svelte/store`, so anything the tests need to read has to live on this side
// of the line. (`live.js` and `liveStore.js` are split for the same reason;
// a stray `svelte/store` import took the whole suite out of CI once already.)
//
// The hash, not the query string: Evidence prerenders to static files and the
// Worker serves them, so a query string would be a cache key for a page whose
// content never varies by it. A hash never reaches the server at all.

// `home` leads, and it is the one view that is not a cut of the data: it is
// the landing, a page of tiles you navigate OUT of, the way Ballpark Pal's
// front page works (docs/FOOTBALL_PAL.md). Everything after it is a real
// view. The list stays flat here because the hash names exactly one.
export const VIEWS = [
  'home', 'card', 'games', 'likely', 'players', 'dfs', 'positions', 'record',
  'accuracy', 'ratings', 'weather', 'matchups', 'export',
];

export const DEFAULTS = { view: 'home', league: 'all', game: '' };

/** What each view is called, everywhere it is named. */
export const VIEW_LABEL = {
  card: 'Card', games: 'Games', likely: 'Most likely', players: 'Players',
  dfs: 'DFS', positions: 'Positions', record: 'Record', accuracy: 'Accuracy',
  ratings: 'Ratings', weather: 'Weather', matchups: 'Matchups',
  export: 'Export',
};

/** The question each view answers, for the landing's tiles.
 *
 * A tile with a name and a number is a menu item; the line under it is what
 * makes the landing worth scrolling. One sentence, present tense, about what
 * you get — not about what the view is called again.
 */
export const VIEW_BLURB = {
  card: 'What cleared the gate, at what price, and why.',
  games: 'Every game on the board, with its projection and its live score.',
  likely: 'What the sim is surest of, priced or not.',
  players: 'The prop board by player, beside the DFS row for the same name.',
  dfs: "Tonight's lineups, one per slate, with salary and projected points.",
  positions: 'What is riding right now, and where it stands.',
  record: 'Graded results by league and market, and what the monitor flagged.',
  accuracy: 'Finished games against the distributions they were given.',
  ratings: 'Power ratings for teams and for players.',
  matchups: 'Unit against unit, both sides of each game.',
  weather: 'Wind and cover, for the games the weather actually moves.',
  export: 'Every table in this build, as CSV.',
};

// Ballpark Pal's menu, for football (docs/FOOTBALL_PAL.md): the views stay
// one surface, and the groups say which question each answers. `home` is
// deliberately absent — it is where these tiles LIVE, not one of them.
export const GROUPS = [
  { label: 'Outlook', views: ['games'] },
  { label: 'Odds', views: ['card', 'likely', 'positions'] },
  { label: 'Fantasy', views: ['dfs', 'players'] },
  { label: 'Research', views: ['ratings', 'matchups', 'weather'] },
  { label: 'Model', views: ['record', 'accuracy'] },
  { label: 'Data', views: ['export'] },
].map((g) => ({ ...g, views: g.views.filter((v) => VIEWS.includes(v)) }));

/** `#view=dfs&league=mlb&game=abc` → the state it names, defaults filled in. */
export function parseHash(hash) {
  const raw = String(hash ?? '').replace(/^#/, '');
  const params = new URLSearchParams(raw);
  const view = params.get('view');
  return {
    view: VIEWS.includes(view) ? view : DEFAULTS.view,
    league: params.get('league') || DEFAULTS.league,
    game: params.get('game') || DEFAULTS.game,
  };
}

/** The state → the shortest hash that round-trips it. */
export function toHash(state) {
  const params = new URLSearchParams();
  for (const key of ['view', 'league', 'game']) {
    const value = state?.[key];
    // A default is left out rather than spelled: `#` beats
    // `#view=games&league=all&game=` for a surface people paste to each other.
    if (value && value !== DEFAULTS[key]) params.set(key, value);
  }
  const text = params.toString();
  return text ? `#${text}` : '';
}
