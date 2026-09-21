// The private mobile board — /board on this Worker (docs/PHASE13_STAGE2_CLOUDFLARE.md).
//
// A VIEW over the export layer's CSVs and nothing else. The owner's Stage 2
// directive is explicit and this file is written to be auditable against it:
//
//   * read-only — there is no POST route and no dispatch; the iOS Shortcut is
//     the single control plane (docs/IOS_SHORTCUTS.md);
//   * consumer of exports only — no simulation, no pricing, no wagering
//     calculation, no business logic. Every number rendered here was computed
//     by Velocity and written to a CSV. This module parses, selects, sorts and
//     formats. It never derives a quantity;
//   * Velocity remains the sole source of truth. If a number looks wrong, the
//     bug is upstream of here by construction.
//
// Sorting is presentation, not computation: `.sort()` on a column the export
// already wrote states no new fact. Joining props.csv's projection onto a play
// by (player, market) is a lookup for display, not a derivation.
//
// Pure functions, no Worker globals, so site/tests/board.test.mjs can exercise
// the whole thing under `node --test` with no runtime.

// --- CSV ---------------------------------------------------------------------

// A real parser, not a split on commas: the `reason` column carries prose with
// commas and the odd quote, and a naive split silently shifts every column
// after it — which would put a stake where a confidence belongs and look
// entirely plausible.
export function parseCsv(text) {
  if (!text) return [];
  const clean = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text; // BOM
  const rows = [];
  let field = '';
  let row = [];
  let quoted = false;
  for (let i = 0; i < clean.length; i += 1) {
    const ch = clean[i];
    if (quoted) {
      if (ch === '"') {
        if (clean[i + 1] === '"') { field += '"'; i += 1; } else { quoted = false; }
      } else field += ch;
      continue;
    }
    if (ch === '"') { quoted = true; continue; }
    if (ch === ',') { row.push(field); field = ''; continue; }
    if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && clean[i + 1] === '\n') i += 1;
      row.push(field); field = '';
      if (row.length > 1 || row[0] !== '') rows.push(row);
      row = [];
      continue;
    }
    field += ch;
  }
  row.push(field);
  if (row.length > 1 || row[0] !== '') rows.push(row);
  if (!rows.length) return [];
  const header = rows[0];
  return rows.slice(1).map((values) => {
    const record = {};
    header.forEach((name, i) => { record[name] = values[i] ?? ''; });
    return record;
  });
}

// --- values ------------------------------------------------------------------

export function num(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

const MINUS = '−'; // docs/SITE.md: a negative number wears a real minus

function fixed(value, places) {
  const n = num(value);
  if (n === null) return '—';
  return `${n < 0 ? MINUS : ''}${Math.abs(n).toFixed(places)}`;
}

export function pct(value, places = 1) {
  const n = num(value);
  if (n === null) return '—';
  return `${n < 0 ? MINUS : ''}${Math.abs(n * 100).toFixed(places)}%`;
}

export function money(value) {
  const n = num(value);
  return n === null ? '—' : `$${n.toFixed(2)}`;
}

export function signedPoints(value, places = 1) {
  const n = num(value);
  if (n === null) return '—';
  if (n === 0) return `0.${'0'.repeat(places)}`;
  return `${n < 0 ? MINUS : '+'}${Math.abs(n).toFixed(places)}`;
}

// How old the numbers are, said in words. The data's own stamp, never the
// clock: a page that reports when it was REQUESTED calls stale data fresh,
// which is the worst failure a dashboard has.
export function ageLabel(generatedAt, now = new Date()) {
  if (!generatedAt) return 'age unknown';
  const made = new Date(generatedAt);
  if (Number.isNaN(made.getTime())) return 'age unknown';
  const minutes = Math.round((now.getTime() - made.getTime()) / 60000);
  if (minutes < 0) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = minutes / 60;
  if (hours < 24) return `${hours.toFixed(1)} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

// --- the model ---------------------------------------------------------------

const TIERS = ['A+', 'A', 'B', 'Watch'];

function byNumberDesc(key) {
  return (a, b) => (num(b[key]) ?? -Infinity) - (num(a[key]) ?? -Infinity);
}

function absEdgeDesc(key) {
  return (a, b) => Math.abs(num(b[key]) ?? 0) - Math.abs(num(a[key]) ?? 0);
}

// props.csv holds the projection and the line; plays.csv holds the tier, the
// confidence and the stake. The card wants both, so the projection is looked
// up by (player, market). A lookup, not a derivation.
function propDetail(props) {
  const index = new Map();
  for (const row of props) {
    index.set(`${(row.player || '').toLowerCase()}|${row.market || ''}`, row);
  }
  return (play) => {
    // plays.csv's selection reads "Bijan Robinson Over 64.5 rush_yds"; the
    // market column carries the stat, so the player is what precedes the side.
    const selection = play.selection || '';
    const market = play.market || '';
    const name = selection.split(/\s+(Over|Under)\s+/i)[0] || '';
    return index.get(`${name.toLowerCase()}|${market}`) || null;
  };
}

export function boardModel(files = {}, now = new Date()) {
  const readiness = parseCsv(files['readiness.csv']);
  const plays = parseCsv(files['plays.csv']);
  const props = parseCsv(files['props.csv']);
  const games = parseCsv(files['games.csv']);
  const dfs = parseCsv(files['dfs.csv']);

  const verdict = readiness.find((r) => r.verdict)?.verdict || 'UNKNOWN';
  const generatedAt = readiness[0]?.generated_at || plays[0]?.generated_at
    || games[0]?.generated_at || '';
  const season = readiness[0]?.season || games[0]?.season || '';
  const week = readiness[0]?.week || games[0]?.week || '';
  const kickoff = num(readiness[0]?.minutes_to_kickoff);

  const detail = propDetail(props);
  const withProps = plays
    .filter((p) => p.bet_type === 'prop')
    .map((p) => ({ ...p, _detail: detail(p) }));
  const gamePlays = plays.filter((p) => p.bet_type !== 'prop');
  const tiered = (rows, tier) => rows
    .filter((r) => r.tier === tier)
    .sort(byNumberDesc('confidence'));

  return {
    verdict,
    generatedAt,
    age: ageLabel(generatedAt, now),
    season,
    week,
    kickoff,
    surfaces: readiness.map((r) => ({
      label: r.label, status: r.status, detail: r.detail, required: r.required,
    })),
    aPlus: tiered(gamePlays, 'A+'),
    a: tiered(gamePlays, 'A'),
    props: TIERS.slice(0, 3)
      .flatMap((tier) => tiered(withProps, tier)),
    games: [...games].sort(absEdgeDesc('total_edge')),
    dfsCore: [...dfs].sort(byNumberDesc('projection')).slice(0, 8),
    dfsValues: [...dfs].sort(byNumberDesc('value_score')).slice(0, 8),
    watch: plays.filter((p) => p.tier === 'Watch').sort(byNumberDesc('edge')),
  };
}

// --- rendering ---------------------------------------------------------------

export function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

const VERDICT_TONE = {
  READY: 'ok', DEGRADED: 'warn', 'NOT READY': 'bad', UNKNOWN: 'warn',
};

// Portrait first, per the directive: cards rather than tables, stacked
// sections, and the argument behind a <details> so a tap opens it. <details>
// is native — no JavaScript ships with this page at all, which is also why it
// cannot misbehave on a tablet browser.
const CSS = `
:root{--ink:#14181f;--dim:#5b6472;--line:#e3e7ee;--bg:#f6f7f9;--card:#fff;
--navy:#1f3864;--ok:#1b7f3b;--okbg:#e8f5ec;--warn:#8a6100;--warnbg:#fdf3dc;
--bad:#a3212f;--badbg:#fbe9ea}
@media(prefers-color-scheme:dark){:root{--ink:#e9edf3;--dim:#9aa5b4;
--line:#2a313c;--bg:#12151a;--card:#1a1f27;--navy:#9db8ec;--okbg:#14301f;
--ok:#6ddc93;--warnbg:#332a12;--warn:#f0c264;--badbg:#341a1d;--bad:#f28b95}}
*{box-sizing:border-box}
body{margin:0;padding:0 14px 56px;background:var(--bg);color:var(--ink);
font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
-webkit-text-size-adjust:100%}
main{max-width:720px;margin:0 auto}
h1{font-size:19px;margin:18px 0 2px;letter-spacing:-.01em}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.07em;
color:var(--dim);margin:26px 0 8px}
.sub{color:var(--dim);font-size:13px;margin:0 0 14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:12px 14px;margin:0 0 8px;overflow-wrap:anywhere}
.row{display:flex;align-items:baseline;gap:10px}
.grow{flex:1;min-width:0}
.chip{flex:none;font-weight:700;font-size:12px;padding:3px 8px;border-radius:99px;
background:var(--bg);border:1px solid var(--line)}
.chip.ok{background:var(--okbg);color:var(--ok);border-color:transparent}
.chip.warn{background:var(--warnbg);color:var(--warn);border-color:transparent}
.chip.bad{background:var(--badbg);color:var(--bad);border-color:transparent}
.call{font-weight:650;font-size:17px;color:var(--navy)}
.meta{color:var(--dim);font-size:13px;margin-top:3px}
.money{flex:none;font-weight:700;font-variant-numeric:tabular-nums}
details{margin-top:8px}
summary{cursor:pointer;color:var(--dim);font-size:13px;padding:6px 0;
list-style:none;min-height:30px}
summary::-webkit-details-marker{display:none}
summary::after{content:" ⌄"}
details[open] summary::after{content:" ⌃"}
.why{color:var(--dim);font-size:13.5px;padding:2px 0 4px}
.kv{display:flex;flex-wrap:wrap;gap:4px 16px;margin-top:6px;
font-variant-numeric:tabular-nums;font-size:13.5px}
.kv span b{font-weight:650;color:var(--ink)}
.kv span{color:var(--dim)}
.empty{color:var(--dim);font-size:14px;padding:4px 0 10px}
footer{color:var(--dim);font-size:12px;margin-top:34px;padding-top:12px;
border-top:1px solid var(--line)}
`;

function kv(pairs) {
  const live = pairs.filter(([, v]) => v !== '—' && v !== '' && v != null);
  if (!live.length) return '';
  return `<div class="kv">${live
    .map(([k, v]) => `<span>${escapeHtml(k)} <b>${escapeHtml(v)}</b></span>`)
    .join('')}</div>`;
}

// Which game, and when it starts. A prop names a player and nothing else, so
// without this the reader cannot tell who Matthew Golden is playing, nor
// whether the game has already kicked off. Both decide whether the card is
// still actionable. Central time, to match the workbook and the DFS cards.
export function kickoffLabel(value) {
  if (!value) return '';
  const when = new Date(value);
  if (Number.isNaN(when.getTime())) return '';
  return when.toLocaleString('en-US', {
    timeZone: 'America/Chicago', weekday: 'short',
    hour: 'numeric', minute: '2-digit',
  }) + ' CT';
}

export function gameLine(play) {
  const bits = [];
  if (play.matchup) bits.push(String(play.matchup));
  const kick = kickoffLabel(play.kickoff);
  if (kick) bits.push(kick);
  if (play.market) bits.push(String(play.market));
  return bits.length ? bits.join(' · ') + ' · ' : '';
}

function playCard(play, extra = []) {
  const reason = String(play.reason || '');
  const body = reason.includes(' — ') ? reason.split(' — ').slice(1).join(' — ') : reason;
  const tone = play.tier === 'Watch' ? '' : 'ok';
  return `<article class="card">
  <div class="row">
    <span class="chip ${tone}">${escapeHtml(play.tier || '')}</span>
    <span class="grow call">${escapeHtml(play.selection || '')}</span>
    <span class="money">${escapeHtml(money(play.stake))}</span>
  </div>
  <div class="meta">${escapeHtml(gameLine(play))}edge ${escapeHtml(pct(play.edge))} · confidence ${escapeHtml(fixed(play.confidence, 1))}</div>
  ${kv(extra)}
  ${body ? `<details><summary>Why</summary><div class="why">${escapeHtml(body)}</div></details>` : ''}
</article>`;
}

function section(title, cards, emptyNote) {
  return `<h2>${escapeHtml(title)}</h2>${
    cards.length ? cards.join('') : `<p class="empty">${escapeHtml(emptyNote)}</p>`}`;
}

export function renderBoard(model, { now = new Date() } = {}) {
  const tone = VERDICT_TONE[model.verdict] || 'warn';
  const missing = model.surfaces.filter((s) => s.status && s.status !== 'ok');
  const kickoffLine = model.kickoff === null ? ''
    : model.kickoff >= 0
      ? `next kickoff in ${Math.round(model.kickoff)} min`
      : `first kickoff ${Math.abs(Math.round(model.kickoff))} min ago`;

  const status = `<article class="card">
  <div class="row">
    <span class="chip ${tone}">${escapeHtml(model.verdict)}</span>
    <span class="grow meta" style="margin:0">Updated ${escapeHtml(model.age)}${
  kickoffLine ? ` · ${escapeHtml(kickoffLine)}` : ''}</span>
  </div>
  <div class="meta">${escapeHtml(model.generatedAt || 'no run found')}</div>
  ${missing.length ? `<details><summary>${missing.length} surface${
  missing.length === 1 ? '' : 's'} missing or stale</summary><div class="why">${
  missing.map((s) => `${escapeHtml(s.label)}: ${escapeHtml(s.status)}${
    s.detail ? ` — ${escapeHtml(s.detail)}` : ''}`).join('<br>')}</div></details>` : ''}
</article>`;

  const propCards = model.props.map((p) => playCard(p, [
    ['line', p._detail ? fixed(p._detail.line, 1) : '—'],
    ['projection', p._detail ? fixed(p._detail.projection, 1) : '—'],
  ]));

  const gameCards = model.games.map((g) => `<article class="card">
  <div class="row">
    <span class="grow call">${escapeHtml(g.away_team || '')} @ ${escapeHtml(g.home_team || '')}</span>
  </div>
  ${kv([
    ['spread', fixed(g.market_spread, 1)],
    ['total', fixed(g.market_total, 1)],
    ['proj', fixed(g.model_total, 1)],
    ['spread edge', signedPoints(g.spread_edge)],
    ['total edge', signedPoints(g.total_edge)],
    ['cover', pct(g.cover_probability)],
    ['over', pct(g.over_probability)],
  ])}
</article>`);

  const dfsCard = (p) => `<article class="card">
  <div class="row">
    <span class="grow call">${escapeHtml(p.player || '')}</span>
    <span class="money">${escapeHtml(p.salary ? `$${Number(p.salary).toLocaleString('en-US')}` : '—')}</span>
  </div>
  <div class="meta">${escapeHtml(p.position || '')} · ${escapeHtml(p.team || '')}</div>
  ${kv([
    ['proj', fixed(p.projection, 1)],
    ['ceiling', fixed(p.ceiling, 1)],
    ['value', fixed(p.value_score, 2)],
    ['stack', fixed(p.stack_rating, 1)],
  ])}
</article>`;

  return `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow">
<meta name="color-scheme" content="light dark">
<title>Velocity — Board</title>
<style>${CSS}</style>
</head><body><main>
<h1>Velocity — Board</h1>
<p class="sub">${escapeHtml([model.season, model.week && `Week ${model.week}`]
    .filter(Boolean).join(' · ') || 'season unknown')}</p>
${status}
${section('A+ Plays', model.aPlus.map((p) => playCard(p)), 'No A+ plays on this board.')}
${section('A Plays', model.a.map((p) => playCard(p)), 'No A plays on this board.')}
${section('Props', propCards, 'No props on this board.')}
${section('Betting Card', gameCards, 'No games on this board.')}
${section('DFS Core', model.dfsCore.map(dfsCard), 'No DFS pool on this board.')}
${section('Top DFS Values', model.dfsValues.map(dfsCard), 'No DFS pool on this board.')}
${section('Watch List', model.watch.map((p) => playCard(p)), 'Nothing on the watch list.')}
<footer>Read-only view of Velocity's exports. Runs are dispatched from the
iOS Shortcut, never from this page. Numbers are the simulation's own —
nothing on this page is computed here.<br>Rendered ${escapeHtml(now.toISOString().replace(/\.\d+Z$/, 'Z'))}.</footer>
</main></body></html>`;
}

// Every file the board reads. Named once so the Worker and the publish step
// cannot disagree about the set.
export const BOARD_FILES = [
  'readiness.csv', 'plays.csv', 'props.csv', 'games.csv', 'dfs.csv',
];
