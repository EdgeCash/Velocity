// The hub's whole navigation state, in the URL.
//
// The point of this surface is that nothing is a page: switching from the
// board to DFS to your open positions never loads a document, and opening a
// game expands it where it sits. That is only half a win if the resulting view
// cannot be linked or reloaded — "no tab switching" should not also mean "no
// back button" — so the three pieces of state live in the hash and the browser
// keeps doing its job.
//
// The vocabulary and the hash codec live in `nav.js`, which has no
// dependencies. This module is only the store, and importing `svelte/store`
// is the whole reason for that split: CI runs the hub's tests against these
// source files with no node_modules, so anything a test reads has to stay on
// the other side of this import. Take the names from `nav.js`, not from here.

import { writable } from 'svelte/store';

import { DEFAULTS, parseHash, toHash } from './nav.js';

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
