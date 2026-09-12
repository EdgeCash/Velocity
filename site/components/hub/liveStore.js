// The live store: the polling loop and the shape the components subscribe to.
//
// It is a separate module from `live.js` for one concrete reason. This file
// imports Svelte; `live.js` must not, because the tests import `live.js`
// directly and run under bare `node --test` with no node_modules. The pure
// parsing and planning functions therefore live next door, and everything
// stateful lives here.

import { writable } from 'svelte/store';

import { attachGameId, fetchJson, fetchScoreboards, indexGames, scoreboardPlan } from './live.js';

const EMPTY = { games: [], byGame: {}, updated: null, ok: false, tried: false };

function createLive() {
  const { subscribe, set, update } = writable(EMPTY);
  let timer = null;
  let controller = null;
  let plan = {};
  let index = {};

  async function load() {
    controller?.abort();
    controller = new AbortController();
    const { signal } = controller;
    try {
      let { games, ok } = await fetchScoreboards(plan, index, signal);
      if (!ok) {
        // The viewer's own network blocks ESPN — fall back to the Worker,
        // which carries a proxy for exactly this case. It answers for today
        // only, which is the right trade: a blocked viewer gets live games
        // rather than nothing.
        try {
          const data = await fetchJson('/api/scores', signal);
          if (Array.isArray(data?.games)) {
            games = data.games.map((g) => ({
              league: String(g.lg ?? '').toLowerCase(),
              event_id: '',
              away: { abbr: g.away, display: '', short: g.away, location: '' },
              home: { abbr: g.home, display: '', short: g.home, location: '' },
              away_score: Number(g.as ?? 0),
              home_score: Number(g.hs ?? 0),
              state: g.state ?? 'pre',
              detail: g.detail ?? '',
              start: g.start ?? '',
              game_id: null,
            }));
            for (const g of games) g.game_id = attachGameId(g, index);
            ok = true;
          }
        } catch {
          /* leave ok false; the hub shows its offline note */
        }
      }
      const byGame = {};
      for (const game of games) if (game.game_id) byGame[game.game_id] = game;
      const order = { in: 0, pre: 1, post: 2 };
      games.sort((a, b) => (order[a.state] ?? 3) - (order[b.state] ?? 3));
      set({ games, byGame, updated: new Date(), ok, tried: true });
    } catch (err) {
      if (err?.name === 'AbortError') return;
      update((s) => ({ ...s, tried: true }));
    }
  }

  return {
    subscribe,
    /** Begin polling for the games we hold. Safe to call again on new data. */
    start(games, teamIndex, everyMs = 45_000) {
      plan = scoreboardPlan(games);
      index = indexGames(games, teamIndex);
      if (!Object.keys(plan).length) return;
      load();
      clearInterval(timer);
      timer = setInterval(load, everyMs);
    },
    stop() {
      clearInterval(timer);
      timer = null;
      controller?.abort();
      controller = null;
    },
    refresh: load,
  };
}

export const live = createLive();
