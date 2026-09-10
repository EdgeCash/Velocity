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

/** Plain fixed-point, em-dash for missing. */
export function num(value, dp = 2) {
  const n = Number(value);
  if (value === null || value === undefined || Number.isNaN(n)) return '—';
  return n.toFixed(dp);
}

/** A fraction as a percentage; `sign` adds the leading +/−. */
export function pct(value, dp = 1, sign = false) {
  const n = Number(value);
  if (value === null || value === undefined || Number.isNaN(n)) return '—';
  const body = `${Math.abs(n * 100).toFixed(dp)}%`;
  if (!sign) return `${(n * 100).toFixed(dp)}%`;
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
  return MARKET_LABEL[key] ?? key.replace(/_/g, ' ');
}

/** over/under/home/away → OVER/UNDER/HOME/AWAY, players left alone. */
export function sideLabel(side) {
  return String(side ?? '').toUpperCase();
}
