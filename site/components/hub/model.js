// Shaping the flat query results into the objects the hub renders.
//
// Evidence hands every source back as a flat row list, which is the right
// shape for a table and the wrong one for this surface: the hub's unit is a
// GAME, and a game pulls rows from six tables that agree on `game_id` and on
// nothing else. Doing that join in SQL would mean one wide query per panel and
// a re-query on every filter change; doing it once here, in memory, is both
// cheaper and the only version where a game can carry its own board rows, its
// own DFS players and its own open positions as nested lists.
//
// Everything below is pure. No fetches, no stores, no Svelte — so the joins
// can be tested without a browser (tests/live.test.mjs).

/** Rows grouped by a key, preserving input order within each group. */
export function groupBy(rows, key) {
  const out = new Map();
  for (const row of rows ?? []) {
    const k = typeof key === 'function' ? key(row) : row?.[key];
    if (k === undefined || k === null || k === '') continue;
    const bucket = out.get(k);
    if (bucket) bucket.push(row);
    else out.set(k, [row]);
  }
  return out;
}

/** The sentinel league every empty source carries (see build_site_data.py). */
export const SENTINEL = '__none__';

export function realRows(rows) {
  return (rows ?? []).filter((r) => String(r?.league ?? '') !== SENTINEL);
}

/** A board row's identity as a market: everything but which venue quoted it. */
function marketKey(row) {
  const point = row?.point === null || row?.point === undefined || Number.isNaN(Number(row.point))
    ? ''
    : Number(row.point).toFixed(1);
  return `${row?.market ?? ''}|${row?.side ?? ''}|${point}`;
}

/** American odds → the implied probability, vig included. */
export function impliedProb(price) {
  const n = Number(price);
  if (!Number.isFinite(n) || n === 0) return null;
  return n > 0 ? 100 / (n + 100) : -n / (-n + 100);
}

/** The board's rows for one game, collapsed to one entry per market.
 *
 * The board carries a row per venue per market, and 68 of the 88 rows on a
 * typical slate are exchange contracts — so the flat list is mostly the same
 * six markets quoted over and over. Collapsing to one row per market with the
 * venues nested is what makes it readable, and it is also the only way to say
 * the thing a bettor actually wants: **who is best on this line right now**.
 */
export function collapseMarkets(rows) {
  const markets = [];
  for (const [, quotes] of groupBy(rows, marketKey)) {
    const first = quotes[0];
    const venues = quotes.map((q) => ({
      venue: String(q.venue_key ?? q.venue ?? q.book ?? ''),
      label: String(q.venue_label ?? q.book ?? ''),
      price: Number(q.price),
      point: q.point === null || q.point === undefined ? null : Number(q.point),
      edge: q.edge === null || q.edge === undefined ? null : Number(q.edge),
      stake: Number(q.stake_sized ?? 0),
      exchange: String(q.venue ?? '') !== 'sportsbook' && String(q.venue ?? '') !== '',
    }));
    // Best price first. A plain signed descending sort is correct for American
    // odds and not a shortcut: the scale has no values between −100 and +100,
    // so signed order and payout order are the same order (+110 pays 1.10,
    // +105 1.05, −110 0.909, −200 0.50). A price we could not parse sorts last
    // rather than winning the comparison as NaN.
    venues.sort((a, b) => {
      const x = Number.isFinite(a.price) ? a.price : -Infinity;
      const y = Number.isFinite(b.price) ? b.price : -Infinity;
      return y - x;
    });
    const best = venues[0] ?? null;
    markets.push({
      key: marketKey(first),
      market: String(first.market ?? ''),
      side: String(first.side ?? ''),
      point: first.point === null || first.point === undefined ? null : Number(first.point),
      p_model: Number(first.p_model),
      p_fair: first.p_fair === null || first.p_fair === undefined ? null : Number(first.p_fair),
      tier: first.tier ? String(first.tier) : '',
      conviction: first.conviction === null || first.conviction === undefined
        ? null : Number(first.conviction),
      rationale: first.rationale ? String(first.rationale) : '',
      note: first.note ? String(first.note) : '',
      stake: Math.max(...quotes.map((q) => Number(q.stake_sized ?? 0) || 0), 0),
      best,
      venues,
      books: venues.filter((v) => !v.exchange),
      exchanges: venues.filter((v) => v.exchange),
    });
  }
  // Tier first (A before B before C), then the model's own conviction. A board
  // sorted by edge alone leads with whatever is noisiest.
  const rank = { A: 0, B: 1, C: 2 };
  markets.sort((a, b) => {
    const t = (rank[a.tier] ?? 9) - (rank[b.tier] ?? 9);
    if (t) return t;
    return (b.conviction ?? 0) - (a.conviction ?? 0);
  });
  return markets;
}

/** Everything the hub knows about one game, keyed by game_id.
 *
 * `games` is the spine rather than `board` or `projections`, because a game
 * can be on the schedule with no priced market (a league we rate but do not
 * bet, a board that arrived after the odds pull) and it still belongs on the
 * surface — silently dropping it is how a missing league goes unnoticed.
 */
export function buildGames({
  games = [],
  projections = [],
  board = [],
  publish = [],
  positions = [],
  dfs = [],
  weather = [],
  props = [],
  lineMoves = [],
  injuries = [],
} = {}) {
  const projByGame = new Map(realRows(projections).map((p) => [p.game_id, p]));
  const boardByGame = groupBy(realRows(board), 'game_id');
  const publishByGame = groupBy(realRows(publish), 'game_id');
  const positionsByGame = groupBy(positions ?? [], 'game_id');
  const weatherByGame = new Map(realRows(weather).map((w) => [w.game_id, w]));
  const propsByGame = groupBy(realRows(props), 'game_id');
  const movesByGame = groupBy(realRows(lineMoves), 'game_id');
  // DFS rows and injury rows name a TEAM, not a game, so they attach by team
  // within league. Keyed on the upper-cased team string both sides carry.
  const byTeam = (rows) => groupBy(
    realRows(rows),
    (r) => `${r.league}|${String(r.team ?? '').toUpperCase()}`,
  );
  const dfsByTeam = byTeam(dfs);
  const injuredByTeam = byTeam(injuries);

  const out = [];
  for (const game of realRows(games)) {
    const id = game.game_id;
    const league = String(game.league ?? '').toLowerCase();
    const proj = projByGame.get(id) ?? null;
    const rows = boardByGame.get(id) ?? [];
    const markets = collapseMarkets(rows);
    const published = (publishByGame.get(id) ?? []).filter((p) => p.published);
    const open = positionsByGame.get(id) ?? [];

    // A DFS player — or an injured one — counts for this game when their team
    // is one of the two. Which side they are on is kept, because "who is out"
    // is only useful when you can see it is not evenly split.
    const dfsPlayers = [];
    const injured = [];
    for (const [side, team] of [['away', game.away_team], ['home', game.home_team]]) {
      const code = String(team ?? '').toUpperCase();
      for (const row of dfsByTeam.get(`${league}|${code}`) ?? []) dfsPlayers.push(row);
      for (const row of injuredByTeam.get(`${league}|${code}`) ?? []) {
        injured.push({ ...row, side });
      }
    }

    out.push({
      game_id: id,
      league,
      home_team: String(game.home_team ?? ''),
      away_team: String(game.away_team ?? ''),
      kickoff: game.kickoff ? new Date(game.kickoff) : null,
      proj,
      markets,
      n_markets: markets.length,
      published,
      n_published: published.length,
      staked: markets.reduce((sum, m) => sum + (m.stake || 0), 0),
      positions: open,
      dfs: dfsPlayers,
      // A prop is a bet on this game, so it belongs in this game's sheet
      // rather than on a board of its own — that separation is exactly what
      // the rebuild exists to undo.
      props: propsByGame.get(id) ?? [],
      // Only markets that actually moved; an unmoved line is not news.
      moves: (movesByGame.get(id) ?? []).filter(
        (m) => Number(m.point_open) !== Number(m.point_now)
          || Number(m.price_open) !== Number(m.price_now),
      ),
      injuries: injured.filter((row) => row.is_out),
      weather: weatherByGame.get(id) ?? null,
      // Filled in by the live store at render time; kept on the object so a
      // card has one place to look.
      score: null,
    });
  }

  // Soonest first, and a game with no kickoff sorts last rather than crashing
  // the comparator.
  out.sort((a, b) => {
    const at = a.kickoff ? a.kickoff.getTime() : Infinity;
    const bt = b.kickoff ? b.kickoff.getTime() : Infinity;
    return at - bt;
  });
  return out;
}

/** The leagues present, with counts — the filter's own data. */
export function leagueCounts(games) {
  const counts = new Map();
  for (const game of games ?? []) {
    counts.set(game.league, (counts.get(game.league) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([league, n]) => ({ league, n }))
    .sort((a, b) => b.n - a.n || a.league.localeCompare(b.league));
}

/* ------------------------------------------------------------------ *
 * Market health.
 *
 * The monitor (velocity/report/monitor.py) reads the season chain back as a
 * per-market trailing table over 7- and 30-day windows, and flags a market
 * that is losing to its close, losing money, or claiming more than it earns.
 *
 * The long window is the one that decides — the 7-day is an early warning —
 * so the index below is keyed on the 30-day rows, and every consumer that
 * asks "is this market in trouble?" gets the same answer.
 * ------------------------------------------------------------------ */

/** The window the monitor treats as deciding, as opposed to warning. */
export const LONG_WINDOW = 30;

/** `league|market` → the long-window health row, for markets actually flagged.
 *
 * Thin markets are left out on purpose. `thin` means fewer than twenty bets
 * in the window, the monitor suppresses every other flag on them, and the
 * string it writes ("thin (1 bets)") is a statement that it cannot judge —
 * not a warning. Surfacing those beside real flags is how a warning stops
 * meaning anything.
 */
export function flaggedMarkets(rows, window = LONG_WINDOW) {
  const out = new Map();
  for (const row of realRows(rows)) {
    if (Number(row.window_days) !== window) continue;
    if (row.thin) continue;
    const flags = String(row.flags ?? '');
    if (!flags) continue;
    out.set(`${row.league}|${row.market}`, row);
  }
  return out;
}

/** The health table for one league filter, long window first, flagged first. */
export function healthRows(rows, league = 'all', window = LONG_WINDOW) {
  return realRows(rows)
    .filter((r) => Number(r.window_days) === window)
    .filter((r) => league === 'all' || r.league === league)
    .sort((a, b) => {
      // Flagged first, then thin last, then by size — the order the operator
      // reads in: what needs a decision, what is fine, what cannot be judged.
      const rank = (r) => (r.thin ? 2 : String(r.flags ?? '') ? 0 : 1);
      const d = rank(a) - rank(b);
      if (d) return d;
      return Number(b.n_bets ?? 0) - Number(a.n_bets ?? 0);
    });
}

/** A date-ish value → epoch ms, or null if it does not name a moment.
 *
 * `new Date(null)` is the EPOCH, not an invalid date — so a null timestamp
 * passed straight to `new Date(...).getTime()` comes back as a perfectly
 * finite 0 and sorts to January 1970 instead of being rejected. Every place
 * on this surface that turns a banked timestamp into a number goes through
 * here so that trap is sprung once rather than per caller.
 */
export function toTime(value) {
  if (value === null || value === undefined || value === '') return null;
  const t = value instanceof Date ? value.getTime() : new Date(value).getTime();
  return Number.isFinite(t) ? t : null;
}

/** The bankroll curve: one running-total point per settled day.
 *
 * This reads `profit_sized` — the day's own increment — and NOT the units
 * table's `units`/`units_sized`, which are already a per-league running total
 * (`build_site_data.build_units` takes a cumsum before writing them). Summing
 * those across leagues and accumulating again compounds the same profit twice;
 * it is a plausible-looking mistake that produces a plausible-looking curve,
 * which is exactly why it is pulled out here and pinned by a test rather than
 * left inline.
 */
export function dailyCurve(rows) {
  const byDate = new Map();
  for (const row of realRows(rows)) {
    const at = toTime(row?.slate_date);
    if (at === null) continue;
    const value = Number(row.profit_sized ?? row.profit ?? 0) || 0;
    byDate.set(at, (byDate.get(at) ?? 0) + value);
  }
  let running = 0;
  return [...byDate.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([t, v]) => {
      running += v;
      return { t, v: running };
    });
}

/** A DFS entry's rows grouped into the lineups they actually form.
 *
 * The DFS tables are flat player rows that only become a lineup once grouped
 * by the slate they were built for. `suffix`/`slate` name the DraftKings
 * contest; `format` separates showdown variants. A row set with neither still
 * yields one lineup rather than none.
 */
export function buildLineups(rows, kind) {
  const key = (r) => [
    r.league ?? '',
    r.slate ?? r.suffix ?? '',
    r.format ?? '',
    r.game_type ?? '',
    r.unit ?? '',
  ].join('|');
  const out = [];
  for (const [, players] of groupBy(realRows(rows), key)) {
    const first = players[0];
    const salary = players.reduce((s, p) => s + (Number(p.salary) || 0), 0);
    const points = players.reduce((s, p) => s + (Number(p.points) || 0), 0);
    out.push({
      kind,
      league: String(first.league ?? ''),
      slate: String(first.slate ?? first.suffix ?? ''),
      format: String(first.format ?? ''),
      unit: String(first.unit ?? ''),
      game_time: String(first.game_time ?? ''),
      start: first.slate_start ? new Date(first.slate_start) : null,
      salary,
      points,
      players,
    });
  }
  out.sort((a, b) => b.points - a.points);
  return out;
}
