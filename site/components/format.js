// Number formatting for the Velocity surfaces.
//
// One rule behind all of it: a number that carries a color must also carry a
// sign. Profit green and loss red are indistinguishable to a deuteranope, so
// the sign — not the hue — is what actually says which way the money went
// (the hue is the fast second read for everyone else).

/** A signed number, fixed to `dp`, with a real minus sign. */
export function signed(value, dp = 2) {
  const n = Number(value);
  if (value === null || value === undefined || Number.isNaN(n)) return '—';
  return `${n > 0 ? '+' : n < 0 ? '−' : ''}${Math.abs(n).toFixed(dp)}`;
}

/** The real minus sign, U+2212, in place of the ASCII hyphen.
 *
 * `toFixed` emits a hyphen-minus, which on the board face is visibly shorter
 * and sits at the wrong height beside a tabular digit — the one typographic
 * detail that most makes a board look like a spreadsheet. Every formatter here
 * goes through this rather than each caller remembering, because the callers
 * that forget are the ones nobody looks at: a spread POINT is negative as
 * often as a profit is, and it reached the surface through `num`.
 */
function minus(text) {
  return text.replace('-', '−');
}

/** Is this a value worth printing as a number at all?
 *
 * `Number.isFinite(Number(x))` is the obvious guard and it is WRONG for the
 * two values that actually turn up: `Number(null)` is 0 and `Number('')` is 0,
 * both perfectly finite. A component guarding with it therefore decides to
 * render a field it has no value for, and the formatter then prints an
 * em-dash — a labelled "Real − claim: —" where the field should simply not
 * have been there. Every such guard on this surface goes through here.
 */
export function isNum(value) {
  if (value === null || value === undefined || value === '') return false;
  return Number.isFinite(Number(value));
}

/** Plain fixed-point, em-dash for missing. */
export function num(value, dp = 2) {
  const n = Number(value);
  if (value === null || value === undefined || Number.isNaN(n)) return '—';
  return minus(n.toFixed(dp));
}

/** A fraction as a percentage; `sign` adds the leading +/−. */
export function pct(value, dp = 1, sign = false) {
  const n = Number(value);
  if (value === null || value === undefined || Number.isNaN(n)) return '—';
  const body = `${Math.abs(n * 100).toFixed(dp)}%`;
  if (!sign) return minus(`${(n * 100).toFixed(dp)}%`);
  return `${n > 0 ? '+' : n < 0 ? '−' : ''}${body}`;
}

/** American odds always wear their sign — that is what makes them odds. */
export function american(value) {
  const n = Number(value);
  if (value === null || value === undefined || Number.isNaN(n)) return '—';
  return `${n > 0 ? '+' : '−'}${Math.abs(Math.round(n))}`;
}

/** A handicap: signed for spreads, bare for totals. */
export function line(value, market) {
  const n = Number(value);
  if (value === null || value === undefined || Number.isNaN(n)) return '';
  if (String(market || '').startsWith('spread')) return signed(n, 1).replace('.0', '');
  return n.toFixed(1).replace(/\.0$/, '');
}

/** ▲ / ▼ / · — the secondary channel that makes the color redundant. */
export function arrow(value) {
  const n = Number(value);
  if (value === null || value === undefined || Number.isNaN(n) || n === 0) return '·';
  return n > 0 ? '▲' : '▼';
}

/** 'pos' | 'neg' | 'flat' — the class the color hangs off. */
export function tone(value) {
  const n = Number(value);
  if (value === null || value === undefined || Number.isNaN(n) || n === 0) return 'flat';
  return n > 0 ? 'pos' : 'neg';
}

/** 20260909T191129Z → Sep 9, 19:11 UTC. */
export function stampLabel(stamp) {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})/.exec(stamp ?? '');
  if (!m) return stamp ?? '';
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${months[+m[2] - 1]} ${+m[3]}, ${m[4]}:${m[5]} UTC`;
}

/** A kickoff Date → "Sun 1:00 PM" in the viewer's own zone. */
export function kickoffLabel(value) {
  if (!value) return '';
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString(undefined, {
    weekday: 'short', hour: 'numeric', minute: '2-digit',
  });
}

/** Market codes → the words a bettor uses. */
export const MARKET_LABEL = {
  spread: 'Spread',
  total: 'Total',
  moneyline: 'Moneyline',
  team_total_home: 'Team total (home)',
  team_total_away: 'Team total (away)',
  parlay: 'Parlay',
};

export function marketLabel(market) {
  const key = String(market ?? '');
  if (MARKET_LABEL[key]) return MARKET_LABEL[key];
  // Prop markets arrive as free text from the odds feed ("pitcher strikeouts",
  // "player_reception_yds") and have no entry above. Sentence-casing them
  // stops a table reading half title-case and half not, which is what a raw
  // passthrough produced.
  const words = key.replace(/_/g, ' ').trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : '';
}

/** over/under/home/away → OVER/UNDER/HOME/AWAY, players left alone. */
export function sideLabel(side) {
  return String(side ?? '').toUpperCase();
}

/** Venue codes → the name the venue actually uses for itself. */
const VENUE_NAME = {
  draftkings: 'DraftKings', fanduel: 'FanDuel', betmgm: 'BetMGM',
  betrivers: 'BetRivers', pointsbetus: 'PointsBet', williamhill_us: 'Caesars',
  betonlineag: 'BetOnline', lowvig: 'LowVig', bovada: 'Bovada',
  mybookieag: 'MyBookie', betus: 'BetUS', fanatics: 'Fanatics',
  espnbet: 'ESPN BET', hardrockbet: 'Hard Rock', ballybet: 'Bally Bet',
  pinnacle: 'Pinnacle', novig: 'Novig', prophetx: 'ProphetX',
  kalshi: 'Kalshi', polymarket: 'Polymarket',
};

/* Each venue gets a monogram tile in its own colour. The genre never writes a
   book's name in a dense view — it shows a mark — and a two-letter tile in the
   right hue is the version of that we can ship without anyone's logo asset.
   A venue we have no colour for falls back to the neutral chip. */
const VENUE_COLOR = {
  draftkings: '#53d337', fanduel: '#1493ff', betmgm: '#c8a55b',
  betrivers: '#3d8bd4', pointsbetus: '#ed1c24', williamhill_us: '#c8a94e',
  betonlineag: '#d8232a', lowvig: '#5b8dff', bovada: '#cc2b2b',
  mybookieag: '#e08a1e', betus: '#d43f3f', fanatics: '#e0405c',
  espnbet: '#e03131', hardrockbet: '#9b4dca', ballybet: '#e8a33d',
  pinnacle: '#d24b4b', novig: '#5b7cff', prophetx: '#f5a623',
  kalshi: '#00d09c', polymarket: '#4a7dff',
};

export function venueLabel(venue) {
  const key = String(venue ?? '').trim().toLowerCase();
  if (!key) return '';
  return VENUE_NAME[key] ?? key.charAt(0).toUpperCase() + key.slice(1);
}

/** The two-letter mark on the tile: KA, PO, DK, FD, MG. */
export function venueMark(venue) {
  const name = venueLabel(venue);
  if (!name) return '';
  const caps = name.replace(/[^A-Za-z]/g, '');
  const initials = name.match(/[A-Z]/g);
  if (initials && initials.length >= 2) return initials.slice(0, 2).join('');
  return caps.slice(0, 2).toUpperCase();
}

export function venueColor(venue) {
  return VENUE_COLOR[String(venue ?? '').trim().toLowerCase()] ?? null;
}

/** An exchange contract is not a sportsbook line, and the card says so. */
export const LADDER_VENUES = new Set(['kalshi', 'polymarket']);

export function isExchange(venue) {
  return LADDER_VENUES.has(String(venue ?? '').trim().toLowerCase());
}

/* Over/under abbreviates by density, the way every board in the genre does
   it: `o48.5` in a grid, `O 48.5` on a tap target, `Over 48.5` where the bet
   is being confirmed. `mode` picks which. */
export function overUnder(side, point, mode = 'tap') {
  const s = String(side ?? '').toLowerCase();
  const n = Number(point);
  const value = Number.isNaN(n) ? '' : String(n.toFixed(1)).replace(/\.0$/, '');
  if (s !== 'over' && s !== 'under') return null;
  if (mode === 'grid') return `${s[0]}${value}`;
  if (mode === 'long') return `${s[0].toUpperCase()}${s.slice(1)} ${value}`;
  return `${s[0].toUpperCase()} ${value}`;
}

/* Where a bet sits on the simulated outcome distribution, and which way it
   wins. The distributions table carries two kinds per game — `margin` (home
   score minus away) and `total` — so a bet has to be turned into a cut point
   on one of them plus a direction.

   Spreads are the fiddly half. A home bet at −1.5 covers when the home margin
   clears +1.5, so the cut is the *negated* handicap; an away bet at +1.5
   covers when the margin stays under +1.5, so the cut is the handicap itself
   and the direction flips. Team totals have no matching distribution and
   return null rather than guess.

   `splitTie` is the moneyline's own wrinkle, and it is not cosmetic. The
   margin distribution is a continuous simulation rounded into integer bins,
   so the `0` bin is really the interval around zero rather than a literal
   tie — and the model prices a moneyline as
   `P(margin > 0) + P(margin = 0) / 2`, which is exactly what its own
   `p_home_win` reports. Counting the bin strictly understates the side by
   about 1.4 points, enough for the printed Sim to disagree with the Belief
   sitting directly above it. A *spread* push is a real push, refunded rather
   than split, and reconciles on the strict count — so the split is asked for
   per market rather than applied to every integer cut. */
export function distThreshold(market, side, point) {
  const m = String(market ?? '').toLowerCase();
  const s = String(side ?? '').toLowerCase();
  const p = Number(point);

  if (m === 'moneyline') {
    if (s === 'home') return { kind: 'margin', at: 0, above: true, splitTie: true };
    if (s === 'away') return { kind: 'margin', at: 0, above: false, splitTie: true };
    return null;
  }
  if (!Number.isFinite(p)) return null;
  if (m === 'total') {
    if (s === 'over') return { kind: 'total', at: p, above: true, splitTie: false };
    if (s === 'under') return { kind: 'total', at: p, above: false, splitTie: false };
    return null;
  }
  if (m === 'spread') {
    if (s === 'home') return { kind: 'margin', at: -p, above: true, splitTie: false };
    if (s === 'away') return { kind: 'margin', at: p, above: false, splitTie: false };
    return null;
  }
  return null;
}

/** `team → {code, color, logo}` from the `velocity.teams` rows.
 *
 * Identity is a join away from every surface that shows a team, and the join
 * key is the team string the data already carries — nflverse codes for the
 * NFL, school names for college. Colours arrive pre-lifted for the dark
 * surface (`color_dark`), because the lightness maths lives in Python where it
 * is tested; `color` is the unadjusted brand primary and only stands in when
 * the lift produced nothing.
 */
export function teamIndex(rows) {
  const out = {};
  for (const row of rows ?? []) {
    const team = row?.team === undefined || row?.team === null ? '' : String(row.team);
    if (!team) continue;
    const identity = {
      code: String(row.code ?? '') || team.slice(0, 3).toUpperCase(),
      color: String(row.color_dark ?? '') || String(row.color ?? ''),
      logo: String(row.logo ?? ''),
    };
    // Keyed both ways. A surface that spans leagues — the card does — must not
    // let one league's "Miami" answer for the other's, and one that has only
    // the team string still resolves.
    out[team] = identity;
    if (row.league) out[`${String(row.league)}|${team}`] = identity;
  }
  return out;
}

/** Identity for one team — never undefined, so a page renders either way.
 *
 * A team the table has never seen still gets a code, because the alternative
 * is a blank where a team name belongs. The logo and colour are simply absent,
 * which is exactly what `TeamMark` is built to handle.
 */
export function teamMark(index, team, league = '') {
  const key = team === undefined || team === null ? '' : String(team);
  const scoped = league ? `${String(league)}|${key}` : '';
  return (index && (index[scoped] || index[key]))
    || { code: key.slice(0, 3).toUpperCase(), color: '', logo: '' };
}

/** Two club colours that will not be mistaken for one another.
 *
 * Clubs share colours: the Patriots and the Seahawks wear the same navy
 * (#002244) and the Bengals and Broncos the same orange, so a sheet can put
 * two identical rules on the page and look broken. This is the card
 * renderer's rule (`velocity.report.assets.bar_colors`) applied to the web
 * surface: when the pair is too close, the **away** side steps lighter and a
 * little less saturated. Lighter is the safe direction on a dark panel — it
 * only adds contrast — and identity never rests on colour anyway, since the
 * mark and the name sit right beside it.
 */
export function distinctPair(awayHex, homeHex) {
  const rgb = (hex) => {
    const h = String(hex ?? '').replace('#', '');
    if (h.length !== 6) return null;
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  };
  const a = rgb(awayHex), h = rgb(homeHex);
  if (!a || !h) return [awayHex, homeHex];
  const distance = a.reduce((sum, v, i) => sum + Math.abs(v - h[i]), 0);
  if (distance >= 0.55) return [awayHex, homeHex];
  // RGB → HLS, lift and desaturate, back again — the same three steps the
  // Python does, with the same constants.
  const max = Math.max(...a), min = Math.min(...a), l = (max + min) / 2;
  let hue = 0, sat = 0;
  if (max !== min) {
    const d = max - min;
    sat = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === a[0]) hue = ((a[1] - a[2]) / d + (a[1] < a[2] ? 6 : 0)) / 6;
    else if (max === a[1]) hue = ((a[2] - a[0]) / d + 2) / 6;
    else hue = ((a[0] - a[1]) / d + 4) / 6;
  }
  const lifted = Math.min(l + 0.28, 0.85), muted = Math.max(sat * 0.7, 0.1);
  const chan = (t) => {
    t = (t + 1) % 1;
    const q = lifted < 0.5 ? lifted * (1 + muted) : lifted + muted - lifted * muted;
    const p = 2 * lifted - q;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  const hex = [chan(hue + 1 / 3), chan(hue), chan(hue - 1 / 3)]
    .map((v) => Math.round(v * 255).toString(16).padStart(2, '0')).join('');
  return [`#${hex}`, homeHex];
}
