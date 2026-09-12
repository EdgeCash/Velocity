// The hub's joins, tested without a browser.
//
// Everything here is the kind of code that fails QUIETLY: a team-name match
// that misses drops a whole league's live scores and leaves a board that still
// looks fine, and a box-score parser that guesses column names mislabels a row
// of numbers rather than erroring. Both are worth a test each.
//
// Run: node --test site/tests/   (no dependencies — node's own runner)

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  espnDate,
  indexGames,
  normTeam,
  parseBoxScore,
  parseScoringPlays,
  scoreboardPlan,
} from '../components/hub/live.js';
import {
  buildGames,
  buildLineups,
  collapseMarkets,
  dailyCurve,
  impliedProb,
  leagueCounts,
  realRows,
  toTime,
} from '../components/hub/model.js';

/* ---- team matching ------------------------------------------------- */

test('team keys ignore case, punctuation and accents', () => {
  assert.equal(normTeam('Texas A&M Aggies'), 'texasamaggies');
  assert.equal(normTeam('San José State'), 'sanjosestate');
  assert.equal(normTeam('  PIT  '), 'pit');
});

test('a game is findable by full name and by team code', () => {
  const games = [{
    game_id: 'g1', league: 'nfl',
    home_team: 'Pittsburgh Steelers', away_team: 'Atlanta Falcons',
  }];
  const teams = {
    'nfl|Pittsburgh Steelers': { code: 'PIT' },
    'nfl|Atlanta Falcons': { code: 'ATL' },
  };
  const index = indexGames(games, teams);
  assert.equal(index['nfl|atlantafalcons|pittsburghsteelers'], 'g1');
  assert.equal(index['nfl|atl|pit'], 'g1');
});

test('two leagues with the same city do not answer for each other', () => {
  // "Miami" is a college team and an NFL team; a key without the league
  // would attach the Hurricanes score to the Dolphins card.
  const index = indexGames([
    { game_id: 'cfb', league: 'ncaaf', home_team: 'Miami Hurricanes', away_team: 'Duke Blue Devils' },
    { game_id: 'pro', league: 'nfl', home_team: 'Miami Dolphins', away_team: 'Buffalo Bills' },
  ], {});
  assert.equal(index['ncaaf|dukebluedevils|miamihurricanes'], 'cfb');
  assert.equal(index['nfl|buffalobills|miamidolphins'], 'pro');
  assert.equal(index['nfl|dukebluedevils|miamihurricanes'], undefined);
});

test('a game with no identity row still registers its name spelling', () => {
  // College colours and codes arrive from a separate table that can be cold.
  // The name key has to work on its own or the whole college board goes dark.
  const index = indexGames(
    [{ game_id: 'g', league: 'ncaaf', home_team: 'Akron Zips', away_team: 'Ohio Bobcats' }],
    {},
  );
  assert.equal(index['ncaaf|ohiobobcats|akronzips'], 'g');
});

/* ---- the scoreboard plan ------------------------------------------- */

test("the plan asks for the games' own dates, not just today", () => {
  // The failure this guards: a Friday board carrying Sunday's NFL slate.
  // ESPN's scoreboard defaults to today, so the default would come back empty
  // and every NFL card would show no score forever.
  const now = new Date('2026-09-12T18:00:00Z');
  const plan = scoreboardPlan([
    { league: 'nfl', kickoff: '2026-09-13T17:00:00Z' },
  ], now);
  assert.deepEqual(plan.nfl, ['20260912', '20260913']);
});

test('a league we do not map is left out rather than guessed at', () => {
  const plan = scoreboardPlan([{ league: 'cricket', kickoff: '2026-09-13T17:00:00Z' }]);
  assert.deepEqual(plan, {});
});

test("the sports day is Eastern, so a late Pacific tip keeps its own date", () => {
  // 02:30 UTC on the 13th is 10:30pm Eastern on the 12th — still "tonight".
  assert.equal(espnDate('2026-09-13T02:30:00Z'), '20260912');
  assert.equal(espnDate('2026-09-13T17:00:00Z'), '20260913');
});

/* ---- the board ------------------------------------------------------ */

const QUOTE = (venue, price, extra = {}) => ({
  game_id: 'g1', league: 'nfl', market: 'total', side: 'over', point: 48.5,
  book: venue, venue: venue === 'kalshi' || venue === 'polymarket' ? venue : 'sportsbook',
  venue_key: venue, venue_label: venue, price, p_model: 0.53, tier: 'B',
  conviction: 0.4, stake_sized: 0, ...extra,
});

test('one market quoted at five venues collapses to one row', () => {
  const [market] = collapseMarkets([
    QUOTE('fanduel', -110), QUOTE('kalshi', 113), QUOTE('betmgm', -105),
  ]);
  assert.equal(market.venues.length, 3);
  assert.equal(market.market, 'total');
});

test('best price wins across the sign break', () => {
  // +113 pays 1.13 and −105 pays 0.952; a comparator on magnitude would
  // put −105 first and quote the bettor the worse of the two.
  const [market] = collapseMarkets([
    QUOTE('betmgm', -105), QUOTE('kalshi', 113), QUOTE('fanduel', -110),
  ]);
  assert.equal(market.best.price, 113);
  assert.equal(market.best.venue, 'kalshi');
});

test('exchanges and sportsbooks are separable but ride the same market', () => {
  const [market] = collapseMarkets([
    QUOTE('fanduel', -110), QUOTE('kalshi', 113), QUOTE('polymarket', 108),
  ]);
  assert.deepEqual(market.exchanges.map((v) => v.venue), ['kalshi', 'polymarket']);
  assert.deepEqual(market.books.map((v) => v.venue), ['fanduel']);
});

test('the same market at two different points stays two markets', () => {
  // o48.5 and o51.5 are different bets; collapsing them would print one
  // price against the other's line.
  const markets = collapseMarkets([
    QUOTE('fanduel', -110), QUOTE('kalshi', 120, { point: 51.5 }),
  ]);
  assert.equal(markets.length, 2);
});

test('markets sort by tier before conviction', () => {
  const markets = collapseMarkets([
    QUOTE('fanduel', -110, { market: 'spread', tier: 'C', conviction: 0.9 }),
    QUOTE('fanduel', -110, { market: 'moneyline', tier: 'A', conviction: 0.1 }),
    QUOTE('fanduel', -110, { market: 'total', tier: 'B', conviction: 0.5 }),
  ]);
  assert.deepEqual(markets.map((m) => m.tier), ['A', 'B', 'C']);
});

test('implied probability crosses the favourite/underdog line correctly', () => {
  assert.ok(Math.abs(impliedProb(100) - 0.5) < 1e-9);
  assert.ok(Math.abs(impliedProb(-110) - 0.5238) < 1e-3);
  assert.ok(Math.abs(impliedProb(150) - 0.4) < 1e-9);
  assert.equal(impliedProb(null), null);
});

/* ---- the game spine ------------------------------------------------- */

test('the sentinel row every empty source carries never reaches the surface', () => {
  assert.deepEqual(realRows([{ league: '__none__' }, { league: 'nfl' }]), [{ league: 'nfl' }]);
});

test('a scheduled game with no priced market still appears', () => {
  // The failure this guards: a league whose odds pull failed vanishing from
  // the board entirely instead of showing up with nothing priced.
  const built = buildGames({
    games: [{ game_id: 'g9', league: 'wnba', home_team: 'Atlanta Dream', away_team: 'Chicago Sky', kickoff: '2026-09-12T23:00:00Z' }],
  });
  assert.equal(built.length, 1);
  assert.equal(built[0].n_markets, 0);
});

test('games sort by kickoff, and an undated one sorts last', () => {
  const built = buildGames({
    games: [
      { game_id: 'late', league: 'nfl', home_team: 'A', away_team: 'B', kickoff: '2026-09-14T00:00:00Z' },
      { game_id: 'none', league: 'nfl', home_team: 'C', away_team: 'D', kickoff: null },
      { game_id: 'soon', league: 'nfl', home_team: 'E', away_team: 'F', kickoff: '2026-09-12T18:00:00Z' },
    ],
  });
  assert.deepEqual(built.map((g) => g.game_id), ['soon', 'late', 'none']);
});

test('DFS players attach to a game by team, both sides of it', () => {
  // Away before home, matching how a matchup is written and how the card
  // renders it — so a reader scanning the block is not silently re-ordered
  // relative to the row above it.
  const built = buildGames({
    games: [{ game_id: 'g1', league: 'mlb', home_team: 'DET', away_team: 'COL', kickoff: '2026-09-12T22:40:00Z' }],
    dfs: [
      { league: 'mlb', team: 'DET', player_name: 'Colt Keith', points: 8.77 },
      { league: 'mlb', team: 'COL', player_name: 'Mason Adams', points: 13.19 },
      { league: 'mlb', team: 'WSH', player_name: 'Cade Cavalli', points: 17.1 },
    ],
  });
  assert.deepEqual(built[0].dfs.map((p) => p.player_name), ['Mason Adams', 'Colt Keith']);
});

test('a prop attaches to the game it is a bet on', () => {
  const built = buildGames({
    games: [{ game_id: 'g1', league: 'mlb', home_team: 'DET', away_team: 'COL' }],
    props: [
      { game_id: 'g1', league: 'mlb', player: 'T. Skubal', market: 'pitcher strikeouts' },
      { game_id: 'other', league: 'mlb', player: 'Someone else', market: 'x' },
    ],
  });
  assert.equal(built[0].props.length, 1);
  assert.equal(built[0].props[0].player, 'T. Skubal');
});

test('only lines that actually moved count as moves', () => {
  // An unmoved line is not news, and a "Moved since open" block full of
  // unchanged numbers trains you to stop reading it.
  const built = buildGames({
    games: [{ game_id: 'g1', league: 'nfl', home_team: 'A', away_team: 'B' }],
    lineMoves: [
      { game_id: 'g1', league: 'nfl', market: 'total', side: 'over',
        point_open: 48.5, price_open: -110, point_now: 49.5, price_now: -110 },
      { game_id: 'g1', league: 'nfl', market: 'spread', side: 'home',
        point_open: -3.5, price_open: -110, point_now: -3.5, price_now: -115 },
      { game_id: 'g1', league: 'nfl', market: 'moneyline', side: 'home',
        point_open: null, price_open: -160, point_now: null, price_now: -160 },
    ],
  });
  // The point move and the price move both count; the unchanged one does not.
  assert.deepEqual(built[0].moves.map((m) => m.market), ['total', 'spread']);
});

test('injuries attach by team and remember which side they are on', () => {
  // "Who is out" is only useful when you can see it is not evenly split.
  const built = buildGames({
    games: [{ game_id: 'g1', league: 'nfl', home_team: 'PIT', away_team: 'ATL' }],
    injuries: [
      { league: 'nfl', team: 'PIT', player_name: 'D. Elliott', is_out: true },
      { league: 'nfl', team: 'ATL', player_name: 'T. Tagovailoa', is_out: true },
      { league: 'nfl', team: 'ATL', player_name: 'Questionable Guy', is_out: false },
      { league: 'nfl', team: 'BUF', player_name: 'Not in this game', is_out: true },
    ],
  });
  assert.deepEqual(
    built[0].injuries.map((i) => [i.side, i.player_name]),
    [['away', 'T. Tagovailoa'], ['home', 'D. Elliott']],
  );
});

test('only published rows count as published', () => {
  const built = buildGames({
    games: [{ game_id: 'g1', league: 'nfl', home_team: 'A', away_team: 'B' }],
    publish: [
      { game_id: 'g1', league: 'nfl', published: true },
      { game_id: 'g1', league: 'nfl', published: false },
    ],
  });
  assert.equal(built[0].n_published, 1);
});

test('league counts drive the filter and are ordered by size', () => {
  const counts = leagueCounts([
    { league: 'nfl' }, { league: 'ncaaf' }, { league: 'ncaaf' },
  ]);
  assert.deepEqual(counts, [{ league: 'ncaaf', n: 2 }, { league: 'nfl', n: 1 }]);
});

/* ---- DFS ------------------------------------------------------------ */

test('DFS rows group into one lineup per slate, with its totals', () => {
  const lineups = buildLineups([
    { league: 'mlb', slate: 'Turbo', player_name: 'A', salary: 9100, points: 17.1 },
    { league: 'mlb', slate: 'Turbo', player_name: 'B', salary: 7600, points: 13.19 },
    { league: 'mlb', slate: 'Main', player_name: 'C', salary: 5000, points: 9.0 },
  ], 'classic');
  assert.equal(lineups.length, 2);
  const turbo = lineups.find((l) => l.slate === 'Turbo');
  assert.equal(turbo.salary, 16700);
  assert.ok(Math.abs(turbo.points - 30.29) < 1e-9);
});

/* ---- the bankroll curve --------------------------------------------- */

test('the curve accumulates the daily increment, never the running total', () => {
  // The bug this pins: the units table's `units`/`units_sized` are ALREADY a
  // per-league cumsum, so accumulating them compounds the same profit twice.
  // These rows are shaped exactly like the real ones — two leagues, three
  // days, `units_sized` running per league — and the curve must finish at the
  // sum of `profit_sized` (+6), not at the sum of the running totals (+17).
  const rows = [
    { league: 'mlb', slate_date: '2026-08-18', profit_sized: 2, units_sized: 2 },
    { league: 'mlb', slate_date: '2026-08-19', profit_sized: 3, units_sized: 5 },
    { league: 'nfl', slate_date: '2026-08-19', profit_sized: -1, units_sized: -1 },
    { league: 'nfl', slate_date: '2026-08-20', profit_sized: 2, units_sized: 1 },
  ];
  const curve = dailyCurve(rows);
  assert.equal(curve.length, 3);
  assert.equal(curve[curve.length - 1].v, 6);
  assert.deepEqual(curve.map((p) => p.v), [2, 4, 6]);
});

test('the curve falls back to unsized profit when a day predates sizing', () => {
  const curve = dailyCurve([
    { league: 'mlb', slate_date: '2026-08-18', profit: 4 },
    { league: 'mlb', slate_date: '2026-08-19', profit: -1, profit_sized: -2 },
  ]);
  assert.deepEqual(curve.map((p) => p.v), [4, 2]);
});

test('a day with no usable date is dropped rather than sorted to the epoch', () => {
  // `new Date(null)` is the EPOCH, not an invalid date, so a null timestamp
  // passes a bare Number.isFinite check and lands in January 1970 — at the
  // FRONT of the curve, silently shifting every later point.
  const curve = dailyCurve([
    { league: 'mlb', slate_date: null, profit_sized: 99 },
    { league: 'mlb', slate_date: undefined, profit_sized: 50 },
    { league: 'mlb', slate_date: '', profit_sized: 20 },
    { league: 'mlb', slate_date: 'not a date', profit_sized: 10 },
    { league: 'mlb', slate_date: '2026-08-18', profit_sized: 1 },
  ]);
  assert.deepEqual(curve.map((p) => p.v), [1]);
});

test('toTime rejects everything that is not a moment', () => {
  assert.equal(toTime(null), null);
  assert.equal(toTime(undefined), null);
  assert.equal(toTime(''), null);
  assert.equal(toTime('not a date'), null);
  assert.equal(toTime(new Date('2026-08-18T00:00:00Z')), Date.parse('2026-08-18T00:00:00Z'));
  assert.equal(toTime('2026-08-18T00:00:00Z'), Date.parse('2026-08-18T00:00:00Z'));
});

/* ---- the box score -------------------------------------------------- */

test('the box score reads ESPN\'s own column labels rather than naming them', () => {
  // The point of the parser: it never asserts a sport's columns, so it works
  // for baseball and basketball alike and cannot mislabel a renamed column.
  const blocks = parseBoxScore({
    boxscore: {
      players: [{
        team: { abbreviation: 'DET' },
        statistics: [{
          name: 'batting', text: 'Batting',
          labels: ['AB', 'R', 'H', 'RBI'],
          athletes: [
            { athlete: { id: '1', shortName: 'C. Keith' }, position: { abbreviation: '1B' }, stats: ['4', '1', '2', '1'] },
          ],
        }],
      }],
    },
  });
  assert.equal(blocks.length, 1);
  assert.deepEqual(blocks[0].columns, ['AB', 'R', 'H', 'RBI']);
  assert.deepEqual(blocks[0].rows[0].stats, ['4', '1', '2', '1']);
  assert.equal(blocks[0].team, 'DET');
});

test('a stats array longer than its header is trimmed, not invented into', () => {
  const blocks = parseBoxScore({
    boxscore: {
      players: [{
        team: { abbreviation: 'X' },
        statistics: [{
          labels: ['PTS'],
          athletes: [{ athlete: { id: '1', shortName: 'P' }, stats: ['10', '4', '3'] }],
        }],
      }],
    },
  });
  assert.deepEqual(blocks[0].rows[0].stats, ['10']);
});

test('a shape the parser does not recognise yields nothing, never a throw', () => {
  assert.deepEqual(parseBoxScore(undefined), []);
  assert.deepEqual(parseBoxScore({}), []);
  assert.deepEqual(parseBoxScore({ boxscore: { players: [{ statistics: [{}] }] } }), []);
  assert.deepEqual(parseScoringPlays(undefined), []);
});

test('a stat block with headers but no players is dropped', () => {
  // An empty table with four column headings reads as a load failure.
  assert.deepEqual(
    parseBoxScore({
      boxscore: { players: [{ team: { abbreviation: 'X' }, statistics: [{ labels: ['A'], athletes: [] }] }] },
    }),
    [],
  );
});
