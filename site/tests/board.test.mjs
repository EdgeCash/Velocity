// The private mobile board (/board) — docs/PHASE13_STAGE2_CLOUDFLARE.md.
//
// The owner's Stage 2 directive is a short list of properties, and each one
// is a way this page could quietly stop being what was asked for:
//
//   * read-only — no dispatch, no state change, ever;
//   * consumer of exports only — no simulation, pricing, wagering or business
//     logic. It renders numbers Velocity computed; it never derives one;
//   * mobile first — cards, stacked sections, tap-to-expand, no wide tables.
//
// The subtle one is the CSV parser. `reason` is prose with commas and quotes,
// and a naive split shifts every column after it — putting a stake where a
// confidence belongs, and looking entirely plausible while doing it.

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  BOARD_FILES, ageLabel, boardModel, escapeHtml, money, num, parseCsv, pct,
  renderBoard, signedPoints, gameLine, kickoffLabel } from '../board.js';

const NOW = new Date('2026-09-20T23:03:53Z');
const STAMP = '2026-09-20T22:51:53Z';

const FILES = {
  'readiness.csv': [
    'surface,label,status,rows,required,detail,verdict,board_age_minutes,minutes_to_kickoff,generated_at,season,week',
    `games,Board (games),ok,45,yes,,DEGRADED,12.0,131.0,${STAMP},2026,3`,
    `props,Player props,missing,0,no,board 279 min old (bar 75),DEGRADED,12.0,131.0,${STAMP},2026,3`,
  ].join('\n'),
  'plays.csv': [
    'tier,bet_type,selection,market,edge,confidence,stake,reason,generated_at,season,week',
    `A+,game,Under 41.5,total,0.053,5.8,1.69,"Under 41.5 — Model total 37, +4.5 points · rule A",${STAMP},2026,3`,
    `A,game,Atlanta +2.5,spread,0.041,6.2,1.10,"Atlanta +2.5 — Model line ${'−'}2.2",${STAMP},2026,3`,
    `B,prop,Bijan Robinson Over 64.5 rush_yds,rush_yds,0.031,3.7,0.93,"Bijan — model 60.0%",${STAMP},2026,3`,
    `Watch,game,Miami team total Over 16.5,team_total_away,0.034,4.3,0.00,"Miami — VETOED",${STAMP},2026,3`,
  ].join('\n'),
  'props.csv': [
    'player,team,market,line,projection,median,75th_percentile,90th_percentile,99th_percentile,hit_probability,fair_price,market_price,edge,recommended_stake,generated_at,season,week',
    `Bijan Robinson,ATL,rush_yds,64.5,71.2,69,88,106,141,0.6,-115,-110,0.031,0.93,${STAMP},2026,3`,
  ].join('\n'),
  'games.csv': [
    'game_id,league,away_team,home_team,market_spread,market_total,moneyline,model_away_score,model_home_score,model_total,spread_edge,total_edge,cover_probability,over_probability,confidence,kelly_fraction,weather,generated_at,season,week',
    `g1,nfl,Cleveland,Tampa Bay,-2.5,41.5,-142,15.03,21.62,36.65,3.5,-4.5,0.6293,0.3688,,,,${STAMP},2026,3`,
    `g2,nfl,Seattle,Arizona,4.0,40.5,174,24.92,18.21,43.13,-1.0,2.5,0.4450,0.5845,,,,${STAMP},2026,3`,
  ].join('\n'),
  'dfs.csv': [
    'player,team,position,salary,projection,median,ceiling,ownership,value_score,leverage_score,stack_rating,generated_at,season,week',
    `Bijan Robinson,ATL,RB,7600,17.4,16.2,38.1,,2.29,,6.4,${STAMP},2026,3`,
    `Drake London,ATL,WR,6100,13.1,11.8,33.0,,2.15,,5.1,${STAMP},2026,3`,
  ].join('\n'),
};

const model = () => boardModel(FILES, NOW);
const html = () => renderBoard(model(), { now: NOW });

// --- the parser --------------------------------------------------------------

test('a quoted field carrying commas does not shift every later column', () => {
  const rows = parseCsv('a,b,c\n1,"two, and a half",3');
  assert.equal(rows[0].b, 'two, and a half');
  assert.equal(rows[0].c, '3');
});

test('a doubled quote inside a quoted field is one quote', () => {
  assert.equal(parseCsv('a\n"he said ""no"""')[0].a, 'he said "no"');
});

test('the UTF-8 BOM the exports carry is not read as part of the first header', () => {
  const rows = parseCsv('﻿tier,edge\nA+,0.05');
  assert.deepEqual(Object.keys(rows[0]), ['tier', 'edge']);
});

test('CRLF and a trailing newline do not invent a blank row', () => {
  assert.equal(parseCsv('a,b\r\n1,2\r\n').length, 1);
});

test('an empty or absent file is no rows, not a crash', () => {
  assert.deepEqual(parseCsv(''), []);
  assert.deepEqual(parseCsv(undefined), []);
});

// --- values ------------------------------------------------------------------

test('a missing number is an em-dash, never a zero', () => {
  assert.equal(money(''), '—');
  assert.equal(pct(null), '—');
  assert.equal(signedPoints(undefined), '—');
  assert.equal(num(''), null);
});

test('a negative number wears a real minus, per docs/SITE.md', () => {
  assert.ok(pct(-0.053).startsWith('−'));
  assert.ok(signedPoints(-4.5).startsWith('−'));
  assert.ok(!pct(-0.053).includes('-'));
});

test('points edges always state a direction, which is what makes them readable', () => {
  assert.equal(signedPoints(3.5), '+3.5');
  assert.equal(signedPoints(-4.5), '−4.5');
});

test('the age comes from the data stamp, not the clock', () => {
  assert.equal(ageLabel(STAMP, NOW), '12 min ago');
  assert.equal(ageLabel('', NOW), 'age unknown');
  assert.equal(ageLabel('not a date', NOW), 'age unknown');
  assert.ok(ageLabel('2026-09-20T17:00:00Z', NOW).endsWith('h ago'));
});

// --- the model ---------------------------------------------------------------

test('the verdict and the surfaces come straight off readiness.csv', () => {
  const m = model();
  assert.equal(m.verdict, 'DEGRADED');
  assert.equal(m.kickoff, 131);
  const missing = m.surfaces.filter((s) => s.status !== 'ok');
  assert.equal(missing.length, 1);
  assert.match(missing[0].detail, /279 min old/);
});

test('plays split by tier, and props are their own section', () => {
  const m = model();
  assert.deepEqual(m.aPlus.map((p) => p.selection), ['Under 41.5']);
  assert.deepEqual(m.a.map((p) => p.selection), ['Atlanta +2.5']);
  assert.equal(m.props.length, 1);
  assert.equal(m.props[0].tier, 'B');
  assert.equal(m.watch.length, 1);
});

test('a prop play carries the projection and line from props.csv', () => {
  // A lookup for display, not a derivation — the numbers are the export's.
  const prop = model().props[0];
  assert.equal(prop._detail.projection, '71.2');
  assert.equal(prop._detail.line, '64.5');
});

test('games sort by the size of the total edge, either direction', () => {
  assert.deepEqual(model().games.map((g) => g.game_id), ['g1', 'g2']);
});

test('DFS core is by projection and values are by value score', () => {
  const m = model();
  assert.equal(m.dfsCore[0].player, 'Bijan Robinson');
  assert.equal(m.dfsValues[0].player, 'Bijan Robinson');
});

test('an entirely empty board still builds a model', () => {
  const m = boardModel({}, NOW);
  assert.equal(m.verdict, 'UNKNOWN');
  assert.deepEqual([m.aPlus, m.a, m.props, m.games, m.watch].map((x) => x.length),
    [0, 0, 0, 0, 0]);
});

// --- the page ----------------------------------------------------------------

test('every section the directive names is present, in order', () => {
  const page = html();
  const wanted = ['A+ Plays', 'A Plays', 'Props', 'Betting Card', 'DFS Core',
    'Top DFS Values', 'Watch List'];
  let cursor = 0;
  for (const name of wanted) {
    const at = page.indexOf(`>${name}<`, cursor);
    assert.ok(at > -1, `missing section: ${name}`);
    cursor = at;
  }
});

test('the run status leads the page, before any play', () => {
  const page = html();
  assert.ok(page.indexOf('DEGRADED') < page.indexOf('A+ Plays'));
});

test('the page ships no JavaScript at all', () => {
  // Tap-to-expand is <details>, which is native. Nothing to misbehave on a
  // tablet browser, and nothing to keep current.
  const page = html();
  assert.ok(!page.includes('<script'));
  assert.ok(page.includes('<details>'));
});

test('the page is read-only: no form, no button, no dispatch', () => {
  // The iOS Shortcut is the single control plane. A page that can start a run
  // is a page that needs a secret.
  const page = html();
  for (const marker of ['<form', '<button', 'workflow_dispatch', 'api.github.com']) {
    assert.ok(!page.includes(marker), `page carries ${marker}`);
  }
});

test('the page states the data age and never the request time as freshness', () => {
  const page = html();
  assert.ok(page.includes('12 min ago'));
  assert.ok(page.includes(STAMP));
});

test('an empty board says so rather than rendering nothing', () => {
  const page = renderBoard(boardModel({}, NOW), { now: NOW });
  assert.match(page, /No A\+ plays/);
  assert.match(page, /No games on this board/);
});

test('values from the CSVs are escaped into the page', () => {
  assert.equal(escapeHtml('<b>&"x"</b>'), '&lt;b&gt;&amp;&quot;x&quot;&lt;/b&gt;');
  const page = renderBoard(boardModel({
    'plays.csv': 'tier,bet_type,selection,market,edge,confidence,stake,reason\nA+,game,"<img src=x onerror=alert(1)>",total,0.05,5,1,"why"',
  }, NOW), { now: NOW });
  assert.ok(!page.includes('<img src=x'));
  assert.ok(page.includes('&lt;img src=x'));
});

test('no horizontal scroll: long values wrap rather than widen the page', () => {
  assert.match(html(), /overflow-wrap:anywhere/);
});

// --- the wiring --------------------------------------------------------------

test('the Worker reads exactly the files the board declares', () => {
  const worker = readFileSync(new URL('../worker.js', import.meta.url), 'utf8');
  assert.ok(worker.includes('BOARD_FILES'));
  assert.deepEqual(BOARD_FILES,
    ['readiness.csv', 'plays.csv', 'props.csv', 'games.csv', 'dfs.csv']);
});

test('the Worker refuses anything that could change state', () => {
  const worker = readFileSync(new URL('../worker.js', import.meta.url), 'utf8');
  assert.match(worker, /method !== "GET" && request\.method !== "HEAD"/);
  assert.match(worker, /status: 405/);
});

test('the board route is never edge-cached', () => {
  // A cached copy would let a stale board answer a deliberate reload.
  const worker = readFileSync(new URL('../worker.js', import.meta.url), 'utf8');
  assert.match(worker, /"cache-control": "no-store"/);
});

test('wrangler runs the Worker first for /board and binds the bucket', () => {
  const toml = readFileSync(new URL('../wrangler.toml', import.meta.url), 'utf8');
  assert.match(toml, /run_worker_first = \[[^\]]*"\/board"/);
  assert.match(toml, /binding = "BOARD"/);
});

test('the board is published only when Access is confirmed', () => {
  // Not merely when the token exists. The route that serves these files ships
  // behind the same manual confirmation, so the files should not sit in the
  // bucket ahead of it — if the gate is off, the paid-feed CSVs are not there
  // to serve. docs/SITE.md records an unauthenticated request reaching this
  // Worker on 2026-09-12; this is the cheap half of not repeating it.
  for (const name of ['live-slate.yml', 'refresh-exports.yml']) {
    const yml = readFileSync(
      new URL(`../../.github/workflows/${name}`, import.meta.url), 'utf8');
    const step = yml.slice(yml.indexOf('Publish the board to R2'));
    const condition = step.slice(0, step.indexOf('\n        run:'));
    assert.match(condition, /CLOUDFLARE_ACCESS_CONFIRMED == 'true'/, name);
  }
});

test('a play card names the game and the kickoff before the market', () => {
  // "We don't know opponents" — a prop names a player and nothing else, so
  // the board could not say who Matthew Golden was playing or whether the
  // game had started. Both come from plays.csv now.
  assert.equal(
    gameLine({
      matchup: 'Green Bay Packers @ Atlanta Falcons',
      kickoff: '2026-09-22T00:15:00Z',
      market: 'receptions',
    }),
    'Green Bay Packers @ Atlanta Falcons · Mon 7:15 PM CT · receptions · ',
  );
});

test('a card with no game falls back to the market alone', () => {
  // An export written before this column existed, or a game_id that did not
  // join: the card still renders, it just says less.
  assert.equal(gameLine({ market: 'total' }), 'total · ');
  assert.equal(gameLine({}), '');
});

test('an unreadable kickoff is dropped, never printed as Invalid Date', () => {
  assert.equal(kickoffLabel('not a date'), '');
  assert.equal(kickoffLabel(''), '');
  assert.equal(kickoffLabel(undefined), '');
});
