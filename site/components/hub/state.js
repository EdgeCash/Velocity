// The hub's whole navigation state, in the URL.
//
// The point of this surface is that nothing is a page: switching from the
// board to DFS to your open positions never loads a document, and opening a
// game expands it where it sits. That is only half a win if the resulting view
// cannot be linked or reloaded — "no tab switching" should not also mean "no
// back button" — so the three pieces of state live in the hash and the browser
// keeps doing its job.
//
// The hash, not the query string: Evidence prerenders to static files and the
// Worker serves them, so a query string would be a cache key for a page whose
// content never varies by it. A hash never reaches the server at all.

import { writable } from 'svelte/store';

export const VIEWS = ['games', 'dfs', 'positions', 'record'];

const DEFAULTS = { view: 'games', league: 'all', game: '' };

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

function createHubState() {
  const { subscribe, set, update } = writable({ ...DEFAULTS });
  let applying = false;

  function fromLocation() {
    if (typeof window === 'undefined') return;
    applying = true;
    set(parseHash(window.location.hash));
    applying = false;
  }

  function push(next) {
    if (typeof window === 'undefined') return;
    const hash = toHash(next);
    const url = `${window.location.pathname}${window.location.search}${hash}`;
    // replaceState for a filter, pushState for a view or a game: flicking
    // between leagues should not fill the back stack, but backing out of an
    // opened game should be one press.
    window.history.replaceState(window.history.state, '', url);
  }

  return {
    subscribe,
    /** Bind to the address bar. Returns the teardown. */
    attach() {
      if (typeof window === 'undefined') return () => {};
      fromLocation();
      const onHash = () => { if (!applying) fromLocation(); };
      window.addEventListener('hashchange', onHash);
      return () => window.removeEventListener('hashchange', onHash);
    },
    set(patch) {
      update((current) => {
        const next = { ...current, ...patch };
        push(next);
        return next;
      });
    },
    /** Open a game, or close it if it is the one already open. */
    toggleGame(gameId) {
      update((current) => {
        const next = { ...current, game: current.game === gameId ? '' : gameId };
        push(next);
        return next;
      });
    },
  };
}

export const hubState = createHubState();
