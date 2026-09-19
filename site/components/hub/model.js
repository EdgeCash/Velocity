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
// can be tested without a browser (site/tests/hub.test.mjs).

import { isNum, line as handicap, marketLabel, overUnder } from '../format.js';

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
  pool = [],
  weather = [],
  props = [],
  lineMoves = [],
  injuries = [],
  ratings = [],
  cards = [],
  parlays = [],
} = {}) {
  const rated = ratingsIndex(ratings);
  const cardsByGame = splitCards(cards).byGame;
  const inParlays = parlayCounts(parlays);
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
  const poolByTeam = byTeam(pool);
  const injuredByTeam = byTeam(injuries);

  const out = [];
  for (const game of realRows(games)) {
    const id = game.game_id;
    const league = String(game.league ?? '').toLowerCase();
    const proj = projByGame.get(id) ?? null;
    const rows = boardByGame.get(id) ?? [];
    const markets = collapseMarkets(rows);
    // EVERY gate verdict, not only the ones that cleared. The rejects are the
    // half that says whether the thresholds are set where you want them —
    // "conviction 0.72 below 0.72" is the gate working, and it is also the
    // row you would want to know about. Keeping only the published ones is
    // how the card became invisible in the first place.
    const verdicts = publishByGame.get(id) ?? [];
    const published = verdicts.filter((p) => p.published);
    const open = positionsByGame.get(id) ?? [];

    // A DFS player — or an injured one — counts for this game when their team
    // is one of the two. Which side they are on is kept, because "who is out"
    // is only useful when you can see it is not evenly split.
    const dfsPlayers = [];
    const priced = [];
    const injured = [];
    for (const [side, team] of [['away', game.away_team], ['home', game.home_team]]) {
      const code = String(team ?? '').toUpperCase();
      for (const row of dfsByTeam.get(`${league}|${code}`) ?? []) dfsPlayers.push(row);
      for (const row of poolByTeam.get(`${league}|${code}`) ?? []) priced.push({ ...row, side });
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
      verdicts,
      published,
      n_published: published.length,
      staked: markets.reduce((sum, m) => sum + (m.stake || 0), 0),
      positions: open,
      dfs: dfsPlayers,
      // Every priced player on this game, rostered or not. `dfs` above is
      // the roster the optimizer returned; this is what it chose from, which
      // is the half a research surface reads.
      pool: priced,
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
      // Through the PROJECTION's team strings, never the game's — see
      // `ratingsIndex`. A game with no projection has no rating join, which
      // is correct: there is nothing to rate it against.
      ratings: proj
        ? {
          away: rated.get(`${league}|${String(proj.away ?? '')}`) ?? null,
          home: rated.get(`${league}|${String(proj.home ?? '')}`) ?? null,
        }
        : { away: null, home: null },
      // The rendered graphic of this exact matchup — a thing about the game,
      // so it lives with the game rather than in a room of its own.
      cards: cardsByGame.get(id) ?? [],
      // The reverse of the parlay block's own join: a game says how many
      // parlays have a leg on it, so the relationship is visible from both
      // ends rather than only from the parlay's.
      n_parlays: inParlays.get(id) ?? 0,
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
 * The card — the publish gate's own verdicts.
 *
 * This is the primary output of the whole system and the rebuild lost it:
 * `publish` reached the browser and nothing rendered it, so the two plays
 * that cleared the gate on a 101-game slate were findable only by opening
 * game cards one at a time.
 *
 * The plays are scattered across games BY DEFINITION, and gathering them is
 * the product — which is the one argument that earns a view rather than a
 * block inside a game sheet.
 * ------------------------------------------------------------------ */

/* Why a row was held, as a RULE rather than as a sentence.
 *
 * The gate writes its reason with the numbers in it — "edge 0.021 below
 * floor 0.030" — which is exactly what you want to read on the row and
 * exactly what you cannot group by: every near-miss is its own string, so a
 * raw grouping produces forty buckets of one. Each row keeps its own
 * sentence; this is only the heading it files under. */
const HELD_RULES = [
  [/^paper/i, 'Paper — priced, not staked', 3],
  [/tier\s+(\S+)\s+below publishable/i, 'Tier below publishable', 2],
  [/edge\s.*below floor/i, 'Edge below floor', 0],
  [/conviction\s.*below/i, 'Conviction below threshold', 0],
  [/market moved/i, 'Market moved against us', 0],
  [/ceiling|capped/i, 'Edge ceiling', 1],
  [/stale|age/i, 'Price too old', 1],
];

/** `{ rule, rank }` for a gate reason — `rank` orders the groups on screen. */
export function heldRule(reason) {
  const text = String(reason ?? '').trim();
  if (!text) return { rule: 'Held back', rank: 4 };
  for (const [pattern, rule, rank] of HELD_RULES) {
    if (pattern.test(text)) return { rule, rank };
  }
  return { rule: text, rank: 4 };
}

/** Is a HIGHER number better for this side?
 *
 * Spreads are quoted signed FROM the side you are on, so a higher point is
 * always the better number (+3.5 beats +2.5; −2.5 beats −3.5). Totals invert
 * by side: the over wants a lower number, the under a higher one. A market
 * with no point (moneyline, anytime-TD) is judged on price, where higher is
 * better on the American scale for the same reason `collapseMarkets` sorts
 * that way — there are no values between −100 and +100, so signed order and
 * payout order are the same order.
 */
function higherIsBetter(market, side) {
  const kind = String(market ?? '');
  const isTotalish = kind === 'total' || kind.startsWith('team_total');
  if (isTotalish || kind.startsWith('player_') || kind.startsWith('pitcher_')
      || kind.startsWith('batter_')) {
    return String(side ?? '') !== 'over';
  }
  return true;
}

/** How this market moved since it opened, FROM THE BETTOR'S SIDE.
 *
 * The card is read before a bet exists, so the useful question is not the CLV
 * one ("did the close move past me") but "is the number on offer now better or
 * worse than the one that was there at open". A side steamed all week is still
 * a play; it is a play you are buying late, and that is worth seeing on the
 * row rather than inferred from a screenshot.
 *
 * Returns null when the market never moved — an unmoved line is not news.
 */
export function marketMove(moves, market, side) {
  const row = (moves ?? []).find(
    (m) => String(m.market ?? '') === String(market ?? '')
      && String(m.side ?? '') === String(side ?? ''),
  );
  if (!row) return null;

  const hasPoint = row.point_open !== null && row.point_open !== undefined
    && row.point_now !== null && row.point_now !== undefined
    && Number.isFinite(Number(row.point_open)) && Number.isFinite(Number(row.point_now));
  const from = hasPoint ? Number(row.point_open) : Number(row.price_open);
  const to = hasPoint ? Number(row.point_now) : Number(row.price_now);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return null;

  const delta = to - from;
  if (Math.abs(delta) < 1e-9) return null;
  const better = higherIsBetter(market, side) ? delta > 0 : delta < 0;

  return {
    kind: hasPoint ? 'point' : 'price',
    from,
    to,
    delta,
    // 'better'/'worse' for the reader taking this side NOW — never rendered as
    // colour alone: pos↔warn is ΔE 6.2 under protanopia, so every chip that
    // uses this carries a glyph and a word too.
    direction: better ? 'better' : 'worse',
    glyph: better ? '▲' : '▼',
    // Said plainly, because "line moved" without a subject is the kind of
    // phrasing that reads as a recommendation when it is a fact.
    label: better
      ? 'better number than open'
      : 'worse number than open',
  };
}

/** What the record says about beating the close in THIS market.
 *
 * Spreads, totals and moneylines close efficiently enough that beating the
 * close is skill; props and team totals do not, and their rows carry
 * `clv_trusted = false` (docs/WAGERING.md §6). A card that showed one CLV
 * number for every market would quietly average a meaningful signal with a
 * meaningless one, so this hands back the flag and lets the surface say
 * "judge on P/L" instead of printing a number that does not mean what it
 * looks like.
 */
export function clvTrust(clvRows, league, market) {
  const want = String(market ?? '');
  const lg = String(league ?? '');
  const row = realRows(clvRows).find(
    (r) => String(r.market ?? '') === want && String(r.league ?? '') === lg,
  );
  if (!row) return null;
  const n = Number(row.n_bets);
  return {
    market: want,
    trusted: row.clv_trusted === true || row.clv_trusted === 'true',
    nBets: Number.isFinite(n) ? n : 0,
    meanClv: Number(row.mean_price_clv),
    units: Number(row.units),
  };
}

/** The model's number against the market's, and the gap between them.
 *
 * `p_fair` is the DE-VIGGED market probability, so the gap is the whole
 * argument for the bet in one subtraction — and it is the pair the card has
 * been carrying in `market_row` and throwing away. Null when either side is
 * missing: a bar drawn against an absent market number is a bar that invents
 * its own reference.
 */
export function modelVsMarket(marketRow) {
  const pModel = Number(marketRow?.p_model);
  const pFair = Number(marketRow?.p_fair);
  if (!Number.isFinite(pModel) || !Number.isFinite(pFair)) return null;
  return { pModel, pFair, gap: pModel - pFair };
}

/** The strongest single thing to say about a play, for the one-line why.
 *
 * Ranked by what would change a decision: the model's own sentence if it wrote
 * one, then a line that moved, then a side missing players, then weather. One
 * line only — the rest is behind the expand, and a "summary" that lists
 * everything is the row it was meant to summarise.
 */
export function playHeadline(play) {
  if (play?.market_row?.rationale) return String(play.market_row.rationale);
  if (play?.move) return `Line ${play.move.label}`;
  const out = play?.injuries ?? [];
  if (out.length) {
    const side = out[0].side === 'home' ? play.home_team : play.away_team;
    return out.length === 1
      ? `${out[0].player_name} out for ${side}`
      : `${out.length} out for ${side} and the other side`;
  }
  const wx = play?.weather;
  if (wx && wx.covered === false && Number.isFinite(Number(wx.wind_mph))
      && Number(wx.wind_mph) >= 12) {
    return `Wind ${Math.round(Number(wx.wind_mph))} mph, outdoors`;
  }
  return '';
}

/** The card: what cleared the gate, and what did not and why.
 *
 * Takes the ALREADY-BUILT games so the venue join is the one `collapseMarkets`
 * already did — `publish` carries a price but never says which venue quoted
 * it, and a play you cannot place is half a play.
 */
export function buildCard(games) {
  const plays = [];
  const held = [];
  for (const game of games ?? []) {
    // Every gate verdict for this game, not just the published ones: the
    // rejects are the half that says whether the thresholds are set right.
    for (const row of game.verdicts ?? []) {
      const market = (game.markets ?? []).find(
        (m) => m.market === String(row.market ?? '') && m.side === String(row.side ?? ''),
      ) ?? null;
      // Everything the row needs to be decided ON THE ROW. The game already
      // holds all of it; the card used to take the price and leave the
      // reasoning behind, which is what sent a reader to the game sheet to
      // answer "why" for a play the card had already made.
      const entry = {
        ...row,
        game_id: game.game_id,
        league: game.league,
        home_team: game.home_team,
        away_team: game.away_team,
        kickoff: game.kickoff,
        market_row: market,
        best: market?.best ?? null,
        point: market?.point ?? null,
        // p_model vs the de-vigged p_fair: the argument for the bet, in one
        // subtraction.
        vs: modelVsMarket(market),
        // Every venue that quoted it, best first — a play you cannot shop is
        // a play you overpay for.
        venues: market?.venues ?? [],
        move: marketMove(game.moves, row.market, row.side),
        injuries: game.injuries ?? [],
        weather: game.weather ?? null,
        ratings: game.ratings ?? { away: null, home: null },
      };
      if (row.published) plays.push({ ...entry, headline: playHeadline(entry) });
      else held.push({ ...entry, ...heldRule(row.reason) });
    }
  }
  // The card ranks by the money the model wants on it, then by edge. Ranking
  // by edge alone leads with whatever is noisiest, which is the same mistake
  // the old board made.
  plays.sort((a, b) => {
    const s = (Number(b.stake_sized) || 0) - (Number(a.stake_sized) || 0);
    if (s) return s;
    return (Number(b.edge) || 0) - (Number(a.edge) || 0);
  });
  return { plays, held };
}

/** Held rows grouped by rule, near-misses first. */
export function heldGroups(held) {
  const out = new Map();
  for (const row of held ?? []) {
    const bucket = out.get(row.rule) ?? { rule: row.rule, rank: row.rank, rows: [] };
    bucket.rows.push(row);
    out.set(row.rule, bucket);
  }
  for (const bucket of out.values()) {
    // Within a rule, the strongest opinion first — on a near-miss group that
    // is the row that most nearly made it.
    bucket.rows.sort((a, b) => (Number(b.edge) || 0) - (Number(a.edge) || 0));
  }
  // A near-miss is news; "paper" and "tier B below publishable" are the gate
  // working as designed on rows that were never candidates. Ordering by rank
  // puts the ones worth a decision at the top.
  return [...out.values()].sort((a, b) => a.rank - b.rank || b.rows.length - a.rows.length);
}

/* ------------------------------------------------------------------ *
 * Parlays and cards — the two things on this surface that are not about
 * one game, and are not about no game either.
 * ------------------------------------------------------------------ */

/** Parlays with their legs parsed and the games they touch resolved.
 *
 * `legs_json` carries a `game_id` per leg, which is what lets a parlay be a
 * first-class citizen here rather than a string in a table: the block can
 * jump you to the game a leg is on, and a game can say how many parlays it
 * is part of. Without that join a parlay is just prose.
 *
 * A row whose JSON will not parse keeps its rendered `legs` string and gets
 * no game links — degraded, not dropped, because the price and the edge are
 * still true.
 */
export function buildParlays(rows) {
  const out = [];
  for (const row of realRows(rows)) {
    let legs = [];
    try {
      const parsed = JSON.parse(String(row.legs_json ?? '[]'));
      if (Array.isArray(parsed)) legs = parsed;
    } catch {
      legs = [];
    }
    const gameIds = [...new Set(
      legs.map((l) => String(l?.game_id ?? '')).filter(Boolean),
    )];
    out.push({
      ...row,
      // The source carries BOTH: `legs` is a rendered one-line string and
      // `legs_json` the structured version. The parsed array takes the name,
      // and the string is kept under its own so the degraded path (JSON that
      // will not parse) still has something true to print.
      legs_string: String(row.legs ?? ''),
      legs,
      gameIds,
      // `same_game` is the producer's own flag and means "two or more legs
      // share a game", NOT "every leg is the same game" — a three-leg parlay
      // with two MIA@LV legs and one WAS@PHI leg is flagged true. Reading it
      // as the latter would put cross-game parlays inside one game's sheet.
      correlated: Boolean(row.same_game),
    });
  }
  out.sort((a, b) => Number(b.ev ?? 0) - Number(a.ev ?? 0));
  return out;
}

/** `game_id` → how many parlays have a leg on it. */
export function parlayCounts(parlays) {
  const counts = new Map();
  for (const parlay of parlays ?? []) {
    for (const id of parlay.gameIds ?? []) {
      counts.set(id, (counts.get(id) ?? 0) + 1);
    }
  }
  return counts;
}

/** The rendered graphics, split by whether they belong to a game or a league.
 *
 * Most are per-matchup sheets. The record cards are one per league and carry
 * no `game_id` at all, so they have no game to sit in and belong with the
 * record they are a picture of.
 */
export function splitCards(rows) {
  const byGame = new Map();
  const byLeague = [];
  for (const row of realRows(rows)) {
    const id = String(row?.game_id ?? '').trim();
    if (!id) {
      byLeague.push(row);
      continue;
    }
    const bucket = byGame.get(id) ?? [];
    bucket.push(row);
    byGame.set(id, bucket);
  }
  return { byGame, byLeague };
}

/* ------------------------------------------------------------------ *
 * Power ratings.
 *
 * The join key is the trap. Ratings are keyed by the string each league's fit
 * uses — `SEA` for the NFL, `Alabama` for college, `Minnesota Lynx` for the
 * WNBA — and the GAMES table carries none of those: it has full club names
 * ("Seattle Seahawks", "Alabama Crimson Tide"). The PROJECTIONS table is the
 * one that carries the fit's own spelling, and it matches ratings on every
 * team in all three live leagues. Joining ratings to games instead matches
 * almost nothing and renders a head-to-head with one side blank — which is
 * exactly what it did the first time anyone tried.
 * ------------------------------------------------------------------ */

/** `league|team` → the rating row, keyed by the fit's own team spelling. */
export function ratingsIndex(rows) {
  const out = new Map();
  for (const row of realRows(rows)) {
    const team = String(row?.team ?? '');
    if (!team) continue;
    out.set(`${String(row.league).toLowerCase()}|${team}`, row);
  }
  return out;
}

/** `league|<fit spelling>` → the club name a reader would recognise.
 *
 * The fits name teams the way their own data does, and for the NFL that is a
 * three-letter code: the ratings table says `PIT` where every other surface
 * says "Pittsburgh Steelers". That is fine to read in a row of numbers and
 * useless to SEARCH — typing "steel" matched nothing. The identity table
 * already maps full name to code, so reversing it gives the ratings table a
 * name to show and a second string to match on.
 *
 * College is the one it cannot fix: the fit says "Alabama" and the identity
 * table says "Alabama Crimson Tide", which share no key. Those fall back to
 * the fit's own spelling — still searchable as "Alabama", just not as
 * "Crimson Tide" — rather than being guessed at with a prefix match that
 * would confidently map "Miami" to the wrong school.
 */
export function ratingAliases(teams) {
  const out = new Map();
  for (const row of realRows(teams)) {
    const league = String(row?.league ?? '').toLowerCase();
    const name = String(row?.team ?? '');
    const code = String(row?.code ?? '');
    if (!league || !name) continue;
    if (code) out.set(`${league}|${code}`, name);
    out.set(`${league}|${name}`, name);
  }
  return out;
}

/* Which way is better, per statistic.
 *
 * `net = off − def`, so a LOWER defensive number is the better one, and a
 * LOWER power rank is better. A naive "higher wins" marks the wrong side on
 * two of the five rows. Pace is neither — it is context, not an advantage,
 * and marking a side on it would invent a claim the model does not make. */
export const RATING_STATS = [
  { key: 'rank', label: 'Rank', lowerWins: true, dp: 0 },
  { key: 'net', label: 'Net', lowerWins: false, dp: 2 },
  { key: 'off', label: 'Off', lowerWins: false, dp: 2 },
  { key: 'def', label: 'Def', lowerWins: true, dp: 2 },
  { key: 'pace', label: 'Pace', lowerWins: null, dp: 1 },
];

/** The head-to-head rows for one game, with the advantage resolved per stat. */
export function ratingRows(away, home) {
  if (!away || !home) return [];
  const rows = [];
  for (const stat of RATING_STATS) {
    // `isNum`, not `Number.isFinite(Number(...))`: the football and baseball
    // fits report no pace at all and the column arrives as NULL, which
    // `Number()` turns into a perfectly finite 0 — printing "Pace 0.0 / 0.0"
    // for two teams that play a normal number of possessions.
    if (!isNum(away[stat.key]) || !isNum(home[stat.key])) continue;
    const a = Number(away[stat.key]);
    const h = Number(home[stat.key]);
    let edge = null; // 'away' | 'home' | null
    if (stat.lowerWins !== null && a !== h) {
      const awayBetter = stat.lowerWins ? a < h : a > h;
      edge = awayBetter ? 'away' : 'home';
    }
    rows.push({ ...stat, away: a, home: h, edge });
  }
  return rows;
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

/* ---- Most Likely ---------------------------------------------------------
   The outcomes the simulation is surest of, ranked by probability, with the
   market's best price beside each so a reader sees at once whether the book
   agrees — Ballpark Pal's page of the same name, for football
   (docs/FOOTBALL_PAL.md). Nothing here is a pick: an 80% favourite at −400
   is 80% likely and no bet at all, and the row says both. The card is the
   other question (what cleared the gate); this is what the sim expects. */

export const LIKELY_FAMILIES = [
  ['winners', 'Winners'],
  ['spreads', 'Spreads'],
  ['totals', 'Totals'],
  ['players', 'Players'],
];

function likelyFamily(market) {
  const m = String(market ?? '');
  if (m === 'moneyline') return 'winners';
  if (m.startsWith('spread')) return 'spreads';
  if (m === 'total' || m.startsWith('team_total')) return 'totals';
  return null;
}

function teamFor(game, side) {
  if (side === 'home') return String(game.home_team ?? '');
  if (side === 'away') return String(game.away_team ?? '');
  return '';
}

/** The outcome in words: "Chiefs win", "Chiefs −3.5", "Over 45.5", "Bills team total Under 20.5". */
export function outcomeLabel(game, market) {
  const m = String(market.market ?? '');
  const side = String(market.side ?? '').toLowerCase();
  const point = market.point;
  if (m === 'moneyline') return `${teamFor(game, side)} win`;
  if (m.startsWith('spread')) {
    const hcp = handicap(point, 'spread');
    return `${teamFor(game, side)}${hcp ? ` ${hcp}` : ''}`;
  }
  const ou = overUnder(side, point, 'long');
  if (m === 'total') return ou ?? `${side} ${point ?? ''}`.trim();
  if (m.startsWith('team_total')) {
    const team = teamFor(game, m.endsWith('home') ? 'home' : 'away');
    return `${team} team total ${ou ?? ''}`.trim();
  }
  return `${side} ${point ?? ''}`.trim();
}

/** One row per player line and side, at its best price across books. */
export function collapseProps(rows) {
  const out = new Map();
  for (const r of realRows(rows)) {
    const side = String(r.side ?? '').toLowerCase();
    const key = `${r.player}|${r.market}|${side}|${r.point}`;
    const price = Number(r.price);
    const cur = out.get(key);
    const better = Number.isFinite(price) && (!cur || !Number.isFinite(cur.price) || price > cur.price);
    if (!cur || better) {
      out.set(key, {
        player: String(r.player ?? ''),
        market: String(r.market ?? ''),
        side,
        point: r.point === null || r.point === undefined ? null : Number(r.point),
        p_model: Number(r.p_model),
        p_fair: r.p_fair === null || r.p_fair === undefined ? null : Number(r.p_fair),
        price: Number.isFinite(price) ? price : null,
        venue: String(r.book ?? ''),
        n_books: (cur?.n_books ?? 0) + 1,
      });
    } else {
      cur.n_books += 1;
    }
  }
  return [...out.values()];
}

/** The most likely outcomes by family, each family's rows sorted surest first. */
export function mostLikely(games, { limit = 12 } = {}) {
  const rows = [];
  for (const g of games ?? []) {
    const base = {
      game_id: g.game_id, league: g.league, kickoff: g.kickoff ?? null,
      away_team: String(g.away_team ?? ''), home_team: String(g.home_team ?? ''),
    };
    for (const m of g.markets ?? []) {
      const family = likelyFamily(m.market);
      if (!family || !Number.isFinite(m.p_model)) continue;
      rows.push({
        ...base, family, player: '',
        market: m.market, side: m.side, point: m.point,
        label: outcomeLabel(g, m),
        p_model: m.p_model,
        p_fair: Number.isFinite(m.p_fair) ? m.p_fair : null,
        price: Number.isFinite(m.best?.price) ? m.best.price : null,
        venue: m.best?.venue ?? '',
        tier: m.tier ?? '',
      });
    }
    for (const p of collapseProps(g.props ?? [])) {
      if (!Number.isFinite(p.p_model)) continue;
      const ou = overUnder(p.side, p.point, 'long') ?? '';
      rows.push({
        ...base, family: 'players', player: p.player,
        market: p.market, side: p.side, point: p.point,
        label: `${p.player} ${ou} ${marketLabel(p.market).toLowerCase()}`.replace(/\s+/g, ' ').trim(),
        p_model: p.p_model, p_fair: p.p_fair, price: p.price, venue: p.venue, tier: '',
      });
    }
  }
  rows.sort((a, b) => b.p_model - a.p_model || a.label.localeCompare(b.label));
  return LIKELY_FAMILIES
    .map(([key, label]) => {
      const all = rows.filter((r) => r.family === key);
      return { key, label, rows: all, top: all.slice(0, limit), n: all.length };
    })
    .filter((s) => s.n > 0);
}

/* ---- Players -------------------------------------------------------------
   The prop board turned around: one row per player line with both sides on
   it, so a reader can look a PLAYER up rather than a game. Ballpark Pal's
   "PrizePicks & Underdog" grammar (line, over %, under %, odds), against
   the sportsbooks this repo actually prices. A DFS salary and projection
   ride along when the player is on a built lineup. */

const normName = (value) => String(value ?? '').toLowerCase().replace(/[^a-z0-9]/g, '');

export function playerBook(games) {
  const rows = [];
  for (const g of games ?? []) {
    // The pool first, the roster second: the pool prices every draftable, so
    // a player with a prop line but no roster spot still gets their salary
    // and projection. Before the pool was persisted, this map held the nine
    // rostered players and every other row showed an em-dash.
    const dfsByName = new Map();
    for (const d of g.dfs ?? []) dfsByName.set(normName(d.player_name), d);
    for (const d of g.pool ?? []) dfsByName.set(normName(d.player_name), d);
    const pairs = new Map();
    for (const p of collapseProps(g.props ?? [])) {
      const key = `${p.player}|${p.market}|${p.point}`;
      const row = pairs.get(key) ?? {
        game_id: g.game_id, league: g.league, kickoff: g.kickoff ?? null,
        away_team: String(g.away_team ?? ''), home_team: String(g.home_team ?? ''),
        player: p.player, market: p.market, point: p.point, over: null, under: null,
      };
      if (p.side === 'over') row.over = p;
      else if (p.side === 'under') row.under = p;
      pairs.set(key, row);
    }
    for (const row of pairs.values()) {
      const dfs = dfsByName.get(normName(row.player)) ?? null;
      const fromOver = row.over?.p_model;
      const fromUnder = row.under?.p_model;
      const pOver = Number.isFinite(fromOver) ? fromOver
        : Number.isFinite(fromUnder) ? 1 - fromUnder : null;
      // Same null-is-not-zero trap as playerPool above.
      const salary = isNum(dfs?.salary) ? Number(dfs.salary) : null;
      const points = isNum(dfs?.points) ? Number(dfs.points) : null;
      const value = isNum(dfs?.value) ? Number(dfs.value) : null;
      rows.push({
        ...row,
        p_over: pOver,
        p_under: pOver === null ? null : 1 - pOver,
        lean: pOver === null ? '' : pOver >= 0.5 ? 'over' : 'under',
        sure: pOver === null ? 0 : Math.max(pOver, 1 - pOver),
        team: String(dfs?.team ?? ''),
        position: String(dfs?.position ?? ''),
        salary,
        dfs_points: points,
        dfs_value: value,
      });
    }
  }
  rows.sort((a, b) => a.player.localeCompare(b.player)
    || String(a.market).localeCompare(String(b.market))
    || (a.point ?? 0) - (b.point ?? 0));
  return rows;
}

/* ---- Accuracy ------------------------------------------------------------
   The season's Sim Checks, summarised: is the sim biased, how wide is it
   wrong, and is its WIDTH right. The last one is the honest check a
   simulation owes its reader: if the pregame distributions are calibrated,
   the actual totals land uniformly across their deciles and half of them
   inside the middle 50%. Bias and error say how good the centre is; the
   deciles say whether the spread of the belief is real. */

export function accuracySummary(rows, league = 'all') {
  const games = realRows(rows)
    .filter((r) => league === 'all' || String(r.league) === league)
    .map((r) => ({
      ...r,
      home_score: Number(r.home_score), away_score: Number(r.away_score),
      mu_home: Number(r.mu_home), mu_away: Number(r.mu_away),
      fair_total: Number(r.fair_total), p_home_win: Number(r.p_home_win),
      total_percentile: Number(r.total_percentile),
      winner_percentile: Number(r.winner_percentile),
      p_winner_pregame: Number(r.p_winner_pregame),
      date: toTime(r.game_date),
    }))
    .filter((r) => Number.isFinite(r.home_score) && Number.isFinite(r.away_score));
  const n = games.length;
  if (!n) {
    return {
      n: 0, games: [], total_bias: null, total_bias_pct: null, total_mae: null,
      margin_mae: null, fav_won: null, brier: null, deciles: Array(10).fill(0),
      middle_share: null, n_pct: 0,
    };
  }
  const mean = (xs) => (xs.length ? xs.reduce((s, x) => s + x, 0) / xs.length : null);
  const projTotal = (g) => (Number.isFinite(g.fair_total) ? g.fair_total : g.mu_home + g.mu_away);
  const projMargin = (g) => g.mu_home - g.mu_away;
  const withTotal = games.filter((g) => Number.isFinite(projTotal(g)));
  const withMargin = games.filter((g) => Number.isFinite(projMargin(g)));
  // Sim minus actual, so a positive bias means the sim expects more points
  // than the games produced — the same sign Ballpark Pal reports.
  const totalErr = withTotal.map((g) => projTotal(g) - (g.home_score + g.away_score));
  const marginErr = withMargin.map((g) => projMargin(g) - (g.home_score - g.away_score));
  const actualTotal = mean(withTotal.map((g) => g.home_score + g.away_score));
  const withFav = games.filter((g) => Number.isFinite(g.p_winner_pregame));
  const favWon = withFav.filter((g) => g.p_winner_pregame > 0.5).length;
  const withP = games.filter((g) => Number.isFinite(g.p_home_win));
  const brier = mean(withP.map((g) => (g.p_home_win - (g.home_score > g.away_score ? 1 : 0)) ** 2));
  const deciles = Array(10).fill(0);
  let middle = 0;
  let withPct = 0;
  for (const g of games) {
    if (!Number.isFinite(g.total_percentile)) continue;
    withPct += 1;
    deciles[Math.min(9, Math.max(0, Math.floor(g.total_percentile * 10)))] += 1;
    if (g.total_percentile >= 0.25 && g.total_percentile <= 0.75) middle += 1;
  }
  games.sort((a, b) => (b.date ?? 0) - (a.date ?? 0) || String(a.game_id).localeCompare(String(b.game_id)));
  const bias = mean(totalErr);
  return {
    n,
    games,
    total_bias: bias,
    total_bias_pct: bias === null || !actualTotal ? null : bias / actualTotal,
    total_mae: mean(totalErr.map(Math.abs)),
    margin_mae: mean(marginErr.map(Math.abs)),
    fav_won: withFav.length ? favWon / withFav.length : null,
    brier,
    deciles,
    middle_share: withPct ? middle / withPct : null,
    n_pct: withPct,
  };
}

/** Every priced player on the visible games, one row each, dearest value first.
 *
 * The DFS half of Ballpark Pal's player pages: what each draftable costs,
 * what the model projects, and the points per $1,000 that decides whether
 * the price is right. `rostered` marks the ones the optimizer took, so the
 * lineup reads as a subset of the pool rather than a separate list.
 */
export function playerPool(games) {
  const rows = [];
  for (const g of games ?? []) {
    for (const p of realRows(g.pool ?? [])) {
      // `isNum` rather than `Number.isFinite(Number(x))`: Number(null) is 0,
      // not NaN, and the public tier blanks salary and value to null — so the
      // naive test renders a priceless row as "$0" instead of an em-dash.
      const salary = isNum(p.salary) ? Number(p.salary) : null;
      const points = isNum(p.points) ? Number(p.points) : null;
      const value = isNum(p.value) ? Number(p.value) : null;
      rows.push({
        game_id: g.game_id,
        league: g.league,
        kickoff: g.kickoff ?? null,
        away_team: String(g.away_team ?? ''),
        home_team: String(g.home_team ?? ''),
        player: String(p.player_name ?? ''),
        position: String(p.position ?? ''),
        team: String(p.team ?? ''),
        slate: String(p.slate ?? ''),
        status: String(p.status ?? ''),
        salary,
        points,
        value,
        rostered: !!p.rostered,
      });
    }
  }
  // By the model's number, because that is the column a reader scans first;
  // value sorts on demand in the panel.
  rows.sort((a, b) => (b.points ?? -Infinity) - (a.points ?? -Infinity)
    || a.player.localeCompare(b.player));
  return rows;
}

/* ---- Weather -------------------------------------------------------------
   Conditions per outdoor game, and what the model did about them.

   The honest framing matters here and is the reason the panel says it out
   loud: the lab measured wind on NFL totals over 2014–2025 and promoted it
   as a BIAS CORRECTION, not an edge (docs/MODEL_LAB.md Round 5). The bare
   model over-projected windy totals — 46.3% O/U against the close on windy
   games — and the adjustment recovers about 1.8 points of that. It makes a
   windy total honest; it does not beat the close on windy games. A surface
   that presented these rows as plays would be inventing an edge the lab
   explicitly declined to claim.

   Two wind numbers, never folded together: `wind_mph` is the kickoff-hour
   forecast (conditions), `wind_model_mph` the daily max the adjustment was
   fitted on and priced from. */

/** The wind speed above which the lab found a measurable totals effect. */
export const WIND_THRESHOLD_MPH = 15;

export function weatherBoard(games) {
  const rows = [];
  for (const g of games ?? []) {
    const wx = g.weather;
    if (!wx) continue;
    const num = (v) => (isNum(v) ? Number(v) : null);
    const marketTotal = (g.markets ?? [])
      .find((m) => String(m.market) === 'total');
    rows.push({
      game_id: g.game_id,
      league: g.league,
      kickoff: g.kickoff ?? null,
      away_team: String(g.away_team ?? ''),
      home_team: String(g.home_team ?? ''),
      covered: wx.covered === true,
      temp_f: num(wx.temp_f),
      wind_mph: num(wx.wind_mph),
      precip_pct: num(wx.precip_pct),
      wind_model_mph: num(wx.wind_model_mph),
      precip_in: num(wx.precip_in),
      wind_points: num(wx.wind_points),
      precip_points: num(wx.precip_points),
      // What the adjustment moved on the total (≤ 0). Null means no weather
      // model ran for this league, which is not the same as zero.
      total_points: num(wx.total_points),
      model_total: g.proj && isNum(g.proj.fair_total) ? Number(g.proj.fair_total) : null,
      market_total: marketTotal && isNum(marketTotal.point) ? Number(marketTotal.point) : null,
    });
  }
  // Windiest first among the outdoor games; covered venues sort last, since
  // a dome has nothing to read.
  rows.sort((a, b) => {
    if (a.covered !== b.covered) return a.covered ? 1 : -1;
    const aw = Math.max(a.wind_mph ?? -Infinity, a.wind_model_mph ?? -Infinity);
    const bw = Math.max(b.wind_mph ?? -Infinity, b.wind_model_mph ?? -Infinity);
    return bw - aw;
  });
  return rows;
}

/** The headline counts over a weather board. */
export function weatherSummary(rows) {
  const all = rows ?? [];
  const outdoor = all.filter((r) => !r.covered);
  const windy = outdoor.filter((r) => {
    const w = Math.max(r.wind_mph ?? -Infinity, r.wind_model_mph ?? -Infinity);
    return Number.isFinite(w) && w >= WIND_THRESHOLD_MPH;
  });
  const adjusted = outdoor.filter((r) => isNum(r.total_points) && r.total_points !== 0);
  const moved = adjusted.reduce((sum, r) => sum + r.total_points, 0);
  return {
    n: all.length,
    covered: all.length - outdoor.length,
    outdoor: outdoor.length,
    windy: windy.length,
    adjusted: adjusted.length,
    // Total points removed across the board, so "the weather is worth 4.2
    // points today" is one number rather than a column to add up by eye.
    points_moved: moved,
  };
}
