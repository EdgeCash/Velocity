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
  american, marketLabel, num, pct, signed, venueMark,
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

test('a prop market from the feed is cased like every other market', () => {
  assert.equal(marketLabel('moneyline'), 'Moneyline');
  assert.equal(marketLabel('pitcher strikeouts'), 'Pitcher strikeouts');
  assert.equal(marketLabel('player_reception_yds'), 'Player reception yds');
  assert.equal(marketLabel(null), '');
});

test('every venue resolves to a two-letter mark', () => {
  assert.equal(venueMark('kalshi'), 'KA');
  assert.equal(venueMark('polymarket'), 'PO');
  assert.equal(venueMark('draftkings'), 'DK');
  assert.equal(venueMark('williamhill_us'), 'CA'); // Caesars
  assert.equal(venueMark(''), '');
});
