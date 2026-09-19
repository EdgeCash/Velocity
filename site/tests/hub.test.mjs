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
  accuracySummary,
  buildCard,
  buildGames,
  clvTrust,
  collapseProps,
  mostLikely,
  outcomeLabel,
  playerBook,
  marketMove,
  modelVsMarket,
  playHeadline,
  buildLineups,
  buildParlays,
  collapseMarkets,
  dailyCurve,
  flaggedMarkets,
  healthRows,
  heldGroups,
  heldRule,
  impliedProb,
  leagueCounts,
  parlayCounts,
  ratingAliases,
  ratingRows,
  ratingsIndex,
  realRows,
  splitCards,
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

/* ---- the card --------------------------------------------------------- */

const VERDICT = (over = {}) => ({
  game_id: 'g1', league: 'nfl', market: 'spread', side: 'away',
  price: -109, stake_sized: 0.95, edge: 0.038, tier: 'A', conviction: 0.82,
  published: true, reason: '', ...over,
});

function cardFrom(verdicts, board = []) {
  return buildCard(buildGames({
    games: [{ game_id: 'g1', league: 'nfl', home_team: 'LV', away_team: 'MIA' }],
    publish: verdicts,
    board,
  }));
}

test('the card separates what cleared the gate from what did not', () => {
  const { plays, held } = cardFrom([
    VERDICT(),
    VERDICT({ market: 'total', published: false, reason: 'tier B below publishable' }),
  ]);
  assert.equal(plays.length, 1);
  assert.equal(held.length, 1);
});

test('every verdict reaches the card, not just the published ones', () => {
  // The bug this pins: buildGames kept only `published`, so the 86 rejects on
  // a real slate were dropped before anything could render them — which is
  // how the gate became unauditable.
  const built = buildGames({
    games: [{ game_id: 'g1', league: 'nfl', home_team: 'LV', away_team: 'MIA' }],
    publish: [VERDICT(), VERDICT({ published: false, reason: 'paper' })],
  });
  assert.equal(built[0].verdicts.length, 2, 'both verdicts survive');
  assert.equal(built[0].n_published, 1, 'but only one counts as published');
});

test('the card ranks by the money the model wants, then by edge', () => {
  // Ranking by edge alone leads with whatever is noisiest — the same mistake
  // the old board made.
  const { plays } = cardFrom([
    VERDICT({ market: 'total', stake_sized: 0.4, edge: 0.09 }),
    VERDICT({ market: 'spread', stake_sized: 1.2, edge: 0.03 }),
    VERDICT({ market: 'moneyline', stake_sized: 1.2, edge: 0.05 }),
  ]);
  assert.deepEqual(plays.map((p) => p.market), ['moneyline', 'spread', 'total']);
});

test('a play carries the venue, which publish itself never says', () => {
  // `publish` has a price but no book. Without the board join the card can
  // tell you what to bet and not where, which is half a play.
  const { plays } = cardFrom([VERDICT()], [
    { game_id: 'g1', league: 'nfl', market: 'spread', side: 'away', point: 3.5,
      book: 'kalshi', venue: 'kalshi', venue_key: 'kalshi', venue_label: 'Kalshi',
      price: 113, p_model: 0.55 },
    { game_id: 'g1', league: 'nfl', market: 'spread', side: 'away', point: 3.5,
      book: 'fanduel', venue: 'sportsbook', venue_key: 'fanduel',
      venue_label: 'FanDuel', price: -109, p_model: 0.55 },
  ]);
  assert.equal(plays[0].best.venue, 'kalshi', 'the best-priced venue');
  assert.equal(plays[0].point, 3.5, 'and the line it is on');
});

test('a play with no matching board row still appears, without a venue', () => {
  // Degrading beats disappearing: the gate published it, so it belongs on
  // the card even if the board row cannot be matched.
  const { plays } = cardFrom([VERDICT()]);
  assert.equal(plays.length, 1);
  assert.equal(plays[0].best, null);
});

test('held reasons group by RULE, not by their sentence', () => {
  // The gate writes the numbers into the reason — "edge 0.021 below floor
  // 0.030" — so grouping on the raw string gives one bucket per row. The
  // sentence stays on the row; only the heading is normalised.
  assert.equal(heldRule('edge 0.021 below floor 0.030').rule, 'Edge below floor');
  assert.equal(heldRule('edge 0.028 below floor 0.030').rule, 'Edge below floor');
  assert.equal(heldRule('conviction 0.71 below 0.72').rule, 'Conviction below threshold');
  assert.equal(heldRule('tier B below publishable').rule, 'Tier below publishable');
  assert.equal(
    heldRule('paper — priced, not staked (paper market)').rule,
    'Paper — priced, not staked',
  );
  assert.equal(
    heldRule('market moved 0.017 against us since pricing').rule,
    'Market moved against us',
  );
});

test('an unrecognised reason is kept verbatim rather than swallowed', () => {
  // A new gate rule must not silently file itself under an existing bucket.
  assert.equal(heldRule('some brand new rule').rule, 'some brand new rule');
  assert.equal(heldRule('').rule, 'Held back');
});

test('near misses sort above the gate working as designed', () => {
  // "Paper" and "tier B below publishable" are rows that were never
  // candidates; an edge that missed the floor by 0.002 is a decision.
  const groups = heldGroups([
    { rule: 'Paper — priced, not staked', rank: 3, edge: 0.01 },
    { rule: 'Edge below floor', rank: 0, edge: 0.028 },
    { rule: 'Tier below publishable', rank: 2, edge: 0.02 },
    { rule: 'Conviction below threshold', rank: 0, edge: 0.04 },
  ]);
  assert.equal(groups[0].rank, 0);
  assert.equal(groups[1].rank, 0);
  assert.deepEqual(groups.map((g) => g.rule).slice(2),
    ['Tier below publishable', 'Paper — priced, not staked']);
});

test('within a rule the strongest opinion leads', () => {
  const [group] = heldGroups([
    { rule: 'Edge below floor', rank: 0, edge: 0.021 },
    { rule: 'Edge below floor', rank: 0, edge: 0.029 },
    { rule: 'Edge below floor', rank: 0, edge: 0.025 },
  ]);
  assert.deepEqual(group.rows.map((r) => r.edge), [0.029, 0.025, 0.021]);
});

test('a slate where nothing clears yields an empty card, not an error', () => {
  // The common case: on the Sept 12 slate, 2 of 88 cleared. A slate where
  // none do is normal and the panel has to say so rather than look broken.
  const { plays, held } = cardFrom([
    VERDICT({ published: false, reason: 'tier B below publishable' }),
  ]);
  assert.deepEqual(plays, []);
  assert.equal(held.length, 1);
});

test('the card is empty rather than throwing when there are no games', () => {
  assert.deepEqual(buildCard([]), { plays: [], held: [] });
  assert.deepEqual(buildCard(undefined), { plays: [], held: [] });
});

/* ---- parlays and cards ------------------------------------------------ */

const PARLAY = {
  league: 'nfl', n_legs: 3, price: 849, p_win: 0.3787, ev: 2.665,
  same_game: true, stake: 1.0695,
  legs: 'MIA@LV moneyline away (+157) + MIA@LV spread away 3.5 (−109) + WAS@PHI spread home −4.5 (−108)',
  legs_json: JSON.stringify([
    { game_id: 'mialv', market: 'moneyline', side: 'away', price: 157, label: 'MIA@LV' },
    { game_id: 'mialv', market: 'spread', side: 'away', price: -109, point: 3.5, label: 'MIA@LV' },
    { game_id: 'wasphi', market: 'spread', side: 'home', price: -108, point: -4.5, label: 'WAS@PHI' },
  ]),
};

test('a parlay resolves the games its legs are on', () => {
  // This is what makes a parlay a first-class object here rather than a
  // string in a table: the block can jump to the game a leg is on, and the
  // game can say it is in a parlay.
  const [parlay] = buildParlays([PARLAY]);
  assert.equal(parlay.legs.length, 3);
  assert.deepEqual(parlay.gameIds, ['mialv', 'wasphi']);
});

test('same_game means correlated legs, not a one-game parlay', () => {
  // The producer's flag means two or more legs SHARE a game. Reading it as
  // "every leg is this game" would file a cross-game parlay inside one
  // game's sheet — this parlay touches two.
  const [parlay] = buildParlays([PARLAY]);
  assert.equal(parlay.correlated, true);
  assert.equal(parlay.gameIds.length, 2, 'it is not a one-game parlay');
});

test('a parlay whose legs JSON is broken keeps its price and its prose', () => {
  const [parlay] = buildParlays([{ ...PARLAY, legs_json: '{not json' }]);
  assert.deepEqual(parlay.legs, []);
  assert.deepEqual(parlay.gameIds, []);
  assert.equal(parlay.price, 849, 'the price is still true');
  assert.ok(parlay.legs_string.startsWith('MIA@LV'), 'the rendered string survives');
});

test('the rendered legs string is not overwritten by the parsed array', () => {
  // Both live on the source row under different names; the parsed array takes
  // `legs` and the string has to be kept somewhere or the degraded path above
  // renders "[object Object]".
  const [parlay] = buildParlays([PARLAY]);
  assert.ok(Array.isArray(parlay.legs));
  assert.equal(typeof parlay.legs_string, 'string');
});

test('parlays sort by EV, best first', () => {
  const out = buildParlays([
    { ...PARLAY, ev: 1.4 }, { ...PARLAY, ev: 2.6 }, { ...PARLAY, ev: 2.0 },
  ]);
  assert.deepEqual(out.map((p) => p.ev), [2.6, 2.0, 1.4]);
});

test('a game knows how many parlays have a leg on it', () => {
  const parlays = buildParlays([PARLAY, { ...PARLAY, legs_json: JSON.stringify([
    { game_id: 'mialv', market: 'total', side: 'over', price: -110, label: 'MIA@LV' },
  ]) }]);
  const counts = parlayCounts(parlays);
  // Two legs on one game inside one parlay still counts that parlay ONCE.
  assert.equal(counts.get('mialv'), 2);
  assert.equal(counts.get('wasphi'), 1);
});

test('cards split by whether they belong to a game or a league', () => {
  // The record card is one per league and carries no game_id, so it has no
  // game to sit in; the matchup sheets do.
  const { byGame, byLeague } = splitCards([
    { kind: 'sheet', league: 'nfl', game_id: 'g1', file: 'a.png' },
    { kind: 'simcheck', league: 'nfl', game_id: 'g1', file: 'b.png' },
    { kind: 'recordcard', league: 'nfl', game_id: '', file: 'c.png' },
    { kind: 'recordcard', league: 'mlb', game_id: '   ', file: 'd.png' },
  ]);
  assert.equal(byGame.get('g1').length, 2);
  assert.deepEqual(byLeague.map((c) => c.file), ['c.png', 'd.png']);
});

test("a game carries its own cards and its own parlay count", () => {
  const built = buildGames({
    games: [{ game_id: 'mialv', league: 'nfl', home_team: 'LV', away_team: 'MIA' }],
    cards: [{ kind: 'sheet', league: 'nfl', game_id: 'mialv', file: 'a.png' }],
    parlays: buildParlays([PARLAY]),
  });
  assert.equal(built[0].cards.length, 1);
  assert.equal(built[0].n_parlays, 1);
});

/* ---- power ratings --------------------------------------------------- */

test('ratings join through the projection, not the game', () => {
  // The trap: ratings are keyed by each fit's own team spelling — `PIT` for
  // the NFL, `Alabama` for college — and the GAMES table carries neither
  // ("Pittsburgh Steelers", "Alabama Crimson Tide"). Projections carry the
  // fit's spelling. Joining to games matches nothing and renders a
  // head-to-head with both sides blank.
  const built = buildGames({
    games: [{ game_id: 'g1', league: 'nfl',
      home_team: 'Pittsburgh Steelers', away_team: 'Atlanta Falcons' }],
    projections: [{ game_id: 'g1', league: 'nfl', home: 'PIT', away: 'ATL' }],
    ratings: [
      { league: 'nfl', team: 'PIT', net: 2.1, off: 1.0, def: -1.1, rank: 7 },
      { league: 'nfl', team: 'ATL', net: -0.4, off: 0.6, def: 1.0, rank: 19 },
    ],
  });
  assert.equal(built[0].ratings.home.rank, 7);
  assert.equal(built[0].ratings.away.rank, 19);
});

test('a game with no projection has no rating join rather than a wrong one', () => {
  const built = buildGames({
    games: [{ game_id: 'g1', league: 'nfl', home_team: 'PIT', away_team: 'ATL' }],
    ratings: [{ league: 'nfl', team: 'PIT', net: 2.1, rank: 7 }],
  });
  assert.equal(built[0].ratings.home, null);
  assert.equal(built[0].ratings.away, null);
});

test('ratings do not cross leagues', () => {
  // College and the WNBA both have an "Atlanta"; an unkeyed index would rate
  // one with the other's numbers.
  const index = ratingsIndex([
    { league: 'wnba', team: 'Atlanta Dream', net: 4.76 },
    { league: 'nfl', team: 'ATL', net: -0.4 },
  ]);
  assert.equal(index.get('wnba|Atlanta Dream').net, 4.76);
  assert.equal(index.get('nfl|Atlanta Dream'), undefined);
});

test('a code-keyed rating resolves to the club name it is searchable by', () => {
  // The NFL fit rates `PIT`; typing "steel" found nothing until the identity
  // table was reversed onto it.
  const alias = ratingAliases([
    { league: 'nfl', team: 'Pittsburgh Steelers', code: 'PIT' },
    { league: 'wnba', team: 'Atlanta Dream', code: 'ATL' },
  ]);
  assert.equal(alias.get('nfl|PIT'), 'Pittsburgh Steelers');
  // A fit that already uses the full name resolves to itself.
  assert.equal(alias.get('wnba|Atlanta Dream'), 'Atlanta Dream');
  // ...and the two leagues' ATL do not collide.
  assert.equal(alias.get('wnba|ATL'), 'Atlanta Dream');
  assert.equal(alias.get('nfl|ATL'), undefined);
});

test('a college spelling with no identity match is left as the fit wrote it', () => {
  // The fit says "Alabama", the identity table says "Alabama Crimson Tide",
  // and they share no key. Falling back is right; a prefix match would
  // confidently map "Miami" to the wrong school.
  const alias = ratingAliases([
    { league: 'ncaaf', team: 'Alabama Crimson Tide', code: 'ALA' },
  ]);
  assert.equal(alias.get('ncaaf|Alabama'), undefined);
});

test('a lower defensive number is the BETTER one', () => {
  // The bug the old matchup sheet shipped: a naive "higher wins" marks the
  // wrong side on Def and on Rank — two of the five rows.
  const rows = ratingRows(
    { rank: 19, net: -0.4, off: 0.6, def: 1.0 },
    { rank: 7, net: 2.1, off: 1.0, def: -1.1 },
  );
  const by = Object.fromEntries(rows.map((r) => [r.key, r.edge]));
  assert.equal(by.def, 'home', 'home allows 1.1 fewer than average and wins Def');
  assert.equal(by.rank, 'home', 'rank 7 beats rank 19');
  assert.equal(by.net, 'home');
  assert.equal(by.off, 'home');
});

test('the away side can win a row too', () => {
  const rows = ratingRows(
    { rank: 3, net: 8.0, off: 5.0, def: -3.0 },
    { rank: 40, net: 1.0, off: 4.0, def: 3.0 },
  );
  assert.deepEqual(rows.map((r) => r.edge), ['away', 'away', 'away', 'away']);
});

test('pace is context and is never marked as an advantage', () => {
  // Marking a side on pace would invent a claim the model does not make:
  // fast is not better than slow.
  const rows = ratingRows(
    { rank: 1, net: 6.8, off: 4.47, def: -2.34, pace: 81.33 },
    { rank: 2, net: 6.74, off: 0.22, def: -6.51, pace: 78.73 },
  );
  const pace = rows.find((r) => r.key === 'pace');
  assert.ok(pace, 'pace is still shown');
  assert.equal(pace.edge, null);
});

test('a tied row marks neither side', () => {
  const rows = ratingRows({ net: 2.0, rank: 5 }, { net: 2.0, rank: 5 });
  assert.deepEqual(rows.map((r) => r.edge), [null, null]);
});

test('a statistic the fit does not report is dropped, not printed as zero', () => {
  // The football and baseball fits report no pace, and the column arrives as
  // an explicit NULL — which `Number()` turns into a perfectly finite 0. The
  // first version of this test used a MISSING key (undefined → NaN) and so
  // passed while the real data rendered "Pace 0.0 / 0.0" for two teams that
  // play a normal number of possessions. Both spellings are pinned now.
  const nulled = ratingRows(
    { rank: 1, net: 7.51, off: 2.16, def: -5.35, pace: null },
    { rank: 2, net: 4.83, off: 5.6, def: 0.77, pace: null },
  );
  assert.ok(!nulled.some((r) => r.key === 'pace'), 'an explicit null drops the row');

  const missing = ratingRows(
    { rank: 1, net: 7.51, off: 2.16, def: -5.35 },
    { rank: 2, net: 4.83, off: 5.6, def: 0.77 },
  );
  assert.deepEqual(missing.map((r) => r.key), ['rank', 'net', 'off', 'def']);
});

test('a statistic one side has and the other does not is dropped', () => {
  // Half a row reads as a team with zero of something, not as missing data.
  const rows = ratingRows(
    { rank: 1, net: 6.8, pace: 81.33 },
    { rank: 2, net: 6.74, pace: null },
  );
  assert.ok(!rows.some((r) => r.key === 'pace'));
});

/* ---- market health --------------------------------------------------- */

const HEALTH = [
  // Flagged on the long window — the one that decides.
  { league: 'mlb', market: 'spread', window_days: 30, thin: false,
    flags: 'negative CLV', n_bets: 203, flag_exclusion: false },
  // Flagged, but only on the short window: an early warning, not a verdict.
  { league: 'mlb', market: 'total', window_days: 7, thin: false,
    flags: 'negative ROI', n_bets: 39, flag_exclusion: false },
  { league: 'mlb', market: 'total', window_days: 30, thin: false,
    flags: '', n_bets: 194, flag_exclusion: false },
  // Thin: the monitor cannot judge it, and says so in the flags string.
  { league: 'ncaaf', market: 'total', window_days: 30, thin: true,
    flags: 'thin (1 bets)', n_bets: 1, flag_exclusion: false },
  // Clean.
  { league: 'mlb', market: 'moneyline', window_days: 30, thin: false,
    flags: '', n_bets: 179, flag_exclusion: false },
  { league: '__none__', market: 'x', window_days: 30, thin: false, flags: 'nope' },
];

test('only the long window decides which markets are flagged', () => {
  // The 7-day window is an early warning; treating it as a flag would put an
  // amber mark on a market the monitor has not actually called.
  const flagged = flaggedMarkets(HEALTH);
  assert.deepEqual([...flagged.keys()], ['mlb|spread']);
});

test('a thin market is never flagged, however alarming its string looks', () => {
  // "thin (1 bets)" is the monitor saying it CANNOT judge — the opposite of a
  // warning. Surfacing it beside real flags is how a warning stops meaning
  // anything, and a truthy `flags` string alone would do exactly that.
  const flagged = flaggedMarkets(HEALTH);
  assert.ok(!flagged.has('ncaaf|total'));
});

test('the flag index is keyed by league and market together', () => {
  // Two leagues both run a market called `total`; a key without the league
  // would put MLB's flag on every college total on the board.
  const flagged = flaggedMarkets([
    { league: 'mlb', market: 'total', window_days: 30, thin: false, flags: 'negative CLV' },
    { league: 'ncaaf', market: 'total', window_days: 30, thin: false, flags: '' },
  ]);
  assert.ok(flagged.has('mlb|total'));
  assert.ok(!flagged.has('ncaaf|total'));
});

test('health rows read flagged first, then clean, then what cannot be judged', () => {
  const rows = healthRows(HEALTH, 'all');
  assert.deepEqual(
    rows.map((r) => `${r.league}|${r.market}`),
    ['mlb|spread', 'mlb|total', 'mlb|moneyline', 'ncaaf|total'],
  );
});

test('the health table honours the league filter', () => {
  assert.deepEqual(
    healthRows(HEALTH, 'ncaaf').map((r) => r.market),
    ['total'],
  );
});

test('the sentinel row never reaches the health table', () => {
  assert.ok(!healthRows(HEALTH, 'all').some((r) => r.league === '__none__'));
  assert.ok(!flaggedMarkets(HEALTH).has('__none__|x'));
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

// ---- the decision brief ---------------------------------------------------
// The card carries the reasoning now, and the piece most likely to be wrong
// QUIETLY is direction: "the line moved" is only useful if it says moved which
// way FOR THE SIDE YOU ARE ON, and that inverts between the over and the
// under. A sign error here reads as a confident, plausible, backwards fact.

test('a spread move is judged from the side you are taking', () => {
  const moves = [{ market: 'spread', side: 'home', point_open: -2.5, point_now: -3.5 }];
  // Laying 3.5 where 2.5 was available is the worse number, full stop.
  assert.equal(marketMove(moves, 'spread', 'home').direction, 'worse');
  const back = [{ market: 'spread', side: 'home', point_open: -3.5, point_now: -2.5 }];
  assert.equal(marketMove(back, 'spread', 'home').direction, 'better');
});

test('a total move inverts between the over and the under', () => {
  const up = (side) => [{ market: 'total', side, point_open: 44, point_now: 45.5 }];
  // The SAME move, opposite verdicts — the case a single rule gets backwards.
  assert.equal(marketMove(up('over'), 'total', 'over').direction, 'worse');
  assert.equal(marketMove(up('under'), 'total', 'under').direction, 'better');
});

test('a market with no point is judged on price', () => {
  const moves = [{ market: 'moneyline', side: 'home', price_open: -140, price_now: -120 }];
  const move = marketMove(moves, 'moneyline', 'home');
  assert.equal(move.kind, 'price');
  assert.equal(move.direction, 'better');
});

test('player props follow the over/under rule, not the spread one', () => {
  const moves = [{ market: 'player_pass_yds', side: 'over', point_open: 249.5, point_now: 255.5 }];
  assert.equal(marketMove(moves, 'player_pass_yds', 'over').direction, 'worse');
});

test('an unmoved line is not news', () => {
  const moves = [{ market: 'spread', side: 'home', point_open: -3, point_now: -3 }];
  assert.equal(marketMove(moves, 'spread', 'home'), null);
  assert.equal(marketMove([], 'spread', 'home'), null);
});

test('direction never rides on colour alone', () => {
  const moves = [{ market: 'spread', side: 'home', point_open: -3.5, point_now: -2.5 }];
  const move = marketMove(moves, 'spread', 'home');
  // pos vs warn measures ΔE 6.2 under protanopia, so a chip that only changed
  // hue would be unreadable. Both carriers have to be present.
  assert.ok(move.glyph);
  assert.match(move.label, /better|worse/);
});

test('the model/market gap is against the DE-VIGGED number', () => {
  const vs = modelVsMarket({ p_model: 0.582, p_fair: 0.541 });
  assert.ok(Math.abs(vs.gap - 0.041) < 1e-9);
  // Missing either side draws no bar rather than inventing a reference.
  assert.equal(modelVsMarket({ p_model: 0.58 }), null);
  assert.equal(modelVsMarket(null), null);
});

test('CLV hands back the trust flag rather than one averaged number', () => {
  const rows = [
    { market: 'spread', league: 'nfl', n_bets: 214, mean_price_clv: 1.8, clv_trusted: true },
    { market: 'player_pass_yds', league: 'nfl', n_bets: 40, mean_price_clv: 9.1, clv_trusted: false },
  ];
  assert.equal(clvTrust(rows, 'nfl', 'spread').trusted, true);
  // The prop's 9.1 is the bigger number and the one that means nothing.
  assert.equal(clvTrust(rows, 'nfl', 'player_pass_yds').trusted, false);
  assert.equal(clvTrust(rows, 'mlb', 'spread'), null);
});

test('the one-line why prefers the model’s own sentence', () => {
  const play = {
    market_row: { rationale: 'Rest edge and a backup tackle' },
    move: { label: 'better number than open' },
  };
  assert.match(playHeadline(play), /Rest edge/);
});

test('the one-line why falls through to what is actually there', () => {
  assert.match(
    playHeadline({ move: { label: 'worse number than open' } }),
    /worse number/,
  );
  assert.match(
    playHeadline({
      injuries: [{ player_name: 'A. Hamilton', side: 'home' }],
      home_team: 'Baltimore', away_team: 'Kansas City',
    }),
    /Hamilton out for Baltimore/,
  );
  // An indoor game's weather is not a headline.
  assert.equal(playHeadline({ weather: { covered: true, wind_mph: 30 } }), '');
  assert.equal(playHeadline({}), '');
});

test('buildCard carries the brief onto the row', () => {
  const games = [{
    game_id: 'g1',
    league: 'nfl',
    home_team: 'Baltimore',
    away_team: 'Kansas City',
    kickoff: null,
    verdicts: [{ game_id: 'g1', market: 'spread', side: 'home', published: true, reason: '' }],
    markets: [{
      market: 'spread', side: 'home', p_model: 0.58, p_fair: 0.54,
      rationale: 'why', venues: [{ venue: 'dk', price: -108 }], best: { venue: 'dk' },
    }],
    moves: [{ market: 'spread', side: 'home', point_open: -2.5, point_now: -3.5 }],
    injuries: [{ player_name: 'A. Hamilton', side: 'home' }],
    weather: { covered: false, wind_mph: 14 },
    ratings: { away: { net: 6.1 }, home: { net: 4.2 } },
  }];
  const { plays } = buildCard(games);
  assert.equal(plays.length, 1);
  const play = plays[0];
  // Every piece the brief renders has to reach the row, or the panel silently
  // renders an empty section.
  assert.ok(play.vs);
  assert.equal(play.venues.length, 1);
  assert.equal(play.move.direction, 'worse');
  assert.equal(play.injuries.length, 1);
  assert.equal(play.ratings.home.net, 4.2);
  assert.equal(play.headline, 'why');
});

/* ---- Most Likely, Players, Accuracy (docs/FOOTBALL_PAL.md) --------------- */

const likelyGame = () => ({
  game_id: 'g1', league: 'nfl', kickoff: null,
  away_team: 'Kansas City', home_team: 'Baltimore',
  markets: [
    { market: 'moneyline', side: 'home', point: null, p_model: 0.62, p_fair: 0.58, tier: '',
      best: { price: -150, venue: 'fanduel' } },
    { market: 'moneyline', side: 'away', point: null, p_model: 0.38, p_fair: 0.42, tier: '',
      best: { price: 130, venue: 'fanduel' } },
    { market: 'total', side: 'under', point: 45.5, p_model: 0.55, p_fair: 0.51, tier: 'B',
      best: { price: -108, venue: 'kalshi' } },
    { market: 'spread', side: 'home', point: -3.5, p_model: 0.51, p_fair: 0.5, tier: '',
      best: { price: -110, venue: 'dk' } },
    { market: 'team_total_away', side: 'over', point: 20.5, p_model: 0.57, p_fair: null, tier: '',
      best: null },
  ],
  props: [
    { league: 'nfl', game_id: 'g1', player: 'Lamar Jackson', market: 'pass_yards', side: 'over',
      point: 224.5, book: 'fanduel', price: -115, p_model: 0.58, p_fair: 0.52 },
    { league: 'nfl', game_id: 'g1', player: 'Lamar Jackson', market: 'pass_yards', side: 'over',
      point: 224.5, book: 'betmgm', price: -105, p_model: 0.58, p_fair: 0.5 },
    { league: 'nfl', game_id: 'g1', player: 'Lamar Jackson', market: 'pass_yards', side: 'under',
      point: 224.5, book: 'fanduel', price: -105, p_model: 0.42, p_fair: 0.48 },
    { league: '__none__', game_id: 'g1', player: '', market: '', side: '', point: null },
  ],
  dfs: [{ player_name: 'Lamar Jackson', team: 'BAL', position: 'QB', salary: 8200, points: 22.4 }],
});

test('outcomes read as sentences, with the handicap and the over/under spelled out', () => {
  const g = likelyGame();
  assert.equal(outcomeLabel(g, g.markets[0]), 'Baltimore win');
  assert.equal(outcomeLabel(g, g.markets[2]), 'Under 45.5');
  assert.equal(outcomeLabel(g, g.markets[3]), 'Baltimore −3.5');
  assert.equal(outcomeLabel(g, g.markets[4]), 'Kansas City team total Over 20.5');
});

test('most likely ranks by the sim, surest first, within each family', () => {
  const sections = mostLikely([likelyGame()]);
  assert.deepEqual(sections.map((s) => s.key), ['winners', 'spreads', 'totals', 'players']);
  const winners = sections.find((s) => s.key === 'winners');
  assert.equal(winners.rows[0].label, 'Baltimore win');
  assert.equal(winners.rows[0].p_model, 0.62);
  assert.equal(winners.rows[0].price, -150);
  assert.equal(winners.rows[0].venue, 'fanduel');
  assert.equal(winners.rows[1].label, 'Kansas City win');
  const totals = sections.find((s) => s.key === 'totals');
  assert.deepEqual(totals.rows.map((r) => r.label), ['Kansas City team total Over 20.5', 'Under 45.5']);
  assert.equal(totals.rows[0].price, null);
  const players = sections.find((s) => s.key === 'players');
  // The two sides of one line are two outcomes; the surer one leads and the
  // sentinel row never reaches the surface.
  assert.equal(players.n, 2);
  assert.equal(players.rows[0].label, 'Lamar Jackson Over 224.5 pass yards');
  assert.equal(players.rows[0].price, -105);
});

test('a family caps at the limit but keeps its full list for the toggle', () => {
  const sections = mostLikely([likelyGame()], { limit: 1 });
  const winners = sections.find((s) => s.key === 'winners');
  assert.equal(winners.top.length, 1);
  assert.equal(winners.rows.length, 2);
  assert.equal(winners.n, 2);
});

test('a prop line collapses to its best price across books', () => {
  const lines = collapseProps(likelyGame().props);
  assert.equal(lines.length, 2);
  const over = lines.find((l) => l.side === 'over');
  assert.equal(over.price, -105);
  assert.equal(over.venue, 'betmgm');
  assert.equal(over.n_books, 2);
  assert.equal(over.p_model, 0.58);
});

test('the player book pairs both sides of a line and carries the DFS row', () => {
  const rows = playerBook([likelyGame()]);
  assert.equal(rows.length, 1);
  const r = rows[0];
  assert.equal(r.player, 'Lamar Jackson');
  assert.equal(r.point, 224.5);
  assert.equal(r.p_over, 0.58);
  assert.ok(Math.abs(r.p_under - 0.42) < 1e-9);
  assert.equal(r.lean, 'over');
  assert.equal(r.over.price, -105);
  assert.equal(r.under.price, -105);
  assert.equal(r.team, 'BAL');
  assert.equal(r.position, 'QB');
  assert.equal(r.salary, 8200);
  assert.equal(r.dfs_points, 22.4);
});

test('a line quoted on one side only still gets both probabilities', () => {
  const g = likelyGame();
  g.props = g.props.filter((p) => p.side !== 'over');
  const [r] = playerBook([g]);
  assert.equal(r.over, null);
  assert.ok(Math.abs(r.p_over - 0.58) < 1e-9);
  assert.equal(r.lean, 'over');
});

test('accuracy summarises bias, error and calibration, newest game first', () => {
  const rows = [
    { league: 'nfl', game_id: 'a', game_date: '2026-09-13', mu_home: 25, mu_away: 21,
      fair_total: 46, p_home_win: 0.62, home_score: 27, away_score: 20,
      total_percentile: 0.55, winner_percentile: 0.6, p_winner_pregame: 0.62 },
    { league: 'nfl', game_id: 'b', game_date: '2026-09-06', mu_home: 20, mu_away: 24,
      fair_total: 44, p_home_win: 0.4, home_score: 31, away_score: 17,
      total_percentile: 0.85, winner_percentile: 0.9, p_winner_pregame: 0.4 },
    { league: '__none__', game_id: 'x' },
  ];
  const s = accuracySummary(rows);
  assert.equal(s.n, 2);
  assert.equal(s.games[0].game_id, 'a');
  // Sim minus actual: (46 − 47) and (44 − 48), so the sim ran 2.5 points low.
  assert.ok(Math.abs(s.total_bias + 2.5) < 1e-9);
  assert.ok(Math.abs(s.total_mae - 2.5) < 1e-9);
  // Margins: (4 − 7) and (−4 − 14) → 3 and 18 → 10.5.
  assert.ok(Math.abs(s.margin_mae - 10.5) < 1e-9);
  assert.equal(s.fav_won, 0.5);
  assert.ok(Math.abs(s.brier - ((0.62 - 1) ** 2 + (0.4 - 1) ** 2) / 2) < 1e-9);
  assert.equal(s.deciles[5], 1);
  assert.equal(s.deciles[8], 1);
  assert.equal(s.middle_share, 0.5);
  assert.equal(accuracySummary(rows, 'ncaaf').n, 0);
});

test('an empty accuracy chain is a typed empty summary, not a crash', () => {
  const s = accuracySummary([{ league: '__none__' }]);
  assert.equal(s.n, 0);
  assert.equal(s.total_bias, null);
  assert.equal(s.deciles.length, 10);
});
