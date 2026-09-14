// The board's number rules, where they are actually implemented.
//
// The old site's pages were Evidence DataTables, and these rules were checked
// by scanning the markdown for `fmt=` strings (tests/test_site_pages.py). The
// hub renders its numbers in Svelte instead, so the rules now live in
// format.js — and the checking has to follow them here, or retiring the tables
// silently retired the rules with them.
//
// The two rules, both from docs/SITE.md:
//
//   * a negative number wears a REAL minus (U+2212), never a hyphen;
//   * a number that carries a colour also carries a sign, because profit green
//     and loss red are indistinguishable under deuteranopia.

import test from 'node:test';
import assert from 'node:assert/strict';

import {
  ageLabel, ageTone, american, isNum, marketLabel, num, pct, signed,
  stampLabel, stampTime, venueMark,
} from '../components/format.js';

const MINUS = '−';
const HYPHEN = '-';

test('a negative fixed-point number wears a real minus', () => {
  // The gap this closes: `toFixed` emits a hyphen, and `num` is what renders a
  // SPREAD POINT — which is negative as often as a profit is.
  assert.equal(num(-3.5, 1), `${MINUS}3.5`);
  assert.ok(!num(-3.5, 1).includes(HYPHEN));
  assert.equal(num(3.5, 1), '3.5');
});

test('a negative percentage wears a real minus with or without a sign', () => {
  assert.equal(pct(-0.0163, 2), `${MINUS}1.63%`);
  assert.equal(pct(-0.0163, 2, true), `${MINUS}1.63%`);
  assert.ok(!pct(-0.0163, 2).includes(HYPHEN));
});

test('signed always states the direction, which is what makes the colour safe', () => {
  assert.equal(signed(22.9, 2), '+22.90');
  assert.equal(signed(-22.9, 2), `${MINUS}22.90`);
  // Zero is neither, and must not claim to be positive.
  assert.equal(signed(0, 2), '0.00');
});

test('American odds always wear their sign — that is what makes them odds', () => {
  assert.equal(american(110), '+110');
  assert.equal(american(-110), `${MINUS}110`);
  assert.ok(!american(-110).includes(HYPHEN));
});

test('a missing number is an em-dash, never a zero', () => {
  // Printing 0 for "we have no price" is the one formatting bug that changes a
  // decision rather than just looking wrong.
  for (const fn of [num, pct, signed, american]) {
    assert.equal(fn(null), '—');
    assert.equal(fn(undefined), '—');
    assert.equal(fn(NaN), '—');
  }
});

test('isNum rejects the two values that pass a naive finiteness check', () => {
  // This is the whole reason it exists. `Number(null)` is 0 and `Number('')`
  // is 0 — both finite — so `Number.isFinite(Number(x))` says YES to a value
  // there is nothing to print for, the component renders the field, and the
  // formatter fills it with an em-dash. A labelled "Real − claim: —" where
  // the field should not have been there at all.
  assert.equal(Number.isFinite(Number(null)), true, 'the trap being closed');
  assert.equal(isNum(null), false);
  assert.equal(isNum(''), false);
  assert.equal(isNum(undefined), false);
  assert.equal(isNum(NaN), false);
  assert.equal(isNum(0), true, 'zero is a real number and must print');
  assert.equal(isNum(-3.5), true);
  assert.equal(isNum('48.5'), true, 'a numeric string from the feed still counts');
  assert.equal(isNum('not a number'), false);
  assert.equal(isNum(Infinity), false);
});

test('a prop market from the feed is cased like every other market', () => {
  assert.equal(marketLabel('moneyline'), 'Moneyline');
  assert.equal(marketLabel('pitcher strikeouts'), 'Pitcher strikeouts');
  assert.equal(marketLabel('player_reception_yds'), 'Player reception yds');
  assert.equal(marketLabel(null), '');
});

/* ---- when the board was last updated ---------------------------------- */

test('a capture stamp reads as the UTC instant it names', () => {
  assert.equal(stampTime('20260912T235148Z'), Date.UTC(2026, 8, 12, 23, 51, 48));
  // The stamps in the wild carry the Z; the ones in older filenames do not.
  assert.equal(stampTime('20260912T235148'), Date.UTC(2026, 8, 12, 23, 51, 48));
  assert.equal(stampLabel('20260909T191129Z'), 'Sep 9, 19:11 UTC');
});

test('a naive built_at is read as UTC, not as the viewer local time', () => {
  // THE trap this function exists for. `built_at` is written by
  // build_site_data.py as `pd.Timestamp.now("UTC").tz_localize(None)` — a UTC
  // instant with the zone stripped off — and `Date.parse` is *specified* to
  // read a bare ISO string as LOCAL time. A viewer west of Greenwich would
  // therefore see a build timestamped in the future, and the staleness chip
  // would read "updated in 4h": a number that looks like broken data rather
  // than a broken clock.
  const want = Date.UTC(2026, 8, 12, 23, 51, 48);
  assert.equal(stampTime('2026-09-12T23:51:48'), want);
  assert.equal(stampTime('2026-09-12 23:51:48'), want, 'DuckDB spells it with a space');
  assert.equal(stampTime('2026-09-12T23:51:48.123456'), want + 123);
  assert.equal(stampTime('2026-09-12T23:51:48Z'), want, 'an explicit zone still works');
  assert.equal(stampTime(new Date(want)), want, 'and a real Date passes through');
});

test('a missing or unreadable stamp is null, never an instant', () => {
  // `new Date(null)` is the epoch, which would render as "56y ago" rather
  // than as "we do not know" — the same trap `toTime` closes in model.js.
  for (const value of [null, undefined, '', 'not a stamp', '2026-13-45']) {
    assert.equal(stampTime(value), null, `${value} must not parse`);
  }
});

test('an age reads as the answer to "how old", not as a timestamp', () => {
  assert.equal(ageLabel(0), 'just now');
  assert.equal(ageLabel(14 * 60_000), '14m ago');
  assert.equal(ageLabel(3 * 3600_000), '3h ago');
  assert.equal(ageLabel(50 * 3600_000), '2d ago');
  // A runner's clock a minute ahead of the viewer's must not print "−1m ago".
  assert.equal(ageLabel(-90_000), 'just now');
  assert.equal(ageLabel(NaN), 'unknown');
});

test('the staleness tiers are the slate cadence, not round numbers', () => {
  // live-slate.yml runs 16:53 and 22:53 UTC, so six hours apart and then
  // eighteen. Past 8h a run has been missed; past 20h the evening run went
  // missing too and the whole board is yesterday's.
  assert.equal(ageTone(2 * 3600_000), 'fresh');
  assert.equal(ageTone(7.9 * 3600_000), 'fresh');
  assert.equal(ageTone(8 * 3600_000), 'aging');
  assert.equal(ageTone(19 * 3600_000), 'aging');
  assert.equal(ageTone(20 * 3600_000), 'stale');
  // No stamp is stale by definition: not knowing how old the prices are is
  // not the reassuring case.
  assert.equal(ageTone(NaN), 'stale');
});

test('every venue resolves to a two-letter mark', () => {
  assert.equal(venueMark('kalshi'), 'KA');
  assert.equal(venueMark('polymarket'), 'PO');
  assert.equal(venueMark('draftkings'), 'DK');
  assert.equal(venueMark('williamhill_us'), 'CA'); // Caesars
  assert.equal(venueMark(''), '');
});
